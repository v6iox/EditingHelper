// Audio cross-correlation sync — a 1:1 port of the desktop engine
// (editsync/sync.py + audio.py): onset envelopes for the coarse match,
// raw-audio refinement for precision, confidence + peak-ratio gating.
import Accelerate
import Foundation

struct SyncMatch {
    let offset: Double       // seconds into the reference where the clip starts
    let confidence: Double   // normalized cross-correlation at the peak
    let peakRatio: Double    // winning peak vs best competing peak
    var isConfident: Bool { confidence >= 0.35 && peakRatio >= 1.5 }
}

enum SyncEngine {
    static let sampleRate = 8000.0
    static let envelopeFps = 100.0

    // MARK: envelope (log-energy onset, matching the Python engine)

    static func onsetEnvelope(_ samples: [Float]) -> [Float] {
        let hop = Int(sampleRate / envelopeFps)
        let frames = samples.count / hop
        guard frames >= 2 else { return [0] }
        var env = [Float](repeating: 0, count: frames)
        for i in 0..<frames {
            var rms: Float = 0
            samples.withUnsafeBufferPointer { buf in
                vDSP_rmsqv(buf.baseAddress! + i * hop, 1, &rms, vDSP_Length(hop))
            }
            env[i] = log(1 + 1000 * rms)
        }
        var onset = [Float](repeating: 0, count: frames)
        for i in 1..<frames {
            onset[i] = max(0, env[i] - env[i - 1])
        }
        // 3-frame moving average, same as the desktop engine
        var smooth = onset
        for i in 1..<(frames - 1) {
            smooth[i] = (onset[i - 1] + onset[i] + onset[i + 1]) / 3
        }
        return smooth
    }

    static func zscore(_ x: [Float]) -> [Float] {
        var mean: Float = 0, sd: Float = 0
        vDSP_normalize(x, 1, nil, 1, &mean, &sd, vDSP_Length(x.count))
        guard sd > 1e-12 else { return [Float](repeating: 0, count: x.count) }
        var out = [Float](repeating: 0, count: x.count)
        var negMean = -mean, invSd = 1 / sd
        vDSP_vsadd(x, 1, &negMean, &out, 1, vDSP_Length(x.count))
        vDSP_vsmul(out, 1, &invSd, &out, 1, vDSP_Length(x.count))
        return out
    }

    // MARK: FFT cross-correlation (full lags, like numpy's rotation)

    /// Cross-correlation of ref with tgt for lags -(tgt.count-1)...(ref.count-1).
    static func fftCrossCorrelate(_ ref: [Float], _ tgt: [Float]) -> [Float] {
        let n = ref.count + tgt.count - 1
        var nfft = 1
        while nfft < n { nfft <<= 1 }
        guard
            let forward = vDSP_DFT_zop_CreateSetup(nil, vDSP_Length(nfft), .FORWARD),
            let inverse = vDSP_DFT_zop_CreateSetup(forward, vDSP_Length(nfft), .INVERSE)
        else { return [] }
        defer {
            vDSP_DFT_DestroySetup(forward)
            vDSP_DFT_DestroySetup(inverse)
        }

        func dft(_ x: [Float], _ setup: OpaquePointer,
                 _ inRe: [Float], _ inIm: [Float]) -> ([Float], [Float]) {
            var outRe = [Float](repeating: 0, count: nfft)
            var outIm = [Float](repeating: 0, count: nfft)
            vDSP_DFT_Execute(setup, inRe, inIm, &outRe, &outIm)
            _ = x
            return (outRe, outIm)
        }

        var refPad = ref + [Float](repeating: 0, count: nfft - ref.count)
        var tgtPad = tgt + [Float](repeating: 0, count: nfft - tgt.count)
        let zeros = [Float](repeating: 0, count: nfft)
        let (refRe, refIm) = dft(refPad, forward, refPad, zeros)
        let (tgtRe, tgtIm) = dft(tgtPad, forward, tgtPad, zeros)
        _ = (refPad, tgtPad)
        refPad = []; tgtPad = []

        // ref * conj(tgt)
        var prodRe = [Float](repeating: 0, count: nfft)
        var prodIm = [Float](repeating: 0, count: nfft)
        for i in 0..<nfft {
            prodRe[i] = refRe[i] * tgtRe[i] + refIm[i] * tgtIm[i]
            prodIm[i] = refIm[i] * tgtRe[i] - refRe[i] * tgtIm[i]
        }
        var corrRe = [Float](repeating: 0, count: nfft)
        var corrIm = [Float](repeating: 0, count: nfft)
        vDSP_DFT_Execute(inverse, prodRe, prodIm, &corrRe, &corrIm)
        var scale = 1 / Float(nfft)
        vDSP_vsmul(corrRe, 1, &scale, &corrRe, 1, vDSP_Length(nfft))

        // rotate: negative lags live at the tail of the circular result
        let negCount = tgt.count - 1
        var full = [Float]()
        full.reserveCapacity(n)
        if negCount > 0 {
            full.append(contentsOf: corrRe[(nfft - negCount)...])
        }
        full.append(contentsOf: corrRe[0..<ref.count])
        return full
    }

    // MARK: matching

    static func normalizedPeak(_ ref: [Float], _ tgt: [Float], lag: Int) -> Double {
        let rStart = max(lag, 0)
        let tStart = max(-lag, 0)
        let length = min(ref.count - rStart, tgt.count - tStart)
        guard length > 1 else { return 0 }
        var dot: Float = 0, rNorm: Float = 0, tNorm: Float = 0
        ref.withUnsafeBufferPointer { r in
            tgt.withUnsafeBufferPointer { t in
                vDSP_dotpr(r.baseAddress! + rStart, 1, t.baseAddress! + tStart, 1,
                           &dot, vDSP_Length(length))
                vDSP_svesq(r.baseAddress! + rStart, 1, &rNorm, vDSP_Length(length))
                vDSP_svesq(t.baseAddress! + tStart, 1, &tNorm, vDSP_Length(length))
            }
        }
        let denom = sqrt(Double(rNorm)) * sqrt(Double(tNorm))
        return denom < 1e-12 ? 0 : Double(dot) / denom
    }

    /// Find where the target clip's audio begins inside the reference.
    static func findClip(refAudio: [Float], tgtAudio: [Float]) -> SyncMatch {
        let refEnv = zscore(onsetEnvelope(refAudio))
        let tgtEnv = zscore(onsetEnvelope(tgtAudio))
        let corr = fftCrossCorrelate(refEnv, tgtEnv)
        guard !corr.isEmpty else {
            return SyncMatch(offset: 0, confidence: 0, peakRatio: 0)
        }
        let lagOffset = tgtEnv.count - 1
        var bestIdx = 0
        var bestVal = -Float.greatestFiniteMagnitude
        for (i, v) in corr.enumerated() where v > bestVal {
            bestVal = v
            bestIdx = i
        }
        let bestLag = bestIdx - lagOffset

        // best competing peak outside a ±2 s exclusion zone
        let excl = Int(2 * envelopeFps)
        var second = -Float.greatestFiniteMagnitude
        for (i, v) in corr.enumerated()
        where abs(i - bestIdx) > excl && v > second { second = v }
        let peakRatio = second > 1e-9
            ? Double(bestVal) / Double(second)
            : Double.infinity

        let confidence = max(0, normalizedPeak(refEnv, tgtEnv, lag: bestLag))
        let coarse = Double(bestLag) / envelopeFps

        // refine against raw audio ±0.5 s around the coarse match
        let refined = refineOffset(refAudio: refAudio, tgtAudio: tgtAudio,
                                   coarse: coarse)
        return SyncMatch(offset: refined, confidence: confidence,
                         peakRatio: peakRatio)
    }

    static func refineOffset(
        refAudio: [Float], tgtAudio: [Float], coarse: Double,
        window: Double = 0.5, probeSeconds: Double = 20
    ) -> Double {
        let sr = sampleRate
        let probeLen = min(tgtAudio.count, Int(probeSeconds * sr))
        let tStart = max(0, (tgtAudio.count - probeLen) / 2)
        let probe = zscore(Array(tgtAudio[tStart..<(tStart + probeLen)]))

        let pad = Int(window * sr)
        let rCenter = Int(coarse * sr) + tStart
        let rStart = max(0, rCenter - pad)
        let rEnd = min(refAudio.count, rCenter + probeLen + pad)
        guard rEnd - rStart > probeLen else { return coarse }
        let region = zscore(Array(refAudio[rStart..<rEnd]))

        let corr = fftCrossCorrelate(region, probe)
        guard !corr.isEmpty else { return coarse }
        var bestIdx = 0
        var bestVal = -Float.greatestFiniteMagnitude
        for (i, v) in corr.enumerated() where v > bestVal {
            bestVal = v
            bestIdx = i
        }
        let bestLag = bestIdx - (probe.count - 1)
        let refined = Double(rStart + bestLag - tStart) / sr
        return abs(refined - coarse) > window + 0.1 ? coarse : refined
    }
}
