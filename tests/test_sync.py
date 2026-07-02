"""Sync engine tests using synthetic audio (no ffmpeg required)."""

from __future__ import annotations

import numpy as np
import pytest

from editsync.audio import ENVELOPE_FPS, SAMPLE_RATE, onset_envelope
from editsync.sync import (
    correlate_envelopes,
    find_clip_in_reference,
    measure_drift,
    refine_offset,
)

RNG = np.random.default_rng(42)


def scene_audio(seconds: float, sr: int = SAMPLE_RATE) -> np.ndarray:
    """Speech-like synthetic audio: noise bursts with irregular timing."""
    n = int(seconds * sr)
    x = np.zeros(n)
    t = 0
    while t < n:
        burst = int(RNG.uniform(0.05, 0.4) * sr)
        gap = int(RNG.uniform(0.05, 0.5) * sr)
        end = min(t + burst, n)
        x[t:end] = RNG.normal(0, RNG.uniform(0.2, 1.0), end - t)
        t = end + gap
    return x


def as_second_mic(x: np.ndarray, snr_scale: float = 0.3, gain: float = 0.5) -> np.ndarray:
    """Simulate the same scene captured by a different microphone."""
    return gain * x + RNG.normal(0, snr_scale * x.std(), len(x))


class TestCorrelateEnvelopes:
    def test_finds_known_offset(self):
        ref = scene_audio(120)
        start = int(43.7 * SAMPLE_RATE)
        tgt = as_second_mic(ref[start : start + 15 * SAMPLE_RATE])

        offset, confidence, peak_ratio = correlate_envelopes(
            onset_envelope(ref), onset_envelope(tgt)
        )
        assert offset == pytest.approx(43.7, abs=0.05)
        assert confidence > 0.5
        assert peak_ratio > 1.5

    def test_unrelated_audio_scores_low(self):
        ref = scene_audio(60)
        unrelated = scene_audio(10)
        _, confidence, _ = correlate_envelopes(
            onset_envelope(ref), onset_envelope(unrelated)
        )
        assert confidence < 0.35

    def test_silence_does_not_crash(self):
        ref = np.zeros(60 * SAMPLE_RATE)
        tgt = np.zeros(5 * SAMPLE_RATE)
        offset, confidence, _ = correlate_envelopes(
            onset_envelope(ref), onset_envelope(tgt)
        )
        assert confidence == 0.0


class TestRefineOffset:
    def test_sub_frame_accuracy(self):
        ref = scene_audio(90)
        true_offset = 21.0 + 137 / SAMPLE_RATE  # deliberately off-frame
        start = int(true_offset * SAMPLE_RATE)
        tgt = as_second_mic(ref[start : start + 12 * SAMPLE_RATE])

        coarse = 21.05  # envelope-level estimate, a few hops off
        refined = refine_offset(ref, tgt, coarse)
        assert refined == pytest.approx(true_offset, abs=0.002)

    def test_degenerate_input_returns_coarse(self):
        ref = np.zeros(SAMPLE_RATE)
        tgt = np.zeros(SAMPLE_RATE // 2)
        assert refine_offset(ref, tgt, 0.1) == 0.1


class TestDrift:
    def test_no_drift_measures_near_zero(self):
        ref = scene_audio(200)
        start = 30 * SAMPLE_RATE
        tgt = as_second_mic(ref[start : start + 90 * SAMPLE_RATE])
        ppm, checked = measure_drift(ref, tgt, 30.0)
        assert checked
        assert abs(ppm) < 60

    def test_short_clip_skips_drift_check(self):
        ref = scene_audio(60)
        tgt = ref[: 5 * SAMPLE_RATE].copy()
        _, checked = measure_drift(ref, tgt, 0.0)
        assert not checked


class TestFullPipeline:
    def test_end_to_end_sync(self):
        ref = scene_audio(180)
        start = int(97.3 * SAMPLE_RATE)
        tgt = as_second_mic(ref[start : start + 20 * SAMPLE_RATE])

        result = find_clip_in_reference(
            ref, tgt, onset_envelope(ref), onset_envelope(tgt)
        )
        assert result.offset == pytest.approx(97.3, abs=0.01)
        assert result.is_confident

    def test_wrong_reference_not_confident(self):
        ref = scene_audio(120)
        other = scene_audio(15)
        result = find_clip_in_reference(
            ref, other, onset_envelope(ref), onset_envelope(other)
        )
        assert not result.is_confident
