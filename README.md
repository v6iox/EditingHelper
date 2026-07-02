# EditSync

Automatic audio-sync of multi-camera footage into an editable timeline —
built for the **DJI Action (horizontal, always recording)** +
**Meta glasses (vertical, recorded only when needed)** workflow, targeting
**Final Cut Pro** first, with Premiere Pro and OpenTimelineIO exports for
portability to other editors.

**Download the app:** grab `EditSync.dmg` (Mac) or `EditSync-windows.zip`
(Windows) from the [Releases page](../../releases/latest). No setup —
ffmpeg is bundled. Mac first launch: right-click → Open (unsigned app).

Comes in three flavors:

- **The EditSync app** — a downloadable black-and-white desktop app:
  drag in footage, pick options in plain language, press one button.
  ffmpeg is bundled; nothing to install, no terminal. See
  [docs/APP_GUIDE.md](docs/APP_GUIDE.md) for downloading/using it and how
  releases are built (GitHub Actions produces the macOS `.dmg` and
  Windows `.zip`; `./packaging/build_macos.sh` builds locally).
- **The `editsync` CLI** — the same engine, scriptable (documented below).
- **EditSync for iPhone** (`ios/`) — same sync brain, mobile ending:
  live preview of the finished edit, one tap to save to the camera
  roll. See [docs/IOS_GUIDE.md](docs/IOS_GUIDE.md).

## What it does

1. **Scans** your footage and auto-classifies each file as *primary*
   (landscape DJI recording) or *overlay* (vertical Meta clip) using camera
   metadata, filename hints, and orientation.
2. **Audio-syncs** every overlay clip against the continuous primary
   recording using two-stage cross-correlation (onset envelopes for the
   coarse match, raw audio for sub-frame refinement) — no timecode or
   clapboard needed, and the two cameras' very different microphones are
   handled by design.
3. **Builds a timeline** where:
   - the DJI footage is the primary storyline (multiple files are laid out
     chronologically, so split recordings just work),
   - each Meta clip sits on its **own layer above** at the exact synced
     position, using **its own audio**, fully trimmable/extendable by hand,
   - the DJI audio is **automatically ducked** (volume keyframes) wherever
     a Meta clip overlaps, so the overlay's audio wins without manual mixing.
4. **Exports** to Final Cut Pro (`.fcpxml`), Adobe Premiere Pro (`.xml`),
   and OpenTimelineIO (`.otio` — imports into DaVinci Resolve and more).

## Install

Requires Python 3.10+ and [ffmpeg](https://ffmpeg.org/download.html)
(`brew install ffmpeg` on macOS).

```sh
pip install .
editsync doctor   # verify ffmpeg/ffprobe/numpy are available
```

## Usage

Drop all of a shoot's files (DJI + Meta) into one folder, then:

```sh
editsync sync ./footage -o myvideo.fcpxml
```

Double-click / drag `myvideo.fcpxml` into Final Cut Pro and the synced,
layered timeline appears as a new project. See
[docs/FCP_GUIDE.md](docs/FCP_GUIDE.md) for the full workflow.

More examples:

```sh
# check classification before syncing
editsync probe ./footage

# export every supported format at once + a JSON sync report
editsync sync ./footage -o myvideo --format all --report sync.json

# analyze only; print the sync report without writing timelines
editsync sync ./footage --dry-run

# scale vertical clips to fill the frame instead of pillarboxing
editsync sync ./footage -o myvideo.fcpxml --overlay-style fill

# keep the DJI audio audible under overlays at -18 dB instead of muting
editsync sync ./footage -o myvideo.fcpxml --duck -18

# big shoot: use file timestamps to narrow the audio search
editsync sync ./footage -o myvideo.fcpxml --search-window 120
```

## Quality-of-life features

- **Sync confidence scoring** — every placement gets a confidence score and
  a marker in the timeline; ambiguous or weak matches are *left off* the
  timeline (listed in the report) instead of being silently misplaced, and
  low-confidence placements get a red to-do marker so you can verify them.
- **Automatic audio ducking** with smooth fades where overlays sit (FCP).
- **Clock-drift detection** — long clips are aligned at head and tail
  independently; drift beyond ~100 ppm is flagged in the report.
- **Multi-file primary support** — DJI cameras split long recordings;
  segments are ordered by creation time and treated as one continuous
  storyline (`--preserve-gaps` keeps real-world pauses instead).
- **Vertical-video framing presets** — `blur-bg` (the wide shot stays
  behind the vertical clip with a keyframed Gaussian blur, strength set
  by `--blur-amount`), `center` (pillarbox over the sharp wide shot),
  `fill` (scale to fill), `pip-left` / `pip-right` picture-in-picture.
- **Minimal or one-lane-per-clip layering** — overlapping overlays never
  collide; `--lane-per-clip` gives every clip its own layer if you prefer.
- **Finished video** — `--render` composites everything (overlays,
  blur, title fade, ducked audio, looped music) into one final MP4 with
  ffmpeg; also a "Finished video" checkbox in the app with a Watch
  button. No editor required.
- **Opening title cards** — `--title "Front Bumper Removal"
  --title-description "2024 Toyota GR86"` puts a white card with the
  title and description at the start, fading out over `--title-fade`
  seconds after `--title-hold`; four text arrangements via
  `--title-style` (live previews in the app). FCPXML-only — the CLI and
  app warn if a Premiere export would drop it.
- **Background music** — loop a song under the whole video at background
  level (`--music song.mp3 --music-volume -22`), each pass its own
  trimmable clip on a layer below; `--music-duck` silences it under
  glasses clips with smooth fades. Both off by default.
- **Auto-updates** — the app offers one-click self-update from the
  bottom-left corner when a newer release is available.
- **Roles** — clips are tagged `dialogue.DJI` / `dialogue.Meta` in FCP so
  you can audition, mute, or mix each camera as a group.
- **JSON report** (`--report`) with every offset, confidence, and warning —
  scriptable and diffable.
- **`editsync probe`** and **`--dry-run`** to sanity-check before writing.
- **Classification overrides** (`--primary/--overlay` globs) when heuristics
  aren't enough.

## Porting to other editors

The sync engine, timeline model, and exporters are strictly separated —
adding a new editor means writing one exporter module against the
`Timeline` model. Premiere and OTIO exporters ship already; see
[docs/PORTING.md](docs/PORTING.md) for the recipe and per-editor notes
(DaVinci Resolve, CapCut, kdenlive, ...).

## Project layout

```
src/editsync/
  media.py        ffprobe wrapper + camera classification
  audio.py        audio extraction + onset envelopes (ffmpeg + numpy)
  sync.py         two-stage cross-correlation, confidence, drift
  timeline.py     editor-agnostic timeline model (rational times)
  builder.py      orchestration: match, place, layer, duck
  report.py       text/JSON sync reports
  cli.py          `editsync` command
  gui/            the desktop app (PySide6, monochrome theme)
  exporters/
    fcpxml.py     Final Cut Pro (primary target)
    premiere.py   Adobe Premiere Pro (xmeml)
    otio.py       OpenTimelineIO (universal interchange)
packaging/        PyInstaller spec + macOS/Windows build scripts
tests/            unit, GUI (offscreen), and ffmpeg-backed e2e tests
docs/             app guide, FCP workflow guide, porting guide
```

## Development

```sh
pip install -e . pytest
pytest
```
