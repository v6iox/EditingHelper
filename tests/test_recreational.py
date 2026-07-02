"""Recreational mode: the mode switcher, the timeline editor, and the
one-click automations (offscreen Qt, no display needed)."""

from __future__ import annotations

import os
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint  # noqa: E402
from PySide6.QtGui import QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from editsync.builder import BuildResult  # noqa: E402
from editsync.gui.editor import RangeDialog, TimelineView  # noqa: E402
from editsync.gui.recreational import RecreationalPage  # noqa: E402
from editsync.gui.window import MainWindow  # noqa: E402
from editsync.gui.worker import StudioAnalysis  # noqa: E402
from editsync.media import Role  # noqa: E402
from editsync.timeline import Timeline, TimelineClip  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def clean_settings():
    MainWindow._settings().clear()
    yield
    MainWindow._settings().clear()


@pytest.fixture(autouse=True)
def no_network_update_check(monkeypatch):
    from editsync import updater

    monkeypatch.setattr(updater, "check_for_update", lambda: None)


@pytest.fixture
def timeline(make_media) -> Timeline:
    """30 s primary + one overlay at 10..15 s."""
    tl = Timeline(name="T", frame_rate=Fraction(30), width=1920, height=1080)
    tl.clips.append(
        TimelineClip(
            make_media(name="DJI_0001.mp4", duration=30.0, role=Role.PRIMARY),
            Fraction(0), Fraction(30), Fraction(0), 0, role="DJI",
        )
    )
    tl.clips.append(
        TimelineClip(
            make_media(name="meta_001.mp4", width=1080, height=1920,
                       duration=5.0, role=Role.OVERLAY),
            Fraction(10), Fraction(5), Fraction(0), 1, role="Meta",
        )
    )
    return tl


def view_with(timeline: Timeline) -> TimelineView:
    view = TimelineView()
    view.resize(900, 300)
    view.set_timeline(timeline)
    view.fit()
    return view


class TestModeSwitch:
    def test_default_is_training(self, qapp):
        win = MainWindow()
        assert win.mode == "training"
        assert win.stack.currentWidget() is win.setup_page

    def test_switching_shows_the_studio(self, qapp):
        win = MainWindow()
        win.set_mode("recreational")
        assert win.stack.currentWidget() is win.rec_page
        win.set_mode("training")
        assert win.stack.currentWidget() is win.setup_page

    def test_mode_persists_across_launches(self, qapp):
        win = MainWindow()
        win.set_mode("recreational")
        win._save_settings()
        again = MainWindow()
        assert again.mode == "recreational"
        assert again.stack.currentWidget() is again.rec_page

    def test_unknown_mode_falls_back(self, qapp):
        win = MainWindow()
        win.set_mode("nonsense")
        assert win.mode == "training"


class TestTimelineView:
    def test_primary_out_trim_ripples(self, qapp, timeline):
        view = view_with(timeline)
        view.trim(timeline.primary_clips[0], "out", 25.0)
        assert abs(float(view.timeline.duration) - 25.0) < 0.05

    def test_primary_in_trim_keeps_overlay_content_sync(self, qapp, timeline):
        view = view_with(timeline)
        view.trim(timeline.primary_clips[0], "in", 4.0)
        prim = view.timeline.primary_clips[0]
        ov = view.timeline.overlay_clips[0]
        # 4 s removed from the head: overlay slid left with its content
        assert abs(float(ov.timeline_start) - 6.0) < 0.05
        assert abs(float(prim.source_start) - 4.0) < 0.05

    def test_overlay_head_trim_advances_source(self, qapp, timeline):
        view = view_with(timeline)
        ov = timeline.overlay_clips[0]
        view.trim(ov, "in", 11.0)
        assert abs(float(ov.source_start) - 1.0) < 0.05
        assert abs(float(ov.timeline_start) - 11.0) < 0.05
        assert abs(float(ov.duration) - 4.0) < 0.05

    def test_overlay_tail_trim_clamps_to_media(self, qapp, timeline):
        view = view_with(timeline)
        ov = timeline.overlay_clips[0]
        view.trim(ov, "out", 40.0)  # way past the 5 s of material
        assert float(ov.duration) <= 5.0 + 1 / 30

    def test_delete_primary_piece_closes_the_gap(self, qapp, timeline, make_media):
        view = view_with(timeline)
        second = make_media(name="DJI_0002.mp4", duration=10.0, role=Role.PRIMARY)
        view.append_clip(second, 0.0, 10.0)
        assert abs(float(view.timeline.duration) - 40.0) < 0.05
        view.delete_clip(view.timeline.primary_clips[1])
        assert abs(float(view.timeline.duration) - 30.0) < 0.05

    def test_last_primary_piece_cannot_be_deleted(self, qapp, timeline):
        view = view_with(timeline)
        view.delete_clip(view.timeline.primary_clips[0])
        assert len(view.timeline.primary_clips) == 1

    def test_delete_overlay(self, qapp, timeline):
        view = view_with(timeline)
        view.delete_clip(timeline.overlay_clips[0])
        assert view.timeline.overlay_clips == []

    def test_set_clip_range_on_primary_cuts_both_ends(self, qapp, timeline):
        view = view_with(timeline)
        view.set_clip_range(view.timeline.primary_clips[0], 5.0, 20.0)
        prim = view.timeline.primary_clips
        assert abs(float(view.timeline.duration) - 20.0) < 0.05
        assert abs(float(prim[0].source_start) - 5.0) < 0.05

    def test_snap_to_beats(self, qapp, timeline):
        view = view_with(timeline)
        view.set_analysis(beats=[i * 0.5 for i in range(60)])
        view.set_zoom(120.0)  # threshold = 9px / 120pps = 0.075 s
        assert view.snap_time(10.03) == pytest.approx(10.0)
        # far from anything snappable -> unchanged
        assert view.snap_time(10.37) == pytest.approx(10.37)

    def test_paints_without_error(self, qapp, timeline):
        view = view_with(timeline)
        view.set_analysis(
            waveforms={
                str(timeline.primary_clips[0].media.path): np.abs(
                    np.sin(np.linspace(0, 60, 750))
                )
            },
            beats=[i * 0.5 for i in range(60)],
            regions={"silence": [(3.0, 5.0)], "highlight": [(8.0, 14.0)]},
        )
        image = QImage(900, 300, QImage.Format_ARGB32)
        painter = QPainter(image)
        view.render(painter, QPoint(0, 0))
        painter.end()

    def test_edit_signals_fire_in_order(self, qapp, timeline):
        view = view_with(timeline)
        events: list[str] = []
        view.about_to_edit.connect(lambda: events.append("before"))
        view.edited.connect(lambda _d: events.append("after"))
        view.trim(timeline.overlay_clips[0], "out", 14.0)
        assert events == ["before", "after"]


def studio_page(qapp, timeline: Timeline, beats=None) -> RecreationalPage:
    """A RecreationalPage opened on a fabricated analysis (no ffmpeg)."""
    from editsync.autoedit import BeatGrid

    page = RecreationalPage()
    page.resize(1100, 700)

    fps = 100
    envs_rms: dict[str, np.ndarray] = {}
    envs_onset: dict[str, np.ndarray] = {}
    for clip in timeline.clips:
        n = int(float(clip.media.duration) * fps)
        rng = np.random.default_rng(3)
        loud = np.abs(rng.normal(0.3, 0.05, n))
        # quiet stretch 5..9 s of the primary for tighten tests
        if clip.lane == 0:
            loud[5 * fps : 9 * fps] = 0.0005
        envs_rms[str(clip.media.path)] = loud
        envs_onset[str(clip.media.path)] = np.abs(rng.normal(0.2, 0.05, n))

    analysis = StudioAnalysis(
        result=BuildResult(timeline=timeline, matches=[]),
        waveforms={},
        rms_envs=envs_rms,
        onset_envs=envs_onset,
        silences=[],
        highlights=[],
        beats=beats,
    )
    page._on_analysis(analysis)
    return page


class TestAutomations:
    def test_tighten_dead_air_cuts_the_quiet(self, qapp, timeline):
        page = studio_page(qapp, timeline)
        before = float(page.view.timeline.duration)
        page.tighten_dead_air()
        after = float(page.view.timeline.duration)
        assert after < before - 2.5  # ~3.5 s of dead air went away
        # the overlay survived and still exists
        assert len(page.view.timeline.overlay_clips) == 1

    def test_tighten_is_undoable(self, qapp, timeline):
        page = studio_page(qapp, timeline)
        before = float(page.view.timeline.duration)
        page.tighten_dead_air()
        assert page._undo
        page.undo()
        assert float(page.view.timeline.duration) == pytest.approx(before)

    def test_keep_best_moments_shrinks_to_target(self, qapp, timeline):
        page = studio_page(qapp, timeline)
        page.keep_slider.setValue(1)  # keep ~15 s
        page.keep_best_moments()
        after = float(page.view.timeline.duration)
        assert after < 30.0
        assert len(page.view.timeline.overlay_clips) == 1  # protected

    def test_cut_to_beat_lands_boundaries_on_grid(self, qapp, make_media):
        from editsync.autoedit import BeatGrid

        tl = Timeline(name="T", frame_rate=Fraction(30), width=1920, height=1080)
        a = make_media(name="DJI_0001.mp4", duration=30.0, role=Role.PRIMARY)
        tl.clips.append(
            TimelineClip(a, Fraction(0), Fraction(33, 10), Fraction(0), 0, role="DJI")
        )
        tl.clips.append(
            TimelineClip(a, Fraction(33, 10), Fraction(37, 10), Fraction(10),
                         0, role="DJI")
        )
        grid = BeatGrid(bpm=120.0, beats=[i * 0.5 for i in range(60)],
                        confidence=0.9)
        page = studio_page(qapp, tl, beats=grid)
        page.cut_to_beat()
        for clip in page.view.timeline.primary_clips:
            end = float(clip.timeline_end)
            nearest = min(abs(end - b) for b in page._timeline_beats())
            assert nearest < 0.06, end

    def test_reset_returns_to_synced_cut(self, qapp, timeline):
        page = studio_page(qapp, timeline)
        original = float(page.view.timeline.duration)
        page.tighten_dead_air()
        page.view.trim(page.view.timeline.primary_clips[0], "out", 10.0)
        page.reset_to_sync()
        assert float(page.view.timeline.duration) == pytest.approx(original)

    def test_music_relaid_after_edit(self, qapp, timeline, make_media):
        song = make_media(name="song.mp3", width=0, height=0, duration=12.0)
        song.role = Role.MUSIC
        page = studio_page(qapp, timeline)
        page.media = [song]
        page.music_enable.setChecked(True)
        page.view.trim(page.view.timeline.primary_clips[0], "out", 20.0)
        music = page.view.timeline.music_clips
        assert music, "music should be re-laid over the edited timeline"
        assert float(music[-1].timeline_end) == pytest.approx(20.0, abs=0.05)

    def test_settings_roundtrip(self, qapp):
        win = MainWindow()
        win.rec_page.fmt_video.setChecked(False)
        win.rec_page.fmt_capcut.setChecked(True)
        win.rec_page.keep_slider.setValue(8)
        win._save_settings()
        again = MainWindow()
        assert not again.rec_page.fmt_video.isChecked()
        assert again.rec_page.fmt_capcut.isChecked()
        assert again.rec_page.keep_slider.value() == 8


import shutil  # noqa: E402


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
class TestStudioEndToEnd:
    def test_sync_analyze_edit_export(self, qapp, tmp_path):
        """Real footage through the studio: sync + analysis worker,
        tighten, then an FCPXML export of the edited timeline."""
        import numpy as np
        from tests.test_integration import SR, scene_audio, write_video

        from editsync.gui.worker import ExportWorker, StudioWorker, SyncJob
        from editsync.builder import BuildOptions
        from editsync.media import classify, probe

        master = scene_audio(24)
        master[int(14 * SR) : int(19 * SR)] = 0.0005  # dead air to find
        dji = tmp_path / "DJI_0001.mp4"
        write_video(dji, master, 640, 360, "2026-06-20T10:00:00Z")
        seg = (0.6 * master[6 * SR : 10 * SR]).astype(np.float32)
        meta = tmp_path / "meta_001.mp4"
        write_video(meta, seg, 360, 640, "2026-06-20T10:00:06Z")

        media = [probe(dji), probe(meta)]
        classify(media)
        job = SyncJob(
            media=media,
            options=BuildOptions(project_name="Studio E2E"),
            formats=[],
            output_dir=tmp_path,
        )
        worker = StudioWorker(job)
        results: list = []
        worker.finished_ok.connect(results.append)
        worker.failed.connect(lambda msg: pytest.fail(msg))
        worker.run()  # synchronous on purpose
        analysis = results[0]

        assert len(analysis.result.matches) == 1 and analysis.result.matches[0].placed
        assert analysis.waveforms and analysis.rms_envs
        # the dead air we planted is found (timeline domain)
        assert any(13.5 < s < 15 and 18 < e < 19.6 for s, e in analysis.silences), (
            analysis.silences
        )

        page = RecreationalPage()
        page._on_analysis(analysis)
        before = float(page.view.timeline.duration)
        page.tighten_dead_air()
        after = float(page.view.timeline.duration)
        assert after < before - 3.0
        # overlay still synced over the same content
        assert len(page.view.timeline.overlay_clips) == 1

        exporter = ExportWorker(
            page.view.timeline, ["fcpxml"], tmp_path,
            page.build_options(),
        )
        outputs: list = []
        exporter.finished_ok.connect(outputs.append)
        exporter.failed.connect(lambda msg: pytest.fail(msg))
        exporter.run()
        written, _video = outputs[0]
        assert written and written[0].suffix == ".fcpxml"
        assert written[0].exists() and written[0].stat().st_size > 500


class TestRangeDialog:
    def test_selection_roundtrip(self, qapp, make_media):
        media = make_media(name="a.mp4", duration=20.0)
        dialog = RangeDialog(media, None, None, analyze=False)
        dialog.strip.set_selection(4.0, 12.0)
        assert dialog.selection == pytest.approx((4.0, 8.0))
        # spinboxes follow the strip
        assert dialog.start_spin.value() == pytest.approx(4.0)
        assert dialog.length_spin.value() == pytest.approx(8.0)

    def test_spinboxes_drive_the_strip(self, qapp, make_media):
        media = make_media(name="a.mp4", duration=20.0)
        dialog = RangeDialog(media, None, None, analyze=False)
        dialog.start_spin.setValue(2.0)
        dialog.length_spin.setValue(5.0)
        s, e = dialog.strip.selection()
        assert (s, e) == pytest.approx((2.0, 7.0))

    def test_defaults_to_clip_range(self, qapp, timeline):
        ov = timeline.overlay_clips[0]
        dialog = RangeDialog(ov.media, ov, None, analyze=False)
        assert dialog.selection[0] == pytest.approx(0.0)
        assert dialog.selection[1] == pytest.approx(5.0)
