"""Exporter registry.

Each exporter turns the editor-agnostic :class:`editsync.timeline.Timeline`
into a project file for one editing application. To port editsync to a new
editor, add a module here with an `export(timeline, path)` function and
register it in EXPORTERS. See docs/PORTING.md.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from ..timeline import Timeline
from . import capcut, fcpxml, otio, premiere

EXPORTERS: dict[str, tuple[Callable[[Timeline, Path], None], str, str]] = {
    # name: (export function, default extension, description)
    "fcpxml": (fcpxml.export, ".fcpxml", "Final Cut Pro (also imports into DaVinci Resolve)"),
    "premiere": (premiere.export, ".xml", "Adobe Premiere Pro (xmeml)"),
    "otio": (otio.export, ".otio", "OpenTimelineIO JSON (universal interchange)"),
    "capcut": (
        capcut.export,
        ".capcut",
        "CapCut desktop draft folder (experimental; no official format exists)",
    ),
}


def export(fmt: str, timeline: Timeline, path: Path) -> None:
    if fmt not in EXPORTERS:
        raise ValueError(
            f"Unknown format '{fmt}'. Available: {', '.join(EXPORTERS)}"
        )
    EXPORTERS[fmt][0](timeline, path)


def default_extension(fmt: str) -> str:
    return EXPORTERS[fmt][1]
