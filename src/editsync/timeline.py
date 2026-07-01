"""Editor-agnostic timeline model.

Times are `fractions.Fraction` seconds so every exporter can render them
losslessly in its own representation (rational seconds for FCPXML, frame
counts for Premiere, rational times for OTIO).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction

from .media import MediaFile


def quantize(t: Fraction, frame_duration: Fraction) -> Fraction:
    """Snap a time to the nearest frame boundary."""
    frames = round(t / frame_duration)
    return frames * frame_duration


@dataclass
class Marker:
    time: Fraction  # seconds, relative to clip source start
    text: str
    completed: bool | None = None  # None = standard marker, bool = to-do marker


@dataclass
class TimelineClip:
    media: MediaFile
    timeline_start: Fraction  # seconds on the sequence timeline
    duration: Fraction  # seconds
    source_start: Fraction  # trim into the source media, seconds
    lane: int  # 0 = primary storyline, 1+ = connected/overlay layers
    role: str = ""  # e.g. "DJI" / "Meta" -> audio/video roles or track names
    markers: list[Marker] = field(default_factory=list)
    sync_confidence: float | None = None
    transform_scale: tuple[float, float] | None = None
    transform_position: tuple[float, float] | None = None

    @property
    def timeline_end(self) -> Fraction:
        return self.timeline_start + self.duration


@dataclass
class DuckRegion:
    """Timeline interval where the primary camera's audio should be ducked."""

    start: Fraction
    end: Fraction
    level_db: float


@dataclass
class Timeline:
    name: str
    frame_rate: Fraction
    width: int
    height: int
    clips: list[TimelineClip] = field(default_factory=list)
    duck_regions: list[DuckRegion] = field(default_factory=list)

    @property
    def frame_duration(self) -> Fraction:
        return 1 / self.frame_rate

    @property
    def duration(self) -> Fraction:
        if not self.clips:
            return Fraction(0)
        return max(c.timeline_end for c in self.clips)

    @property
    def primary_clips(self) -> list[TimelineClip]:
        return sorted(
            (c for c in self.clips if c.lane == 0),
            key=lambda c: c.timeline_start,
        )

    @property
    def overlay_clips(self) -> list[TimelineClip]:
        return sorted(
            (c for c in self.clips if c.lane > 0),
            key=lambda c: (c.timeline_start, c.lane),
        )

    @property
    def lane_count(self) -> int:
        return max((c.lane for c in self.clips), default=0)


def assign_lanes(clips: list[TimelineClip], lane_per_clip: bool = False) -> None:
    """Assign overlay lanes so overlapping clips never share a lane.

    Default is greedy interval coloring (minimal lanes). With
    `lane_per_clip`, every overlay gets its own lane, which some editors
    prefer for manual trimming.
    """
    overlays = sorted((c for c in clips if c.lane != 0), key=lambda c: c.timeline_start)
    if lane_per_clip:
        for i, clip in enumerate(overlays, start=1):
            clip.lane = i
        return

    lane_free_at: list[Fraction] = []  # lane index -> time the lane frees up
    for clip in overlays:
        for i, free_at in enumerate(lane_free_at):
            if clip.timeline_start >= free_at:
                clip.lane = i + 1
                lane_free_at[i] = clip.timeline_end
                break
        else:
            lane_free_at.append(clip.timeline_end)
            clip.lane = len(lane_free_at)


def merge_intervals(
    intervals: list[tuple[Fraction, Fraction]],
    min_gap: Fraction = Fraction(1, 2),
) -> list[tuple[Fraction, Fraction]]:
    """Merge overlapping/near-adjacent intervals (used for duck regions)."""
    if not intervals:
        return []
    ordered = sorted(intervals)
    merged = [list(ordered[0])]
    for start, end in ordered[1:]:
        if start <= merged[-1][1] + min_gap:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(s, e) for s, e in merged]
