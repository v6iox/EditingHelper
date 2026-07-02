"""Automatic-editing intelligence for recreational mode.

Everything here is pure analysis + timeline surgery — numpy over audio
that `audio.extract_audio` already decodes, and `Timeline` transforms.
No editor- or GUI-specific code, so the same automations back the
desktop app, the CLI, and (ported) the iPhone app:

- `detect_silences`  — find dead air worth cutting out
- `detect_beats`     — tempo + beat grid of a song (for beat-snapped cuts)
- `score_highlights` — rank the most exciting moments of a recording
- `cut_intervals` / `apply_cuts` — ripple-delete timeline ranges while
  keeping every glasses clip in sync with the main camera
- `plan_montage`     — assemble chosen portions of clips into a sequence,
  optionally snapping every cut to the music's beats
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from fractions import Fraction

import numpy as np

from .audio import ENVELOPE_FPS, SAMPLE_RATE, onset_envelope, zscore
from .media import MediaFile, Role
from .timeline import Timeline, TimelineClip, quantize

Interval = tuple[Fraction, Fraction]


# --------------------------------------------------------------------------
# envelopes


def rms_envelope(
    samples: np.ndarray,
    sample_rate: int = SAMPLE_RATE,
    fps: int = ENVELOPE_FPS,
) -> np.ndarray:
    """Per-frame RMS loudness (linear), `fps` frames per second."""
    hop = sample_rate // fps
    n = len(samples) // hop
    if n < 1:
        return np.zeros(1)
    frames = samples[: n * hop].reshape(n, hop)
    return np.sqrt(np.mean(frames * frames, axis=1))


def _smooth(x: np.ndarray, width: int) -> np.ndarray:
    if width <= 1 or len(x) < 2:
        return x
    kernel = np.ones(width) / width
    return np.convolve(x, kernel, mode="same")


# --------------------------------------------------------------------------
# dead air


def detect_silences(
    samples: np.ndarray,
    sample_rate: int = SAMPLE_RATE,
    *,
    fps: int = ENVELOPE_FPS,
    drop_db: float = 25.0,
    floor_db: float = -50.0,
    min_duration: float = 1.2,
    pad: float = 0.25,
) -> list[tuple[float, float]]:
    return detect_silences_env(
        rms_envelope(samples, sample_rate, fps),
        fps=fps,
        drop_db=drop_db,
        floor_db=floor_db,
        min_duration=min_duration,
        pad=pad,
    )


def detect_silences_env(
    rms: np.ndarray,
    *,
    fps: int = ENVELOPE_FPS,
    drop_db: float = 25.0,
    floor_db: float = -50.0,
    min_duration: float = 1.2,
    pad: float = 0.25,
) -> list[tuple[float, float]]:
    """Stretches of dead air: quieter than the speech level by `drop_db`
    (never above `floor_db` absolute), at least `min_duration` long after
    keeping `pad` seconds of natural pause at each edge.

    Takes the raw RMS envelope so the editor can re-run it after every
    cut without re-decoding audio."""
    rms = _smooth(rms, fps // 4)
    db = 20.0 * np.log10(np.maximum(rms, 1e-6))
    speech = float(np.percentile(db, 90))
    threshold = min(speech - drop_db, floor_db)
    quiet = db < threshold

    out: list[tuple[float, float]] = []
    run_start: int | None = None
    for i, q in enumerate(np.append(quiet, False)):
        if q and run_start is None:
            run_start = i
        elif not q and run_start is not None:
            start, end = run_start / fps + pad, i / fps - pad
            if end - start >= min_duration:
                out.append((start, end))
            run_start = None
    return out


# --------------------------------------------------------------------------
# beats


@dataclass
class BeatGrid:
    bpm: float
    beats: list[float]  # seconds
    confidence: float  # 0-1; below ~0.1 treat as "no usable pulse"

    def nearest(self, t: float) -> float:
        if not self.beats:
            return t
        i = int(np.argmin(np.abs(np.asarray(self.beats) - t)))
        return self.beats[i]


def detect_beats(
    samples: np.ndarray,
    sample_rate: int = SAMPLE_RATE,
    *,
    fps: int = ENVELOPE_FPS,
    min_bpm: float = 60.0,
    max_bpm: float = 200.0,
) -> BeatGrid:
    """Estimate tempo and a beat grid from the onset envelope.

    Autocorrelation of the onset envelope picks the beat period; the
    phase is whatever offset lines the grid up with the most onset
    energy. Good enough to snap cuts to; not a full beat tracker."""
    env = zscore(onset_envelope(samples, sample_rate, fps))
    n = len(env)
    duration = len(samples) / sample_rate
    lo = max(2, int(round(fps * 60.0 / max_bpm)))
    hi = min(n - 1, int(round(fps * 60.0 / min_bpm)))
    if hi <= lo or n < 4 * lo:
        return BeatGrid(bpm=0.0, beats=[], confidence=0.0)

    padded = np.zeros(2 * n)
    padded[:n] = env
    spectrum = np.fft.rfft(padded)
    ac = np.fft.irfft(spectrum * np.conj(spectrum))[:n]
    if ac[0] <= 0:
        return BeatGrid(bpm=0.0, beats=[], confidence=0.0)
    ac = ac / ac[0]

    lag = lo + int(np.argmax(ac[lo : hi + 1]))
    # parabolic refinement around the integer-lag peak
    if 1 < lag < n - 1:
        a, b, c = ac[lag - 1], ac[lag], ac[lag + 1]
        denom = a - 2 * b + c
        if abs(denom) > 1e-12:
            lag = lag + float(np.clip(0.5 * (a - c) / denom, -0.5, 0.5))
    period = lag / fps
    confidence = float(max(0.0, min(1.0, ac[int(round(lag))])))

    # phase: try every frame offset within one period, keep the one whose
    # grid positions collect the most onset energy
    best_offset, best_score = 0.0, -np.inf
    for off_frame in range(int(period * fps)):
        idx = np.arange(off_frame, n, period * fps).astype(int)
        score = float(env[idx].sum()) / max(len(idx), 1)
        if score > best_score:
            best_offset, best_score = off_frame / fps, score

    beats = list(np.arange(best_offset, duration, period))
    return BeatGrid(bpm=60.0 / period, beats=beats, confidence=confidence)


# --------------------------------------------------------------------------
# highlights


@dataclass
class Highlight:
    start: float
    end: float
    score: float


def score_highlights(
    samples: np.ndarray,
    sample_rate: int = SAMPLE_RATE,
    *,
    fps: int = ENVELOPE_FPS,
    length: float = 6.0,
    top: int = 5,
) -> list[Highlight]:
    return score_highlights_env(
        rms_envelope(samples, sample_rate, fps),
        onset_envelope(samples, sample_rate, fps),
        fps=fps,
        length=length,
        top=top,
    )


def score_highlights_env(
    rms: np.ndarray,
    onset: np.ndarray,
    *,
    fps: int = ENVELOPE_FPS,
    length: float = 6.0,
    top: int = 5,
) -> list[Highlight]:
    """The `top` most exciting non-overlapping `length`-second windows.

    Excitement = loudness (people react loudly) + onset density (lots
    happening). Both are z-scored so neither camera's gain wins. Takes
    the raw envelopes so the editor can re-rank after edits cheaply."""
    loud = zscore(_smooth(rms, fps // 2))
    busy = zscore(_smooth(onset, fps // 2))
    n = min(len(loud), len(busy))
    win = int(length * fps)
    if n <= win:
        return [Highlight(0.0, n / fps, 0.0)] if n else []
    excite = loud[:n] + busy[:n]
    sums = np.cumsum(np.insert(excite, 0, 0.0))
    window_scores = (sums[win:] - sums[:-win]) / win  # score of window at i

    order = np.argsort(window_scores)[::-1]
    chosen: list[Highlight] = []
    for i in order:
        start, end = i / fps, (i + win) / fps
        if any(start < h.end and end > h.start for h in chosen):
            continue
        chosen.append(Highlight(start, end, float(window_scores[i])))
        if len(chosen) >= top:
            break
    return sorted(chosen, key=lambda h: h.start)


# --------------------------------------------------------------------------
# ripple cuts (tighten dead air)


def cut_intervals(
    timeline: Timeline,
    silences: list[tuple[float, float]],
    *,
    protect_pad: float = 0.35,
    min_cut: float = 0.6,
) -> list[Interval]:
    """Turn detected silences into safe timeline cuts: never inside or
    within `protect_pad` of a glasses clip (cutting there would tear the
    overlay's sync), never past the end, and at least `min_cut` long."""
    total = timeline.duration
    pad = Fraction(protect_pad).limit_denominator(1000)
    protected = [
        (max(Fraction(0), c.timeline_start - pad), c.timeline_end + pad)
        for c in timeline.overlay_clips
    ]

    cuts: list[Interval] = []
    for s_f, e_f in silences:
        pieces = [
            (
                max(Fraction(0), Fraction(s_f).limit_denominator(1000)),
                min(total, Fraction(e_f).limit_denominator(1000)),
            )
        ]
        for ps, pe in protected:
            nxt: list[Interval] = []
            for s, e in pieces:
                if e <= ps or s >= pe:  # disjoint
                    nxt.append((s, e))
                else:  # keep what sticks out either side
                    if s < ps:
                        nxt.append((s, ps))
                    if e > pe:
                        nxt.append((pe, e))
            pieces = nxt
        cuts.extend((s, e) for s, e in pieces if e - s >= Fraction(min_cut).limit_denominator(1000))
    return sorted(cuts)


def _removed_before(t: Fraction, cuts: list[Interval]) -> Fraction:
    gone = Fraction(0)
    for s, e in cuts:
        if e <= t:
            gone += e - s
        elif s < t:
            gone += t - s
    return gone


def apply_cuts(timeline: Timeline, cuts: list[Interval]) -> Timeline:
    """Ripple-delete `cuts` from the timeline: primary clips are split
    around them, everything after shifts left, overlays stay in sync
    (their matching primary content moves with them). Music clips are
    dropped — the caller re-lays music over the new, shorter timeline."""
    fd = timeline.frame_duration
    cuts = sorted(cuts)
    out = Timeline(
        name=timeline.name,
        frame_rate=timeline.frame_rate,
        width=timeline.width,
        height=timeline.height,
        title_card=timeline.title_card,
    )
    for clip in timeline.clips:
        if clip.lane < 0:
            continue
        if clip.lane == 0:
            # intersect the clip with the keep-side of every cut
            pieces: list[Interval] = [(clip.timeline_start, clip.timeline_end)]
            for cs, ce in cuts:
                nxt: list[Interval] = []
                for s, e in pieces:
                    if e <= cs or s >= ce:
                        nxt.append((s, e))
                    else:
                        if s < cs:
                            nxt.append((s, cs))
                        if e > ce:
                            nxt.append((ce, e))
                pieces = nxt
            for s, e in pieces:
                if e - s < fd:
                    continue
                new_start = quantize(s - _removed_before(s, cuts), fd)
                out.clips.append(
                    replace(
                        clip,
                        timeline_start=new_start,
                        duration=quantize(e - s, fd),
                        source_start=clip.source_start + (s - clip.timeline_start),
                        markers=list(clip.markers),
                    )
                )
        else:
            shift = _removed_before(clip.timeline_start, cuts)
            out.clips.append(
                replace(
                    clip,
                    timeline_start=quantize(clip.timeline_start - shift, fd),
                    markers=list(clip.markers),
                )
            )
    return out


# --------------------------------------------------------------------------
# montage


@dataclass
class Selection:
    """A chosen portion of a source clip ('use this bit')."""

    media: MediaFile
    start: Fraction  # into the source, seconds
    end: Fraction

    @property
    def duration(self) -> Fraction:
        return self.end - self.start


def plan_montage(
    selections: list[Selection],
    frame_duration: Fraction,
    *,
    beats: BeatGrid | None = None,
    min_len: float = 0.8,
    start: Fraction = Fraction(0),
) -> list[TimelineClip]:
    """Butt-join the selections into a storyline (lane 0), in order.

    With a confident `beats` grid, each cut point is nudged to the
    nearest beat — segments only ever get *shorter* (never past their
    selected material) and never shorter than `min_len` seconds."""
    use_beats = beats is not None and beats.confidence >= 0.1 and beats.beats
    clips: list[TimelineClip] = []
    cursor = start
    for sel in selections:
        dur = sel.duration
        if dur <= 0:
            continue
        if use_beats and float(dur) >= min_len * 2:
            # the latest beat inside the selected material (cuts only ever
            # shorten a segment, never stretch past what was chosen)
            fits = [
                b for b in beats.beats
                if min_len <= b - float(cursor) <= float(dur)
            ]
            if fits:
                dur = Fraction(max(fits)).limit_denominator(48000) - cursor
        dur = quantize(dur, frame_duration)
        if dur <= 0:
            continue
        clips.append(
            TimelineClip(
                media=sel.media,
                timeline_start=quantize(cursor, frame_duration),
                duration=dur,
                source_start=sel.start,
                lane=0,
                role="Meta" if sel.media.role == Role.OVERLAY else "DJI",
            )
        )
        cursor = clips[-1].timeline_end
    return clips
