// Build the AVFoundation composition from a TimelinePlan: the live
// viewer plays it instantly (no rendering), and the exporter writes the
// finalized video for the camera roll.
import AVFoundation
import UIKit

struct BuiltComposition {
    let composition: AVMutableComposition
    let videoComposition: AVMutableVideoComposition
    let audioMix: AVMutableAudioMix
    let renderSize: CGSize
    /// Same video composition with the title card burned in via Core
    /// Animation — usable for export only (AVPlayer can't drive the
    /// animation tool; the preview draws the card as a SwiftUI overlay).
    let exportVideoComposition: AVMutableVideoComposition
}

enum CompositionError: LocalizedError {
    case noVideoTrack(String)
    var errorDescription: String? {
        if case .noVideoTrack(let name) = self {
            return "\(name) has no video track."
        }
        return nil
    }
}

enum CompositionBuilder {

    static func build(plan: TimelinePlan, options: SyncOptions) async throws
        -> BuiltComposition
    {
        let composition = AVMutableComposition()
        var layerInstructions: [AVMutableVideoCompositionLayerInstruction] = []
        var mixParams: [AVMutableAudioMixInputParameters] = []

        // --- primary storyline ------------------------------------------
        guard
            let primaryVideo = composition.addMutableTrack(
                withMediaType: .video,
                preferredTrackID: kCMPersistentTrackID_Invalid),
            let primaryAudio = composition.addMutableTrack(
                withMediaType: .audio,
                preferredTrackID: kCMPersistentTrackID_Invalid)
        else { throw CompositionError.noVideoTrack("composition") }

        var renderSize = CGSize(width: 1920, height: 1080)
        var frameDuration = CMTime(value: 1, timescale: 30)
        var firstTransform = CGAffineTransform.identity

        for (i, clip) in plan.primaries.enumerated() {
            let asset = AVURLAsset(url: clip.url)
            let vTracks = try await asset.loadTracks(withMediaType: .video)
            guard let vTrack = vTracks.first else {
                throw CompositionError.noVideoTrack(clip.url.lastPathComponent)
            }
            let duration = try await asset.load(.duration)
            let at = CMTime(seconds: plan.primaryStarts[i], preferredTimescale: 600)
            try primaryVideo.insertTimeRange(
                CMTimeRange(start: .zero, duration: duration),
                of: vTrack, at: at)
            if let aTrack = try await asset.loadTracks(withMediaType: .audio).first {
                try primaryAudio.insertTimeRange(
                    CMTimeRange(start: .zero, duration: duration),
                    of: aTrack, at: at)
            }
            if i == 0 {
                let natural = try await vTrack.load(.naturalSize)
                let transform = try await vTrack.load(.preferredTransform)
                renderSize = natural.applying(transform)
                renderSize = CGSize(width: abs(renderSize.width),
                                    height: abs(renderSize.height))
                let fps = try await vTrack.load(.nominalFrameRate)
                if fps > 0 {
                    frameDuration = CMTime(value: 1, timescale: CMTimeScale(fps.rounded()))
                }
                firstTransform = transform
            }
        }

        let primaryLayer = AVMutableVideoCompositionLayerInstruction(
            assetTrack: primaryVideo)
        primaryLayer.setTransform(firstTransform, at: .zero)
        // duck the main camera's audio under every overlay
        let primaryParams = AVMutableAudioMixInputParameters(track: primaryAudio)
        applyDuck(primaryParams, regions: plan.duckRegions, base: 1.0,
                  duckedTo: options.duckPrimary ? 0.0 : 1.0)
        mixParams.append(primaryParams)

        // --- overlays -----------------------------------------------------
        var overlayLayers: [AVMutableVideoCompositionLayerInstruction] = []
        for placed in plan.overlays {
            let asset = AVURLAsset(url: placed.clip.url)
            guard
                let vTrack = try await asset.loadTracks(withMediaType: .video).first,
                let videoTrack = composition.addMutableTrack(
                    withMediaType: .video,
                    preferredTrackID: kCMPersistentTrackID_Invalid)
            else { continue }
            let start = CMTime(seconds: placed.timelineStart, preferredTimescale: 600)
            let range = CMTimeRange(
                start: .zero,
                duration: CMTime(seconds: placed.duration, preferredTimescale: 600))
            try videoTrack.insertTimeRange(range, of: vTrack, at: start)

            let layer = AVMutableVideoCompositionLayerInstruction(
                assetTrack: videoTrack)
            let transform = try await overlayTransform(
                track: vTrack, style: options.overlayStyle, renderSize: renderSize)
            layer.setTransform(transform, at: .zero)
            // only visible during its own interval
            layer.setOpacity(0, at: .zero)
            layer.setOpacity(1, at: start)
            layer.setOpacity(0, at: start + range.duration)
            overlayLayers.append(layer)

            if let aTrack = try await asset.loadTracks(withMediaType: .audio).first,
               let audioTrack = composition.addMutableTrack(
                   withMediaType: .audio,
                   preferredTrackID: kCMPersistentTrackID_Invalid)
            {
                try audioTrack.insertTimeRange(range, of: aTrack, at: start)
                mixParams.append(AVMutableAudioMixInputParameters(track: audioTrack))
            }
        }

        // --- background music --------------------------------------------
        if let music = plan.music,
           let musicTrack = composition.addMutableTrack(
               withMediaType: .audio,
               preferredTrackID: kCMPersistentTrackID_Invalid)
        {
            let asset = AVURLAsset(url: music.url)
            if let aTrack = try await asset.loadTracks(withMediaType: .audio).first {
                let musicDur = try await asset.load(.duration).seconds
                var cursor = 0.0
                while cursor < plan.totalDuration && musicDur > 0.1 {
                    let chunk = min(musicDur, plan.totalDuration - cursor)
                    try musicTrack.insertTimeRange(
                        CMTimeRange(
                            start: .zero,
                            duration: CMTime(seconds: chunk, preferredTimescale: 600)),
                        of: aTrack,
                        at: CMTime(seconds: cursor, preferredTimescale: 600))
                    cursor += chunk
                }
            }
            let params = AVMutableAudioMixInputParameters(track: musicTrack)
            let base = Float(pow(10, plan.musicDb / 20))
            params.setVolume(base, at: .zero)
            applyDuck(params, regions: plan.musicDuckRegions,
                      base: base, duckedTo: 0)
            mixParams.append(params)
        }

        // --- assemble ------------------------------------------------------
        let instruction = AVMutableVideoCompositionInstruction()
        instruction.timeRange = CMTimeRange(
            start: .zero,
            duration: CMTime(seconds: plan.totalDuration, preferredTimescale: 600))
        // first layer instruction renders on top
        instruction.layerInstructions = overlayLayers.reversed() + [primaryLayer]

        let videoComposition = AVMutableVideoComposition()
        videoComposition.renderSize = renderSize
        videoComposition.frameDuration = frameDuration
        videoComposition.instructions = [instruction]

        let audioMix = AVMutableAudioMix()
        audioMix.inputParameters = mixParams

        let exportComposition = AVMutableVideoComposition()
        exportComposition.renderSize = renderSize
        exportComposition.frameDuration = frameDuration
        exportComposition.instructions = [instruction]
        if let title = plan.title {
            exportComposition.animationTool = titleAnimationTool(
                title: title, renderSize: renderSize)
        }

        return BuiltComposition(
            composition: composition,
            videoComposition: videoComposition,
            audioMix: audioMix,
            renderSize: renderSize,
            exportVideoComposition: exportComposition)
    }

    // MARK: helpers

    private static func applyDuck(
        _ params: AVMutableAudioMixInputParameters,
        regions: [Interval], base: Float, duckedTo: Float
    ) {
        let fade = TimelinePlanner.duckFade
        for region in regions {
            let s = CMTime(seconds: max(0, region.start - fade),
                           preferredTimescale: 600)
            let sIn = CMTime(seconds: region.start, preferredTimescale: 600)
            let e = CMTime(seconds: region.end, preferredTimescale: 600)
            let eOut = CMTime(seconds: region.end + fade, preferredTimescale: 600)
            params.setVolumeRamp(fromStartVolume: base, toEndVolume: duckedTo,
                                 timeRange: CMTimeRange(start: s, end: sIn))
            params.setVolume(duckedTo, at: sIn)
            params.setVolumeRamp(fromStartVolume: duckedTo, toEndVolume: base,
                                 timeRange: CMTimeRange(start: e, end: eOut))
        }
    }

    private static func overlayTransform(
        track: AVAssetTrack, style: OverlayStyle, renderSize: CGSize
    ) async throws -> CGAffineTransform {
        let natural = try await track.load(.naturalSize)
        let preferred = try await track.load(.preferredTransform)
        let oriented = natural.applying(preferred)
        let w = abs(oriented.width), h = abs(oriented.height)
        guard w > 0, h > 0 else { return preferred }

        let scale: CGFloat
        switch style {
        case .fill:
            scale = max(renderSize.width / w, renderSize.height / h)
        case .pipLeft, .pipRight:
            scale = min(renderSize.width * 0.4 / w, renderSize.height * 0.62 / h)
        case .center:
            scale = min(renderSize.width / w, renderSize.height / h)
        }
        let scaledW = w * scale, scaledH = h * scale
        let tx: CGFloat
        switch style {
        case .pipLeft: tx = renderSize.width * 0.04
        case .pipRight: tx = renderSize.width - scaledW - renderSize.width * 0.04
        default: tx = (renderSize.width - scaledW) / 2
        }
        let ty = (renderSize.height - scaledH) / 2
        return preferred
            .concatenating(CGAffineTransform(scaleX: scale, y: scale))
            .concatenating(CGAffineTransform(translationX: tx, y: ty))
    }

    /// White card + title/description text, opacity-animated for export.
    private static func titleAnimationTool(
        title: TitleSpec, renderSize: CGSize
    ) -> AVVideoCompositionCoreAnimationTool {
        let parent = CALayer()
        parent.frame = CGRect(origin: .zero, size: renderSize)
        let videoLayer = CALayer()
        videoLayer.frame = parent.frame
        parent.addSublayer(videoLayer)
        let card = TitleCardLayer.make(spec: title, size: renderSize)

        let fadeOut = CABasicAnimation(keyPath: "opacity")
        fadeOut.fromValue = 1
        fadeOut.toValue = 0
        fadeOut.beginTime = AVCoreAnimationBeginTimeAtZero + title.hold
        fadeOut.duration = title.fade
        fadeOut.fillMode = .forwards
        fadeOut.isRemovedOnCompletion = false
        card.add(fadeOut, forKey: "fade")
        parent.addSublayer(card)

        return AVVideoCompositionCoreAnimationTool(
            postProcessingAsVideoLayer: videoLayer, in: parent)
    }
}
