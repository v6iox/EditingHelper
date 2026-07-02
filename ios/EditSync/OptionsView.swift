// Plain-language options — the same choices as the desktop app.
import SwiftUI

struct OptionsView: View {
    @ObservedObject var model: AppModel

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            section("OPENING TITLE — LEAVE EMPTY FOR NONE")
            TextField("Title — e.g. Front Bumper Removal",
                      text: $model.options.title.title)
                .textFieldStyle(.plain)
                .padding(10)
                .background(Theme.surface2)
                .clipShape(RoundedRectangle(cornerRadius: 8))
            TextField("Description — e.g. 2024 Toyota GR86",
                      text: $model.options.title.descriptionLine)
                .textFieldStyle(.plain)
                .padding(10)
                .background(Theme.surface2)
                .clipShape(RoundedRectangle(cornerRadius: 8))
            if model.options.title.isEnabled {
                Picker("Style", selection: $model.options.title.style) {
                    ForEach(TitleStyleKey.allCases) { style in
                        Text(style.rawValue).tag(style)
                    }
                }
                .pickerStyle(.segmented)
                HStack {
                    Text("Stays \(model.options.title.hold, specifier: "%.1f")s")
                        .font(.caption).foregroundStyle(Theme.gray)
                    Slider(value: $model.options.title.hold, in: 1...8, step: 0.5)
                    Text("Fades \(model.options.title.fade, specifier: "%.2f")s")
                        .font(.caption).foregroundStyle(Theme.gray)
                    Slider(value: $model.options.title.fade, in: 0.25...3, step: 0.25)
                }
            }

            section("VERTICAL CLIPS LOOK LIKE")
            Picker("Framing", selection: $model.options.overlayStyle) {
                ForEach(OverlayStyle.allCases) { style in
                    Text(style.rawValue).tag(style)
                }
            }
            .pickerStyle(.segmented)

            Toggle("Mute the main camera while a glasses clip plays",
                   isOn: $model.options.duckPrimary)
                .tint(Theme.teal)

            section("BACKGROUND MUSIC")
            Toggle("Loop my music file quietly under the whole video",
                   isOn: $model.options.musicEnabled)
                .tint(Theme.teal)
                .disabled(!model.hasMusic)
            if model.options.musicEnabled {
                HStack {
                    Text("Volume \(Int(model.options.musicDb)) dB")
                        .font(.caption).foregroundStyle(Theme.gray)
                    Slider(value: $model.options.musicDb, in: -40 ... -8, step: 1)
                }
                Toggle("Silence the music while a glasses clip plays",
                       isOn: $model.options.musicDuck)
                    .tint(Theme.teal)
            }
            if !model.hasMusic {
                Text("Add a music file above to enable this.")
                    .font(.caption2).foregroundStyle(Theme.grayDim)
            }
        }
        .padding(14)
        .background(Theme.surface)
        .clipShape(RoundedRectangle(cornerRadius: 12))
    }

    private func section(_ text: String) -> some View {
        Text(text)
            .font(.system(size: 11, weight: .semibold))
            .kerning(1.4)
            .foregroundStyle(Theme.gray)
    }
}
