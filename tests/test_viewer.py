"""The live viewer: framing math, drag-to-reposition, scrubbing, and
the renderer honoring dragged positions (offscreen Qt)."""

from __future__ import annotations

import os
import shutil
from fractions import Fraction
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QImage, QMouseEvent, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from editsync.gui.viewer import (  # noqa: E402
    FrameFetcher,
    LiveViewer,
    LiveViewerPanel,
    blur_active,
    clips_at,
    overlay_rect_px,
)
from editsync.media import Role  # noqa: E402
from editsync.timeline import BlurRegion, Timeline, TimelineClip  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def stop_frame_fetchers():
    """No frame-fetcher thread may outlive its test (Qt aborts at exit)."""
    yield
    from editsync.gui.viewer import FrameFetcher

    FrameFetcher.stop_all()



@pytest.fixture
def timeline(make_media) -> Timeline:
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


def mouse(kind, x, y):
    return QMouseEvent(
        kind, QPointF(x, y), Qt.LeftButton, Qt.LeftButton, Qt.NoModifier
    )


class TestGeometry:
    def test_center_style_fits_vertical_clip(self, timeline):
        ov = timeline.overlay_clips[0]
        x, y, w, h = overlay_rect_px(timeline, ov, "center")
        assert h == pytest.approx(1080)
        assert w == pytest.approx(1080 * 1080 / 1920)  # 607.5
        assert x == pytest.approx((1920 - w) / 2)
        assert y == pytest.approx(0)

    def test_pip_sides(self, timeline):
        ov = timeline.overlay_clips[0]
        xl, _, wl, _ = overlay_rect_px(timeline, ov, "pip-left")
        xr, _, wr, _ = overlay_rect_px(timeline, ov, "pip-right")
        assert xl == pytest.approx(1920 * 0.04)
        assert xr == pytest.approx(1920 - wr - 1920 * 0.04)
        assert wl == wr

    def test_fill_covers_the_frame(self, timeline):
        ov = timeline.overlay_clips[0]
        assert overlay_rect_px(timeline, ov, "fill") == (0.0, 0.0, 1920.0, 1080.0)

    def test_dragged_position_offsets_from_center(self, timeline):
        ov = timeline.overlay_clips[0]
        ov.transform_position = (100.0, 50.0)  # +y up
        x, y, w, h = overlay_rect_px(timeline, ov, "center")
        assert x == pytest.approx((1920 - w) / 2 + 100)
        assert y == pytest.approx((1080 - h) / 2 - 50)  # up = smaller y

    def test_clips_at_and_blur(self, timeline):
        timeline.blur_regions = [BlurRegion(Fraction(10), Fraction(15), 50.0)]
        assert len(clips_at(timeline, 12.0)) == 2
        assert len(clips_at(timeline, 2.0)) == 1
        assert blur_active(timeline, 12.0)
        assert not blur_active(timeline, 2.0)


class TestDragToReposition:
    def _viewer(self, timeline) -> LiveViewer:
        viewer = LiveViewer()
        viewer.resize(800, 450)  # exactly 1920x1080 / 2.4
        viewer.timeline = timeline
        viewer.overlay_style = "center"
        viewer.time = 12.0  # overlay visible
        return viewer

    def test_drag_moves_what_is_visible(self, qapp, timeline):
        viewer = self._viewer(timeline)
        ov = timeline.overlay_clips[0]
        rect = viewer.overlay_widget_rect(ov)
        start = rect.center()
        began, finished = [], []
        viewer.drag_started.connect(began.append)
        viewer.drag_finished.connect(finished.append)

        viewer.mousePressEvent(mouse(QMouseEvent.MouseButtonPress, start.x(), start.y()))
        viewer.mouseMoveEvent(
            mouse(QMouseEvent.MouseMove, start.x() + 40, start.y() - 50)
        )
        viewer.mouseReleaseEvent(
            mouse(QMouseEvent.MouseButtonRelease, start.x() + 40, start.y() - 50)
        )

        assert began == [ov] and finished == [ov]
        px, py = ov.transform_position
        s = 800 / 1920
        assert px == pytest.approx(40 / s, abs=1)
        assert py == pytest.approx(50 / s, abs=1)  # up is positive

    def test_press_outside_overlay_does_nothing(self, qapp, timeline):
        viewer = self._viewer(timeline)
        began = []
        viewer.drag_started.connect(began.append)
        viewer.mousePressEvent(mouse(QMouseEvent.MouseButtonPress, 5, 5))
        assert began == []
        assert timeline.overlay_clips[0].transform_position is None

    def test_no_dragging_in_fill_style(self, qapp, timeline):
        viewer = self._viewer(timeline)
        viewer.overlay_style = "fill"
        viewer.mousePressEvent(mouse(QMouseEvent.MouseButtonPress, 400, 225))
        assert viewer._drag is None

    def test_paints_without_error(self, qapp, timeline):
        timeline.blur_regions = [BlurRegion(Fraction(10), Fraction(15), 50.0)]
        viewer = self._viewer(timeline)
        viewer.overlay_style = "blur-bg"
        frame = QImage(640, 360, QImage.Format_RGB32)
        frame.fill(0xFF808080)
        viewer.frames = {
            str(c.media.path): frame for c in timeline.clips
        }
        image = QImage(800, 450, QImage.Format_ARGB32)
        painter = QPainter(image)
        viewer.render(painter, QPoint(0, 0))
        painter.end()


class TestPanel:
    def test_seek_clamps_and_updates_scrubber(self, qapp, timeline):
        panel = LiveViewerPanel()
        panel.set_timeline(timeline, "center", 50.0)
        panel.seek(12.0, emit=False)
        assert panel.viewer.time == pytest.approx(12.0)
        assert panel.scrubber.value() == 12000
        panel.seek(999.0, emit=False)
        assert panel.viewer.time <= 30.0
        panel.shutdown()

    def test_scrub_emits_time_changed(self, qapp, timeline):
        panel = LiveViewerPanel()
        panel.set_timeline(timeline, "center", 50.0)
        times = []
        panel.time_changed.connect(times.append)
        panel._on_scrub(8000)
        assert times and times[0] == pytest.approx(8.0)
        panel.shutdown()

    def test_play_pause_toggles(self, qapp, timeline):
        panel = LiveViewerPanel()
        panel.set_timeline(timeline, "center", 50.0)
        panel.play()
        assert panel._playing and panel.play_btn.text() == "⏸"
        panel.pause()
        assert not panel._playing and panel.play_btn.text() == "▶"
        panel.shutdown()


class TestViewerUndoIntegration:
    def test_viewer_drag_is_undoable(self, qapp, timeline, monkeypatch):
        from editsync import updater
        from editsync.gui.recreational import RecreationalPage
        from tests.test_recreational import studio_page

        monkeypatch.setattr(
            updater,
            "check_for_update_detailed",
            lambda: updater.CheckResult("current", detail=updater.__version__),
        )
        page = studio_page(qapp, timeline)
        ov = page.view.timeline.overlay_clips[0]
        panel = page.viewer_panel
        panel.viewer.resize(800, 450)
        panel.viewer.time = 12.0
        rect = panel.viewer.overlay_widget_rect(ov)
        start = rect.center()
        panel.viewer.mousePressEvent(
            mouse(QMouseEvent.MouseButtonPress, start.x(), start.y())
        )
        panel.viewer.mouseMoveEvent(
            mouse(QMouseEvent.MouseMove, start.x() + 24, start.y())
        )
        panel.viewer.mouseReleaseEvent(
            mouse(QMouseEvent.MouseButtonRelease, start.x() + 24, start.y())
        )
        moved = page.view.timeline.overlay_clips[0].transform_position
        assert moved is not None and moved[0] > 0
        assert page._undo
        page.undo()
        assert page.view.timeline.overlay_clips[0].transform_position is None
        panel.shutdown()


class TestRendererHonorsPosition:
    def test_dragged_overlay_position_in_graph(self, timeline):
        from editsync.renderer import build_command

        ov = timeline.overlay_clips[0]
        ov.transform_position = (96.0, -42.5)
        cmd = build_command(timeline, Path("/tmp/out.mp4"), overlay_style="center")
        graph = cmd[cmd.index("-filter_complex") + 1]
        assert "overlay=(W-w)/2+(96.0):(H-h)/2-(-42.5)" in graph

    def test_fill_ignores_position(self, timeline):
        from editsync.renderer import build_command

        ov = timeline.overlay_clips[0]
        ov.transform_position = (96.0, 0.0)
        cmd = build_command(timeline, Path("/tmp/out.mp4"), overlay_style="fill")
        graph = cmd[cmd.index("-filter_complex") + 1]
        assert "(W-w)/2+(96" not in graph


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
class TestFrameFetcher:
    def test_fetches_real_frames(self, qapp, tmp_path):
        import numpy as np
        from tests.test_integration import scene_audio, write_video

        clip = tmp_path / "DJI_0001.mp4"
        write_video(clip, scene_audio(4), 320, 180, "2026-06-20T10:00:00Z")

        fetcher = FrameFetcher()
        got: list = []
        fetcher.frames_ready.connect(lambda t, frames: got.append((t, frames)))
        fetcher.start()
        fetcher.request(1.0, [(str(clip), 1.0)])
        for _ in range(200):
            QApplication.processEvents()
            if got:
                break
            fetcher.wait(50)
        fetcher.stop()
        assert got, "fetcher never returned a frame"
        _, frames = got[0]
        image = frames[str(clip)]
        assert not image.isNull() and image.width() > 0
