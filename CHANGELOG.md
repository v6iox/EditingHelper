# Changelog

## 1.6.0 — 2026-07-02

- **Live viewer in the studio.** Recreational mode now has a real
  program monitor above the timeline: it shows the composed frame at
  the playhead — main camera (blurred where the blurred-background
  style is active) with every glasses clip framed exactly as the
  finished video will frame it. **Play/pause** runs a silent live
  preview; the draft render still has the sound.
- **Scrubbing everywhere.** Drag the **puck** under the picture, or
  drag directly in the timeline ruler — the picture follows in real
  time (frames are decoded in the background and cached, so the UI
  never stalls).
- **Drag what's visible.** Grab a glasses clip *inside the picture*
  and drop it where you want it. The new position is undoable, drawn
  live, and honored end-to-end: the finished-video render, Final Cut
  (`adjust-transform`), CapCut drafts, and OTIO metadata all place the
  clip where you dropped it. (Also fixed the CapCut draft's vertical
  axis, which pointed the opposite way from Final Cut's.)

## 1.5.0 — 2026-07-02

- **Fixed: automatic update checks never worked in the packaged app.**
  The app's bundled Python has no certificate store (macOS doesn't
  expose its keychain to it), so every secure connection to GitHub
  failed verification and the updater stayed silent. Certificates now
  ship inside the app (certifi). Anyone on an older version needs to
  download this release manually **once** — from here on the bottom-left
  update prompt works.
- **New: "Check for updates" button** in the footer of both modes. It
  always tells you what happened: "You're up to date", "Version X is
  ready" (with the install prompt), or the exact reason the check
  failed (offline, GitHub rate limit, certificate problems, a release
  whose downloads aren't finished uploading yet).

- **Recreational mode — the studio.** A switcher in the top-right corner
  flips the app between its two sides. Training mode is the familiar
  one-button flow; Recreational mode opens your footage (still synced by
  sound first) on a real multi-track timeline with an auto-editing suite:
  - **Timeline editing**: waveforms on every clip, drag glasses clips,
    drag edges to trim, Delete to remove, zoom/fit, snapping to beats,
    clip edges, and whole seconds. Trims and deletes on the main-camera
    track are *ripple* edits — everything after slides left and every
    glasses clip stays in sync with the content underneath it.
  - **Tighten dead air** — finds the quiet stretches and cuts them out
    in one click (never inside a glasses clip).
  - **Keep the best moments** — scores every second by loudness and
    activity and keeps only the top moments (target length slider);
    glasses clips are always kept.
  - **Cut to the beat** — detects the music's tempo and nudges every cut
    onto the beat grid.
  - **Pick the part to use** — double-click any clip for an intuitive
    portion picker: thumbnails, waveform, draggable in/out handles.
  - **Add a clip to the end**, unlimited **Undo**, and **Back to the
    synced cut**.
  - **Draft preview** — a fast rough MP4 of the current timeline, opened
    in your player; full-quality **Export** to every format the app
    supports (Final Cut, Premiere, Resolve/OTIO, CapCut, finished MP4)
    with music, ducking, and framing styles intact.
  - The mode and every studio option persist across launches.

## 1.4.1 — 2026-07-02

- **Fixed: footage turned overly red when a glasses clip played** in the
  finished-video render. Meta glasses record HDR (HLG/BT.2020); the
  renderer was compositing those frames as ordinary SDR, which
  oversaturates the picture. HDR sources are now tone-mapped down to
  BT.709 (with a matrix-only fallback on ffmpeg builds without zscale),
  SDR sources with a different YUV matrix are converted too, and the
  output file is tagged `bt709` so every player reads it the same way.
  The iPhone app's live preview and export now pin their working color
  space to SDR BT.709 for the same reason.

## 1.4.0 — 2026-07-02

- **CapCut support on all platforms.** Desktop (Mac/Windows): a new
  "CapCut" save option writes a CapCut desktop *draft* folder — primary
  storyline, overlays at their synced positions on their own tracks,
  volume-keyframed ducking, looping music, and the title card with its
  fade. CapCut has no official import format, so this targets its local
  draft schema and is labeled experimental; INSTRUCTIONS.txt in the
  folder covers installing it, and the finished-video render is the
  always-works fallback. CLI: `-f capcut`. iPhone: a **"Send to CapCut
  or another app"** share button on the preview screen hands the
  finished video straight to CapCut mobile (which has no project-import
  mechanism, so the finalized video is the correct hand-off).

## 1.3.0 — 2026-07-02

- **Finished video (MP4)**: a new "Save for" option renders the whole
  synced edit to one final video — overlays framed and blurred, title
  card fading, audio ducked, music looped — no editing software needed.
  A "Watch the video" button opens it straight from the results screen.
  CLI: `--render`.
- **Hidden test mode**: triple-click the logo (no visual hint) to open
  a secret menu that generates a complete demo shoot — main camera
  (optionally split into two files), glasses clips with genuinely
  overlapping audio, and a song — and loads it, so every feature can be
  demonstrated without real footage. CLI: `editsync demo`.
- **EditSync for iPhone** (`ios/`): a native SwiftUI app with the same
  sync engine (ported to Apple's Accelerate framework), same options,
  and a mobile-first ending — the finished edit plays instantly in a
  live viewer, then saves to the camera roll with one tap. Built and
  compile-checked by CI on every change; see docs/IOS_GUIDE.md for
  TestFlight / unlisted App Store distribution.

## 1.2.0 — 2026-07-02

- **Opening title card** for training videos: a title (panel/procedure)
  with a description line under it (year/make/model) on a white
  full-frame card at the start of the video, fading out at an
  adjustable speed to reveal the footage already playing. Four
  arrangements with live visual previews in the app — Classic,
  Lower left, Statement, Elegant. Empty title = no card. CLI:
  `--title`, `--title-description`, `--title-style`, `--title-hold`,
  `--title-fade`.
- **Every setting now persists** across app restarts: project name,
  title card text/style/timing, framing, blur, audio behavior, music
  options, formats, layers, and window layout.
- The update check now works out of the box for everyone (the
  repository is public).

## 1.1.0 — 2026-07-02

- **Background music** (off by default): drop a song in with your
  footage and turn on "Loop my music file quietly under the whole
  video" — it loops for the full duration on its own layer beneath the
  video, at background level (volume slider, default −22 dB), so your
  voice stays on top. Each pass is a separate clip you can trim or swap
  in the editor. CLI: `--music FILE --music-volume DB`.
- **Silence music during glasses clips** (off by default): keyframes
  the music to silence with smooth fades wherever a vertical clip
  plays. CLI: `--music-duck`.
- **Auto-updater**: when a newer release exists, a prompt appears in
  the bottom-left corner on launch; one click downloads it, swaps the
  app in place, and relaunches. (Requires the app to be able to see the
  releases — see the note in docs/APP_GUIDE.md.)

## 1.0.1 — 2026-07-02

- macOS builds are now **code-signed with a Developer ID certificate,
  notarized by Apple, and stapled** — the app opens with a normal
  double-click, no Gatekeeper warning or right-click workaround.
- Added `packaging/setup_signing.sh`, a one-command setup that
  provisions the signing certificate and CI secrets.

## 1.0.0 — 2026-07-02

First release. EditSync — by 86 Auto Lab.

### The app
- Drag-and-drop desktop app (macOS `.dmg`, Windows `.zip`) with a
  black-and-white design, animated controls, and 86 Auto Lab branding.
  ffmpeg is bundled; no installation steps, no terminal.
- Drop a shoot's footage (or the whole folder) → files auto-labeled
  MAIN CAM / OVERLAY → plain-language options → one button → a ready
  timeline file.
- Options and window layout are remembered between launches.

### The engine
- Audio sync of vertical Meta glasses clips against continuous
  horizontal DJI footage: onset-envelope FFT correlation plus raw-audio
  refinement (sub-frame accuracy), confidence + ambiguity scoring, and
  clock-drift detection. Weak matches are reported, never guessed.
- DJI file-split chunks are handled as one continuous recording — clips
  spanning a boundary sync correctly.
- Timeline output: primary storyline + each overlay on its own layer at
  the synced position with its own audio, fully trimmable afterwards.
- Automatic treatments under overlays: keyframed audio ducking of the
  main camera, and the "blurred background" look (keyframed Gaussian
  blur, adjustable strength).
- Framing presets: blurred background (default), centered, fill,
  picture-in-picture left/right.
- Exports: Final Cut Pro FCPXML 1.11 (also imports into DaVinci
  Resolve), Adobe Premiere Pro xmeml, OpenTimelineIO JSON.
- Sync-confidence markers in the timeline; red to-do markers on
  placements worth checking by ear; JSON sync reports.
- `editsync` CLI with the full option surface for scripting.
