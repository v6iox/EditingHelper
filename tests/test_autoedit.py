"""Auto-editing intelligence: silences, beats, highlights, cuts, montage."""

from __future__ import annotations

from fractions import Fraction

import numpy as np
import pytest

from editsync.autoedit import (
    BeatGrid,
    Selection,
    apply_cuts,
    cut_intervals,
    detect_beats,
    detect_silences,
    plan_montage,
    rms_envelope,
    score_highlights,
)
from editsync.media import Role
from editsync.timeline import Timeline, TimelineClip

SR = 8000
RNG = np.random.default_rng(11)


def speech(seconds: float, level: float = 0.3) -> np.ndarray:
    """Busy, loud pseudo-speech: noise bursts at syllable rate."""
    n = int(seconds * SR)
    x = RNG.normal(0, level, n)
    gate = (np.sin(2 * np.pi * 4.0 * np.arange(n) / SR) > -0.3).astype(float)
    return (x * gate).astype(np.float64)


def near_silence(seconds: float) -> np.ndarray:
    return RNG.normal(0, 0.001, int(seconds * SR))


class TestSilences:
    def test_finds_the_quiet_middle(self):
        audio = np.concatenate([speech(5), near_silence(4), speech(5)])
        found = detect_silences(audio, SR)
        assert len(found) == 1
        s, e = found[0]
        assert s == pytest.approx(5.0, abs=0.6)
        assert e == pytest.approx(9.0, abs=0.6)

    def test_short_pauses_are_kept(self):
        audio = np.concatenate([speech(5), near_silence(0.5), speech(5)])
        assert detect_silences(audio, SR) == []

    def test_all_speech_no_cuts(self):
        assert detect_silences(speech(20), SR) == []


class TestBeats:
    def test_click_track_tempo_and_phase(self):
        # 120 BPM click track: a sharp burst every 0.5 s
        seconds, bpm = 30, 120
        n = seconds * SR
        audio = RNG.normal(0, 0.002, n)
        period = int(SR * 60 / bpm)
        for i in range(0, n - 400, period):
            audio[i : i + 400] += RNG.normal(0, 0.6, 400)
        grid = detect_beats(audio, SR)
        assert grid.confidence > 0.1
        assert grid.bpm == pytest.approx(120, abs=3) or grid.bpm == pytest.approx(
            60, abs=2
        )
        # grid positions should land near real click times
        clicks = np.arange(0, seconds, 60 / bpm)
        for b in grid.beats[2:10]:
            assert np.min(np.abs(clicks - b)) < 0.12

    def test_noise_has_low_confidence(self):
        grid = detect_beats(RNG.normal(0, 0.1, 20 * SR), SR)
        assert grid.confidence < 0.5

    def test_nearest(self):
        grid = BeatGrid(bpm=120, beats=[0.0, 0.5, 1.0], confidence=1.0)
        assert grid.nearest(0.6) == 0.5
        assert grid.nearest(0.9) == 1.0


class TestHighlights:
    def test_loud_busy_section_wins(self):
        audio = np.concatenate(
            [near_silence(10), speech(6, level=0.6), near_silence(10)]
        )
        top = score_highlights(audio, SR, top=1)
        assert len(top) == 1
        # the exciting window overlaps the loud middle (10..16 s)
        assert top[0].start < 16 and top[0].end > 10

    def test_no_overlap_between_picks(self):
        audio = np.concatenate([speech(30, level=0.4)])
        picks = score_highlights(audio, SR, length=5.0, top=4)
        for a in picks:
            for b in picks:
                if a is not b:
                    assert a.end <= b.start or b.end <= a.start

    def test_rms_envelope_length(self):
        env = rms_envelope(np.ones(SR * 2), SR, 100)
        assert len(env) == 200


@pytest.fixture
def synced_timeline(make_media) -> Timeline:
    """20 s primary + one overlay at 12..15 s."""
    tl = Timeline(name="T", frame_rate=Fraction(30), width=1920, height=1080)
    tl.clips.append(
        TimelineClip(
            make_media(name="DJI_0001.mp4", duration=20.0, role=Role.PRIMARY),
            Fraction(0), Fraction(20), Fraction(0), 0, role="DJI",
        )
    )
    tl.clips.append(
        TimelineClip(
            make_media(name="meta_001.mp4", width=1080, height=1920,
                       duration=3.0, role=Role.OVERLAY),
            Fraction(12), Fraction(3), Fraction(0), 1, role="Meta",
        )
    )
    return tl


class TestCuts:
    def test_cut_avoids_overlays(self, synced_timeline):
        # silence spans the overlay: only the parts outside it are cut
        cuts = cut_intervals(synced_timeline, [(10.0, 17.0)])
        assert len(cuts) == 2
        (s1, e1), (s2, e2) = cuts
        assert float(e1) <= 12.0 and float(s2) >= 15.0

    def test_tiny_leftovers_dropped(self, synced_timeline):
        cuts = cut_intervals(synced_timeline, [(11.3, 15.4)])
        assert cuts == []  # both leftover slivers < min_cut

    def test_apply_cuts_ripples_and_preserves_sync(self, synced_timeline):
        cuts = cut_intervals(synced_timeline, [(2.0, 5.0)])
        assert len(cuts) == 1
        tightened = apply_cuts(synced_timeline, cuts)

        prim = tightened.primary_clips
        assert len(prim) == 2
        # first piece 0..2, second piece starts right after, sourced at 5 s
        assert float(prim[0].duration) == pytest.approx(2.0, abs=0.05)
        assert float(prim[1].timeline_start) == pytest.approx(2.0, abs=0.05)
        assert float(prim[1].source_start) == pytest.approx(5.0, abs=0.05)

        ov = tightened.overlay_clips[0]
        # the overlay shifted left exactly as much as the primary under it
        assert float(ov.timeline_start) == pytest.approx(9.0, abs=0.05)
        # its synced primary content: timeline 9.0 -> piece 2 source 5+(9-2)=12
        assert float(prim[1].source_start + (ov.timeline_start - prim[1].timeline_start)) == pytest.approx(12.0, abs=0.05)
        assert float(tightened.duration) == pytest.approx(17.0, abs=0.05)

    def test_music_is_dropped_for_relaying(self, synced_timeline, make_media):
        song = make_media(name="song.mp3", width=0, height=0, duration=8.0)
        synced_timeline.clips.append(
            TimelineClip(song, Fraction(0), Fraction(8), Fraction(0), -1,
                         role="Music", volume_db=-22.0)
        )
        tightened = apply_cuts(synced_timeline, [(Fraction(2), Fraction(5))])
        assert tightened.music_clips == []


class TestMontage:
    def test_butt_joined_in_order(self, make_media):
        a = make_media(name="a.mp4", duration=30.0, role=Role.PRIMARY)
        b = make_media(name="b.mp4", duration=30.0, role=Role.OVERLAY)
        clips = plan_montage(
            [
                Selection(a, Fraction(5), Fraction(9)),
                Selection(b, Fraction(0), Fraction(3)),
            ],
            Fraction(1, 30),
        )
        assert len(clips) == 2
        assert clips[0].timeline_start == 0
        assert clips[1].timeline_start == clips[0].timeline_end
        assert clips[0].role == "DJI" and clips[1].role == "Meta"
        assert clips[1].source_start == 0

    def test_beat_snapping_shortens_to_beat(self, make_media):
        a = make_media(name="a.mp4", duration=60.0, role=Role.PRIMARY)
        grid = BeatGrid(bpm=120, beats=[i * 0.5 for i in range(120)],
                        confidence=0.8)
        clips = plan_montage(
            [
                Selection(a, Fraction(0), Fraction(33, 10)),   # 3.3 s
                Selection(a, Fraction(10), Fraction(137, 10)), # 3.7 s
            ],
            Fraction(1, 30),
            beats=grid,
        )
        # every cut point lands on a half-second beat
        for c in clips:
            end = float(c.timeline_end)
            assert min(abs(end - b) for b in grid.beats) < 1 / 30 + 1e-6
            assert float(c.duration) <= float(Fraction(37, 10)) + 1e-6

    def test_low_confidence_grid_ignored(self, make_media):
        a = make_media(name="a.mp4", duration=60.0, role=Role.PRIMARY)
        grid = BeatGrid(bpm=120, beats=[i * 0.5 for i in range(20)],
                        confidence=0.01)
        clips = plan_montage(
            [Selection(a, Fraction(0), Fraction(33, 10))],
            Fraction(1, 30),
            beats=grid,
        )
        assert float(clips[0].duration) == pytest.approx(3.3, abs=0.04)

    def test_empty_selection_skipped(self, make_media):
        a = make_media(name="a.mp4", duration=10.0)
        clips = plan_montage(
            [Selection(a, Fraction(5), Fraction(5))], Fraction(1, 30)
        )
        assert clips == []
