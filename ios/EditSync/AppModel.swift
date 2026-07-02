// App state: media intake, options, sync pipeline, export.
import AVFoundation
import Photos
import PhotosUI
import SwiftUI
import UniformTypeIdentifiers

@MainActor
final class AppModel: ObservableObject {
    enum Stage {
        case setup
        case working(String)
        case preview
    }

    @Published var stage: Stage = .setup
    @Published var clips: [SourceClip] = []
    @Published var options = SyncOptions()
    @Published var plan: TimelinePlan?
    @Published var built: BuiltComposition?
    @Published var errorMessage: String?
    @Published var exportProgress: Double?
    @Published var savedToCameraRoll = false
    @Published var exportedURL: URL?  // finished video, for the share sheet

    var hasPrimary: Bool { clips.contains { $0.role == .primary } }
    var hasOverlay: Bool { clips.contains { $0.role == .overlay } }
    var hasMusic: Bool { clips.contains { $0.role == .music } }
    var canSync: Bool { hasPrimary && hasOverlay }

    // MARK: intake

    func addVideos(_ items: [PhotosPickerItem]) async {
        for item in items {
            guard let movie = try? await item.loadTransferable(type: PickedMovie.self)
            else { continue }
            await addClip(url: movie.url)
        }
    }

    func addClip(url: URL) async {
        let asset = AVURLAsset(url: url)
        var clip = SourceClip(url: url, role: .primary)
        if let duration = try? await asset.load(.duration) {
            clip.duration = duration.seconds
        }
        if let track = try? await asset.loadTracks(withMediaType: .video).first {
            if let size = try? await track.load(.naturalSize),
               let transform = try? await track.load(.preferredTransform)
            {
                let oriented = size.applying(transform)
                clip.isPortrait = abs(oriented.height) > abs(oriented.width)
            }
            clip.role = clip.isPortrait ? .overlay : .primary
        } else {
            clip.role = .music  // audio-only file
        }
        if let date = try? await asset.load(.creationDate),
           let value = try? await date.load(.dateValue)
        {
            clip.creationDate = value
        }
        clips.append(clip)
    }

    func addMusic(url: URL) async {
        guard url.startAccessingSecurityScopedResource() else { return }
        defer { url.stopAccessingSecurityScopedResource() }
        // copy into our sandbox so playback/export can read it later
        let dest = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString)
            .appendingPathExtension(url.pathExtension)
        do {
            try FileManager.default.copyItem(at: url, to: dest)
        } catch {
            errorMessage = "Couldn't read that audio file."
            return
        }
        var clip = SourceClip(url: dest, role: .music)
        let asset = AVURLAsset(url: dest)
        if let duration = try? await asset.load(.duration) {
            clip.duration = duration.seconds
        }
        clips.append(clip)
    }

    func remove(_ clip: SourceClip) {
        clips.removeAll { $0.id == clip.id }
    }

    // MARK: pipeline

    func sync() {
        stage = .working("Listening to your footage…")
        savedToCameraRoll = false
        Task {
            do {
                let plan = try await TimelinePlanner.plan(
                    clips: clips, options: options
                ) { [weak self] message in
                    Task { @MainActor in self?.stage = .working(message) }
                }
                self.plan = plan
                stage = .working("Building your video…")
                built = try await CompositionBuilder.build(
                    plan: plan, options: options)
                stage = .preview
            } catch {
                errorMessage = error.localizedDescription
                stage = .setup
            }
        }
    }

    func reset() {
        stage = .setup
        plan = nil
        built = nil
        exportProgress = nil
        exportedURL = nil
    }

    // MARK: export → camera roll

    func saveToCameraRoll() {
        guard let built, let plan else { return }
        exportProgress = 0
        Task {
            do {
                let outURL = FileManager.default.temporaryDirectory
                    .appendingPathComponent("EditSync-\(UUID().uuidString).mov")
                guard let session = AVAssetExportSession(
                    asset: built.composition,
                    presetName: AVAssetExportPresetHighestQuality)
                else { throw ExportError.sessionFailed }
                session.outputURL = outURL
                session.outputFileType = .mov
                session.videoComposition = built.exportVideoComposition
                session.audioMix = built.audioMix
                session.timeRange = CMTimeRange(
                    start: .zero,
                    duration: CMTime(seconds: plan.totalDuration,
                                     preferredTimescale: 600))

                let progressTask = Task { [weak self] in
                    while !Task.isCancelled {
                        let p = Double(session.progress)
                        await MainActor.run { self?.exportProgress = p }
                        try? await Task.sleep(nanoseconds: 300_000_000)
                    }
                }
                await session.export()
                progressTask.cancel()
                if session.status != .completed {
                    throw session.error ?? ExportError.sessionFailed
                }
                exportedURL = outURL

                try await PHPhotoLibrary.shared().performChanges {
                    PHAssetChangeRequest.creationRequestForAssetFromVideo(
                        atFileURL: outURL)
                }
                exportProgress = nil
                savedToCameraRoll = true
            } catch {
                exportProgress = nil
                errorMessage = "Couldn't save: \(error.localizedDescription)"
            }
        }
    }
}

enum ExportError: LocalizedError {
    case sessionFailed
    var errorDescription: String? { "The video export failed." }
}

/// PhotosPicker transferable that lands the movie in our temp directory.
struct PickedMovie: Transferable {
    let url: URL

    static var transferRepresentation: some TransferRepresentation {
        FileRepresentation(contentType: .movie) { movie in
            SentTransferredFile(movie.url)
        } importing: { received in
            let dest = FileManager.default.temporaryDirectory
                .appendingPathComponent(UUID().uuidString)
                .appendingPathExtension(received.file.pathExtension)
            try FileManager.default.copyItem(at: received.file, to: dest)
            return PickedMovie(url: dest)
        }
    }
}
