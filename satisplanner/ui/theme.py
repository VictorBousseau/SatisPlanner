"""Dark theme with an industrial orange accent.

No game logo, trademark or branded asset is reproduced here: colours only.
"""

from typing import Final

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

ACCENT: Final = "#F0A04B"
BACKGROUND: Final = "#1E2124"
SURFACE: Final = "#282C31"
SURFACE_RAISED: Final = "#32373D"
TEXT: Final = "#E6E6E6"
TEXT_MUTED: Final = "#9AA0A6"

# Node state colours, used by the canvas from phase 3 on.
STATE_NOMINAL: Final = "#5FB85F"
STATE_STARVED: Final = "#E8912D"
STATE_BLOCKED: Final = "#D9534F"

_STYLESHEET: Final = f"""
QWidget {{
    background-color: {BACKGROUND};
    color: {TEXT};
}}
QMainWindow::separator {{
    background-color: {SURFACE_RAISED};
    width: 2px;
    height: 2px;
}}
QStatusBar {{
    background-color: {SURFACE};
    color: {TEXT_MUTED};
}}
QToolTip {{
    background-color: {SURFACE_RAISED};
    color: {TEXT};
    border: 1px solid {ACCENT};
}}
"""


def apply_theme(app: QApplication) -> None:
    """Apply the palette, base font and stylesheet to the whole application."""
    app.setStyle("Fusion")

    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(BACKGROUND))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.Base, QColor(SURFACE))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(SURFACE_RAISED))
    palette.setColor(QPalette.ColorRole.Text, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.Button, QColor(SURFACE))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(ACCENT))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(BACKGROUND))
    palette.setColor(QPalette.ColorRole.Link, QColor(ACCENT))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(SURFACE_RAISED))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(TEXT_MUTED))
    app.setPalette(palette)

    # A condensed sans-serif when available, with graceful fallbacks.
    font = QFont()
    font.setFamilies(["Roboto Condensed", "Segoe UI Semibold", "Segoe UI", "Arial"])
    font.setPointSize(10)
    font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
    app.setFont(font)

    app.setStyleSheet(_STYLESHEET)

    # Crisp lines on high-DPI displays for the canvas that lands in phase 3.
    app.setAttribute(Qt.ApplicationAttribute.AA_DontCreateNativeWidgetSiblings, True)
