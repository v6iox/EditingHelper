"""Smoke tests for the desktop app (offscreen Qt, no display needed).

These verify the UI wiring — file intake, role summary, option plumbing,
and the sync worker end-to-end — not pixel-perfect rendering.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from editsync.builder import BuildOptions  # noqa: E402
from editsync.gui.window import MainWindow  # noqa: E402
from editsync.gui.worker import ProbeWorker, SyncJob, SyncWorker  # noqa: E402
from editsync.media import Role  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def clean_settings():
    """Each test starts from default (empty) persisted settings."""
    MainWindow._settings().clear()
    yield
    MainWindow._settings().clear()


def enter_event():
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QEnterEvent

    return QEnterEvent(QPointF(1, 1), QPointF(1, 1), QPointF(1, 1))


def leave_event():
    from PySide6.QtCore import QEvent

    return QEvent(QEvent.Leave)


class TestMainWindow:
    def test_sync_disabled_until_both_roles_present(self, qapp, make_media):
        win = MainWindow()
        assert not win.sync_btn.isEnabled()

        win._on_file_ready(make_media(name="DJI_0001.mp4", role=Role.PRIMARY))
        win._refresh_summary()
        assert not win.sync_btn.isEnabled()
        assert "glasses" in win.status_hint.text().lower()

        win._on_file_ready(
            make_media(name="meta_001.mp4", width=1440, height=1920, role=Role.OVERLAY)
        )
        win._refresh_summary()
        assert win.sync_btn.isEnabled()

    def test_duplicate_files_ignored(self, qapp, make_media):
        win = MainWindow()
        m = make_media(name="DJI_0001.mp4", role=Role.PRIMARY)
        win._on_file_ready(m)
        win._on_file_ready(m)
        assert len(win.media) == 1

    def test_remove_and_clear(self, qapp, make_media):
        win = MainWindow()
        m = make_media(name="DJI_0001.mp4", role=Role.PRIMARY)
        win._on_file_ready(m)
        win._remove_file(m)
        assert win.media == []
        win._on_file_ready(m)
        win._clear_files()
        assert win.media == []

    def test_format_selection(self, qapp):
        win = MainWindow()
        assert win._formats() == ["fcpxml"]
        win.fmt_premiere.setChecked(True)
        win.fmt_otio.setChecked(True)
        assert win._formats() == ["fcpxml", "premiere", "otio"]

    def test_option_defaults(self, qapp):
        win = MainWindow()
        assert win.style_seg.value() == "blur-bg"
        assert win.duck_seg.value() == "-60"
        assert win.blur_slider.value() == 50
        assert not win.lane_per_clip.isChecked()

    def test_blur_slider_follows_style(self, qapp):
        win = MainWindow()
        win._on_style_changed("center")
        assert not win.blur_slider.isEnabled()
        win._on_style_changed("blur-bg")
        assert win.blur_slider.isEnabled()

    def test_brand_logo_loads(self, qapp):
        from editsync.gui.window import (
            ICON_PATH,
            LOGO_PATH,
            VERTICAL_LOGO_PATH,
            brand_logo,
        )

        assert LOGO_PATH.is_file()
        assert VERTICAL_LOGO_PATH.is_file()
        assert ICON_PATH.is_file()
        for vertical in (False, True):
            label = brand_logo(30, vertical=vertical)
            assert label is not None
            assert not label.pixmap().isNull()

    def test_settings_round_trip(self, qapp):
        win = MainWindow()
        win.style_seg.set_value("pip-right")
        win.duck_seg.set_value("-18")
        win.blur_slider.setValue(83)
        win.fmt_otio.setChecked(True)
        win.lane_per_clip.setChecked(True)
        win._save_settings()

        fresh = MainWindow()
        assert fresh.style_seg.value() == "pip-right"
        assert fresh.duck_seg.value() == "-18"
        assert fresh.blur_slider.value() == 83
        assert fresh.fmt_otio.isChecked()
        assert fresh.lane_per_clip.isChecked()
        # blur slider disabled because the restored style isn't blur-bg
        assert not fresh.blur_slider.isEnabled()
        # reset stored state so later runs start from defaults
        fresh._settings().clear()


class TestAnimatedWidgets:
    def test_hover_progress_animates(self, qapp):
        from PySide6.QtCore import QAbstractAnimation
        from editsync.gui.widgets import AnimatedButton

        for kind in ("default", "primary", "ghost", "segment"):
            btn = AnimatedButton("Test", kind=kind)
            assert btn._hover.progress == 0.0
            btn.enterEvent(enter_event())
            assert btn._hover.state() == QAbstractAnimation.Running
            btn._hover.setCurrentTime(btn._hover.duration())  # jump to end
            assert btn._hover.progress == 1.0
            btn.leaveEvent(leave_event())
            btn._hover.setCurrentTime(btn._hover.duration())
            assert btn._hover.progress == 0.0

    def test_buttons_render_all_states(self, qapp):
        from editsync.gui.widgets import AnimatedButton

        for kind in ("default", "primary", "ghost", "segment"):
            btn = AnimatedButton("Render me", kind=kind)
            if kind == "segment":
                btn.setCheckable(True)
                btn.setChecked(True)
            btn.resize(btn.sizeHint())
            assert not btn.grab().isNull()
            btn.setEnabled(False)
            assert not btn.grab().isNull()

    def test_dropzone_renders_and_signals(self, qapp):
        from editsync.gui.widgets import DropZone

        zone = DropZone()
        zone.resize(400, 180)
        assert not zone.grab().isNull()
        # enter/leave drive the hover animation without crashing
        zone.enterEvent(enter_event())
        zone.leaveEvent(leave_event())
        assert not zone.grab().isNull()

    def test_segmented_set_value(self, qapp):
        from editsync.gui.widgets import Segmented

        seg = Segmented([("a", "A"), ("b", "B")], default="a")
        changes = []
        seg.changed.connect(changes.append)
        seg.set_value("b")
        assert seg.value() == "b"
        assert changes == ["b"]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
class TestWorkers:
    def test_probe_worker(self, qapp, tmp_path):
        from tests.test_integration import scene_audio, write_video

        video = tmp_path / "DJI_0001.mp4"
        write_video(video, scene_audio(3), 640, 360, "2026-06-20T10:00:00Z")

        found, failed = [], []
        worker = ProbeWorker([tmp_path])
        worker.file_ready.connect(found.append)
        worker.file_failed.connect(lambda p, e: failed.append(p))
        worker.run()  # synchronous: exercise the logic without an event loop
        assert len(found) == 1
        assert found[0].role == Role.PRIMARY
        assert not failed

    def test_sync_worker_end_to_end(self, qapp, tmp_path):
        from tests.test_integration import SR, scene_audio, write_video
        from editsync.media import classify, probe
        import numpy as np

        master = scene_audio(30)
        dji = tmp_path / "DJI_0001.mp4"
        write_video(dji, master, 640, 360, "2026-06-20T10:00:00Z")
        seg = (0.6 * master[8 * SR : 14 * SR]).astype(np.float32)
        meta = tmp_path / "meta_001.mp4"
        write_video(meta, seg, 360, 640, "2026-06-20T10:00:08Z")

        media = [probe(dji), probe(meta)]
        classify(media)
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        job = SyncJob(
            media=media,
            options=BuildOptions(project_name="GUI Test"),
            formats=["fcpxml", "otio"],
            output_dir=out_dir,
        )
        outcomes, errors = [], []
        worker = SyncWorker(job)
        worker.finished_ok.connect(outcomes.append)
        worker.failed.connect(errors.append)
        worker.run()

        assert not errors
        assert len(outcomes) == 1
        outcome = outcomes[0]
        assert (out_dir / "GUI_Test.fcpxml").exists()
        assert (out_dir / "GUI_Test.otio").exists()
        assert (out_dir / "GUI_Test.sync-report.json").exists()
        placed = [m for m in outcome.result.matches if m.placed]
        assert len(placed) == 1
        assert abs(float(placed[0].timeline_start) - 8.0) < 0.05

    def test_sync_worker_reports_errors(self, qapp, make_media):
        # no primary footage -> friendly failure signal, not a crash
        job = SyncJob(
            media=[make_media(name="meta.mp4", width=1080, height=1920, role=Role.OVERLAY)],
            options=BuildOptions(),
            formats=["fcpxml"],
            output_dir=Path("/nonexistent"),
        )
        errors = []
        worker = SyncWorker(job)
        worker.failed.connect(errors.append)
        worker.run()
        assert errors and "primary" in errors[0].lower()


class TestBundledFfmpegLookup:
    def test_env_dir_takes_priority(self, tmp_path, monkeypatch):
        from editsync.media import require_tool

        fake = tmp_path / "ffmpeg"
        fake.write_text("#!/bin/sh\n")
        fake.chmod(0o755)
        monkeypatch.setenv("EDITSYNC_FFMPEG_DIR", str(tmp_path))
        assert require_tool("ffmpeg") == str(fake)
