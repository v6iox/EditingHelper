"""editsync command-line interface.

    editsync sync ./footage -o myvideo.fcpxml
    editsync sync ./footage --format all --report sync.json
    editsync probe ./footage
    editsync doctor
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .builder import BuildOptions, build
from .media import (
    MediaFile,
    ProbeError,
    Role,
    classify,
    collect_video_files,
    probe,
    require_tool,
)
from .report import probe_table, text_report, write_json_report


def _probe_all(paths: list[Path]) -> list[MediaFile]:
    media = []
    for p in paths:
        try:
            media.append(probe(p))
        except ProbeError as exc:
            print(f"warning: skipping {p.name}: {exc}", file=sys.stderr)
    return media


def _classified(args) -> list[MediaFile]:
    files = collect_video_files([Path(p) for p in args.inputs])
    if not files:
        raise ProbeError("No video files found in the given inputs.")
    media = _probe_all(files)
    classify(
        media,
        primary_patterns=args.primary or None,
        overlay_patterns=args.overlay or None,
    )
    return media


def cmd_sync(args) -> int:
    media = _classified(args)
    primaries = [m for m in media if m.role == Role.PRIMARY]
    overlays = [m for m in media if m.role == Role.OVERLAY]
    music_files = [m for m in media if m.role == Role.MUSIC]
    unknown = [m for m in media if m.role == Role.UNKNOWN]

    music = None
    if args.music:
        from .media import probe as _probe

        music = next(
            (m for m in music_files if m.path == Path(args.music).resolve()),
            None,
        ) or _probe(Path(args.music))
    elif music_files:
        print(
            f"note: found audio file(s) "
            f"{', '.join(m.path.name for m in music_files)}; pass "
            f"--music <file> to loop one as background music",
            file=sys.stderr,
        )
    for m in unknown:
        print(
            f"warning: could not classify {m.path.name}; ignoring "
            f"(use --primary/--overlay globs to include it)",
            file=sys.stderr,
        )
    silent = [m for m in overlays if not m.has_audio]
    for m in silent:
        print(
            f"warning: {m.path.name} has no audio track; cannot audio-sync it",
            file=sys.stderr,
        )
    overlays = [m for m in overlays if m.has_audio]

    print(probe_table(media))
    print()

    opts = BuildOptions(
        project_name=args.project_name,
        min_confidence=args.min_confidence,
        duck_db=None if args.duck.lower() in ("off", "none") else float(args.duck),
        lane_per_clip=args.lane_per_clip,
        music_db=args.music_volume,
        music_duck=args.music_duck,
        title_text=args.title,
        title_description=args.title_description,
        title_style=args.title_style,
        title_hold=args.title_hold,
        title_fade=args.title_fade,
        preserve_gaps=args.preserve_gaps,
        overlay_style=args.overlay_style,
        blur_amount=args.blur_amount,
        force_place=args.force_place,
        add_sync_markers=not args.no_markers,
        search_window=args.search_window,
        max_workers=args.jobs,
    )

    result = build(
        primaries, overlays, opts, progress=lambda msg: print(msg), music=music
    )
    print()
    print(text_report(result))

    if args.report:
        write_json_report(result, Path(args.report))
        print(f"\nJSON report written to {args.report}")

    if args.dry_run:
        print("\n(dry run: no timeline files written)")
        return 0

    from . import exporters

    formats = list(exporters.EXPORTERS) if args.format == "all" else [args.format]
    out_base = Path(args.output) if args.output else Path(args.project_name.replace(" ", "_"))
    for fmt in formats:
        ext = exporters.default_extension(fmt)
        out = out_base if out_base.suffix and len(formats) == 1 else out_base.with_suffix(ext)
        exporters.export(fmt, result.timeline, out)
        print(f"Wrote {fmt}: {out}")
    if result.timeline.title_card is not None and "premiere" in formats:
        print(
            "note: the opening title card is included in the Final Cut Pro "
            "export only; Premiere's interchange format cannot carry it.",
            file=sys.stderr,
        )

    unplaced = [m for m in result.matches if not m.placed]
    if unplaced:
        print(
            f"\n{len(unplaced)} clip(s) were not placed - see the report above.",
            file=sys.stderr,
        )
    return 0


def cmd_probe(args) -> int:
    media = _classified(args)
    print(probe_table(media))
    return 0


def cmd_doctor(_args) -> int:
    ok = True
    for tool in ("ffmpeg", "ffprobe"):
        try:
            exe = require_tool(tool)
            print(f"ok: {tool} found at {exe}")
        except ProbeError as exc:
            print(f"MISSING: {exc}")
            ok = False
    try:
        import numpy

        print(f"ok: numpy {numpy.__version__}")
    except ImportError:
        print("MISSING: numpy (pip install numpy)")
        ok = False
    print(f"editsync {__version__}")
    return 0 if ok else 1


def _add_common_input_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "inputs",
        nargs="+",
        help="video files and/or directories to scan",
    )
    parser.add_argument(
        "--primary",
        action="append",
        metavar="GLOB",
        help="filename glob treated as primary footage (repeatable)",
    )
    parser.add_argument(
        "--overlay",
        action="append",
        metavar="GLOB",
        help="filename glob treated as overlay footage (repeatable)",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="editsync",
        description="Audio-sync multicam footage into an editable timeline.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_sync = sub.add_parser("sync", help="sync footage and export a timeline")
    _add_common_input_args(p_sync)
    p_sync.add_argument("-o", "--output", help="output file path (extension optional)")
    p_sync.add_argument(
        "-f",
        "--format",
        default="fcpxml",
        choices=["fcpxml", "premiere", "otio", "all"],
        help="timeline format to export (default: fcpxml)",
    )
    p_sync.add_argument(
        "--project-name", default="EditSync Project", help="name of the project/sequence"
    )
    p_sync.add_argument(
        "--overlay-style",
        default="center",
        choices=["center", "blur-bg", "fill", "pip-left", "pip-right"],
        help="how vertical overlay clips are framed in the horizontal sequence "
        "(blur-bg keeps the clip sharp and blurs the main camera behind it)",
    )
    p_sync.add_argument(
        "--blur-amount",
        type=float,
        default=50.0,
        metavar="0-100",
        help="background blur strength for --overlay-style blur-bg (default: 50)",
    )
    p_sync.add_argument(
        "--duck",
        default="-60",
        help="dB level for primary audio under overlays, or 'off' (default: -60)",
    )
    p_sync.add_argument(
        "--music",
        metavar="FILE",
        help="audio file to loop as background music under the whole video "
        "(off unless given)",
    )
    p_sync.add_argument(
        "--music-volume",
        type=float,
        default=-22.0,
        metavar="DB",
        help="background music level in dB (default: -22)",
    )
    p_sync.add_argument(
        "--title",
        default="",
        help="opening title text shown over a white card that fades out",
    )
    p_sync.add_argument(
        "--title-description",
        default="",
        help="line under the title (e.g. year/make/model)",
    )
    p_sync.add_argument(
        "--title-style",
        default="classic",
        choices=["classic", "lower-left", "statement", "elegant"],
        help="arrangement of the title text on the card (default: classic)",
    )
    p_sync.add_argument(
        "--title-hold",
        type=float,
        default=3.0,
        metavar="SECONDS",
        help="how long the title card stays fully visible (default: 3)",
    )
    p_sync.add_argument(
        "--title-fade",
        type=float,
        default=1.0,
        metavar="SECONDS",
        help="how long the fade-out takes (default: 1)",
    )
    p_sync.add_argument(
        "--music-duck",
        action="store_true",
        help="mute the background music while an overlay clip plays",
    )
    p_sync.add_argument(
        "--min-confidence",
        type=float,
        default=0.35,
        help="minimum sync confidence to place a clip (default: 0.35)",
    )
    p_sync.add_argument(
        "--force-place",
        action="store_true",
        help="place low-confidence clips anyway (they get warning markers)",
    )
    p_sync.add_argument(
        "--lane-per-clip",
        action="store_true",
        help="give every overlay its own layer instead of packing lanes",
    )
    p_sync.add_argument(
        "--preserve-gaps",
        action="store_true",
        help="keep real-world gaps between primary recordings (needs creation timestamps)",
    )
    p_sync.add_argument(
        "--search-window",
        type=float,
        default=None,
        metavar="SECONDS",
        help="use file creation times to pre-filter which primary each overlay "
        "is matched against (speeds up large shoots, disambiguates repeated audio)",
    )
    p_sync.add_argument(
        "--no-markers", action="store_true", help="skip sync-confidence markers"
    )
    p_sync.add_argument("--report", help="also write a JSON sync report to this path")
    p_sync.add_argument(
        "--dry-run", action="store_true", help="analyze and report without writing timelines"
    )
    p_sync.add_argument(
        "-j", "--jobs", type=int, default=4, help="parallel audio extraction jobs"
    )
    p_sync.set_defaults(func=cmd_sync)

    p_probe = sub.add_parser("probe", help="show how files would be classified")
    _add_common_input_args(p_probe)
    p_probe.set_defaults(func=cmd_probe)

    p_doctor = sub.add_parser("doctor", help="check that dependencies are installed")
    p_doctor.set_defaults(func=cmd_doctor)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (ProbeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
