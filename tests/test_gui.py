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


@pytest.fixture(autouse=True)
def no_network_update_check(monkeypatch):
    """Window construction must never hit the network in tests."""
    from editsync import updater

    monkeypatch.setattr(
        updater,
        "check_for_update_detailed",
        lambda: updater.CheckResult("current", detail=updater.__version__),
    )


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
        win.fmt_capcut.setChecked(True)
        win.fmt_video.setChecked(True)
        assert win._formats() == ["fcpxml", "premiere", "otio", "capcut", "video"]
        assert not MainWindow().fmt_capcut.isChecked()  # off by default

    def test_video_option_defaults_off_and_persists(self, qapp):
        win = MainWindow()
        assert not win.fmt_video.isChecked()
        win.fmt_video.setChecked(True)
        win._save_settings()
        fresh = MainWindow()
        assert fresh.fmt_video.isChecked()

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


class TestMusicControls:
    def test_disabled_without_music_file(self, qapp, make_media):
        win = MainWindow()
        win._on_file_ready(make_media(name="DJI_0001.mp4", role=Role.PRIMARY))
        win._refresh_summary()
        assert not win.music_enable.isEnabled()
        assert win.music_hint.isVisibleTo(win)

    def test_enabled_with_music_file(self, qapp, make_media):
        from fractions import Fraction

        win = MainWindow()
        song = make_media(name="song.mp3", width=0, height=0, duration=30.0)
        song.frame_rate = Fraction(0)
        song.role = Role.MUSIC
        win._on_file_ready(song)
        win._refresh_summary()
        assert win.music_enable.isEnabled()
        assert not win.music_enable.isChecked()  # off by default
        assert not win.music_duck.isEnabled()  # gated on the main toggle
        win.music_enable.setChecked(True)
        assert win.music_duck.isEnabled()
        assert not win.music_duck.isChecked()  # also off by default
        assert win.music_vol_slider.value() == -22
        assert win._music_file() is song

    def test_music_badge(self, qapp, make_media):
        from fractions import Fraction
        from editsync.gui.widgets import FileRow

        song = make_media(name="song.mp3", width=0, height=0, duration=30.0)
        song.frame_rate = Fraction(0)
        song.role = Role.MUSIC
        row = FileRow(song)
        assert not row.grab().isNull()


class TestTitleControls:
    def test_defaults_empty_and_classic(self, qapp):
        win = MainWindow()
        assert win.title_edit.text() == ""
        assert win.title_desc_edit.text() == ""
        assert win.title_style_picker.value() == "classic"
        assert win.title_hold_slider.value() == 6  # 3.0 s
        assert win.title_fade_slider.value() == 4  # 1.00 s

    def test_previews_render_for_all_styles(self, qapp):
        from editsync.gui.title_picker import TitleStylePicker
        from editsync.titles import STYLES

        picker = TitleStylePicker()
        picker.update_sample("Front Bumper", "2024 GR86")
        for key in STYLES:
            card = picker._cards[key]
            assert not card.preview.pixmap().isNull()

    def test_style_click_changes_value(self, qapp):
        from editsync.gui.title_picker import TitleStylePicker

        picker = TitleStylePicker()
        changes = []
        picker.changed.connect(changes.append)
        picker._on_click("statement")
        assert picker.value() == "statement"
        assert changes == ["statement"]
        assert picker._cards["statement"].selected
        assert not picker._cards["classic"].selected

    def test_release_outside_card_cancels_click(self, qapp):
        # regression: press then drag off the card and release = no select
        from PySide6.QtCore import QEvent, QPointF, Qt as QtNS
        from PySide6.QtGui import QMouseEvent
        from editsync.gui.title_picker import TitleStylePicker

        picker = TitleStylePicker()
        card = picker._cards["statement"]
        card.resize(card.sizeHint())
        outside = QMouseEvent(
            QEvent.MouseButtonRelease,
            QPointF(-500, -500),
            QtNS.LeftButton,
            QtNS.NoButton,
            QtNS.NoModifier,
        )
        card.mouseReleaseEvent(outside)
        assert picker.value() == "classic"
        inside = QMouseEvent(
            QEvent.MouseButtonRelease,
            QPointF(5, 5),
            QtNS.LeftButton,
            QtNS.NoButton,
            QtNS.NoModifier,
        )
        card.mouseReleaseEvent(inside)
        assert picker.value() == "statement"

    def test_all_settings_persist(self, qapp):
        win = MainWindow()
        win.name_edit.setText("Bumper Series")
        win.title_edit.setText("Front Bumper Removal")
        win.title_desc_edit.setText("2024 Toyota GR86")
        win.title_style_picker.set_value("lower-left")
        win.title_hold_slider.setValue(10)
        win.title_fade_slider.setValue(8)
        win.music_enable.setChecked(True)
        win._save_settings()

        fresh = MainWindow()
        assert fresh.name_edit.text() == "Bumper Series"
        assert fresh.title_edit.text() == "Front Bumper Removal"
        assert fresh.title_desc_edit.text() == "2024 Toyota GR86"
        assert fresh.title_style_picker.value() == "lower-left"
        assert fresh.title_hold_slider.value() == 10
        assert fresh.title_fade_slider.value() == 8
        assert fresh.music_enable.isChecked()


class TestUpdatePill:
    def test_pill_appears_bottom_left_and_dismisses(self, qapp):
        from editsync.gui.update import MARGIN, UpdatePill
        from editsync.updater import UpdateInfo

        win = MainWindow()
        win.resize(800, 700)
        win.show()
        info = UpdateInfo(
            version="9.9.9",
            tag="v9.9.9",
            asset_url="https://example.com/asset",
            page_url="https://example.com/releases",
        )
        pill = UpdatePill(info, win)
        assert "9.9.9" in pill.label.text()
        assert pill.x() == MARGIN
        assert pill.y() == win.height() - pill.height() - MARGIN
        # reposition follows the window
        win.resize(900, 800)
        pill.reposition()
        assert pill.y() == win.height() - pill.height() - MARGIN
        pill.hide()
        assert not pill.isVisible()

    def test_no_pill_when_up_to_date(self, qapp, monkeypatch):
        from editsync.gui import update as update_mod

        monkeypatch.setattr(
            update_mod.updater,
            "check_for_update_detailed",
            lambda: update_mod.updater.CheckResult(
                "current", detail=update_mod.updater.__version__
            ),
        )
        win = MainWindow()
        worker = update_mod.UpdateCheckWorker(win)
        results = []
        worker.found.connect(results.append)
        worker.run()
        assert results == []


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


class TestUpdateFooter:
    """The visible 'Check for updates' action reports every outcome."""

    def _footer(self, monkeypatch, result):
        from editsync.gui import update as update_mod

        monkeypatch.setattr(
            update_mod.updater, "check_for_update_detailed", lambda: result
        )
        footer = update_mod.UpdateFooter()
        footer.check_btn.click()
        footer._worker.wait(5000)
        # deliver the queued signal from the worker thread
        QApplication.processEvents()
        return footer

    def test_up_to_date_message(self, qapp, monkeypatch):
        from editsync import updater

        footer = self._footer(
            monkeypatch, updater.CheckResult("current", detail="1.5.0")
        )
        assert "up to date" in footer.status.text()
        assert "1.5.0" in footer.status.text()
        assert footer.check_btn.isEnabled()

    def test_error_is_shown_not_swallowed(self, qapp, monkeypatch):
        from editsync import updater

        footer = self._footer(
            monkeypatch,
            updater.CheckResult(
                "error", detail="Couldn't reach GitHub — check your internet connection."
            ),
        )
        assert "GitHub" in footer.status.text()

    def test_update_found_shows_the_pill(self, qapp, monkeypatch):
        from editsync import updater
        from editsync.gui import update as update_mod

        info = updater.UpdateInfo("9.9.9", "v9.9.9", "url", "page")
        monkeypatch.setattr(
            update_mod.updater,
            "check_for_update_detailed",
            lambda: updater.CheckResult("update", info=info),
        )
        win = MainWindow()
        win.resize(1000, 700)
        footer = win.setup_page.widget().findChild(update_mod.UpdateFooter)
        assert footer is not None
        footer.check_btn.click()
        footer._worker.wait(5000)
        QApplication.processEvents()
        assert win.update_pill is not None
        assert "9.9.9" in win.update_pill.label.text()
        assert "9.9.9" in footer.status.text()
