"""In-app updates: the silent check at launch (bottom-left pill when a
newer release exists) and the visible "Check for updates" footer action
that always reports what happened — up to date, update ready, or the
exact reason the check failed."""

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

from .. import __version__, updater
from .widgets import AnimatedButton

MARGIN = 16


class UpdateCheckWorker(QThread):
    checked = Signal(object)  # updater.CheckResult — always emitted
    found = Signal(object)  # updater.UpdateInfo — only when one exists

    def run(self) -> None:
        try:
            result = updater.check_for_update_detailed()
        except Exception as exc:  # belt and braces: checked must always fire
            result = updater.CheckResult(
                "error", detail=f"Update check failed: {exc}"
            )
        self.checked.emit(result)
        if result.status == "update" and result.info is not None:
            self.found.emit(result.info)


class UpdateInstallWorker(QThread):
    progress = Signal(int)
    failed = Signal(str)

    def __init__(self, info, parent=None):
        super().__init__(parent)
        self.info = info

    def run(self) -> None:
        try:
            payload = updater.download_asset(
                self.info, self.progress.emit, self.isInterruptionRequested
            )
            updater.install_and_restart(payload)  # exits the process on success
        except updater.UpdateError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # never die silently mid-install
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

    def install_running(self) -> bool:
        """True while a download/install is in flight — the pill (and its
        worker thread) must not be destroyed then."""
        return self._install_worker is not None and self._install_worker.isRunning()

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


class UpdateFooter(QWidget):
    """The visible side of updates: version line, a "Check for updates"
    action, and an honest status — "you're up to date", "X is ready",
    or exactly why the check failed (the launch check stays silent)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker: UpdateCheckWorker | None = None
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addStretch(1)
        version = QLabel(f"EditSync {__version__} — 86 Auto Lab  ·")
        version.setObjectName("Hint")
        self.check_btn = AnimatedButton("Check for updates", kind="ghost")
        self.check_btn.clicked.connect(self.check_now)
        self.status = QLabel("")
        self.status.setObjectName("Hint")
        layout.addWidget(version)
        layout.addWidget(self.check_btn)
        layout.addWidget(self.status)
        layout.addStretch(1)

    def check_now(self) -> None:
        self.check_btn.setEnabled(False)
        self.status.setText("Checking…")
        self.status.setToolTip("")
        self._worker = UpdateCheckWorker(self)
        self._worker.checked.connect(self._on_checked)
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.start()

    def _on_checked(self, result) -> None:
        self.check_btn.setEnabled(True)
        if result.status == "update" and result.info is not None:
            self.status.setText(
                f"Version {result.info.version} is ready — see the "
                f"prompt in the corner."
            )
            window = self.window()
            if hasattr(window, "show_update_pill"):
                window.show_update_pill(result.info)
        elif result.status == "current":
            self.status.setText(
                f"You're up to date — {result.detail or __version__} "
                f"is the newest."
            )
        else:
            self.status.setText(result.detail or "Couldn't check for updates.")
            self.status.setToolTip(result.detail)


def start_update_check(window: QWidget) -> UpdateCheckWorker:
    """Kick off the silent launch check; shows the pill on a hit."""

    def _show(info) -> None:
        if hasattr(window, "show_update_pill"):
            window.show_update_pill(info)
        else:
            window.update_pill = UpdatePill(info, window)

    worker = UpdateCheckWorker(window)
    worker.found.connect(_show)
    worker.start()
    return worker
