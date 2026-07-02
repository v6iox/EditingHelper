"""Reusable widgets for the EditSync app.

Qt stylesheets can't animate, so the interactive controls here are
custom-painted: each drives a 0-1 hover progress with a QVariantAnimation
and interpolates colors (and glows) in paintEvent. Everything stays inside
the monochrome theme; motion is the affordance, not color.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import (
    QEasingCurve,
    QRectF,
    QSize,
    Qt,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QButtonGroup,
    QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..media import MediaFile, Role
from . import style


def mix(a: str | QColor, b: str | QColor, t: float) -> QColor:
    """Linear blend between two colors, t in 0..1."""
    ca, cb = QColor(a), QColor(b)
    return QColor(
        round(ca.red() + (cb.red() - ca.red()) * t),
        round(ca.green() + (cb.green() - ca.green()) * t),
        round(ca.blue() + (cb.blue() - ca.blue()) * t),
        round(ca.alpha() + (cb.alpha() - ca.alpha()) * t),
    )


class _HoverAnimation(QVariantAnimation):
    """Drives a widget's hover progress and repaints it each frame."""

    def __init__(self, widget: QWidget, duration: int = 160):
        super().__init__(widget)
        self._widget = widget
        self.setDuration(duration)
        self.setEasingCurve(QEasingCurve.OutCubic)
        self.progress = 0.0
        self.valueChanged.connect(self._apply)

    def _apply(self, value) -> None:
        self.progress = float(value)
        self._widget.update()

    def go(self, target: float) -> None:
        self.stop()
        self.setStartValue(self.progress)
        self.setEndValue(target)
        self.start()


class AnimatedButton(QPushButton):
    """Custom-painted button with animated hover states.

    kinds: "default" (dark, border brightens), "primary" (white, glow),
    "ghost" (text-only, brightens), "segment" (checkable pill).
    """

    PADDING = {
        "default": (18, 9),
        "primary": (28, 13),
        "ghost": (8, 4),
        "segment": (15, 8),
    }

    def __init__(self, text: str = "", kind: str = "default", parent=None):
        super().__init__(text, parent)
        self.kind = kind
        self._hover = _HoverAnimation(self)
        self._press = _HoverAnimation(self, duration=90)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover, True)
        font = self.font()
        font.setBold(True)
        if kind == "primary":
            font.setPointSizeF(font.pointSizeF() + 1)
        self.setFont(font)

        self._glow: QGraphicsDropShadowEffect | None = None
        if kind == "primary":
            self._glow = QGraphicsDropShadowEffect(self)
            self._glow.setColor(QColor(245, 245, 245, 0))
            self._glow.setOffset(0, 0)
            self._glow.setBlurRadius(0)
            self.setGraphicsEffect(self._glow)
            # drive the glow from the animation, never from paintEvent
            # (effect setters schedule repaints and would loop)
            self._hover.valueChanged.connect(self._update_glow)

        self.pressed.connect(lambda: self._press.go(1.0))
        self.released.connect(lambda: self._press.go(0.0))
        self.toggled.connect(lambda _checked: self.update())

    def _update_glow(self, value) -> None:
        if self._glow is not None:
            h = float(value) if self.isEnabled() else 0.0
            self._glow.setBlurRadius(26 * h)
            self._glow.setColor(QColor(245, 245, 245, int(70 * h)))

    def enterEvent(self, event) -> None:
        if self.isEnabled():
            self._hover.go(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover.go(0.0)
        super().leaveEvent(event)

    def sizeHint(self) -> QSize:
        pad_x, pad_y = self.PADDING[self.kind]
        text_size = self.fontMetrics().size(Qt.TextShowMnemonic, self.text())
        return QSize(text_size.width() + 2 * pad_x, text_size.height() + 2 * pad_y)

    def paintEvent(self, event) -> None:
        h = self._hover.progress if self.isEnabled() else 0.0
        press = self._press.progress
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = 10 if self.kind == "primary" else 8

        if self.kind == "primary":
            bg = mix("#e8e8e8" if press else style.WHITE, "#ffffff", h)
            if not self.isEnabled():
                bg = QColor(style.LINE)
            painter.setPen(Qt.NoPen)
            painter.setBrush(bg)
            painter.drawRoundedRect(rect, radius, radius)
            text_color = QColor(style.BLACK) if self.isEnabled() else QColor(style.GRAY_DIM)
        elif self.kind == "segment":
            if self.isChecked():
                painter.setPen(Qt.NoPen)
                painter.setBrush(mix(style.WHITE, "#ffffff", h))
                painter.drawRoundedRect(rect, radius, radius)
                text_color = QColor(style.BLACK)
            else:
                painter.setBrush(
                    mix("#00000000", style.SURFACE_2, h)
                )
                painter.setPen(QPen(mix(style.LINE, style.GRAY, h), 1))
                painter.drawRoundedRect(rect, radius, radius)
                text_color = mix(style.GRAY, style.WHITE, h)
            if not self.isEnabled():
                text_color = QColor(style.GRAY_DIM)
        elif self.kind == "ghost":
            painter.setPen(Qt.NoPen)
            text_color = mix(style.GRAY, style.WHITE, h)
            if not self.isEnabled():
                text_color = QColor(style.GRAY_DIM)
        else:  # default
            bg = mix(style.SURFACE_2, style.LINE_SOFT, max(h * 0.6, press))
            painter.setBrush(bg)
            painter.setPen(QPen(mix(style.LINE, style.GRAY, h), 1))
            painter.drawRoundedRect(rect, radius, radius)
            text_color = (
                QColor(style.WHITE) if self.isEnabled() else QColor(style.GRAY_DIM)
            )

        painter.setPen(text_color)
        painter.setFont(self.font())
        painter.drawText(self.rect(), Qt.AlignCenter, self.text())
        painter.end()


class DropZone(QWidget):
    """Drag-and-drop target with animated hover border and marching ants
    while a drag is over it. Clicking opens a file dialog."""

    files_dropped = Signal(list)  # list[Path]
    browse_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(180)
        self._hover = _HoverAnimation(self, duration=200)
        self._drag = _HoverAnimation(self, duration=150)

        self._ants = QVariantAnimation(self)
        self._ants.setStartValue(0.0)
        self._ants.setEndValue(10.0)
        self._ants.setDuration(500)
        self._ants.setLoopCount(-1)
        self._ants.valueChanged.connect(lambda _v: self.update())

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        layout.setSpacing(6)
        icon = QLabel("↓")
        icon.setAlignment(Qt.AlignCenter)
        icon.setStyleSheet(f"font-size: 30px; color: {style.GRAY};")
        title = QLabel("Drop your footage here")
        title.setObjectName("DropTitle")
        title.setAlignment(Qt.AlignCenter)
        sub = QLabel("DJI + Meta glasses clips, or a whole folder — or click to browse")
        sub.setObjectName("DropSub")
        sub.setAlignment(Qt.AlignCenter)
        layout.addWidget(icon)
        layout.addWidget(title)
        layout.addWidget(sub)

    def paintEvent(self, event) -> None:
        h, d = self._hover.progress, self._drag.progress
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)

        bg = mix(style.SURFACE, style.SURFACE_2, max(h, d))
        painter.setPen(Qt.NoPen)
        painter.setBrush(bg)
        painter.drawRoundedRect(rect, 14, 14)

        pen = QPen(mix(mix(style.GRAY_DIM, style.GRAY, h * 0.7), style.WHITE, d), 1.2)
        pen.setDashPattern([5, 4])
        offset = self._ants.currentValue() or 0.0
        pen.setDashOffset(-offset)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(pen)
        painter.drawRoundedRect(rect, 14, 14)
        painter.end()

    def enterEvent(self, event) -> None:
        self._hover.go(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover.go(0.0)
        super().leaveEvent(event)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self._drag.go(1.0)
            self._ants.start()

    def dragLeaveEvent(self, event) -> None:
        self._drag.go(0.0)
        self._ants.stop()
        self.update()

    def dropEvent(self, event) -> None:
        self._drag.go(0.0)
        self._ants.stop()
        paths = [
            Path(url.toLocalFile())
            for url in event.mimeData().urls()
            if url.isLocalFile()
        ]
        if paths:
            self.files_dropped.emit(paths)
        event.acceptProposedAction()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.browse_requested.emit()
        super().mouseReleaseEvent(event)


class FileRow(QWidget):
    """One probed media file. The row highlights on hover and the remove
    button fades in only when the pointer is over the row."""

    removed = Signal(object)  # MediaFile

    def __init__(self, media: MediaFile, parent=None):
        super().__init__(parent)
        self.media = media
        self._hover = _HoverAnimation(self)
        self.setAttribute(Qt.WA_Hover, True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 6, 8)
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

        self.remove_btn = AnimatedButton("✕", kind="ghost")
        self.remove_btn.setFixedWidth(28)
        self.remove_btn.setToolTip("Remove from this sync")
        self.remove_btn.clicked.connect(lambda: self.removed.emit(self.media))
        self._remove_opacity = QGraphicsOpacityEffect(self.remove_btn)
        self._remove_opacity.setOpacity(0.0)
        self.remove_btn.setGraphicsEffect(self._remove_opacity)
        self._hover.valueChanged.connect(
            lambda v: self._remove_opacity.setOpacity(float(v))
        )

        layout.addWidget(badge)
        layout.addWidget(name, stretch=1)
        layout.addWidget(details)
        layout.addWidget(self.remove_btn)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        h = self._hover.progress
        if h > 0:
            painter.setPen(Qt.NoPen)
            bg = QColor(style.SURFACE_2)
            bg.setAlphaF(0.9 * h)
            painter.setBrush(bg)
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(0, 0, 0, -1), 6, 6)
        painter.setPen(QColor(style.LINE_SOFT))
        painter.drawLine(
            self.rect().bottomLeft(), self.rect().bottomRight()
        )
        painter.end()

    def enterEvent(self, event) -> None:
        self._hover.go(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover.go(0.0)
        super().leaveEvent(event)


class Segmented(QWidget):
    """A row of mutually-exclusive animated option buttons."""

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
            btn = AnimatedButton(label, kind="segment")
            btn.setCheckable(True)
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

    def set_value(self, value: str) -> None:
        for btn in self._group.buttons():
            if btn.property("segValue") == value:
                btn.setChecked(True)
                self._value = value
                self.changed.emit(value)
                return


def section_label(text: str) -> QLabel:
    label = QLabel(text.upper())
    label.setObjectName("SectionLabel")
    return label
