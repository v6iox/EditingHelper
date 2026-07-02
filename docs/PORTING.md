# Porting EditSync to other editing software

The codebase is layered so that **only the exporter layer knows about a
specific editor**:

```
media.py / audio.py / sync.py     <- camera + audio science (no editor code)
timeline.py                       <- neutral Timeline model
builder.py                        <- placement policy (lanes, ducking, markers)
exporters/<editor>.py             <- ONE file per editing application
```

To support a new editor you write one module and register it.

## The Timeline contract

An exporter receives a `Timeline`:

| field | meaning |
|---|---|
| `frame_rate, width, height` | sequence format (rational fps, e.g. `30000/1001`) |
| `clips` | `TimelineClip` list; `lane == 0` is the primary storyline, `lane >= 1` are overlay layers |
| `duck_regions` | timeline intervals where primary audio should drop to `level_db` |

Each `TimelineClip` has `media` (path, resolution, fps, audio info),
`timeline_start`, `duration`, `source_start` (all `Fraction` **seconds**,
already frame-quantized), optional `transform_scale`/`transform_position`
(vertical-video framing suggestion), `markers`, `role` ("DJI"/"Meta"), and
`sync_confidence`.

Because times are exact rationals, you can convert losslessly to whatever
the target wants: frame counts (`round(t * frame_rate)`), rational strings
(FCPXML), or float seconds.

## Recipe

1. Create `src/editsync/exporters/myeditor.py` with:

   ```python
   def export(timeline: Timeline, path: Path) -> None: ...
   ```

2. Register it in `exporters/__init__.py`:

   ```python
   EXPORTERS["myeditor"] = (myeditor.export, ".ext", "My Editor")
   ```

3. Add a fixture-based test in `tests/test_exporters.py` (see the existing
   classes — they validate structure against a small three-clip timeline)
   and, if the format is parseable, assertions in
   `tests/test_integration.py`.

That's the whole surface. Don't put editor-specific logic in `builder.py`;
if an editor needs information the model lacks, extend the model neutrally.

## Per-editor notes

- **DaVinci Resolve** — already covered twice: Resolve imports the FCPXML
  export (File → Import → Timeline) and reads `.otio` natively. A dedicated
  exporter is only worth it if you want Resolve-specific extras (fusion
  templates, audio ducking via automation).
- **Adobe Premiere Pro** — shipped (`exporters/premiere.py`, xmeml v4).
  Ducking is intentionally not encoded (xmeml audio level keyframes import
  inconsistently across Premiere versions); the duck intervals are in the
  JSON report and OTIO metadata instead.
- **Anything OTIO-adjacent** (Avid via adapters, kdenlive, Hiero, RV) —
  use the `.otio` output. EditSync-specific data (confidence, duck regions,
  suggested transforms) rides along in `metadata.editsync`.
- **CapCut** — shipped (`exporters/capcut.py`, experimental): CapCut
  has no official interchange format, so the exporter writes its local
  draft folder (`draft_content.json`, community-mapped schema, times in
  microseconds). Expect occasional breakage across CapCut releases; the
  finished-video render is the fallback. CapCut *mobile* has no import
  mechanism at all — the iPhone app shares the finalized video into it.

## Porting the *analysis* somewhere else

The sync engine itself is dependency-light (numpy + ffmpeg subprocesses)
and callable as a library:

```python
from editsync.media import probe, classify, Role
from editsync.builder import build, BuildOptions

media = [probe(p) for p in paths]
classify(media)
result = build(
    [m for m in media if m.role == Role.PRIMARY],
    [m for m in media if m.role == Role.OVERLAY],
    BuildOptions(project_name="My Video"),
)
# result.timeline -> feed your own exporter
# result.matches  -> offsets + confidences, if you only want the numbers
```

so it can be embedded in a GUI app, a watch-folder daemon, or an FCP
Workflow Extension later without touching the math.
