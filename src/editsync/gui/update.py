"""In-app update prompt: a small pill in the bottom-left corner that
appears when a newer release exists and applies the update on click."""

from __future__ import annotations

import webbrowser

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt, QThread, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QWidget,
)

from .. import updater
from .widgets import AnimatedButton

MARGIN = 16


class UpdateCheckWorker(QThread):
    found = Signal(object)  # updater.UpdateInfo

    def run(self) -> None:
        info = updater.check_for_update()
        if info is not None:
            self.found.emit(info)


class UpdateInstallWorker(QThread):
    progress = Signal(int)
    failed = Signal(str)

    def __init__(self, info, parent=None):
        super().__init__(parent)
        self.info = info

    def run(self) -> None:
        try:
            payload = updater.download_asset(self.info, self.progress.emit)
            updater.install_and_restart(payload)  # exits the process on success
        except updater.UpdateError as exc:
            self.failed.emit(str(exc))


class UpdatePill(QFrame):
    """Floating card anchored to the parent's bottom-left corner."""

    def __init__(self, info, parent: QWidget):
        super().__init__(parent)
        self.info = info
        self._install_worker: UpdateInstallWorker | None = None
        self.setObjectName("Card")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 10, 10)
        layout.setSpacing(10)
        self.label = QLabel(f"EditSync {info.version} is ready")
        self.update_btn = AnimatedButton("Update now", kind="primary")
        self.update_btn.clicked.connect(self._start_install)
        dismiss = AnimatedButton("✕", kind="ghost")
        dismiss.setFixedWidth(26)
        dismiss.setToolTip("Not now")
        dismiss.clicked.connect(self.hide)
        layout.addWidget(self.label)
        layout.addWidget(self.update_btn)
        layout.addWidget(dismiss)

        self.adjustSize()
        self.reposition()
        effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(effect)
        self._fade = QPropertyAnimation(effect, b"opacity", self)
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.setDuration(300)
        self._fade.setEasingCurve(QEasingCurve.OutCubic)
        self._fade.finished.connect(lambda: self.setGraphicsEffect(None))
        self.show()
        self.raise_()
        self._fade.start()

    def reposition(self) -> None:
        parent = self.parentWidget()
        if parent is not None:
            self.move(MARGIN, parent.height() - self.height() - MARGIN)

    def _start_install(self) -> None:
        self.update_btn.setEnabled(False)
        self.label.setText("Downloading…")
        self._install_worker = UpdateInstallWorker(self.info, self)
        self._install_worker.progress.connect(
            lambda pct: self.label.setText(f"Downloading… {pct}%")
            if pct < 100
            else self.label.setText("Installing… the app will reopen itself")
        )
        self._install_worker.failed.connect(self._on_failed)
        self._install_worker.start()

    def _on_failed(self, message: str) -> None:
        # fall back to the release page so the user can update manually
        self.label.setText("Couldn't auto-update — opening the download page")
        self.label.setToolTip(message)
        webbrowser.open(self.info.page_url)
        self.update_btn.setText("Retry")
        self.update_btn.setEnabled(True)


def start_update_check(window: QWidget) -> UpdateCheckWorker:
    """Kick off the background check; shows the pill if an update exists."""

    def _show(info) -> None:
        window.update_pill = UpdatePill(info, window)

    worker = UpdateCheckWorker(window)
    worker.found.connect(_show)
    worker.start()
    return worker
