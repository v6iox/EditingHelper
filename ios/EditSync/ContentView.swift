// The app flow: setup (pick + options) → working → live preview + save.
import AVKit
import PhotosUI
import SwiftUI

struct ContentView: View {
    @StateObject private var model = AppModel()

    var body: some View {
        ZStack {
            Theme.black.ignoresSafeArea()
            switch model.stage {
            case .setup:
                SetupView(model: model)
            case .working(let message):
                WorkingView(message: message)
            case .preview:
                PreviewView(model: model)
            }
        }
        .preferredColorScheme(.dark)
        .alert(
            "Something went wrong",
            isPresented: Binding(
                get: { model.errorMessage != nil },
                set: { if !$0 { model.errorMessage = nil } }
            )
        ) {
            Button("OK", role: .cancel) {}
        } message: {
            Text(model.errorMessage ?? "")
        }
    }
}

// MARK: - setup

struct SetupView: View {
    @ObservedObject var model: AppModel
    @State private var pickedVideos: [PhotosPickerItem] = []
    @State private var showMusicImporter = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                Text("EDITSYNC")
                    .font(.system(size: 24, weight: .heavy))
                    .kerning(3)
                Text("Pick everything from the shoot. Your glasses clips are "
                     + "matched to the main camera by sound, and you get one "
                     + "finished video for the camera roll.")
                    .foregroundStyle(Theme.gray)
                    .font(.subheadline)

                PhotosPicker(
                    selection: $pickedVideos,
                    matching: .videos,
                    photoLibrary: .shared()
                ) {
                    pickTile(
                        title: "Pick your footage",
                        subtitle: "DJI + glasses clips from your photo library")
                }
                .onChange(of: pickedVideos) { items in
                    guard !items.isEmpty else { return }
                    pickedVideos = []
                    Task { await model.addVideos(items) }
                }

                Button {
                    showMusicImporter = true
                } label: {
                    pickTile(
                        title: model.hasMusic ? "Music added ✓" : "Add music (optional)",
                        subtitle: "A song from Files to loop quietly underneath")
                }
                .fileImporter(
                    isPresented: $showMusicImporter,
                    allowedContentTypes: [.audio]
                ) { result in
                    if case .success(let url) = result {
                        Task { await model.addMusic(url: url) }
                    }
                }

                if !model.clips.isEmpty {
                    clipList
                }

                OptionsView(model: model)

                Button {
                    model.sync()
                } label: {
                    Text("Make my video")
                        .font(.headline)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 14)
                        .background(model.canSync ? Theme.white : Theme.line)
                        .foregroundStyle(
                            model.canSync ? Theme.black : Theme.grayDim)
                        .clipShape(RoundedRectangle(cornerRadius: 12))
                }
                .disabled(!model.canSync)

                Text("EditSync — 86 Auto Lab")
                    .font(.caption2)
                    .foregroundStyle(Theme.grayDim)
                    .frame(maxWidth: .infinity)
            }
            .padding(20)
        }
        .foregroundStyle(Theme.white)
    }

    private var clipList: some View {
        VStack(spacing: 0) {
            ForEach(model.clips) { clip in
                HStack(spacing: 10) {
                    Text(badge(for: clip.role))
                        .font(.system(size: 10, weight: .heavy))
                        .padding(.horizontal, 8)
                        .padding(.vertical, 3)
                        .background(clip.role == .primary ? Theme.white : .clear)
                        .foregroundStyle(
                            clip.role == .primary ? Theme.black : Theme.white)
                        .overlay(
                            Capsule().stroke(Theme.grayDim, lineWidth: 1)
                                .opacity(clip.role == .primary ? 0 : 1))
                        .clipShape(Capsule())
                    Text(clip.url.lastPathComponent)
                        .font(.footnote)
                        .lineLimit(1)
                    Spacer()
                    Text(timeString(clip.duration))
                        .font(.caption2)
                        .foregroundStyle(Theme.grayDim)
                    Button {
                        model.remove(clip)
                    } label: {
                        Image(systemName: "xmark")
                            .font(.caption2)
                            .foregroundStyle(Theme.gray)
                    }
                }
                .padding(.vertical, 8)
                Divider().background(Theme.lineSoft)
            }
        }
        .padding(14)
        .background(Theme.surface)
        .clipShape(RoundedRectangle(cornerRadius: 12))
    }

    private func badge(for role: ClipRole) -> String {
        switch role {
        case .primary: return "MAIN CAM"
        case .overlay: return "OVERLAY"
        case .music: return "MUSIC"
        }
    }

    private func timeString(_ seconds: Double) -> String {
        let s = Int(seconds)
        return String(format: "%d:%02d", s / 60, s % 60)
    }

    private func pickTile(title: String, subtitle: String) -> some View {
        VStack(spacing: 4) {
            Text(title).font(.headline).foregroundStyle(Theme.white)
            Text(subtitle).font(.caption).foregroundStyle(Theme.gray)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 22)
        .background(Theme.surface)
        .overlay(
            RoundedRectangle(cornerRadius: 14)
                .strokeBorder(
                    Theme.grayDim,
                    style: StrokeStyle(lineWidth: 1, dash: [5, 4]))
        )
        .clipShape(RoundedRectangle(cornerRadius: 14))
    }
}

// MARK: - working

struct WorkingView: View {
    let message: String
    @State private var pulse = false

    var body: some View {
        VStack(spacing: 20) {
            Text("86")
                .font(.system(size: 64, weight: .black).italic())
                .foregroundStyle(Theme.teal)
            Text(message)
                .font(.headline)
                .opacity(pulse ? 0.55 : 1)
                .animation(
                    .easeInOut(duration: 0.8).repeatForever(autoreverses: true),
                    value: pulse)
            ProgressView().tint(Theme.white)
        }
        .foregroundStyle(Theme.white)
        .onAppear { pulse = true }
    }
}
