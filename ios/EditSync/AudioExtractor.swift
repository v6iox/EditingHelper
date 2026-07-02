// Decode any asset's audio to mono Float32 at 8 kHz for sync analysis —
// the iOS equivalent of the desktop engine's ffmpeg extraction.
import AVFoundation

enum AudioExtractorError: Error {
    case noAudioTrack
    case readFailed
}

enum AudioExtractor {
    static let sampleRate = 8000.0

    static func monoSamples(from url: URL) async throws -> [Float] {
        let asset = AVURLAsset(url: url)
        let tracks = try await asset.loadTracks(withMediaType: .audio)
        guard let track = tracks.first else {
            throw AudioExtractorError.noAudioTrack
        }
        let reader = try AVAssetReader(asset: asset)
        let settings: [String: Any] = [
            AVFormatIDKey: kAudioFormatLinearPCM,
            AVSampleRateKey: sampleRate,
            AVNumberOfChannelsKey: 1,
            AVLinearPCMBitDepthKey: 32,
            AVLinearPCMIsFloatKey: true,
            AVLinearPCMIsNonInterleaved: false,
        ]
        let output = AVAssetReaderTrackOutput(track: track, outputSettings: settings)
        output.alwaysCopiesSampleData = false
        reader.add(output)
        guard reader.startReading() else { throw AudioExtractorError.readFailed }

        var samples: [Float] = []
        while let buffer = output.copyNextSampleBuffer() {
            guard let block = CMSampleBufferGetDataBuffer(buffer) else { continue }
            let length = CMBlockBufferGetDataLength(block)
            var data = [Float](repeating: 0, count: length / MemoryLayout<Float>.size)
            data.withUnsafeMutableBytes { dest in
                _ = CMBlockBufferCopyDataBytes(
                    block, atOffset: 0, dataLength: length,
                    destination: dest.baseAddress!
                )
            }
            samples.append(contentsOf: data)
        }
        if reader.status == .failed { throw AudioExtractorError.readFailed }
        return samples
    }
}
