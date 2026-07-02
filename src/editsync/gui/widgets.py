"""Reusable widgets for the EditSync app."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..media import MediaFile, Role


class DropZone(QFrame):
    """Large drag-and-drop target that also opens a file dialog on click."""

    files_dropped = Signal(list)  # list[Path]
    browse_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("DropZone")
        self.setAcceptDrops(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(180)

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        layout.setSpacing(6)

        icon = QLabel("↓")
        icon.setAlignment(Qt.AlignCenter)
        icon.setStyleSheet("font-size: 30px; color: #9a9aa0;")
        title = QLabel("Drop your footage here")
        title.setObjectName("DropTitle")
        title.setAlignment(Qt.AlignCenter)
        sub = QLabel("DJI + Meta glasses clips, or a whole folder — or click to browse")
        sub.setObjectName("DropSub")
        sub.setAlignment(Qt.AlignCenter)

        layout.addWidget(icon)
        layout.addWidget(title)
        layout.addWidget(sub)

    def _set_drag_over(self, on: bool) -> None:
        self.setProperty("dragOver", "true" if on else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self._set_drag_over(True)

    def dragLeaveEvent(self, event) -> None:
        self._set_drag_over(False)

    def dropEvent(self, event) -> None:
        self._set_drag_over(False)
        paths = [
            Path(url.toLocalFile())
            for url in event.mimeData().urls()
            if url.isLocalFile()
        ]
        if paths:
            self.files_dropped.emit(paths)
        event.acceptProposedAction()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.browse_requested.emit()
        super().mouseReleaseEvent(event)


class FileRow(QFrame):
    """One probed media file: name, details, role badge, remove button."""

    removed = Signal(object)  # MediaFile

    def __init__(self, media: MediaFile, parent=None):
        super().__init__(parent)
        self.setObjectName("FileRow")
        self.media = media

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 8, 4, 8)
        layout.setSpacing(10)

        badge = QLabel()
        if media.role == Role.PRIMARY:
            badge.setText("MAIN CAM")
            badge.setObjectName("Badge")
        elif media.role == Role.OVERLAY:
            badge.setText("OVERLAY")
            badge.setObjectName("BadgeOutline")
        else:
            badge.setText("UNKNOWN")
            badge.setObjectName("BadgeDim")
        badge.setToolTip(media.role_reason)

        name = QLabel(media.path.name)
        name.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        secs = float(media.duration)
        mins, s = divmod(int(secs), 60)
        details = QLabel(
            f"{media.display_width}×{media.display_height}  ·  {mins}:{s:02d}"
        )
        details.setObjectName("Hint")

        remove = QPushButton("✕")
        remove.setObjectName("Ghost")
        remove.setFixedWidth(28)
        remove.setToolTip("Remove from this sync")
        remove.clicked.connect(lambda: self.removed.emit(self.media))

        layout.addWidget(badge)
        layout.addWidget(name, stretch=1)
        layout.addWidget(details)
        layout.addWidget(remove)


class Segmented(QWidget):
    """A row of mutually-exclusive option buttons (black & white segments)."""

    changed = Signal(str)

    def __init__(self, options: list[tuple[str, str]], default: str, parent=None):
        """options: list of (value, label)."""
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._value = default

        for value, label in options:
            btn = QPushButton(label)
            btn.setObjectName("Segment")
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setProperty("segValue", value)
            if value == default:
                btn.setChecked(True)
            self._group.addButton(btn)
            layout.addWidget(btn)
        layout.addStretch(1)
        self._group.buttonClicked.connect(self._on_click)

    def _on_click(self, btn) -> None:
        self._value = btn.property("segValue")
        self.changed.emit(self._value)

    def value(self) -> str:
        return self._value


def section_label(text: str) -> QLabel:
    label = QLabel(text.upper())
    label.setObjectName("SectionLabel")
    return label
