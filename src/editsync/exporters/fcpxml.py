"""FCPXML exporter for Final Cut Pro.

Produces an .fcpxml (v1.11) you can drag straight into Final Cut Pro:

- primary footage as sequential clips on the primary storyline
- each overlay as a *connected clip* on its own lane above, at the
  audio-synced position, fully trimmable/extendable afterwards
- volume keyframes ducking the primary camera's audio wherever an overlay
  (with its own audio) sits on top
- scale/position transforms for the chosen vertical-video style
- sync-confidence markers (to-do markers on low-confidence placements)

DaVinci Resolve also imports FCPXML, so this exporter covers both apps.
"""

from __future__ import annotations

import hashlib
import urllib.request
from fractions import Fraction
from pathlib import Path
from typing import Optional
from xml.etree import ElementTree as ET

from ..media import MediaFile
from ..timeline import DuckRegion, Timeline, TimelineClip

FCPXML_VERSION = "1.11"


def fmt_time(t: Fraction) -> str:
    """Render a Fraction of seconds as an FCPXML rational time."""
    t = Fraction(t)
    if t.denominator == 1:
        return f"{t.numerator}s"
    return f"{t.numerator}/{t.denominator}s"


def fmt_db(db: float) -> str:
    return f"{db:g}dB"


def _file_url(path: Path) -> str:
    return "file://" + urllib.request.pathname2url(str(path))


def _asset_uid(path: Path) -> str:
    return hashlib.md5(str(path).encode()).hexdigest().upper()


class _Resources:
    """Deduplicating registry for <format> and <asset> resources."""

    def __init__(self, root: ET.Element):
        self.el = ET.SubElement(root, "resources")
        self._formats: dict[tuple, str] = {}
        self._assets: dict[str, str] = {}
        self._next = 1

    def _new_id(self, prefix: str) -> str:
        rid = f"{prefix}{self._next}"
        self._next += 1
        return rid

    def format_id(
        self, width: int, height: int, frame_duration: Fraction
    ) -> str:
        key = (width, height, frame_duration)
        if key not in self._formats:
            rid = self._new_id("r")
            ET.SubElement(
                self.el,
                "format",
                id=rid,
                frameDuration=fmt_time(frame_duration),
                width=str(width),
                height=str(height),
                colorSpace="1-1-1 (Rec. 709)",
            )
            self._formats[key] = rid
        return self._formats[key]

    def asset_id(self, media: MediaFile) -> str:
        key = str(media.path)
        if key not in self._assets:
            fmt_id = self.format_id(
                media.width, media.height, media.frame_duration
            )
            rid = self._new_id("r")
            asset = ET.SubElement(
                self.el,
                "asset",
                id=rid,
                name=media.name,
                uid=_asset_uid(media.path),
                start="0s",
                duration=fmt_time(media.duration),
                hasVideo="1",
                format=fmt_id,
            )
            if media.has_audio:
                asset.set("hasAudio", "1")
                asset.set("audioSources", "1")
                asset.set("audioChannels", str(max(1, media.audio_channels)))
                asset.set("audioRate", str(media.audio_rate or 48000))
            ET.SubElement(
                asset,
                "media-rep",
                kind="original-media",
                src=_file_url(media.path),
            )
            self._assets[key] = rid
        return self._assets[key]


def _duck_keyframes(
    parent: TimelineClip,
    regions: list[DuckRegion],
    fade: Fraction,
) -> list[tuple[Fraction, float]]:
    """Volume keyframes (in the parent clip's source time) for duck regions
    overlapping the parent, clamped to the parent's extent."""
    keyframes: list[tuple[Fraction, float]] = []
    clip_start, clip_end = parent.timeline_start, parent.timeline_end

    def to_src(t: Fraction) -> Fraction:
        return parent.source_start + (t - clip_start)

    for region in regions:
        if region.end <= clip_start or region.start >= clip_end:
            continue
        points = [
            (region.start - fade, 0.0),
            (region.start, region.level_db),
            (region.end, region.level_db),
            (region.end + fade, 0.0),
        ]
        for t, level in points:
            t_clamped = min(max(t, clip_start), clip_end)
            keyframes.append((to_src(t_clamped), level))
    # keep keyframes strictly increasing in time; when clamping makes two
    # keyframes collide (duck region touching a clip edge), keep the more
    # ducked level so the overlay's audio still wins
    keyframes.sort(key=lambda kv: kv[0])
    unique: list[tuple[Fraction, float]] = []
    for t, level in keyframes:
        if unique and t <= unique[-1][0]:
            unique[-1] = (unique[-1][0], min(unique[-1][1], level))
            continue
        unique.append((t, level))
    return unique


def _add_transform(el: ET.Element, clip: TimelineClip) -> None:
    if clip.transform_scale is None and clip.transform_position is None:
        return
    attrs = {}
    if clip.transform_position is not None:
        x, y = clip.transform_position
        attrs["position"] = f"{x:g} {y:g}"
    if clip.transform_scale is not None:
        sx, sy = clip.transform_scale
        attrs["scale"] = f"{sx:g} {sy:g}"
    ET.SubElement(el, "adjust-transform", **attrs)


def _add_volume_keyframes(
    el: ET.Element, keyframes: list[tuple[Fraction, float]]
) -> None:
    if not keyframes:
        return
    adjust = ET.SubElement(el, "adjust-volume", amount="0dB")
    param = ET.SubElement(adjust, "param", name="amount")
    for t, level in keyframes:
        ET.SubElement(
            param, "keyframe", time=fmt_time(t), value=fmt_db(level)
        )


def _add_markers(el: ET.Element, clip: TimelineClip, frame_duration: Fraction) -> None:
    for marker in clip.markers:
        attrs = {
            "start": fmt_time(marker.time),
            "duration": fmt_time(frame_duration),
            "value": marker.text,
        }
        if marker.completed is not None:
            attrs["completed"] = "1" if marker.completed else "0"
        ET.SubElement(el, "marker", **attrs)


def _overlay_parent(
    overlay: TimelineClip, primaries: list[TimelineClip]
) -> TimelineClip:
    """Pick the primary-storyline clip an overlay connects to: the one whose
    span contains the overlay's start, else the nearest preceding clip."""
    parent = primaries[0]
    for p in primaries:
        if p.timeline_start <= overlay.timeline_start:
            parent = p
        else:
            break
    return parent


def build_tree(timeline: Timeline) -> ET.ElementTree:
    root = ET.Element("fcpxml", version=FCPXML_VERSION)
    resources = _Resources(root)

    seq_format = resources.format_id(
        timeline.width, timeline.height, timeline.frame_duration
    )

    library = ET.SubElement(root, "library")
    event = ET.SubElement(library, "event", name="EditSync")
    project = ET.SubElement(event, "project", name=timeline.name)
    sequence = ET.SubElement(
        project,
        "sequence",
        format=seq_format,
        duration=fmt_time(timeline.duration),
        tcStart="0s",
        tcFormat="NDF",
        audioLayout="stereo",
        audioRate="48k",
    )
    spine = ET.SubElement(sequence, "spine")

    primaries = timeline.primary_clips
    overlays = timeline.overlay_clips
    children: dict[int, list[TimelineClip]] = {id(p): [] for p in primaries}
    for o in overlays:
        children[id(_overlay_parent(o, primaries))].append(o)

    fade = Fraction(1, 4)
    cursor = Fraction(0)
    for p in primaries:
        if p.timeline_start > cursor:
            ET.SubElement(
                spine,
                "gap",
                name="Gap",
                offset=fmt_time(cursor),
                duration=fmt_time(p.timeline_start - cursor),
            )
        p_el = ET.SubElement(
            spine,
            "asset-clip",
            ref=resources.asset_id(p.media),
            offset=fmt_time(p.timeline_start),
            name=p.media.name,
            start=fmt_time(p.source_start),
            duration=fmt_time(p.duration),
            format=resources.format_id(
                p.media.width, p.media.height, p.media.frame_duration
            ),
            tcFormat="NDF",
        )
        if p.role:
            p_el.set("audioRole", f"dialogue.{p.role}")

        _add_volume_keyframes(
            p_el, _duck_keyframes(p, timeline.duck_regions, fade)
        )

        for o in children[id(p)]:
            # connected-clip offsets are expressed in the parent's source
            # time: parent.start + (position on timeline - parent offset)
            child_offset = p.source_start + (o.timeline_start - p.timeline_start)
            o_el = ET.SubElement(
                p_el,
                "asset-clip",
                lane=str(o.lane),
                ref=resources.asset_id(o.media),
                offset=fmt_time(child_offset),
                name=o.media.name,
                start=fmt_time(o.source_start),
                duration=fmt_time(o.duration),
                format=resources.format_id(
                    o.media.width, o.media.height, o.media.frame_duration
                ),
                tcFormat="NDF",
            )
            if o.role:
                o_el.set("audioRole", f"dialogue.{o.role}")
            _add_transform(o_el, o)
            _add_markers(o_el, o, timeline.frame_duration)

        _add_markers(p_el, p, timeline.frame_duration)
        cursor = p.timeline_end

    return ET.ElementTree(root)


def export(timeline: Timeline, path: Path) -> None:
    tree = build_tree(timeline)
    ET.indent(tree, space="    ")
    xml_body = ET.tostring(tree.getroot(), encoding="unicode")
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<!DOCTYPE fcpxml>\n"
        f"{xml_body}\n"
    )
