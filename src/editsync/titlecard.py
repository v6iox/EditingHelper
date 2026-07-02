"""Rasterize the complete title card (white background + text) to a PNG.

Used by the finished-video renderer, which composites the card as an
image. Two paths:

- Qt (when PySide6 is available, i.e. inside the app): pixel-identical
  to the style previews the user picked from.
- ffmpeg drawtext fallback (CLI without Qt): same layout math, using a
  discovered system font.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from .media import ProbeError, require_tool
from .pngutil import write_solid_png
from .timeline import TitleCard
from .titles import get_style

_FONT_CANDIDATES = {
    "darwin": [
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/Supplemental/Georgia.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    ],
    "win32": [
        r"C:\Windows\Fonts\arialbd.ttf",
        r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\georgia.ttf",
    ],
    "linux": [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ],
}


def _find_font() -> str | None:
    platform = "darwin" if sys.platform == "darwin" else (
        "win32" if sys.platform == "win32" else "linux"
    )
    for candidate in _FONT_CANDIDATES[platform]:
        if os.path.isfile(candidate):
            return candidate
    return None


def render_card_png(card: TitleCard, width: int, height: int, path: Path) -> None:
    """Write the full title card image, preferring the Qt renderer."""
    try:
        _render_with_qt(card, width, height, path)
        return
    except Exception:
        pass
    _render_with_ffmpeg(card, width, height, path)


def _render_with_qt(card: TitleCard, width: int, height: int, path: Path) -> None:
    from PySide6.QtGui import QGuiApplication, QImage, QPainter

    from .gui.title_picker import paint_card

    if QGuiApplication.instance() is None:
        raise RuntimeError("no Qt application; use the ffmpeg fallback")
    image = QImage(width, height, QImage.Format_RGB32)
    painter = QPainter(image)
    paint_card(painter, get_style(card.style), card.title, card.description,
               width, height)
    painter.end()
    if not image.save(str(path)):
        raise RuntimeError("QImage.save failed")


def _escape_drawtext(text: str) -> str:
    out = text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\\\\\'")
    return out.replace("%", "\\%")


def _render_with_ffmpeg(
    card: TitleCard, width: int, height: int, path: Path
) -> None:
    font = _find_font()
    if font is None:
        # no usable font: plain white card is better than failing the render
        write_solid_png(path, width, height)
        return
    style = get_style(card.style)
    factor = height / 1080
    title_text = card.title.upper() if style.title_upper else card.title
    desc_text = card.description.upper() if style.desc_upper else card.description
    title_size = round(style.title_size * factor)
    desc_size = round(style.desc_size * factor)
    gap = round(60 * factor)
    cx = 0.5 + style.position[0]
    cy = 0.5 - style.position[1]
    block = title_size + ((gap + desc_size) if desc_text else 0)
    top = f"{cy:.4f}*h-{block / 2:.1f}"
    if style.alignment == "left":
        x_title = x_desc = f"{cx:.4f}*w-0.18*w"
    else:
        x_title = f"{cx:.4f}*w-text_w/2"
        x_desc = f"{cx:.4f}*w-text_w/2"

    font_arg = font.replace("\\", "/").replace(":", "\\:")
    draw = (
        f"drawtext=fontfile='{font_arg}':text='{_escape_drawtext(title_text)}'"
        f":fontsize={title_size}:fontcolor=black:x={x_title}:y={top}"
    )
    if desc_text:
        draw += (
            f",drawtext=fontfile='{font_arg}':text='{_escape_drawtext(desc_text)}'"
            f":fontsize={desc_size}:fontcolor=0x404040"
            f":x={x_desc}:y={top}+{title_size + gap}"
        )
    ffmpeg = require_tool("ffmpeg")
    proc = subprocess.run(
        [
            ffmpeg, "-y", "-v", "error",
            "-f", "lavfi", "-i", f"color=white:s={width}x{height}:d=1",
            "-vf", draw, "-frames:v", "1", str(path),
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise ProbeError(f"Title card rendering failed: {proc.stderr.strip()}")
