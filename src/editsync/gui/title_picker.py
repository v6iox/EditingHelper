"""Title-style picker: clickable preview cards, one per arrangement.

Each card paints a live miniature of the title card — white background,
the user's actual title/description text laid out in that style — so
picking a style is entirely visual. Previews re-render as the user
types.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ..titles import STYLES, TitleStyle
from . import style as theme
from .widgets import _HoverAnimation, mix

PREVIEW_W, PREVIEW_H = 148, 84  # 16:9-ish miniature of the card
REF_H = 1080  # style sizes are defined at this height


def paint_card(
    painter: QPainter,
    style: TitleStyle,
    title: str,
    description: str,
    w: int,
    h: int,
) -> None:
    """Paint the title card (white background + laid-out text) at any size.

    Shared by the miniature previews and the full-resolution card the
    finished-video renderer composites, so they always match.
    """
    painter.fillRect(0, 0, w, h, QColor("#ffffff"))
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setRenderHint(QPainter.TextAntialiasing)

    factor = h / REF_H  # map 1080p-reference sizes onto this canvas
    title_text = title.upper() if style.title_upper else title
    desc_text = description.upper() if style.desc_upper else description

    title_font = QFont(style.title_font)
    title_font.setPixelSize(max(4, round(style.title_size * factor)))
    title_font.setBold(style.title_bold)
    desc_font = QFont(style.desc_font)
    desc_font.setPixelSize(max(3, round(style.desc_size * factor)))

    tm, dm = QFontMetricsF(title_font), QFontMetricsF(desc_font)
    gap = 60 * factor  # spacing between the two lines, at 1080p reference
    block_h = tm.height() + (dm.height() + gap if desc_text else 0)
    # style position: fractions of the frame from center, +y up
    cx = w / 2 + style.position[0] * w
    cy = h / 2 - style.position[1] * h
    top = cy - block_h / 2

    def draw(text: str, font: QFont, color: QColor, y: float, metrics) -> None:
        painter.setFont(font)
        painter.setPen(color)
        if style.alignment == "left":
            x = cx - w * 0.18
        else:
            x = cx - metrics.horizontalAdvance(text) / 2
        painter.drawText(QRectF(x, y, w, metrics.height() * 1.4),
                         Qt.AlignLeft | Qt.AlignTop, text)

    draw(title_text, title_font, QColor("#0b0b0c"), top, tm)
    if desc_text:
        rgb = [round(float(v) * 255) for v in style.desc_color.split()[:3]]
        draw(desc_text, desc_font, QColor(*rgb), top + tm.height() + gap, dm)


def render_preview(
    style: TitleStyle, title: str, description: str, scale: float = 2.0
) -> QPixmap:
    """Paint a miniature of the title card in the given style.

    Rendered at 2x and marked high-DPI so it stays crisp on retina.
    """
    w, h = round(PREVIEW_W * scale), round(PREVIEW_H * scale)
    pixmap = QPixmap(w, h)
    painter = QPainter(pixmap)
    paint_card(painter, style, title, description, w, h)
    painter.end()
    pixmap.setDevicePixelRatio(scale)
    return pixmap


class _StyleCard(QWidget):
    clicked = Signal(str)

    def __init__(self, style: TitleStyle, parent=None):
        super().__init__(parent)
        self.style_def = style
        self.selected = False
        self._hover = _HoverAnimation(self)
        self.setCursor(Qt.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 4)
        layout.setSpacing(5)
        self.preview = QLabel()
        self.preview.setFixedSize(PREVIEW_W, PREVIEW_H)
        self.preview.setScaledContents(True)
        caption = QLabel(style.label)
        caption.setObjectName("Hint")
        caption.setAlignment(Qt.AlignHCenter)
        layout.addWidget(self.preview)
        layout.addWidget(caption)

    def set_preview(self, title: str, description: str) -> None:
        self.preview.setPixmap(render_preview(self.style_def, title, description))

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if self.selected:
            pen = QPen(QColor(theme.WHITE), 2)
        else:
            pen = QPen(mix(theme.LINE, theme.GRAY, self._hover.progress), 1)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(rect, 8, 8)
        painter.end()

    def enterEvent(self, event) -> None:
        self._hover.go(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover.go(0.0)
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        # only a release inside the card counts, so dragging off cancels
        if event.button() == Qt.LeftButton and self.rect().contains(
            event.position().toPoint()
        ):
            self.clicked.emit(self.style_def.key)
        super().mouseReleaseEvent(event)


class TitleStylePicker(QWidget):
    """Row of style cards; exposes value()/set_value like Segmented."""

    changed = Signal(str)

    def __init__(self, default: str = "classic", parent=None):
        super().__init__(parent)
        self._value = default
        self._sample = ("Front Bumper Removal", "2024 Toyota GR86")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self._cards: dict[str, _StyleCard] = {}
        for style in STYLES.values():
            card = _StyleCard(style)
            card.clicked.connect(self._on_click)
            self._cards[style.key] = card
            layout.addWidget(card)
        layout.addStretch(1)
        self._apply_selection()
        self.update_sample(*self._sample)

    def update_sample(self, title: str, description: str) -> None:
        """Re-render every preview with the user's actual text."""
        self._sample = (
            title.strip() or "Front Bumper Removal",
            description.strip() or "2024 Toyota GR86",
        )
        for card in self._cards.values():
            card.set_preview(*self._sample)

    def _apply_selection(self) -> None:
        for key, card in self._cards.items():
            card.selected = key == self._value
            card.update()

    def _on_click(self, key: str) -> None:
        if key != self._value:
            self._value = key
            self._apply_selection()
            self.changed.emit(key)

    def value(self) -> str:
        return self._value

    def set_value(self, value: str) -> None:
        if value in self._cards:
            self._value = value
            self._apply_selection()
