"""Main window: the palette on the left, the canvas in the middle.

The side docks of phase 4 -- the synchronised table, the totals, the diagnostics --
have their places reserved here and nothing more: an empty dock that says what is
coming is honest, a dock full of placeholder numbers would not be.

The catalogue is read once from the database embedded in the package. There is no
setup step and no path to point at: the application is autonomous, and a machine
without Satisfactory installed runs it exactly the same way.
"""

import logging
from typing import Final

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QDockWidget, QLabel, QMainWindow, QMessageBox, QToolBar

from satisplanner import __version__
from satisplanner.core.models import GameData
from satisplanner.core.results import FactoryReport, Severity
from satisplanner.data import db
from satisplanner.ui import theme
from satisplanner.ui.canvas import FactoryScene, FactoryView
from satisplanner.ui.catalogue import PaletteEntry, build_entries
from satisplanner.ui.document import FactoryDocument
from satisplanner.ui.icon_provider import IconProvider
from satisplanner.ui.palette import PaletteWidget

logger = logging.getLogger(__name__)

PALETTE_WIDTH: Final = 320
RESERVED_DOCK_WIDTH: Final = 320

# Placeholder text for the docks phase 4 fills in. Named so it is obvious in the UI
# that the space is reserved rather than broken.
_PHASE_4_DOCKS: Final[tuple[tuple[str, str], ...]] = (
    ("Tableau", "Le tableau synchronise avec le canvas arrive en phase 4."),
    ("Totaux", "Les totaux (matieres, electricite, liste de courses) arrivent en phase 4."),
    ("Diagnostics", "Le panneau de diagnostics arrive en phase 4."),
)


def load_catalogue() -> GameData:
    """The catalogue, from the database shipped inside the package."""
    path = db.default_database_path()
    logger.info("catalogue : %s", path)
    return db.load_game_data_from_file(path)


class MainWindow(QMainWindow):
    """Application shell around one edited factory."""

    def __init__(self, game_data: GameData | None = None) -> None:
        super().__init__()
        self.game_data = game_data if game_data is not None else load_catalogue()
        self.icons = IconProvider.from_default_roots()
        self.entries: list[PaletteEntry] = build_entries(self.game_data)
        self.document = FactoryDocument(self.game_data, self)

        self.setWindowTitle(f"SatisPlanner {__version__} — Satisfactory 1.2")
        self.resize(1600, 950)

        self.scene = FactoryScene(self.document, self.icons, self.entries, self)
        self.view = FactoryView(self.scene, self)
        self.setCentralWidget(self.view)

        self.palette_widget = PaletteWidget(self.game_data, self.icons, self.entries, self)
        self._add_palette_dock()
        self._reserve_phase_4_docks()
        self._build_actions()
        self._connect()

        self._show_catalogue_summary()
        self.document.solve_now()

    # ------------------------------------------------------------------ layout

    def _add_palette_dock(self) -> None:
        dock = QDockWidget("Palette", self)
        dock.setObjectName("dock_palette")
        dock.setWidget(self.palette_widget)
        dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )
        dock.setMinimumWidth(PALETTE_WIDTH)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)
        self.palette_dock = dock

    def _reserve_phase_4_docks(self) -> None:
        """Create the right-hand docks empty, so the layout is already the final one."""
        self.reserved_docks: list[QDockWidget] = []
        for title, message in _PHASE_4_DOCKS:
            dock = QDockWidget(title, self)
            dock.setObjectName(f"dock_{title.lower()}")
            label = QLabel(message, dock)
            label.setWordWrap(True)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setStyleSheet(f"color: {theme.TEXT_MUTED}; padding: 12px;")
            dock.setWidget(label)
            dock.setMinimumWidth(RESERVED_DOCK_WIDTH)
            self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
            self.reserved_docks.append(dock)
        for previous, following in zip(self.reserved_docks, self.reserved_docks[1:], strict=False):
            self.splitDockWidget(previous, following, Qt.Orientation.Vertical)

    def _build_actions(self) -> None:
        toolbar = QToolBar("Edition", self)
        toolbar.setObjectName("toolbar_edition")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        self.undo_action = self.document.undo_stack.createUndoAction(self, "Annuler")
        self.undo_action.setShortcut(QKeySequence.StandardKey.Undo)
        self.redo_action = self.document.undo_stack.createRedoAction(self, "Refaire")
        self.redo_action.setShortcut(QKeySequence.StandardKey.Redo)
        toolbar.addAction(self.undo_action)
        toolbar.addAction(self.redo_action)
        toolbar.addSeparator()

        self.delete_action = QAction("Supprimer", self)
        self.delete_action.setShortcut(QKeySequence.StandardKey.Delete)
        self.delete_action.triggered.connect(self.scene.delete_selection)
        toolbar.addAction(self.delete_action)

        self.adjust_action = QAction("Ajuster ce noeud", self)
        self.adjust_action.setToolTip(
            "Dimensionne le noeud selectionne a ce que ses intrants permettent (calcul local)"
        )
        self.adjust_action.triggered.connect(self._adjust_selection)
        toolbar.addAction(self.adjust_action)
        toolbar.addSeparator()

        self.reset_zoom_action = QAction("Zoom 100 %", self)
        self.reset_zoom_action.triggered.connect(self.view.reset_zoom)
        toolbar.addAction(self.reset_zoom_action)

        self.fit_action = QAction("Tout afficher", self)
        self.fit_action.triggered.connect(self.fit_to_factory)
        toolbar.addAction(self.fit_action)

        menu = self.menuBar().addMenu("&Edition")
        menu.addAction(self.undo_action)
        menu.addAction(self.redo_action)
        menu.addSeparator()
        menu.addAction(self.delete_action)
        menu.addAction(self.adjust_action)

        view_menu = self.menuBar().addMenu("&Affichage")
        view_menu.addAction(self.palette_dock.toggleViewAction())
        for dock in self.reserved_docks:
            view_menu.addAction(dock.toggleViewAction())
        view_menu.addSeparator()
        view_menu.addAction(self.reset_zoom_action)
        view_menu.addAction(self.fit_action)

        help_menu = self.menuBar().addMenu("&Aide")
        about = QAction("A propos", self)
        about.triggered.connect(self._show_about)
        help_menu.addAction(about)

    def _connect(self) -> None:
        self.palette_widget.entryActivated.connect(self._add_at_view_centre)
        self.palette_widget.defaultTransportsChanged.connect(self.scene.set_default_transports)
        self.scene.selectionSummaryChanged.connect(self._show_hint)
        self.document.reportChanged.connect(self._show_report_summary)
        self.scene.set_default_transports(*self.palette_widget.default_transports())

    # ----------------------------------------------------------------- actions

    def _add_at_view_centre(self, entry: PaletteEntry) -> None:
        """Double-click in the palette drops the node in the middle of the view."""
        centre = self.view.mapToScene(self.view.viewport().rect().center())
        self.scene.add_entry(entry, centre)

    def _adjust_selection(self) -> None:
        for item in self.scene.selected_nodes():
            self.scene.adjust_node_if_machine(item.node.id)

    def fit_to_factory(self) -> None:
        rect = self.scene.itemsBoundingRect()
        if rect.isEmpty():
            return
        self.view.fitInView(rect.adjusted(-40, -40, 40, 40), Qt.AspectRatioMode.KeepAspectRatio)

    # ------------------------------------------------------------- status bar

    def _show_hint(self, text: str) -> None:
        if text:
            self.statusBar().showMessage(text, 4000)

    def _show_report_summary(self, report: FactoryReport) -> None:
        """One line saying whether the factory works, and what is wrong if not."""
        if not report.nodes:
            self.statusBar().showMessage(
                "Glissez une recette ou un gisement depuis la palette pour commencer."
            )
            return
        errors = len(report.by_severity(Severity.ERROR))
        warnings = len(report.by_severity(Severity.WARNING))
        parts = [f"{len(report.nodes)} noeud(s)", f"{len(report.edges)} ligne(s)"]
        if not report.converged:
            parts.append("RESOLUTION NON CONVERGEE")
        if not report.is_sustainable:
            parts.append("debits non tenables : un tampon se vide")
        if errors:
            parts.append(f"{errors} erreur(s)")
        if warnings:
            parts.append(f"{warnings} avertissement(s)")
        if not errors and not warnings and report.converged:
            parts.append("usine nominale")
        self.statusBar().showMessage(" — ".join(parts))

    def _show_catalogue_summary(self) -> None:
        """What was loaded, pinned to the right of the status bar.

        A permanent widget rather than a message: the transient side belongs to the
        report, and a start-up notice must not overwrite "your factory is nominal".
        """
        label = QLabel(
            f"{len(self.game_data.recipes)} recettes, {len(self.game_data.items)} items — "
            f"{len(self.icons.index)} icone(s) indexee(s), le reste est dessine",
            self,
        )
        label.setStyleSheet(f"color: {theme.TEXT_MUTED};")
        self.statusBar().addPermanentWidget(label)

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            "A propos de SatisPlanner",
            f"<b>SatisPlanner {__version__}</b><br>"
            "Planificateur d'usines theoriques pour Satisfactory 1.2.<br><br>"
            "L'outil raisonne en <b>debits</b>, pas en geometrie 3D : ni distances, ni "
            "elevations, ni hauteur de refoulement des pompes.<br><br>"
            "Satisfactory, ses donnees et ses icones sont la propriete de "
            "Coffee Stain Studios.",
        )
