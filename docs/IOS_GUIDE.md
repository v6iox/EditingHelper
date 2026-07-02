# EditSync for iPhone

The iOS app does the same job as the desktop app but ends differently:
instead of writing project files for an editor, it plays the finished
edit **immediately in a live viewer** (no render wait — AVFoundation
composes it on the fly) and then saves the **finalized video to the
camera roll** with one tap. No editing software anywhere.

Same brain, same options: audio sync of glasses clips against the main
camera, opening title card with the four styles, framing choices,
main-camera muting, looping background music with optional silencing
under glasses clips.

## Building it (one-time, ~10 minutes on a Mac)

```sh
brew install xcodegen
cd ios
xcodegen                      # generates EditSync.xcodeproj
open EditSync.xcodeproj
```

In Xcode: select the EditSync target → Signing & Capabilities → choose
your team (86 Auto Lab). Plug in an iPhone or pick a simulator, press
Run. CI compiles this project on every pull request, so the source in
`ios/` always builds.

## Getting it on the team's phones "mostly permanently"

Ranked by permanence:

1. **App Store — unlisted distribution (recommended).** Submit once via
   App Store Connect and ask Apple for an *unlisted app link* (a normal
   App Store page reachable only by direct URL). Installs never expire,
   updates arrive like any app, no device management. Best fit for a
   small internal team.
2. **TestFlight.** Product → Archive in Xcode → Distribute → TestFlight.
   Add teammates as internal testers (up to 100) — they install via the
   TestFlight app. Builds expire after **90 days**, so you re-upload a
   build a few times a year; testers get update prompts automatically.
   Fastest to set up; that's the practical start.
3. **Ad Hoc** — install directly on up to 100 registered devices;
   profiles expire yearly. More hassle than TestFlight; skip unless the
   team can't use TestFlight.

## How the pieces map to the desktop engine

| Desktop (Python) | iPhone (Swift) |
|---|---|
| `audio.py` ffmpeg extraction | `AudioExtractor` (AVAssetReader → 8 kHz mono) |
| `sync.py` FFT correlation | `SyncEngine` (Accelerate/vDSP DFT) — same envelope, thresholds, refinement |
| `builder.py` placement | `TimelinePlanner` — same timeline-domain reference, duck regions, music loop |
| exporters / renderer | `CompositionBuilder` — AVMutableComposition + audio mix ramps + title layer |
| Qt app | SwiftUI, same monochrome 86 Auto Lab theme |

## Notes & current limits

- **Blurred-background framing** isn't in the iOS v1 (it needs a custom
  video compositor); vertical clips use Centered/Fill/PiP. On the
  roadmap.
- Meta glasses clips reach the phone via the Meta AI app exporting to
  Photos; DJI footage via the DJI app or a Files import — the picker
  reads anything in the photo library.
- Sync analysis runs on-device; a long shoot takes a minute or two on
  an iPhone. The preview then plays instantly; saving re-encodes at
  highest quality.
- The title card in the live preview is drawn as an overlay (identical
  layout); the saved video has it burned in by Core Animation.
- After saving, **"Send to CapCut or another app"** opens the share
  sheet with the finished video — CapCut mobile appears there when
  installed (it has no project-import mechanism, so the finalized video
  is the hand-off).
