"""Human-readable and machine-readable sync reports."""

from __future__ import annotations

import json
from pathlib import Path

from .builder import BuildResult
from .media import MediaFile


def _fmt_tc(seconds: float) -> str:
    s = max(0.0, seconds)
    h, rem = divmod(int(s), 3600)
    m, sec = divmod(rem, 60)
    frac = s - int(s)
    return f"{h:02d}:{m:02d}:{sec:02d}.{int(frac * 1000):03d}"


def text_report(result: BuildResult) -> str:
    tl = result.timeline
    lines: list[str] = []
    lines.append(f"Project: {tl.name}")
    lines.append(
        f"Sequence: {tl.width}x{tl.height} @ {float(tl.frame_rate):.3f} fps, "
        f"duration {_fmt_tc(float(tl.duration))}"
    )
    lines.append("")
    lines.append("Primary storyline (lane 0):")
    for c in tl.primary_clips:
        lines.append(
            f"  {_fmt_tc(float(c.timeline_start))}  {c.media.path.name}"
            f"  ({_fmt_tc(float(c.duration))})"
        )
    lines.append("")
    lines.append("Overlay clips:")
    placed = [m for m in result.matches if m.placed]
    skipped = [m for m in result.matches if not m.placed]
    if not placed:
        lines.append("  (none placed)")
    for m in placed:
        conf = m.result.confidence if m.result else 0.0
        lines.append(
            f"  {_fmt_tc(float(m.timeline_start))}  {m.media.path.name}"
            f"  -> {m.primary.path.name if m.primary else '?'}"
            f"  confidence {conf:.2f}"
        )
    if skipped:
        lines.append("")
        lines.append("Not placed:")
        for m in skipped:
            lines.append(f"  {m.media.path.name}: {m.reason}")
    if result.warnings:
        lines.append("")
        lines.append("Warnings:")
        for w in result.warnings:
            lines.append(f"  ! {w}")
    return "\n".join(lines)


def json_report(result: BuildResult) -> dict:
    tl = result.timeline
    return {
        "project": tl.name,
        "sequence": {
            "width": tl.width,
            "height": tl.height,
            "frame_rate": float(tl.frame_rate),
            "duration_seconds": float(tl.duration),
        },
        "primary_clips": [
            {
                "file": str(c.media.path),
                "timeline_start": float(c.timeline_start),
                "duration": float(c.duration),
            }
            for c in tl.primary_clips
        ],
        "overlays": [
            {
                "file": str(m.media.path),
                "placed": m.placed,
                "matched_primary": str(m.primary.path) if m.primary else None,
                "timeline_start": float(m.timeline_start)
                if m.timeline_start is not None
                else None,
                "offset_in_primary": m.result.offset if m.result else None,
                "confidence": m.result.confidence if m.result else None,
                "peak_ratio": m.result.peak_ratio if m.result else None,
                "drift_ppm": m.result.drift_ppm if m.result else None,
                "reason": m.reason,
            }
            for m in result.matches
        ],
        "warnings": result.warnings,
    }


def write_json_report(result: BuildResult, path: Path) -> None:
    path.write_text(json.dumps(json_report(result), indent=2))


def probe_table(media: list[MediaFile]) -> str:
    lines = [
        f"{'file':<40} {'role':<8} {'size':<11} {'fps':<7} {'dur':<10} {'reason'}"
    ]
    for m in media:
        lines.append(
            f"{m.path.name:<40} {m.role.value:<8} "
            f"{m.display_width}x{m.display_height:<6} "
            f"{float(m.frame_rate):<7.2f} {_fmt_tc(float(m.duration)):<10} "
            f"{m.role_reason}"
        )
    return "\n".join(lines)
