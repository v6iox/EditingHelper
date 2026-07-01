"""Media probing and camera classification.

Uses ffprobe to read stream/format metadata and classifies each file as
PRIMARY (the continuously-recording landscape camera, e.g. DJI Action 6)
or OVERLAY (short vertical clips, e.g. Meta glasses).
"""

from __future__ import annotations

import datetime as _dt
import fnmatch
import json
import shutil
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from fractions import Fraction
from pathlib import Path
from typing import Optional

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".mts", ".m2ts", ".avi", ".mkv"}

PRIMARY_NAME_HINTS = ("dji", "osmo", "action")
OVERLAY_NAME_HINTS = ("meta", "rayban", "ray-ban", "glasses", "stories", "aria")
PRIMARY_META_HINTS = ("dji",)
OVERLAY_META_HINTS = ("meta", "ray-ban", "rayban", "luxottica", "essilor")


class Role(Enum):
    PRIMARY = "primary"
    OVERLAY = "overlay"
    UNKNOWN = "unknown"


class ProbeError(RuntimeError):
    pass


@dataclass
class MediaFile:
    path: Path
    width: int
    height: int
    rotation: int  # degrees, normalized to 0/90/180/270
    duration: Fraction  # seconds
    frame_rate: Fraction  # frames per second
    audio_rate: int
    audio_channels: int
    has_audio: bool
    creation_time: Optional[_dt.datetime] = None
    make: str = ""
    model: str = ""
    role: Role = Role.UNKNOWN
    role_reason: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def display_width(self) -> int:
        """Width as displayed, accounting for rotation metadata."""
        return self.height if self.rotation in (90, 270) else self.width

    @property
    def display_height(self) -> int:
        return self.width if self.rotation in (90, 270) else self.height

    @property
    def is_portrait(self) -> bool:
        return self.display_height > self.display_width

    @property
    def name(self) -> str:
        return self.path.stem

    @property
    def frame_duration(self) -> Fraction:
        return 1 / self.frame_rate if self.frame_rate else Fraction(1, 30)


def require_tool(name: str) -> str:
    exe = shutil.which(name)
    if not exe:
        raise ProbeError(
            f"'{name}' was not found on PATH. Install ffmpeg (which includes "
            f"ffprobe): https://ffmpeg.org/download.html"
        )
    return exe


def _parse_fraction(text: str, default: Fraction) -> Fraction:
    try:
        if "/" in text:
            num, den = text.split("/", 1)
            if int(den) == 0:
                return default
            return Fraction(int(num), int(den))
        return Fraction(text)
    except (ValueError, ZeroDivisionError):
        return default


def _parse_creation_time(tags: dict) -> Optional[_dt.datetime]:
    for key in ("creation_time", "com.apple.quicktime.creationdate"):
        raw = tags.get(key)
        if not raw:
            continue
        text = raw.strip().replace("Z", "+00:00")
        for fmt in (None, "%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
            try:
                if fmt is None:
                    return _dt.datetime.fromisoformat(text)
                return _dt.datetime.strptime(text, fmt)
            except ValueError:
                continue
    return None


def _stream_rotation(stream: dict) -> int:
    rotation = 0
    tags = stream.get("tags") or {}
    if "rotate" in tags:
        try:
            rotation = int(tags["rotate"])
        except ValueError:
            rotation = 0
    for sd in stream.get("side_data_list") or []:
        if "rotation" in sd:
            try:
                rotation = int(sd["rotation"])
            except (ValueError, TypeError):
                pass
    return rotation % 360


def probe(path: Path) -> MediaFile:
    """Probe a media file with ffprobe and return a MediaFile."""
    ffprobe = require_tool("ffprobe")
    proc = subprocess.run(
        [
            ffprobe,
            "-v", "error",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise ProbeError(f"ffprobe failed for {path}: {proc.stderr.strip()}")
    data = json.loads(proc.stdout)

    video = next(
        (s for s in data.get("streams", []) if s.get("codec_type") == "video"),
        None,
    )
    audio = next(
        (s for s in data.get("streams", []) if s.get("codec_type") == "audio"),
        None,
    )
    if video is None:
        raise ProbeError(f"No video stream in {path}")

    fmt = data.get("format", {})
    fmt_tags = {k.lower(): v for k, v in (fmt.get("tags") or {}).items()}
    video_tags = {k.lower(): v for k, v in (video.get("tags") or {}).items()}
    all_tags = {**video_tags, **fmt_tags}

    duration_text = fmt.get("duration") or video.get("duration") or "0"
    frame_rate = _parse_fraction(
        video.get("avg_frame_rate") or video.get("r_frame_rate") or "30",
        Fraction(30),
    )
    if frame_rate <= 0:
        frame_rate = _parse_fraction(video.get("r_frame_rate") or "30", Fraction(30))

    make = all_tags.get("make", "") or all_tags.get("com.apple.quicktime.make", "")
    model = all_tags.get("model", "") or all_tags.get("com.apple.quicktime.model", "")

    return MediaFile(
        path=path.resolve(),
        width=int(video.get("width", 0)),
        height=int(video.get("height", 0)),
        rotation=_stream_rotation(video),
        duration=_parse_fraction(duration_text, Fraction(0)),
        frame_rate=frame_rate,
        audio_rate=int(audio.get("sample_rate", 48000)) if audio else 0,
        audio_channels=int(audio.get("channels", 0)) if audio else 0,
        has_audio=audio is not None,
        creation_time=_parse_creation_time(all_tags),
        make=make,
        model=model,
    )


def collect_video_files(inputs: list[Path]) -> list[Path]:
    """Expand files/directories into a sorted, de-duplicated list of videos."""
    found: list[Path] = []
    for item in inputs:
        if item.is_dir():
            for child in sorted(item.rglob("*")):
                if child.is_file() and child.suffix.lower() in VIDEO_EXTENSIONS:
                    found.append(child)
        elif item.is_file():
            found.append(item)
        else:
            raise ProbeError(f"Input not found: {item}")
    seen: set[Path] = set()
    unique = []
    for f in found:
        r = f.resolve()
        if r not in seen:
            seen.add(r)
            unique.append(f)
    return unique


def _matches_any(name: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(name.lower(), p.lower()) for p in patterns)


def classify(
    media: list[MediaFile],
    primary_patterns: Optional[list[str]] = None,
    overlay_patterns: Optional[list[str]] = None,
) -> None:
    """Assign a Role to each MediaFile in place.

    Precedence: explicit CLI glob patterns > camera make/model metadata >
    filename hints > orientation (landscape=primary, portrait=overlay).
    """
    for m in media:
        name = m.path.name
        meta_text = f"{m.make} {m.model}".lower()

        if primary_patterns and _matches_any(name, primary_patterns):
            m.role, m.role_reason = Role.PRIMARY, "matched --primary pattern"
        elif overlay_patterns and _matches_any(name, overlay_patterns):
            m.role, m.role_reason = Role.OVERLAY, "matched --overlay pattern"
        elif any(h in meta_text for h in OVERLAY_META_HINTS):
            m.role, m.role_reason = Role.OVERLAY, f"camera metadata ({meta_text.strip()})"
        elif any(h in meta_text for h in PRIMARY_META_HINTS):
            m.role, m.role_reason = Role.PRIMARY, f"camera metadata ({meta_text.strip()})"
        elif any(h in name.lower() for h in OVERLAY_NAME_HINTS):
            m.role, m.role_reason = Role.OVERLAY, "filename hint"
        elif any(h in name.lower() for h in PRIMARY_NAME_HINTS):
            m.role, m.role_reason = Role.PRIMARY, "filename hint"
        elif m.is_portrait:
            m.role, m.role_reason = Role.OVERLAY, "portrait orientation"
        elif m.display_width > 0:
            m.role, m.role_reason = Role.PRIMARY, "landscape orientation"
        else:
            m.role, m.role_reason = Role.UNKNOWN, "could not determine"


def sort_primaries(primaries: list[MediaFile]) -> list[MediaFile]:
    """Order primary segments chronologically (creation time, then name)."""
    def key(m: MediaFile):
        ts = m.creation_time.timestamp() if m.creation_time else float("inf")
        return (ts, m.path.name)

    return sorted(primaries, key=key)
