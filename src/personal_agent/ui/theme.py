"""One visual system for every window: colour tokens (light and dark), fonts and the global stylesheet.

Design: a quiet instrument panel. Cool slate surfaces, ink text, one deep-teal accent for the primary action and focus.
The only loud colour is the risk scale (low, medium, high, critical); it means the same thing on approval cards, diffs,
the speech bubble and the screen-watching badge. Widgets read tokens through `tok()`; nothing else hard-codes a colour.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

LIGHT = {
    "bg": "#ECEFF3", "surface": "#FFFFFF", "sunken": "#F5F7F9", "ink": "#18202E", "muted": "#586377", "line": "#D2D9E1",
    "accent": "#0F6B63", "accent_hover": "#0B554F", "on_accent": "#FFFFFF", "accent_soft": "#DCEEEC",
    "low": "#2C7A4B", "medium": "#9A6A0B", "high": "#BF4E1B", "critical": "#B1232B",
    "add": "#1F7A45", "del": "#B1232B", "hunk": "#2B5CA8",
}
DARK = {
    "bg": "#111519", "surface": "#1A2027", "sunken": "#151A20", "ink": "#E5E9EF", "muted": "#8E9BAE", "line": "#2B3440",
    "accent": "#4FC2B3", "accent_hover": "#73D3C6", "on_accent": "#0B1A18", "accent_soft": "#17332F",
    "low": "#58C282", "medium": "#E0B04A", "high": "#F08A56", "critical": "#F2777D",
    "add": "#58C282", "del": "#F2777D", "hunk": "#7FA6E8",
}
# Avatar and overlays float over any desktop, so they use fixed mid-tone colours instead of the window palette.
STATE = {"idle": "#5B8DEF", "listening": "#3FB7C9", "thinking": "#9B7FE0", "working": "#E3A52F", "speaking": "#4DB87A",
         "awaiting_approval": "#E8742F", "success": "#2FB59A", "error": "#E0525B", "sleeping": "#7C879B"}
AVATAR_INK = "#1B2230"

_cur: dict[str, str] = LIGHT
HEAD_FONT = '"Bahnschrift", "Segoe UI Variable Display", "Segoe UI"'
BODY_FONT = '"Segoe UI Variable Text", "Segoe UI"'


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
QLabel[role="chip"] {{ color: {t['accent']}; }}
QLabel[role="warn"] {{ color: {t['high']}; }}
QLabel[role="error"] {{ color: {t['critical']}; }}

QPushButton {{ background: {t['surface']}; border: 1px solid {t['line']}; border-radius: 6px; padding: 6px 14px; min-height: 18px; }}
QPushButton:hover {{ border-color: {t['muted']}; }}
QPushButton:pressed {{ background: {t['sunken']}; }}
QPushButton:checked {{ background: {t['accent_soft']}; border-color: {t['accent']}; color: {t['accent']}; }}
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

QWidget#QuickInput {{ background: {t['surface']}; border: 1px solid {t['line']}; border-radius: 10px; }}
QWidget#QuickInput QLabel {{ color: {t['muted']}; }}
QLabel#Bubble {{ background: {t['surface']}; color: {t['ink']}; border: 1px solid {t['line']}; border-radius: 12px; padding: 9px 12px; }}
"""


def apply(app: QApplication) -> None:
    """Install the palette, fonts and stylesheet. Call once, right after creating the QApplication."""
    global _cur
    _cur = DARK if is_dark(app) else LIGHT
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
