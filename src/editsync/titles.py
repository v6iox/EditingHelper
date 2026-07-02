"""Title-card style presets.

Each style describes one arrangement of the title + description on the
white card. The same definition drives both the GUI's live previews and
the FCPXML text emission, so what you pick is what Final Cut shows.

Sizes are given at 1080p reference height; the exporter scales them to
the sequence resolution. Positions are fractions of the frame relative
to center (FCP inspector convention: +y is up).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TitleStyle:
    key: str
    label: str
    title_font: str
    title_size: int  # at 1080p reference
    title_bold: bool
    title_upper: bool
    desc_font: str
    desc_size: int
    desc_upper: bool
    alignment: str  # "center" | "left"
    position: tuple[float, float]  # (x, y) as frame fractions from center
    desc_color: str = "0.25 0.25 0.25 1"


STYLES: dict[str, TitleStyle] = {
    style.key: style
    for style in (
        TitleStyle(
            key="classic",
            label="Classic",
            title_font="Helvetica Neue",
            title_size=92,
            title_bold=True,
            title_upper=False,
            desc_font="Helvetica Neue",
            desc_size=48,
            desc_upper=False,
            alignment="center",
            position=(0.0, 0.02),
        ),
        TitleStyle(
            key="lower-left",
            label="Lower left",
            title_font="Helvetica Neue",
            title_size=84,
            title_bold=True,
            title_upper=False,
            desc_font="Helvetica Neue",
            desc_size=44,
            desc_upper=False,
            alignment="left",
            position=(-0.24, -0.24),
        ),
        TitleStyle(
            key="statement",
            label="Statement",
            title_font="Helvetica Neue",
            title_size=118,
            title_bold=True,
            title_upper=True,
            desc_font="Helvetica Neue",
            desc_size=40,
            desc_upper=True,
            alignment="center",
            position=(0.0, 0.04),
        ),
        TitleStyle(
            key="elegant",
            label="Elegant",
            title_font="Georgia",
            title_size=90,
            title_bold=False,
            title_upper=False,
            desc_font="Helvetica Neue",
            desc_size=42,
            desc_upper=True,
            alignment="center",
            position=(0.0, 0.02),
            desc_color="0.4 0.4 0.4 1",
        ),
    )
}

DEFAULT_STYLE = "classic"


def get_style(key: str) -> TitleStyle:
    return STYLES.get(key, STYLES[DEFAULT_STYLE])
