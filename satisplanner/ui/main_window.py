"""Main window. Phase 0: an empty shell; the canvas and docks arrive in phase 3."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QMainWindow, QVBoxLayout, QWidget

from satisplanner import __version__
from satisplanner.ui.theme import TEXT_MUTED


class MainWindow(QMainWindow):
    """Application shell: title bar, status bar, and a central placeholder."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"SatisPlanner {__version__} — Satisfactory 1.2")
        self.resize(1400, 900)
        self.setCentralWidget(self._build_placeholder())
        self.statusBar().showMessage("Phase 0 — squelette applicatif")

    def _build_placeholder(self) -> QWidget:
        container = QWidget(self)
        layout = QVBoxLayout(container)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title = QLabel("SatisPlanner", container)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 34px; font-weight: 600; letter-spacing: 2px;")

        subtitle = QLabel("Le canvas nodal sera ajoute en phase 3.", container)
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setStyleSheet(f"color: {TEXT_MUTED};")

        layout.addWidget(title)
        layout.addWidget(subtitle)
        return container
