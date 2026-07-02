// Placement policy — the iOS mirror of the desktop builder: assemble a
// timeline-domain reference from the primary clips, match every overlay
// against it, compute duck regions, and lay out looping music.
import AVFoundation
import Foundation

enum PlannerError: LocalizedError {
    case noPrimary
    var errorDescription: String? {
        "Add your main camera footage (the horizontal recording) first."
    }
}

enum TimelinePlanner {
    static let duckFade = 0.25

    static func plan(
        clips: [SourceClip],
        options: SyncOptions,
        progress: @escaping (String) -> Void
    ) async throws -> TimelinePlan {
        var primaries = clips.filter { $0.role == .primary }
        let overlays = clips.filter { $0.role == .overlay }
        let music = clips.first { $0.role == .music }
        guard !primaries.isEmpty else { throw PlannerError.noPrimary }
        primaries.sort {
            ($0.creationDate ?? .distantFuture, $0.url.lastPathComponent)
                < ($1.creationDate ?? .distantFuture, $1.url.lastPathComponent)
        }

        var plan = TimelinePlan()
        plan.primaries = primaries
        var cursor = 0.0
        for clip in primaries {
            plan.primaryStarts.append(cursor)
            cursor += clip.duration
        }
        plan.totalDuration = cursor

        // timeline-domain reference audio (clips spanning file splits work)
        progress("Listening to your footage…")
        let sr = SyncEngine.sampleRate
        var reference = [Float](repeating: 0, count: Int(cursor * sr) + 1)
        for (i, clip) in primaries.enumerated() {
            guard let samples = try? await AudioExtractor.monoSamples(from: clip.url)
            else { continue }
            let start = Int(plan.primaryStarts[i] * sr)
            let end = min(start + samples.count, reference.count)
            if end > start {
                reference.replaceSubrange(start..<end,
                                          with: samples[0..<(end - start)])
            }
        }

        var intervals: [Interval] = []
        for overlay in overlays {
            progress("Syncing \(overlay.url.lastPathComponent)…")
            guard let audio = try? await AudioExtractor.monoSamples(from: overlay.url)
            else {
                plan.unplaced.append(overlay)
                continue
            }
            let match = SyncEngine.findClip(refAudio: reference, tgtAudio: audio)
            guard match.isConfident else {
                plan.unplaced.append(overlay)
                continue
            }
            var start = match.offset
            var duration = overlay.duration
            if start < 0 {  // clip began before the main camera; trim its head
                duration += start
                start = 0
            }
            guard duration > 0.1 else {
                plan.unplaced.append(overlay)
                continue
            }
            plan.overlays.append(
                PlacedOverlay(clip: overlay, timelineStart: start,
                              duration: duration, confidence: match.confidence)
            )
            intervals.append(Interval(start: start, end: start + duration))
        }
        plan.overlays.sort { $0.timelineStart < $1.timelineStart }

        let merged = mergeIntervals(intervals)
        if options.duckPrimary { plan.duckRegions = merged }
        if options.musicEnabled, let music {
            plan.music = music
            plan.musicDb = options.musicDb
            if options.musicDuck { plan.musicDuckRegions = merged }
        }
        if options.title.isEnabled { plan.title = options.title }
        return plan
    }

    static func mergeIntervals(
        _ intervals: [Interval], minGap: Double = 0.5
    ) -> [Interval] {
        guard !intervals.isEmpty else { return [] }
        let sorted = intervals.sorted { $0.start < $1.start }
        var merged: [(Double, Double)] = [(sorted[0].start, sorted[0].end)]
        for iv in sorted.dropFirst() {
            if iv.start <= merged[merged.count - 1].1 + minGap {
                merged[merged.count - 1].1 = max(merged[merged.count - 1].1, iv.end)
            } else {
                merged.append((iv.start, iv.end))
            }
        }
        return merged.map { Interval(start: $0.0, end: $0.1) }
    }
}
