# Changelog

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
