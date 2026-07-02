"""Monochrome theme for the EditSync app.

A strict black-and-white palette: near-black surfaces, white type, gray
hairlines. The single accent is pure white — used for the primary action
button and highlights — so the UI reads as calm and photographic.
"""

BLACK = "#0b0b0c"
SURFACE = "#141416"
SURFACE_2 = "#1c1c1f"
LINE = "#2a2a2e"
LINE_SOFT = "#232326"
WHITE = "#f5f5f5"
GRAY = "#9a9aa0"
GRAY_DIM = "#5c5c63"

STYLESHEET = f"""
* {{
    font-family: "Helvetica Neue", "Segoe UI", "Inter", sans-serif;
    color: {WHITE};
    font-size: 13px;
}}

QMainWindow, QWidget#Root {{
    background: {BLACK};
}}

QLabel {{ background: transparent; }}
QLabel#Title {{
    font-size: 22px;
    font-weight: 700;
    letter-spacing: 2px;
}}
QLabel#Subtitle {{
    color: {GRAY};
    font-size: 13px;
}}
QLabel#SectionLabel {{
    color: {GRAY};
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 1.5px;
}}
QLabel#Hint {{ color: {GRAY_DIM}; font-size: 11px; }}
QLabel#BigStatus {{ font-size: 17px; font-weight: 600; }}

QLabel#DropTitle {{ font-size: 16px; font-weight: 600; }}
QLabel#DropSub {{ color: {GRAY}; }}

QFrame#Card {{
    background: {SURFACE};
    border: 1px solid {LINE_SOFT};
    border-radius: 12px;
}}

QLabel#Badge {{
    background: {WHITE};
    color: {BLACK};
    border-radius: 9px;
    padding: 2px 10px;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 1px;
}}
QLabel#BadgeOutline {{
    background: transparent;
    color: {WHITE};
    border: 1px solid {GRAY_DIM};
    border-radius: 9px;
    padding: 2px 10px;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 1px;
}}
QLabel#BadgeDim {{
    background: transparent;
    color: {GRAY_DIM};
    border: 1px solid {LINE};
    border-radius: 9px;
    padding: 2px 10px;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 1px;
}}

QPushButton {{
    background: {SURFACE_2};
    border: 1px solid {LINE};
    border-radius: 8px;
    padding: 8px 18px;
    font-weight: 600;
}}
QPushButton:hover {{ border-color: {GRAY}; }}
QPushButton:pressed {{ background: {LINE_SOFT}; }}
QPushButton:disabled {{ color: {GRAY_DIM}; border-color: {LINE_SOFT}; }}

QLineEdit {{
    background: {SURFACE_2};
    border: 1px solid {LINE};
    border-radius: 8px;
    padding: 8px 12px;
    selection-background-color: {WHITE};
    selection-color: {BLACK};
}}
QLineEdit:focus {{ border-color: {GRAY}; }}

QCheckBox {{ spacing: 8px; color: {WHITE}; }}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border: 1px solid {GRAY_DIM};
    border-radius: 4px;
    background: {SURFACE_2};
}}
QCheckBox::indicator:checked {{
    background: {WHITE};
    border-color: {WHITE};
    image: url(none);
}}

QSlider::groove:horizontal {{
    height: 3px;
    background: {LINE};
    border-radius: 1px;
}}
QSlider::handle:horizontal {{
    width: 14px;
    height: 14px;
    margin: -6px 0;
    border-radius: 7px;
    background: {WHITE};
}}
QSlider::sub-page:horizontal {{ background: {GRAY}; border-radius: 1px; }}

QProgressBar {{
    background: {SURFACE_2};
    border: none;
    border-radius: 3px;
    height: 6px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{ background: {WHITE}; border-radius: 3px; }}

QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{
    background: transparent;
    width: 8px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {LINE};
    border-radius: 4px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{ background: {GRAY_DIM}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}

QPlainTextEdit, QTextEdit {{
    background: {SURFACE};
    border: 1px solid {LINE_SOFT};
    border-radius: 10px;
    color: {GRAY};
    padding: 8px;
}}

QToolTip {{
    background: {SURFACE_2};
    color: {WHITE};
    border: 1px solid {LINE};
    padding: 4px 8px;
}}
"""
