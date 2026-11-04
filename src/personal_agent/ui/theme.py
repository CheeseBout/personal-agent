"""One visual system for every window: colour tokens (light and dark), fonts and the global stylesheet.

Design: a quiet instrument panel. Neutral surfaces (cool fog in light, true black in dark), ink text and ONE highlight colour
the user picks in Settings (default orange): the logo, badges, primary buttons, selection and focus all follow it.
The risk scale (low, medium, high, critical) and the avatar state colours keep their own fixed meaning on approval cards, diffs,
the speech bubble and the screen-watching badge. Widgets read tokens through `tok()`; nothing else hard-codes a colour.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

DEFAULT_HIGHLIGHT = "#E8742F"
LIGHT = {
    "bg": "#E3E8EC", "surface": "#F7F9FA", "sunken": "#EDF1F3", "ink": "#101A33", "muted": "#566280", "line": "#C9D1D9",
    "low": "#1F7A4D", "medium": "#8F6208", "high": "#B94A14", "critical": "#B1232B",
    "add": "#1F7A4D", "del": "#B1232B", "hunk": "#2B5CA8",
}
DARK = {
    "bg": "#0A0A0A", "surface": "#151515", "sunken": "#0F0F0F", "ink": "#ECECEC", "muted": "#9B9B9B", "line": "#2A2A2A",
    "low": "#58C282", "medium": "#E0B04A", "high": "#F08A56", "critical": "#F2777D",
    "add": "#58C282", "del": "#F2777D", "hunk": "#7FA6E8",
}
# Avatar and overlays float over any desktop, so they use fixed mid-tone colours instead of the window palette.
STATE = {"idle": "#5B8DEF", "listening": "#3FB7C9", "thinking": "#9B7FE0", "working": "#E3A52F", "speaking": "#4DB87A",
         "awaiting_approval": "#E8742F", "success": "#2FB59A", "error": "#E0525B", "sleeping": "#7C879B"}
AVATAR_INK = "#1B2230"

HEAD_FONT = '"Bahnschrift", "Segoe UI Variable Display", "Segoe UI"'
BODY_FONT = '"Be Vietnam Pro", "Segoe UI Variable Text", "Segoe UI"'


def _rgb(h: str) -> tuple[int, int, int]:
    return int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)


def mix(a: str, b: str, t: float) -> str:
    """`t` of colour `a` over `b` (both #rrggbb)."""
    r, g, bl = (round(x * t + y * (1 - t)) for x, y in zip(_rgb(a), _rgb(b), strict=True))
    return f"#{r:02X}{g:02X}{bl:02X}"


def _lum(h: str) -> float:
    c = [v / 255 for v in _rgb(h)]
    c = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def on_color(h: str) -> str:
    """Text colour (near-black or white) that contrasts more with `h`."""
    return "#111111" if (_lum(h) + 0.05) / 0.05 > 1.05 / (_lum(h) + 0.05) else "#FFFFFF"


def tokens(dark: bool, highlight: str = DEFAULT_HIGHLIGHT) -> dict[str, str]:
    """The base palette plus every accent token derived from the user's highlight colour."""
    t = dict(DARK if dark else LIGHT)
    t.update(accent=highlight, accent_hover=mix(highlight, "#000000", 0.82), on_accent=on_color(highlight),
             accent_soft=mix(highlight, t["surface"], 0.2 if dark else 0.16), accent_text=mix(highlight, t["ink"], 0.6))
    return t


_cur: dict[str, str] = tokens(False)
_hl = DEFAULT_HIGHLIGHT


def tok(name: str) -> str:
    return _cur[name]


def risk_color(risk: str) -> str:
    return _cur.get(risk, _cur["muted"]) if risk in ("low", "medium", "high", "critical") else _cur["muted"]


def is_dark(app: QApplication) -> bool:
    return app.styleHints().colorScheme() == Qt.ColorScheme.Dark


def mono(size: float = 9.5) -> QFont:
    f = QFont("Cascadia Mono", int(size))
    f.setFamilies(["Cascadia Mono", "Consolas"])
    f.setStyleHint(QFont.StyleHint.Monospace)
    return f


def stylesheet(t: dict[str, str]) -> str:
    return f"""
* {{ font-family: {BODY_FONT}; font-size: 10pt; }}
QWidget {{ background: {t['bg']}; color: {t['ink']}; }}
QToolTip {{ background: {t['ink']}; color: {t['surface']}; border: none; padding: 5px 8px; }}
QLabel {{ background: transparent; }}
QLabel[role="title"] {{ font-family: {HEAD_FONT}; font-size: 12pt; font-weight: 600; }}
QLabel[role="chip"] {{ color: {t['accent_text']}; }}
QLabel[role="warn"] {{ color: {t['high']}; }}
QLabel[role="error"] {{ color: {t['critical']}; }}

QPushButton {{ background: {t['surface']}; border: 1px solid {t['line']}; border-radius: 6px; padding: 6px 14px; min-height: 18px; }}
QPushButton:hover {{ border-color: {t['muted']}; }}
QPushButton:pressed {{ background: {t['sunken']}; }}
QPushButton:checked {{ background: {t['accent_soft']}; border-color: {t['accent']}; color: {t['accent_text']}; }}
QPushButton:disabled {{ color: {t['muted']}; background: {t['sunken']}; border-color: {t['line']}; }}
QPushButton:focus {{ border: 2px solid {t['accent']}; padding: 5px 13px; }}
QPushButton[role="primary"] {{ background: {t['accent']}; color: {t['on_accent']}; border-color: {t['accent']}; font-weight: 600; }}
QPushButton[role="primary"]:hover {{ background: {t['accent_hover']}; border-color: {t['accent_hover']}; }}
QPushButton[role="primary"]:disabled {{ background: {t['sunken']}; color: {t['muted']}; border-color: {t['line']}; }}
QPushButton[role="danger"] {{ color: {t['critical']}; border-color: {t['critical']}; }}

QLineEdit, QPlainTextEdit, QTextBrowser, QTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    background: {t['surface']}; border: 1px solid {t['line']}; border-radius: 6px; padding: 5px 8px;
    selection-background-color: {t['accent']}; selection-color: {t['on_accent']}; }}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus {{ border: 2px solid {t['accent']}; padding: 4px 7px; }}
QTextBrowser {{ padding: 14px 18px; border-radius: 8px; }}
QComboBox QAbstractItemView {{ background: {t['surface']}; border: 1px solid {t['line']}; selection-background-color: {t['accent_soft']}; selection-color: {t['ink']}; }}

QListWidget, QTableWidget {{ background: {t['surface']}; border: 1px solid {t['line']}; border-radius: 8px; gridline-color: {t['line']};
    alternate-background-color: {t['sunken']}; outline: 0; }}
QListWidget::item {{ padding: 4px 6px; }}
QListWidget::item:selected, QTableWidget::item:selected {{ background: {t['accent_soft']}; color: {t['ink']}; }}
QHeaderView::section {{ background: {t['sunken']}; color: {t['muted']}; border: none; border-bottom: 1px solid {t['line']};
    padding: 6px 8px; font-family: {HEAD_FONT}; font-weight: 600; }}
QTableCornerButton::section {{ background: {t['sunken']}; border: none; }}

QTabWidget::pane {{ border: none; border-top: 1px solid {t['line']}; }}
QTabBar::tab {{ background: transparent; color: {t['muted']}; padding: 9px 16px; border: none; border-bottom: 2px solid transparent;
    font-family: {HEAD_FONT}; font-weight: 600; }}
QTabBar::tab:hover {{ color: {t['ink']}; }}
QTabBar::tab:selected {{ color: {t['ink']}; border-bottom: 2px solid {t['accent']}; }}

QGroupBox {{ background: {t['surface']}; border: 1px solid {t['line']}; border-radius: 8px; margin-top: 4px; padding: 12px; }}
QGroupBox[risk="low"] {{ border-left: 5px solid {t['low']}; }}
QGroupBox[risk="medium"] {{ border-left: 5px solid {t['medium']}; }}
QGroupBox[risk="high"] {{ border-left: 5px solid {t['high']}; }}
QGroupBox[risk="critical"] {{ border-left: 5px solid {t['critical']}; }}
QFrame[role="thread"] {{ background: {t['bg']}; border: none; }}

QRadioButton, QCheckBox {{ background: transparent; spacing: 8px; padding: 3px 0; }}
QRadioButton::indicator, QCheckBox::indicator {{ width: 14px; height: 14px; background: {t['surface']}; border: 1.5px solid {t['muted']}; }}
QRadioButton::indicator {{ border-radius: 8px; }}
QCheckBox::indicator {{ border-radius: 4px; }}
QRadioButton::indicator:checked, QCheckBox::indicator:checked {{ background: {t['accent']}; border-color: {t['accent']}; }}
QRadioButton::indicator:focus, QCheckBox::indicator:focus {{ border-color: {t['accent']}; }}

QProgressBar {{ background: {t['sunken']}; border: 1px solid {t['line']}; border-radius: 5px; text-align: center; min-height: 16px; }}
QProgressBar::chunk {{ background: {t['accent']}; border-radius: 4px; }}

QSplitter::handle {{ background: {t['bg']}; width: 8px; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {t['line']}; border-radius: 5px; min-height: 28px; }}
QScrollBar::handle:vertical:hover {{ background: {t['muted']}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {t['line']}; border-radius: 5px; min-width: 28px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QMenu {{ background: {t['surface']}; border: 1px solid {t['line']}; padding: 4px; }}
QMenu::item {{ padding: 6px 22px; border-radius: 4px; }}
QMenu::item:selected {{ background: {t['accent_soft']}; }}
QMenu::separator {{ height: 1px; background: {t['line']}; margin: 4px 8px; }}

QFrame#NavRail {{ background: {t['surface']}; border: none; border-right: 1px solid {t['line']}; }}
QFrame#NavRail QLabel {{ background: transparent; }}
QPushButton[role="nav"] {{ background: transparent; border: none; border-left: 3px solid transparent; border-radius: 6px; color: {t['muted']};
    text-align: left; padding: 8px 12px; font-weight: 500; }}
QPushButton[role="nav"]:hover {{ background: {t['sunken']}; color: {t['ink']}; }}
QPushButton[role="nav"][current="true"] {{ background: {t['accent_soft']}; border-left: 3px solid {t['accent']}; color: {t['ink']}; font-weight: 600; }}
QFrame#NavSep {{ background: {t['line']}; border: none; max-height: 1px; min-height: 1px; }}
QFrame#TopBar {{ background: {t['bg']}; border: none; border-bottom: 1px solid {t['line']}; }}
QToolButton#Burger {{ background: transparent; border: none; border-radius: 6px; padding: 6px; }}
QToolButton#Burger:hover {{ background: {t['sunken']}; }}
QToolButton#Burger:focus {{ border: 2px solid {t['accent']}; }}
QFrame#NavRail QLabel#NavBadge {{ background: {t['accent']}; color: {t['on_accent']}; border-radius: 9px; padding: 0 6px; font-size: 8pt; font-weight: 700; }}
QPushButton[role="swatch"] {{ border: 2px solid {t['surface']}; border-radius: 13px; min-width: 26px; max-width: 26px; min-height: 26px; max-height: 26px; padding: 0; }}
QPushButton[role="swatch"]:checked {{ border: 2px solid {t['ink']}; }}

QFrame#Paused {{ background: {t['sunken']}; border: none; border-left: 5px solid {t['critical']}; border-radius: 8px; }}
QFrame#Paused QLabel {{ color: {t['critical']}; font-weight: 600; }}
QWidget#QuickInput {{ background: transparent; }}
QWidget#QuickInput QLabel {{ color: {t['muted']}; }}
QLabel#Bubble {{ background: {t['surface']}; color: {t['ink']}; border: 1px solid {t['line']}; border-radius: 12px; padding: 9px 12px; }}
"""


def highlight() -> str:
    return _hl


def apply(app: QApplication, highlight: str | None = None) -> None:
    """Install the palette, fonts and stylesheet. Call right after creating the QApplication (again to change the highlight)."""
    global _cur, _hl
    _hl = highlight or _hl
    _cur = tokens(is_dark(app), _hl)
    if app.style().objectName().lower() != "fusion":  # swapping the style object again under live widgets crashes Qt
        app.setStyle("Fusion")
    pal = QPalette()
    for role, key in ((QPalette.ColorRole.Window, "bg"), (QPalette.ColorRole.Base, "surface"), (QPalette.ColorRole.AlternateBase, "sunken"),
                      (QPalette.ColorRole.WindowText, "ink"), (QPalette.ColorRole.Text, "ink"), (QPalette.ColorRole.ButtonText, "ink"),
                      (QPalette.ColorRole.Button, "surface"), (QPalette.ColorRole.Highlight, "accent"),
                      (QPalette.ColorRole.HighlightedText, "on_accent"), (QPalette.ColorRole.PlaceholderText, "muted"),
                      (QPalette.ColorRole.ToolTipBase, "ink"), (QPalette.ColorRole.ToolTipText, "surface")):
        pal.setColor(role, QColor(_cur[key]))
    app.setPalette(pal)
    app.setStyleSheet(stylesheet(_cur))


def set_highlight(app: QApplication, color: str) -> None:
    """Re-skin the running app with a new highlight colour (#rrggbb). Widgets that cached a colour update when rebuilt."""
    if color.lower() != _hl.lower():
        apply(app, color)
