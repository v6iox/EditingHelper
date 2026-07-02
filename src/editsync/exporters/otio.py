"""OpenTimelineIO exporter.

Writes the OTIO JSON schema directly (no dependency on the otio package).
OTIO is the industry interchange format: DaVinci Resolve reads it natively,
and adapters exist for Premiere, Avid, kdenlive, and others — this is the
"portable" output for editors we don't target directly yet.
"""

from __future__ import annotations

import json
from fractions import Fraction
from pathlib import Path

from ..timeline import Timeline, TimelineClip


def _rational_time(seconds: Fraction, rate: float) -> dict:
    return {
        "OTIO_SCHEMA": "RationalTime.1",
        "rate": rate,
        "value": round(float(seconds) * rate, 6),
    }


def _time_range(start: Fraction, duration: Fraction, rate: float) -> dict:
    return {
        "OTIO_SCHEMA": "TimeRange.1",
        "start_time": _rational_time(start, rate),
        "duration": _rational_time(duration, rate),
    }


def _gap(duration: Fraction, rate: float) -> dict:
    return {
        "OTIO_SCHEMA": "Gap.1",
        "name": "Gap",
        "source_range": _time_range(Fraction(0), duration, rate),
        "effects": [],
        "markers": [],
        "metadata": {},
    }


def _clip(clip: TimelineClip, rate: float) -> dict:
    markers = [
        {
            "OTIO_SCHEMA": "Marker.2",
            "name": m.text,
            "color": "RED" if m.completed is not None else "GREEN",
            "marked_range": _time_range(m.time, Fraction(0), rate),
            "metadata": {},
        }
        for m in clip.markers
    ]
    metadata: dict = {"editsync": {"role": clip.role}}
    if clip.sync_confidence is not None:
        metadata["editsync"]["sync_confidence"] = round(clip.sync_confidence, 4)
    if clip.transform_scale is not None:
        metadata["editsync"]["suggested_scale"] = list(clip.transform_scale)
    if clip.transform_position is not None:
        metadata["editsync"]["suggested_position"] = list(clip.transform_position)
    return {
        "OTIO_SCHEMA": "Clip.1",
        "name": clip.media.name,
        "source_range": _time_range(clip.source_start, clip.duration, rate),
        "media_reference": {
            "OTIO_SCHEMA": "ExternalReference.1",
            "target_url": clip.media.path.as_uri(),
            "available_range": _time_range(
                Fraction(0), clip.media.duration, rate
            ),
            "metadata": {},
        },
        "effects": [],
        "markers": markers,
        "metadata": metadata,
    }


def _track(name: str, clips: list[TimelineClip], rate: float) -> dict:
    children: list[dict] = []
    cursor = Fraction(0)
    for clip in sorted(clips, key=lambda c: c.timeline_start):
        if clip.timeline_start > cursor:
            children.append(_gap(clip.timeline_start - cursor, rate))
        children.append(_clip(clip, rate))
        cursor = clip.timeline_end
    return {
        "OTIO_SCHEMA": "Track.1",
        "name": name,
        "kind": "Video",
        "children": children,
        "source_range": None,
        "effects": [],
        "markers": [],
        "metadata": {},
    }


def export(timeline: Timeline, path: Path) -> None:
    rate = float(timeline.frame_rate)
    tracks = [_track("Primary (DJI)", timeline.primary_clips, rate)]
    for lane in range(1, timeline.lane_count + 1):
        clips = [c for c in timeline.overlay_clips if c.lane == lane]
        if clips:
            tracks.append(_track(f"Overlay {lane} (Meta)", clips, rate))

    doc = {
        "OTIO_SCHEMA": "Timeline.1",
        "name": timeline.name,
        "global_start_time": _rational_time(Fraction(0), rate),
        "tracks": {
            "OTIO_SCHEMA": "Stack.1",
            "name": "tracks",
            "children": tracks,
            "source_range": None,
            "effects": [],
            "markers": [],
            "metadata": {},
        },
        "metadata": {
            "editsync": {
                "duck_regions": [
                    {
                        "start": float(r.start),
                        "end": float(r.end),
                        "level_db": r.level_db,
                    }
                    for r in timeline.duck_regions
                ],
                "blur_regions": [
                    {
                        "start": float(r.start),
                        "end": float(r.end),
                        "amount": r.amount,
                    }
                    for r in timeline.blur_regions
                ],
            }
        },
    }
    path.write_text(json.dumps(doc, indent=2))
