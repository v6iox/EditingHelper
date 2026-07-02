// The live viewer: instant playback of the composed edit (no rendering
// wait), then one tap to save the finalized video to the camera roll.
import AVKit
import SwiftUI

struct PreviewView: View {
    @ObservedObject var model: AppModel
    @State private var player: AVPlayer?
    @State private var cardOpacity: Double = 0
    @State private var timeObserver: Any?

    var body: some View {
        VStack(spacing: 16) {
            if let plan = model.plan {
                Text("Your video is ready to watch")
                    .font(.headline)
                summary(plan)
            }

            ZStack {
                if let player {
                    VideoPlayer(player: player)
                        .aspectRatio(
                            (model.built?.renderSize.width ?? 16)
                                / max(model.built?.renderSize.height ?? 9, 1),
                            contentMode: .fit)
                }
                // live-preview title card (burned in on export)
                if let title = model.plan?.title, cardOpacity > 0.001 {
                    TitleCardPreview(spec: title)
                        .opacity(cardOpacity)
                        .allowsHitTesting(false)
                }
            }
            .clipShape(RoundedRectangle(cornerRadius: 12))

            if let progress = model.exportProgress {
                ProgressView(value: progress) {
                    Text("Saving to your camera roll… \(Int(progress * 100))%")
                        .font(.caption)
                        .foregroundStyle(Theme.gray)
                }
                .tint(Theme.white)
            } else if model.savedToCameraRoll {
                Label("Saved to your camera roll", systemImage: "checkmark.circle.fill")
                    .foregroundStyle(Theme.teal)
            }

            HStack(spacing: 12) {
                Button("Start over") { model.reset() }
                    .padding(.vertical, 12)
                    .padding(.horizontal, 18)
                    .background(Theme.surface2)
                    .clipShape(RoundedRectangle(cornerRadius: 10))
                Button {
                    model.saveToCameraRoll()
                } label: {
                    Text(model.savedToCameraRoll
                         ? "Save again" : "Save to Camera Roll")
                        .font(.headline)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 12)
                        .background(Theme.white)
                        .foregroundStyle(Theme.black)
                        .clipShape(RoundedRectangle(cornerRadius: 10))
                }
                .disabled(model.exportProgress != nil)
            }
        }
        .padding(16)
        .foregroundStyle(Theme.white)
        .onAppear(perform: preparePlayer)
        .onDisappear {
            if let observer = timeObserver { player?.removeTimeObserver(observer) }
            player?.pause()
        }
    }

    private func preparePlayer() {
        guard let built = model.built else { return }
        let item = AVPlayerItem(asset: built.composition)
        item.videoComposition = built.videoComposition
        item.audioMix = built.audioMix
        let player = AVPlayer(playerItem: item)
        self.player = player

        if let title = model.plan?.title {
            cardOpacity = 1
            let interval = CMTime(value: 1, timescale: 30)
            timeObserver = player.addPeriodicTimeObserver(
                forInterval: interval, queue: .main
            ) { time in
                let t = time.seconds
                if t <= title.hold {
                    cardOpacity = 1
                } else if t < title.hold + title.fade {
                    cardOpacity = 1 - (t - title.hold) / title.fade
                } else {
                    cardOpacity = 0
                }
            }
        }
        player.play()
    }

    private func summary(_ plan: TimelinePlan) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("\(plan.overlays.count) glasses clip"
                 + (plan.overlays.count == 1 ? "" : "s")
                 + " matched by sound"
                 + (plan.music != nil ? " · music underneath" : ""))
                .font(.caption)
                .foregroundStyle(Theme.gray)
            if !plan.unplaced.isEmpty {
                Text("\(plan.unplaced.count) clip(s) couldn't be matched "
                     + "confidently and were left out.")
                    .font(.caption)
                    .foregroundStyle(Theme.gray)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// SwiftUI rendition of the title card for live preview — the export
/// burns in the identical CALayer version.
struct TitleCardPreview: View {
    let spec: TitleSpec

    var body: some View {
        GeometryReader { geo in
            let l = TitleCardLayer.layout(spec: spec, height: geo.size.height)
            ZStack {
                Color.white
                VStack(spacing: 8) {
                    Text(l.titleText)
                        .font(Font(l.titleFont))
                        .foregroundStyle(Color.black)
                    if !l.descText.isEmpty {
                        Text(l.descText)
                            .font(Font(l.descFont))
                            .foregroundStyle(Color(l.descColor))
                    }
                }
                .multilineTextAlignment(l.leftAligned ? .leading : .center)
                .frame(
                    maxWidth: .infinity,
                    alignment: l.leftAligned ? .leading : .center)
                .padding(.leading, l.leftAligned ? geo.size.width * 0.08 : 0)
                .position(
                    x: geo.size.width * (l.leftAligned ? 0.5 : l.centerXFraction),
                    y: geo.size.height * l.centerYFraction)
            }
        }
    }
}
