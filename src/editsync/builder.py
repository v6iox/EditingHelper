"""Build a Timeline from classified media + sync results.

This is the orchestration layer: it extracts audio, matches every overlay
clip against every primary segment, picks the best match, places clips on
the timeline (frame-quantized), assigns lanes, and computes duck regions.
"""

from __future__ import annotations

import concurrent.futures
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Callable, Optional

import numpy as np

from . import audio as audio_mod
from .media import MediaFile, sort_primaries
from .sync import SyncResult, find_clip_in_reference
from .timeline import (
    BlurRegion,
    DuckRegion,
    Marker,
    Timeline,
    TimelineClip,
    assign_lanes,
    merge_intervals,
    quantize,
)


@dataclass
class BuildOptions:
    project_name: str = "EditSync Project"
    min_confidence: float = 0.35  # below this an overlay is left unplaced
    min_peak_ratio: float = 1.5  # winning peak must beat runner-up by this
    caution_confidence: float = 0.55  # below this a caution marker is added
    duck_db: Optional[float] = -60.0  # None disables primary-audio ducking
    duck_fade: float = 0.25  # seconds of fade into/out of a duck region
    lane_per_clip: bool = False
    preserve_gaps: bool = False  # keep real-world gaps between primary files
    overlay_style: str = "center"  # center | blur-bg | fill | pip-left | pip-right
    blur_amount: float = 50.0  # background blur strength for blur-bg, 0-100
    music_db: float = -22.0  # background-music level under the dialogue
    music_duck: bool = False  # mute the music while an overlay plays
    force_place: bool = False  # place low-confidence clips anyway (flagged)
    add_sync_markers: bool = True
    search_window: Optional[float] = None  # limit search via creation times, s
    max_workers: int = 4


@dataclass
class OverlayMatch:
    media: MediaFile
    primary: Optional[MediaFile]
    result: Optional[SyncResult]
    placed: bool
    reason: str = ""
    timeline_start: Optional[Fraction] = None


@dataclass
class BuildResult:
    timeline: Timeline
    matches: list[OverlayMatch]
    warnings: list[str] = field(default_factory=list)


def _sequence_format(primaries: list[MediaFile]) -> tuple[Fraction, int, int]:
    """Sequence format follows the most common primary-camera format."""
    counts: dict[tuple[Fraction, int, int], int] = {}
    for m in primaries:
        key = (m.frame_rate, m.display_width, m.display_height)
        counts[key] = counts.get(key, 0) + 1
    (rate, w, h), _ = max(counts.items(), key=lambda kv: kv[1])
    return rate, w, h


def _overlay_transform(
    style: str, seq_w: int, seq_h: int, media: MediaFile
) -> tuple[Optional[tuple[float, float]], Optional[tuple[float, float]]]:
    """Scale/position for a vertical clip in a horizontal sequence.

    Editors conform (fit) by default, so a vertical clip is pillarboxed at
    full height. Values approximate inspector units; they are starting
    points the editor can tweak, not pixel-perfect layouts.

    "blur-bg" keeps the vertical clip centered and sharp; the background
    treatment (blurring the primary underneath) is handled separately via
    Timeline.blur_regions.
    """
    if style in ("center", "blur-bg") or media.display_height <= media.display_width:
        return None, None
    fitted_w = media.display_width * (seq_h / media.display_height)
    if style == "fill":
        scale = seq_w / fitted_w if fitted_w else 1.0
        return (round(scale, 4), round(scale, 4)), None
    if style in ("pip-left", "pip-right"):
        scale = 0.6
        x = seq_w * 0.28
        return (scale, scale), (-x if style == "pip-left" else x, 0.0)
    return None, None


def _predicted_offset(primary: MediaFile, overlay: MediaFile) -> Optional[float]:
    if primary.creation_time and overlay.creation_time:
        return (overlay.creation_time - primary.creation_time).total_seconds()
    return None


def _match_overlay(
    overlay: MediaFile,
    primaries: list[MediaFile],
    primary_tl_start: dict[int, Fraction],
    ref_audio: np.ndarray,
    ref_env: np.ndarray,
    tgt_audio: np.ndarray,
    tgt_env: np.ndarray,
    opts: BuildOptions,
) -> SyncResult:
    """Match one overlay against the timeline-domain reference.

    With --search-window and usable creation timestamps, only a slice of
    the reference around the predicted position is searched first (faster,
    and disambiguates repeated audio like music); a weak windowed match
    falls back to the full search.
    """
    sr = audio_mod.SAMPLE_RATE
    fps = audio_mod.ENVELOPE_FPS

    if opts.search_window is not None:
        predictions = [
            float(primary_tl_start[i]) + pred
            for i, p in enumerate(primaries)
            if (pred := _predicted_offset(p, overlay)) is not None
        ]
        if predictions:
            margin = opts.search_window + float(overlay.duration)
            lo = max(0, int(min(predictions) - margin))
            hi = min(
                len(ref_audio) // sr + 1, int(max(predictions) + margin) + 1
            )
            if hi * sr - lo * sr > len(tgt_audio):
                result = find_clip_in_reference(
                    ref_audio[lo * sr : hi * sr],
                    tgt_audio,
                    ref_env[lo * fps : hi * fps],
                    tgt_env,
                )
                result.offset += lo
                if (
                    result.confidence >= opts.min_confidence
                    and result.peak_ratio >= opts.min_peak_ratio
                ):
                    return result

    return find_clip_in_reference(ref_audio, tgt_audio, ref_env, tgt_env)


def add_music(timeline: Timeline, music: MediaFile, opts: BuildOptions) -> None:
    """Loop a music file under the whole timeline on lane -1.

    Each pass of the file becomes its own clip (the last one trimmed), so
    the editor can later swap, trim, or delete individual passes."""
    fd = timeline.frame_duration
    total = timeline.duration
    if music.duration <= 0 or total <= 0:
        return
    pos = Fraction(0)
    while pos < total:
        chunk = min(music.duration, total - pos)
        start_q = quantize(pos, fd)
        dur_q = min(quantize(chunk, fd), total - start_q)
        if dur_q <= 0:
            break
        timeline.clips.append(
            TimelineClip(
                media=music,
                timeline_start=start_q,
                duration=dur_q,
                source_start=Fraction(0),
                lane=-1,
                role="Music",
                volume_db=opts.music_db,
            )
        )
        pos += music.duration


def build(
    primaries: list[MediaFile],
    overlays: list[MediaFile],
    opts: BuildOptions,
    progress: Callable[[str], None] = lambda msg: None,
    music: Optional[MediaFile] = None,
) -> BuildResult:
    if not primaries:
        raise ValueError("No primary (landscape/DJI) footage found to sync against.")

    primaries = sort_primaries(primaries)
    warnings: list[str] = []

    rate, seq_w, seq_h = _sequence_format(primaries)
    timeline = Timeline(name=opts.project_name, frame_rate=rate, width=seq_w, height=seq_h)
    fd = timeline.frame_duration

    # --- lay out the primary storyline ---------------------------------
    primary_tl_start: dict[int, Fraction] = {}
    cursor = Fraction(0)
    prev: Optional[MediaFile] = None
    for i, m in enumerate(primaries):
        if opts.preserve_gaps and prev and prev.creation_time and m.creation_time:
            gap = Fraction(
                (m.creation_time - prev.creation_time).total_seconds()
            ).limit_denominator(1000) - prev.duration
            if gap > 0:
                cursor += gap
        start = quantize(cursor, fd)
        primary_tl_start[i] = start
        timeline.clips.append(
            TimelineClip(
                media=m,
                timeline_start=start,
                duration=quantize(m.duration, fd),
                source_start=Fraction(0),
                lane=0,
                role="DJI",
            )
        )
        cursor = start + quantize(m.duration, fd)
        prev = m

    # --- extract audio (parallel; ffmpeg subprocesses release the GIL) --
    progress(f"Extracting audio from {len(primaries) + len(overlays)} files...")
    all_media = list(primaries) + list(overlays)
    audio_cache: dict[str, np.ndarray] = {}
    env_cache: dict[str, np.ndarray] = {}

    def _load(m: MediaFile) -> tuple[str, np.ndarray]:
        return str(m.path), audio_mod.extract_audio(m.path)

    with concurrent.futures.ThreadPoolExecutor(max_workers=opts.max_workers) as pool:
        for key, samples in pool.map(_load, all_media):
            audio_cache[key] = samples
    for key, samples in audio_cache.items():
        env_cache[key] = audio_mod.onset_envelope(samples)

    # --- assemble a timeline-domain reference track ----------------------
    # Overlays are matched against the primary audio laid out exactly as it
    # sits on the timeline, not per-file. This makes clips that span a
    # DJI file-split boundary (the camera chunks long recordings) sync
    # correctly, since their audio only exists across the joined segments.
    sr = audio_mod.SAMPLE_RATE
    ref_len = int(float(cursor) * sr) + 1
    ref_audio = np.zeros(ref_len)
    for i, m in enumerate(primaries):
        seg = audio_cache[str(m.path)]
        start_idx = int(float(primary_tl_start[i]) * sr)
        end_idx = min(start_idx + len(seg), ref_len)
        ref_audio[start_idx:end_idx] = seg[: end_idx - start_idx]
    ref_env = audio_mod.onset_envelope(ref_audio)

    def _segment_at(tl_seconds: float) -> int:
        idx = 0
        for i in range(len(primaries)):
            if float(primary_tl_start[i]) <= tl_seconds:
                idx = i
        return idx

    # --- match every overlay against the reference track ----------------
    matches: list[OverlayMatch] = []
    for overlay in overlays:
        progress(f"Syncing {overlay.path.name}...")
        tgt_audio = audio_cache[str(overlay.path)]
        tgt_env = env_cache[str(overlay.path)]

        best = _match_overlay(
            overlay, primaries, primary_tl_start, ref_audio, ref_env,
            tgt_audio, tgt_env, opts,
        )
        best_idx = _segment_at(best.offset)

        # both signals must clear their bar: confidence says the audio truly
        # matches, peak_ratio says the match position is unambiguous
        confident = (
            best.confidence >= opts.min_confidence
            and best.peak_ratio >= opts.min_peak_ratio
        )
        if not confident and not opts.force_place:
            matches.append(
                OverlayMatch(
                    overlay,
                    primaries[best_idx],
                    best,
                    False,
                    f"confidence {best.confidence:.2f} / peak ratio "
                    f"{best.peak_ratio:.2f} below thresholds "
                    f"({opts.min_confidence:.2f} / {opts.min_peak_ratio:.2f}); "
                    f"use --force-place to place anyway",
                )
            )
            warnings.append(
                f"{overlay.path.name}: ambiguous or weak audio match "
                f"(confidence {best.confidence:.2f}, peak ratio "
                f"{best.peak_ratio:.2f}); left off the timeline."
            )
            continue

        primary = primaries[best_idx]
        tl_start = Fraction(best.offset).limit_denominator(48000)
        source_start = Fraction(0)
        duration = overlay.duration

        # overlay starts before the first primary recording -> trim its head
        if tl_start < 0:
            trim = -tl_start
            source_start = trim
            duration = duration - trim
            tl_start = Fraction(0)
            warnings.append(
                f"{overlay.path.name}: starts {float(trim):.1f}s before the "
                f"first primary clip; trimmed its head to fit."
            )
        if duration <= 0:
            matches.append(
                OverlayMatch(overlay, primary, best, False, "no usable overlap")
            )
            continue

        tl_start_q = quantize(tl_start, fd)
        clip = TimelineClip(
            media=overlay,
            timeline_start=tl_start_q,
            duration=quantize(duration, fd),
            source_start=source_start,
            lane=1,  # provisional; assign_lanes finalizes
            role="Meta",
            sync_confidence=best.confidence,
        )
        clip.transform_scale, clip.transform_position = _overlay_transform(
            opts.overlay_style, seq_w, seq_h, overlay
        )
        if opts.add_sync_markers:
            pct = min(best.confidence / 0.9, 1.0) * 100
            offset_in_primary = best.offset - float(primary_tl_start[best_idx])
            clip.markers.append(
                Marker(
                    time=source_start,
                    text=f"EditSync: matched {primary.path.name} "
                    f"@ {offset_in_primary:.2f}s ({pct:.0f}% confidence)",
                )
            )
            if best.confidence < opts.caution_confidence or not confident:
                clip.markers.append(
                    Marker(
                        time=source_start + fd,
                        text="EditSync: LOW CONFIDENCE - verify this sync point",
                        completed=False,
                    )
                )
        if best.drift_checked and abs(best.drift_ppm) > 100:
            drift_ms = best.drift_ppm * float(duration) / 1000
            warnings.append(
                f"{overlay.path.name}: clock drift ~{best.drift_ppm:.0f} ppm "
                f"(~{drift_ms:.0f} ms over the clip); audio may slip near the tail."
            )

        if clip.timeline_end > cursor + Fraction(1):
            warnings.append(
                f"{overlay.path.name}: extends past the end of the last "
                f"primary clip."
            )

        timeline.clips.append(clip)
        matches.append(
            OverlayMatch(overlay, primary, best, True, "", tl_start_q)
        )

    assign_lanes(timeline.clips, lane_per_clip=opts.lane_per_clip)

    # --- primary treatment under overlays: audio duck, optional blur ----
    overlay_intervals = merge_intervals(
        [(c.timeline_start, c.timeline_end) for c in timeline.overlay_clips]
    )
    if opts.duck_db is not None:
        timeline.duck_regions = [
            DuckRegion(start=s, end=e, level_db=opts.duck_db)
            for s, e in overlay_intervals
        ]
    if opts.overlay_style == "blur-bg" and opts.blur_amount > 0:
        timeline.blur_regions = [
            BlurRegion(start=s, end=e, amount=opts.blur_amount)
            for s, e in overlay_intervals
        ]

    # --- looping background music ---------------------------------------
    if music is not None:
        progress(f"Laying {music.path.name} under the timeline...")
        add_music(timeline, music, opts)
        if opts.music_duck:
            timeline.music_duck_regions = [
                DuckRegion(start=s, end=e, level_db=-96.0)
                for s, e in overlay_intervals
            ]

    return BuildResult(timeline=timeline, matches=matches, warnings=warnings)
