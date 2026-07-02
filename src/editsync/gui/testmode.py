"""Hidden test mode.

Triple-click the logo (which deliberately shows no pointer cursor and
gives no visual hint) to open a secret menu that generates a complete
demo shoot — main-camera files, vertical glasses clips whose audio truly
overlaps the main recording, and a song — and loads it into the app so
every feature can be demonstrated end to end without real footage.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from PySide6.QtCore import QElapsedTimer, QEvent, QObject, QStandardPaths, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
)

from .. import testmode as engine_testmode
from .widgets import AnimatedButton, section_label

CLICKS_REQUIRED = 3
CLICK_WINDOW_MS = 1600


class SecretTrigger(QObject):
    """Counts rapid clicks on a watched widget and fires after three.

    Installed as an event filter so the widget itself stays a plain,
    cursor-less label — no affordance that it's clickable.
    """

    triggered = Signal()

    def __init__(self, watched, parent=None):
        super().__init__(parent)
        self._timer = QElapsedTimer()
        self._count = 0
        watched.installEventFilter(self)

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.MouseButtonPress:
            if not self._timer.isValid() or self._timer.elapsed() > CLICK_WINDOW_MS:
                self._count = 0
                self._timer.restart()
            self._count += 1
            if self._count >= CLICKS_REQUIRED:
                self._count = 0
                self._timer.invalidate()
                self.triggered.emit()
        return False  # never consume; the label behaves like a label


class _GenerateWorker(QThread):
    progress = Signal(str)
    done = Signal(object)  # DemoShoot
    failed = Signal(str)

    def __init__(self, dest: Path, split: bool, music: bool, parent=None):
        super().__init__(parent)
        self.dest = dest
        self.split = split
        self.music = music

    def run(self) -> None:
        try:
            shoot = engine_testmode.generate_demo_shoot(
                self.dest,
                split_recording=self.split,
                include_music=self.music,
                progress=self.progress.emit,
            )
            self.done.emit(shoot)
        except Exception as exc:
            self.failed.emit(str(exc))


class TestModeDialog(QDialog):
    """The secret menu: pick what the demo shoot includes, generate, load."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Test mode")
        self.setObjectName("Root")
        self.setMinimumWidth(460)
        self.paths: list[Path] = []
        self.fill_sample_title = False
        self._worker: _GenerateWorker | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        layout.addWidget(section_label("Test mode"))
        blurb = QLabel(
            "Generates a fake shoot with real, overlapping audio — a "
            "continuous main-camera recording, vertical glasses clips "
            "recorded by a 'different microphone', and a short song — "
            "then loads it so you can demonstrate every feature without "
            "real footage."
        )
        blurb.setWordWrap(True)
        blurb.setObjectName("Subtitle")
        layout.addWidget(blurb)

        self.split_check = QCheckBox(
            "Split the main recording into two files (tests the file-split case)"
        )
        self.split_check.setChecked(True)
        self.music_check = QCheckBox("Include a demo music file")
        self.music_check.setChecked(True)
        self.title_check = QCheckBox("Fill in a sample opening title")
        self.title_check.setChecked(True)
        layout.addWidget(self.split_check)
        layout.addWidget(self.music_check)
        layout.addWidget(self.title_check)

        self.status = QLabel("")
        self.status.setObjectName("Hint")
        layout.addWidget(self.status)

        buttons = QHBoxLayout()
        cancel = AnimatedButton("Cancel")
        cancel.clicked.connect(self.reject)
        self.generate_btn = AnimatedButton("Generate & load", kind="primary")
        self.generate_btn.clicked.connect(self._generate)
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        buttons.addWidget(self.generate_btn)
        layout.addLayout(buttons)

    def _generate(self) -> None:
        self.generate_btn.setEnabled(False)
        dest = Path(
            QStandardPaths.writableLocation(QStandardPaths.TempLocation)
        ) / f"EditSync-demo-{uuid.uuid4().hex[:8]}"
        self._worker = _GenerateWorker(
            dest,
            split=self.split_check.isChecked(),
            music=self.music_check.isChecked(),
            parent=self,
        )
        self._worker.progress.connect(self.status.setText)
        self._worker.done.connect(self._on_done)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_done(self, shoot) -> None:
        self.paths = list(shoot.files)
        self.fill_sample_title = self.title_check.isChecked()
        self.accept()

    def _on_failed(self, message: str) -> None:
        self.status.setText(f"Couldn't generate the demo: {message}")
        self.generate_btn.setEnabled(True)
