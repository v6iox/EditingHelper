"""Adobe Premiere Pro exporter (xmeml v4, "Final Cut Pro XML" interchange).

Premiere imports this via File > Import. The primary camera goes on video
track V1, overlay lanes become V2+, and every clip carries linked audio on
the matching audio track. Audio ducking is not expressed here (xmeml level
keyframes are unreliable across Premiere versions); the sync report lists
the overlay intervals so ducking can be applied with Essential Sound.
Opening title cards are likewise FCPXML-only (xmeml has no portable title
element) — the CLI and app warn when a title would be dropped.
"""

from __future__ import annotations

import urllib.request
from fractions import Fraction
from pathlib import Path
from xml.etree import ElementTree as ET

from ..media import MediaFile
from ..timeline import Timeline, TimelineClip


def _timebase(rate: Fraction) -> tuple[int, bool]:
    """xmeml expresses rates as an integer timebase + NTSC flag."""
    if rate.denominator == 1001:
        return round(rate * Fraction(1001, 1000)), True
    return round(rate), False


def _rate_el(parent: ET.Element, timebase: int, ntsc: bool) -> None:
    rate = ET.SubElement(parent, "rate")
    ET.SubElement(rate, "timebase").text = str(timebase)
    ET.SubElement(rate, "ntsc").text = "TRUE" if ntsc else "FALSE"


def _to_frames(t: Fraction, rate: Fraction) -> int:
    return round(t * rate)


class _Files:
    """Emit each <file> definition once; later uses are id-only references."""

    def __init__(self, timebase: int, ntsc: bool):
        self.timebase = timebase
        self.ntsc = ntsc
        self._ids: dict[str, str] = {}

    def element(self, media: MediaFile, seq_rate: Fraction) -> ET.Element:
        key = str(media.path)
        if key in self._ids:
            return ET.Element("file", id=self._ids[key])
        fid = f"file-{len(self._ids) + 1}"
        self._ids[key] = fid
        el = ET.Element("file", id=fid)
        ET.SubElement(el, "name").text = media.path.name
        ET.SubElement(el, "pathurl").text = "file://localhost" + urllib.request.pathname2url(
            str(media.path)
        )
        _rate_el(el, *_timebase(media.frame_rate if media.frame_rate else seq_rate))
        ET.SubElement(el, "duration").text = str(_to_frames(media.duration, seq_rate))
        media_el = ET.SubElement(el, "media")
        if media.width > 0:  # audio-only files (music) have no video section
            video = ET.SubElement(media_el, "video")
            chars = ET.SubElement(video, "samplecharacteristics")
            ET.SubElement(chars, "width").text = str(media.width)
            ET.SubElement(chars, "height").text = str(media.height)
        if media.has_audio:
            audio = ET.SubElement(media_el, "audio")
            achars = ET.SubElement(audio, "samplecharacteristics")
            ET.SubElement(achars, "samplerate").text = str(media.audio_rate or 48000)
            ET.SubElement(achars, "depth").text = "16"
            ET.SubElement(audio, "channelcount").text = str(
                max(1, media.audio_channels)
            )
        return el


def _clipitem(
    clip: TimelineClip,
    files: _Files,
    seq_rate: Fraction,
    kind: str,
    index: int,
) -> ET.Element:
    el = ET.Element("clipitem", id=f"clipitem-{kind}-{index}")
    ET.SubElement(el, "name").text = clip.media.name
    ET.SubElement(el, "enabled").text = "TRUE"
    ET.SubElement(el, "start").text = str(_to_frames(clip.timeline_start, seq_rate))
    ET.SubElement(el, "end").text = str(_to_frames(clip.timeline_end, seq_rate))
    ET.SubElement(el, "in").text = str(_to_frames(clip.source_start, seq_rate))
    ET.SubElement(el, "out").text = str(
        _to_frames(clip.source_start + clip.duration, seq_rate)
    )
    _rate_el(el, *_timebase(seq_rate))
    el.append(files.element(clip.media, seq_rate))
    if kind == "audio":
        source = ET.SubElement(el, "sourcetrack")
        ET.SubElement(source, "mediatype").text = "audio"
        ET.SubElement(source, "trackindex").text = "1"
    return el


def export(timeline: Timeline, path: Path) -> None:
    timebase, ntsc = _timebase(timeline.frame_rate)
    files = _Files(timebase, ntsc)

    root = ET.Element("xmeml", version="4")
    sequence = ET.SubElement(root, "sequence", id="sequence-1")
    ET.SubElement(sequence, "name").text = timeline.name
    ET.SubElement(sequence, "duration").text = str(
        _to_frames(timeline.duration, timeline.frame_rate)
    )
    _rate_el(sequence, timebase, ntsc)

    media_el = ET.SubElement(sequence, "media")
    video = ET.SubElement(media_el, "video")
    vformat = ET.SubElement(video, "format")
    vchars = ET.SubElement(vformat, "samplecharacteristics")
    _rate_el(vchars, timebase, ntsc)
    ET.SubElement(vchars, "width").text = str(timeline.width)
    ET.SubElement(vchars, "height").text = str(timeline.height)

    # V1 = primary storyline, V2+ = overlay lanes
    lanes: dict[int, list[TimelineClip]] = {0: timeline.primary_clips}
    for clip in timeline.overlay_clips:
        lanes.setdefault(clip.lane, []).append(clip)

    counter = 0
    audio_tracks: list[list[TimelineClip]] = []
    for lane in sorted(lanes):
        track = ET.SubElement(video, "track")
        for clip in lanes[lane]:
            counter += 1
            track.append(_clipitem(clip, files, timeline.frame_rate, "video", counter))
        audio_tracks.append(lanes[lane])

    audio = ET.SubElement(media_el, "audio")
    counter = 0
    if timeline.music_clips:
        audio_tracks.append(timeline.music_clips)
    for clips in audio_tracks:
        track = ET.SubElement(audio, "track")
        for clip in clips:
            if not clip.media.has_audio:
                continue
            counter += 1
            track.append(_clipitem(clip, files, timeline.frame_rate, "audio", counter))

    tree = ET.ElementTree(root)
    ET.indent(tree, space="    ")
    xml_body = ET.tostring(root, encoding="unicode")
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<!DOCTYPE xmeml>\n"
        f"{xml_body}\n"
    )
