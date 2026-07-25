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

# Node state colours: the liseré around a node says at a glance what is wrong.
STATE_NOMINAL: Final = "#5FB85F"
STATE_STARVED: Final = "#E8912D"
STATE_BLOCKED: Final = "#D9534F"
STATE_IDLE: Final = "#6C757D"

# Canvas. Conveyors and pipes must be told apart without reading the label, so they
# differ in both hue and thickness.
CANVAS_BACKGROUND: Final = "#191C1F"
GRID_LINE: Final = "#23272B"
GRID_LINE_MAJOR: Final = "#2C3136"
BELT_COLOUR: Final = "#B9BFC6"
PIPE_COLOUR: Final = "#4E9FD1"
EDGE_SATURATED: Final = "#D9534F"
EDGE_INVALID: Final = "#D9534F"
EDGE_VALID: Final = "#5FB85F"
SELECTION: Final = ACCENT

BELT_WIDTH: Final = 2.0
PIPE_WIDTH: Final = 4.0

# Canvas grid step, in scene units. Node sizes are multiples of it so that snapped
# nodes line up.
GRID_STEP: Final = 20

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
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    background-color: {SURFACE};
    border: 1px solid {SURFACE_RAISED};
    border-radius: 3px;
    padding: 3px 5px;
}}
QLineEdit:focus, QComboBox:focus {{
    border: 1px solid {ACCENT};
}}
QListView {{
    background-color: {SURFACE};
    border: 1px solid {SURFACE_RAISED};
    border-radius: 3px;
}}
QListView::item {{
    padding: 3px;
}}
QListView::item:selected {{
    background-color: {ACCENT};
    color: {BACKGROUND};
}}
QDockWidget::title {{
    background-color: {SURFACE};
    padding: 5px;
    text-transform: uppercase;
    letter-spacing: 1px;
}}
QToolBar {{
    background-color: {SURFACE};
    border: none;
    spacing: 4px;
}}
QGroupBox {{
    border: 1px solid {SURFACE_RAISED};
    border-radius: 3px;
    margin-top: 8px;
    padding-top: 8px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 8px;
    color: {TEXT_MUTED};
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
