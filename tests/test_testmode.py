"""Test mode: demo-shoot generation and the secret trigger."""

from __future__ import annotations

import os
import shutil

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
class TestDemoShoot:
    def test_generation_and_full_sync(self, tmp_path):
        from editsync.builder import BuildOptions, build
        from editsync.media import Role, classify, probe
        from editsync.testmode import generate_demo_shoot

        messages = []
        shoot = generate_demo_shoot(tmp_path, progress=messages.append)
        assert len(shoot.files) == 6  # 2 primaries + 3 overlays + music
        assert messages and "ready" in messages[-1].lower()

        media = [probe(f) for f in shoot.files]
        classify(media)
        roles = {m.path.name: m.role for m in media}
        assert roles["DJI_DEMO_0001.MP4"] == Role.PRIMARY
        assert roles["DJI_DEMO_0002.MP4"] == Role.PRIMARY
        assert roles["meta_demo_001.mp4"] == Role.OVERLAY
        assert roles["demo_music.m4a"] == Role.MUSIC

        result = build(
            [m for m in media if m.role == Role.PRIMARY],
            [m for m in media if m.role == Role.OVERLAY],
            BuildOptions(),
        )
        placed = {
            m.media.path.name: float(m.timeline_start)
            for m in result.matches if m.placed
        }
        # every demo clip syncs to its known scene position — including
        # meta_demo_002, which spans the file-split boundary
        for name, expected in shoot.expected_overlays.items():
            assert name in placed, f"{name} was not placed"
            assert abs(placed[name] - expected) < 1 / 30 + 0.001

    def test_no_split_no_music(self, tmp_path):
        from editsync.testmode import generate_demo_shoot

        shoot = generate_demo_shoot(
            tmp_path, split_recording=False, include_music=False
        )
        names = {f.name for f in shoot.files}
        assert "DJI_DEMO_0002.MP4" not in names
        assert shoot.music is None
        assert len([n for n in names if n.startswith("meta")]) == 3


class TestSecretTrigger:
    def test_three_quick_clicks_trigger(self, qapp):
        from PySide6.QtCore import QEvent, QPointF, Qt
        from PySide6.QtGui import QMouseEvent
        from PySide6.QtWidgets import QLabel
        from editsync.gui.testmode import SecretTrigger

        label = QLabel("logo")
        fired = []
        trigger = SecretTrigger(label, label)
        trigger.triggered.connect(lambda: fired.append(True))

        def click():
            event = QMouseEvent(
                QEvent.MouseButtonPress, QPointF(2, 2),
                Qt.LeftButton, Qt.NoButton, Qt.NoModifier,
            )
            qapp.sendEvent(label, event)

        click()
        click()
        assert not fired
        click()
        assert fired == [True]
        # counter resets after firing: two more clicks do nothing
        click()
        click()
        assert fired == [True]

    def test_logo_has_no_pointer_cursor(self, qapp, monkeypatch):
        from PySide6.QtCore import Qt
        from editsync import updater
        monkeypatch.setattr(updater, "check_for_update", lambda: None)
        from editsync.gui.window import brand_logo

        logo = brand_logo(34)
        assert logo is not None
        # the secret must not advertise itself
        assert logo.cursor().shape() == Qt.ArrowCursor

    def test_dialog_defaults(self, qapp):
        from editsync.gui.testmode import TestModeDialog

        dialog = TestModeDialog()
        assert dialog.split_check.isChecked()
        assert dialog.music_check.isChecked()
        assert dialog.title_check.isChecked()
        assert dialog.paths == []
