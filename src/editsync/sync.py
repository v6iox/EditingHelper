"""Audio cross-correlation sync engine.

Two-stage alignment:

1. Coarse: FFT cross-correlation of onset envelopes (10 ms resolution).
2. Fine: raw-audio cross-correlation in a small window around the coarse
   result (sub-millisecond resolution at the 8 kHz analysis rate).

Also measures clock drift by aligning the head and tail of a clip
independently, and reports a confidence score so low-quality matches can
be flagged for the editor instead of silently misplaced.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .audio import ENVELOPE_FPS, SAMPLE_RATE, zscore


@dataclass
class SyncResult:
    offset: float  # seconds into the reference where the target starts
    confidence: float  # normalized cross-correlation at the peak, 0..1
    peak_ratio: float  # best peak vs. best competing peak (>1 is good)
    drift_ppm: float = 0.0  # clock drift estimate, parts per million
    drift_checked: bool = False

    @property
    def is_confident(self) -> bool:
        return self.confidence >= 0.35 and self.peak_ratio >= 1.5


def _fft_cross_correlate(ref: np.ndarray, tgt: np.ndarray) -> np.ndarray:
    """Full cross-correlation of ref with tgt (lags -len(tgt)+1 .. len(ref)-1)."""
    n = len(ref) + len(tgt) - 1
    nfft = 1 << (n - 1).bit_length()
    spec = np.fft.rfft(ref, nfft) * np.conj(np.fft.rfft(tgt, nfft))
    corr = np.fft.irfft(spec, nfft)[:n]
    # numpy's circular correlation puts negative lags at the tail; rotate so
    # index 0 corresponds to lag -(len(tgt)-1)
    return np.concatenate((corr[-(len(tgt) - 1):], corr[: len(ref)])) if len(tgt) > 1 else corr[: len(ref)]


def _normalized_peak(ref: np.ndarray, tgt: np.ndarray, lag: int) -> float:
    """True normalized cross-correlation of the overlapping region at `lag`."""
    r_start = max(lag, 0)
    t_start = max(-lag, 0)
    length = min(len(ref) - r_start, len(tgt) - t_start)
    if length <= 1:
        return 0.0
    r = ref[r_start : r_start + length]
    t = tgt[t_start : t_start + length]
    denom = np.linalg.norm(r) * np.linalg.norm(t)
    if denom < 1e-12:
        return 0.0
    return float(np.dot(r, t) / denom)


def correlate_envelopes(
    ref_env: np.ndarray,
    tgt_env: np.ndarray,
    fps: int = ENVELOPE_FPS,
    exclusion_seconds: float = 2.0,
) -> tuple[float, float, float]:
    """Find where tgt_env best aligns inside ref_env.

    Returns (offset_seconds, confidence, peak_ratio). Confidence is the
    normalized correlation of the overlap at the winning lag; peak_ratio
    compares the winning peak against the best peak outside an exclusion
    zone around it (detects ambiguous matches, e.g. repeated music).
    """
    ref = zscore(ref_env)
    tgt = zscore(tgt_env)
    corr = _fft_cross_correlate(ref, tgt)
    lag_offset = len(tgt) - 1

    best_idx = int(np.argmax(corr))
    best_lag = best_idx - lag_offset

    excl = int(exclusion_seconds * fps)
    masked = corr.copy()
    lo = max(0, best_idx - excl)
    hi = min(len(masked), best_idx + excl + 1)
    masked[lo:hi] = -np.inf
    second = float(np.max(masked)) if np.isfinite(masked).any() else 0.0
    peak = float(corr[best_idx])
    peak_ratio = peak / second if second > 1e-9 else float("inf")

    confidence = max(0.0, _normalized_peak(ref, tgt, best_lag))
    return best_lag / fps, confidence, peak_ratio


def refine_offset(
    ref_audio: np.ndarray,
    tgt_audio: np.ndarray,
    coarse_offset: float,
    sample_rate: int = SAMPLE_RATE,
    window: float = 0.5,
    probe_seconds: float = 20.0,
) -> float:
    """Refine a coarse offset using raw audio around the coarse estimate.

    Correlates up to `probe_seconds` of the target against the matching
    reference region, searching ±`window` seconds around `coarse_offset`.
    Returns the refined offset (falls back to coarse on degenerate input).
    """
    probe_len = min(len(tgt_audio), int(probe_seconds * sample_rate))
    # probe from the middle of the target clip where audio is usually stable
    t_start = max(0, (len(tgt_audio) - probe_len) // 2)
    probe = zscore(tgt_audio[t_start : t_start + probe_len])

    pad = int(window * sample_rate)
    r_center = coarse_offset * sample_rate + t_start
    r_start = int(r_center) - pad
    r_end = int(r_center) + probe_len + pad
    r_start_c = max(0, r_start)
    region = ref_audio[r_start_c : min(len(ref_audio), r_end)]
    if len(region) <= probe_len or probe.std() < 1e-12:
        return coarse_offset

    corr = _fft_cross_correlate(zscore(region), probe)
    lag_offset = len(probe) - 1
    best_lag = int(np.argmax(corr)) - lag_offset
    refined = (r_start_c + best_lag - t_start) / sample_rate
    if abs(refined - coarse_offset) > window + 0.1:
        return coarse_offset
    return refined


def measure_drift(
    ref_audio: np.ndarray,
    tgt_audio: np.ndarray,
    offset: float,
    sample_rate: int = SAMPLE_RATE,
    segment_seconds: float = 15.0,
) -> tuple[float, bool]:
    """Estimate clock drift (ppm) by aligning head and tail independently."""
    tgt_dur = len(tgt_audio) / sample_rate
    if tgt_dur < 3 * segment_seconds:
        return 0.0, False

    seg = int(segment_seconds * sample_rate)
    head = zscore(tgt_audio[:seg])
    tail_start = len(tgt_audio) - seg
    tail = zscore(tgt_audio[tail_start:])

    def _align(segment: np.ndarray, seg_offset_samples: int) -> float | None:
        pad = sample_rate // 2
        r_start = int(offset * sample_rate) + seg_offset_samples - pad
        r_start_c = max(0, r_start)
        region = ref_audio[r_start_c : r_start_c + len(segment) + 2 * pad]
        if len(region) <= len(segment) or segment.std() < 1e-12:
            return None
        corr = _fft_cross_correlate(zscore(region), segment)
        best_lag = int(np.argmax(corr)) - (len(segment) - 1)
        return (r_start_c + best_lag - seg_offset_samples) / sample_rate

    head_offset = _align(head, 0)
    tail_offset = _align(tail, tail_start)
    if head_offset is None or tail_offset is None:
        return 0.0, False

    span = tail_start / sample_rate
    drift_ppm = (tail_offset - head_offset) / span * 1e6
    return drift_ppm, True


def find_clip_in_reference(
    ref_audio: np.ndarray,
    tgt_audio: np.ndarray,
    ref_env: np.ndarray,
    tgt_env: np.ndarray,
    sample_rate: int = SAMPLE_RATE,
    check_drift: bool = True,
) -> SyncResult:
    """Full two-stage sync of a target clip against one reference recording."""
    coarse, confidence, peak_ratio = correlate_envelopes(ref_env, tgt_env)
    refined = refine_offset(ref_audio, tgt_audio, coarse, sample_rate)
    drift_ppm, drift_checked = (0.0, False)
    if check_drift:
        drift_ppm, drift_checked = measure_drift(ref_audio, tgt_audio, refined, sample_rate)
    return SyncResult(
        offset=refined,
        confidence=confidence,
        peak_ratio=peak_ratio,
        drift_ppm=drift_ppm,
        drift_checked=drift_checked,
    )
