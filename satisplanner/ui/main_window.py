"""Main window: palette on the left, canvas in the middle, three panels on the right.

This is where the application becomes an application rather than a canvas: a document
has a name, a place on disk and a modified state, closing it asks before throwing
work away, and the three panels are kept in step with the same report the canvas
draws from.

Selection is synchronised in both directions between the canvas and the table. The
loop is broken with one flag rather than by disconnecting signals, because
disconnecting means remembering to reconnect and this way the invariant is local.

The catalogue is read once from the database embedded in the package. There is no
setup step and no path to point at: the application is autonomous, and a machine
without Satisfactory installed runs it exactly the same way.
"""

import logging
from collections.abc import Callable, Sequence
from enum import Enum
from pathlib import Path
from typing import Final

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QAction, QCloseEvent, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDockWidget,
    QFileDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from satisplanner import __version__, logging_setup
from satisplanner.core.graph import SCHEMA_VERSION as DOCUMENT_SCHEMA_VERSION
from satisplanner.core.models import GameData
from satisplanner.core.results import FactoryReport, Severity
from satisplanner.data import db, factory_file
from satisplanner.data.factory_file import FILE_FILTER, FILE_SUFFIX, FactoryFileError
from satisplanner.ui import exporters, theme
from satisplanner.ui.canvas import MAX_SCALE, FactoryScene, FactoryView
from satisplanner.ui.catalogue import EntryKind, PaletteEntry, build_entries
from satisplanner.ui.diagnostics_panel import DiagnosticsPanel
from satisplanner.ui.document import FactoryDocument
from satisplanner.ui.help_dialog import HelpDialog, shortcut_rows
from satisplanner.ui.icon_provider import IconProvider
from satisplanner.ui.item_card import ItemCard
from satisplanner.ui.localisation import install_french_translations
from satisplanner.ui.palette import PaletteWidget
from satisplanner.ui.preferences import Preferences, PreferencesDialog
from satisplanner.ui.table_panel import NodeTablePanel
from satisplanner.ui.totals_panel import TotalsPanel

logger = logging.getLogger(__name__)

PALETTE_WIDTH: Final = 320
PANEL_WIDTH: Final = 380


def load_catalogue() -> GameData:
    """The catalogue, from the database shipped inside the package."""
    path = db.default_database_path()
    logger.info("catalogue : %s", path)
    return db.load_game_data_from_file(path)


class ShareCodeDialog(QDialog):
    """One box for both directions: show a code to copy, or paste one to import."""

    def __init__(self, title: str, code: str = "", *, read_only: bool = False, parent=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(560, 260)
        self.edit = QPlainTextEdit(code, self)
        self.edit.setReadOnly(read_only)
        self.edit.setPlaceholderText("Collez ici un code commencant par SFP1:")
        self.edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)

        buttons = QDialogButtonBox(self)
        if read_only:
            copy = buttons.addButton("Copier le code", QDialogButtonBox.ButtonRole.ActionRole)
            copy.clicked.connect(self._copy)
            # Close carries the reject role, so ``rejected`` closes the box. Matching
            # on the button's own text would work only until Qt speaks French.
            buttons.addButton(QDialogButtonBox.StandardButton.Close)
            buttons.rejected.connect(self.reject)
        else:
            buttons.setStandardButtons(
                QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
            )
            buttons.accepted.connect(self.accept)
            buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(self.edit)
        layout.addWidget(buttons)

    def _copy(self) -> None:
        QGuiApplication.clipboard().setText(self.edit.toPlainText())

    def code(self) -> str:
        return self.edit.toPlainText()


class PartialSaveChoice(Enum):
    """What to do about saving a factory that did not open whole."""

    SAVE_AS = "save_as"
    OVERWRITE = "overwrite"
    CANCEL = "cancel"


def ask_partial_save(parent: QWidget, name: str, description: str) -> PartialSaveChoice:
    """The one place in this application where a reflex can destroy someone's work.

    A file opened partly is missing nodes the catalogue could not describe. Writing it
    back over itself throws those nodes away for good, and the user's hands are
    already on ``Ctrl+S``. So the overwrite is offered -- it is their file and their
    decision -- but never as the default, and never without saying again what is
    about to be lost.
    """
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle("Cette usine n'a pas été ouverte en entier")
    box.setText(
        f"« {name} » contenait des éléments que ce catalogue ne connait pas.\n"
        "Enregistrer par-dessus le fichier d'origine les supprimerait definitivement."
    )
    box.setInformativeText(description)
    save_as = box.addButton("Enregistrer sous...", QMessageBox.ButtonRole.AcceptRole)
    overwrite = box.addButton(f"Ecraser {name}", QMessageBox.ButtonRole.DestructiveRole)
    box.addButton(QMessageBox.StandardButton.Cancel)
    box.setDefaultButton(save_as)
    box.exec()

    clicked = box.clickedButton()
    if clicked is save_as:
        return PartialSaveChoice.SAVE_AS
    if clicked is overwrite:
        return PartialSaveChoice.OVERWRITE
    return PartialSaveChoice.CANCEL


class PdfOptionsDialog(QDialog):
    """The single choice a PDF export offers."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Export PDF")
        self.totals = QCheckBox("Inclure le tableau des totaux et les diagnostics", self)
        self.totals.setChecked(True)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(self.totals)
        layout.addWidget(buttons)

    def include_totals(self) -> bool:
        return self.totals.isChecked()


class MainWindow(QMainWindow):
    """Application shell around one edited factory."""

    def __init__(
        self, game_data: GameData | None = None, settings: QSettings | None = None
    ) -> None:
        super().__init__()
        # Before any dialog can be built: "Cancel" under a French question is this
        # application's own text and Qt's text disagreeing.
        install_french_translations()
        self.game_data = game_data if game_data is not None else load_catalogue()
        # Injectable so a test never writes to the developer's own settings.
        self.preferences = Preferences(settings)
        self.icons = IconProvider.from_default_roots(
            user_directory=self.preferences.effective_icon_directory
        )
        self.entries: list[PaletteEntry] = build_entries(self.game_data)
        self.document = FactoryDocument(self.game_data, self)
        # Built on first use and kept: see show_item_card.
        self.item_card: ItemCard | None = None
        self._syncing_selection = False
        # In creation order, which is menu order, which is the order the help lists.
        self.menus: list[QMenu] = []

        self.resize(1700, 980)
        self.scene = FactoryScene(self.document, self.icons, self.entries, self)
        self.view = FactoryView(self.scene, self)
        self.setCentralWidget(self.view)

        self.palette_widget = PaletteWidget(self.game_data, self.icons, self.entries, self)
        self.table_panel = NodeTablePanel(self.document, self)
        self.totals_panel = TotalsPanel(self.document, self)
        self.diagnostics_panel = DiagnosticsPanel(self.document, self)

        self._build_docks()
        self._build_actions()
        self._connect()

        self.apply_preferences()
        self._show_catalogue_summary()
        self.refresh_title()
        self.document.solve_now()

    # ------------------------------------------------------------------ layout

    def _build_docks(self) -> None:
        self.palette_dock = self._dock("Palette", "palette", self.palette_widget, PALETTE_WIDTH)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.palette_dock)

        self.table_dock = self._dock("Tableau", "tableau", self.table_panel, PANEL_WIDTH)
        self.totals_dock = self._dock("Totaux", "totaux", self.totals_panel, PANEL_WIDTH)
        self.diagnostics_dock = self._dock(
            "Diagnostics", "diagnostics", self.diagnostics_panel, PANEL_WIDTH
        )
        for dock in (self.table_dock, self.totals_dock, self.diagnostics_dock):
            self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
        self.splitDockWidget(self.table_dock, self.totals_dock, Qt.Orientation.Vertical)
        self.splitDockWidget(self.totals_dock, self.diagnostics_dock, Qt.Orientation.Vertical)
        self.panel_docks = (self.table_dock, self.totals_dock, self.diagnostics_dock)

    def _dock(self, title: str, name: str, widget: QWidget, width: int) -> QDockWidget:
        dock = QDockWidget(title, self)
        dock.setObjectName(f"dock_{name}")
        dock.setWidget(widget)
        dock.setMinimumWidth(width)
        return dock

    # ----------------------------------------------------------------- actions

    def _build_actions(self) -> None:
        self._build_file_actions()
        self._build_edit_actions()
        self._build_view_actions()
        self._build_help_actions()

    def _build_file_actions(self) -> None:
        self.new_action = _action(self, "Nouveau", QKeySequence.StandardKey.New, self.new_factory)
        self.open_action = _action(self, "Ouvrir...", QKeySequence.StandardKey.Open, self.open_file)
        self.save_action = _action(self, "Enregistrer", QKeySequence.StandardKey.Save, self.save)
        self.save_as_action = _action(
            self, "Enregistrer sous...", QKeySequence.StandardKey.SaveAs, self.save_as
        )
        self.copy_code_action = _action(
            self, "Copier le code de partage", "Ctrl+Shift+C", self.copy_code
        )
        self.import_code_action = _action(
            self, "Importer depuis un code...", "Ctrl+Shift+V", self.import_code
        )
        self.export_png_action = _action(
            self, "Exporter en PNG...", "Ctrl+Shift+E", self.export_png
        )
        self.export_pdf_action = _action(
            self, "Exporter en PDF...", QKeySequence.StandardKey.Print, self.export_pdf
        )
        self.preferences_action = _action(self, "Préférences...", "Ctrl+,", self.edit_preferences)
        self.quit_action = _action(self, "Quitter", QKeySequence.StandardKey.Quit, self.close)

        menu = self.menuBar().addMenu("&Fichier")
        self.menus.append(menu)
        menu.addAction(self.new_action)
        menu.addAction(self.open_action)
        self.recent_menu = menu.addMenu("Fichiers récents")
        menu.addSeparator()
        menu.addAction(self.save_action)
        menu.addAction(self.save_as_action)
        menu.addSeparator()
        menu.addAction(self.copy_code_action)
        menu.addAction(self.import_code_action)
        menu.addSeparator()
        menu.addAction(self.export_png_action)
        menu.addAction(self.export_pdf_action)
        menu.addSeparator()
        menu.addAction(self.preferences_action)
        menu.addAction(self.quit_action)
        self.refresh_recent_menu()

        toolbar = QToolBar("Fichier", self)
        toolbar.setObjectName("toolbar_fichier")
        toolbar.setMovable(False)
        toolbar.addAction(self.new_action)
        toolbar.addAction(self.open_action)
        toolbar.addAction(self.save_action)
        self.addToolBar(toolbar)

    def _build_edit_actions(self) -> None:
        toolbar = QToolBar("Édition", self)
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

        self.delete_action = _action(
            self, "Supprimer", QKeySequence.StandardKey.Delete, self.scene.delete_selection
        )
        self.select_all_action = _action(
            self, "Tout sélectionner", QKeySequence.StandardKey.SelectAll, self.scene.select_all
        )
        self.copy_action = _action(
            self, "Copier", QKeySequence.StandardKey.Copy, self.scene.copy_selection
        )
        self.cut_action = _action(
            self, "Couper", QKeySequence.StandardKey.Cut, self.scene.cut_selection
        )
        self.paste_action = _action(
            self, "Coller", QKeySequence.StandardKey.Paste, self.scene.paste
        )
        self.duplicate_action = _action(self, "Dupliquer", "Ctrl+D", self.scene.duplicate_selection)
        self.adjust_action = _action(self, "Ajuster ce nœud", "Ctrl+E", self._adjust_selection)
        self.adjust_action.setToolTip(
            "Dimensionne le nœud sélectionné à ce que ses intrants permettent (calcul local)"
        )
        toolbar.addAction(self.delete_action)
        toolbar.addAction(self.adjust_action)

        menu = self.menuBar().addMenu("&Édition")
        self.menus.append(menu)
        menu.addAction(self.undo_action)
        menu.addAction(self.redo_action)
        menu.addSeparator()
        menu.addAction(self.cut_action)
        menu.addAction(self.copy_action)
        menu.addAction(self.paste_action)
        menu.addAction(self.duplicate_action)
        menu.addSeparator()
        menu.addAction(self.select_all_action)
        menu.addAction(self.delete_action)
        menu.addAction(self.adjust_action)

    def _build_view_actions(self) -> None:
        self.zoom_in_action = _action(
            self, "Zoom avant", QKeySequence.StandardKey.ZoomIn, self.view.zoom_in
        )
        self.zoom_out_action = _action(
            self, "Zoom arrière", QKeySequence.StandardKey.ZoomOut, self.view.zoom_out
        )
        self.reset_zoom_action = _action(self, "Zoom 100 %", "Ctrl+0", self.view.reset_zoom)
        self.fit_action = _action(self, "Tout afficher", "Ctrl+Shift+F", self.fit_to_factory)
        self.search_action = _action(
            self, "Rechercher dans la palette", "Ctrl+F", self.focus_search
        )
        self.deployed_action = _action(
            self, "Machines déployées", "Ctrl+M", self.toggle_deployed_rendering
        )
        self.deployed_action.setCheckable(True)

        toolbar = self.findChild(QToolBar, "toolbar_edition")
        if toolbar is not None:
            toolbar.addSeparator()
            toolbar.addAction(self.reset_zoom_action)
            toolbar.addAction(self.fit_action)

        menu = self.menuBar().addMenu("&Affichage")
        self.menus.append(menu)
        menu.addAction(self.palette_dock.toggleViewAction())
        for dock in self.panel_docks:
            menu.addAction(dock.toggleViewAction())
        menu.addSeparator()
        menu.addAction(self.zoom_in_action)
        menu.addAction(self.zoom_out_action)
        menu.addAction(self.reset_zoom_action)
        menu.addAction(self.fit_action)
        menu.addSeparator()
        menu.addAction(self.deployed_action)
        menu.addAction(self.search_action)
        # Actions reachable only by their shortcut still have to belong to a widget
        # that is visible, or Qt never delivers the key.
        self.addAction(self.search_action)

    def _build_help_actions(self) -> None:
        self.help_action = _action(
            self, "Gestes et raccourcis", QKeySequence.StandardKey.HelpContents, self.show_help
        )
        menu = self.menuBar().addMenu("&Aide")
        self.menus.append(menu)
        menu.addAction(self.help_action)
        menu.addSeparator()
        menu.addAction(_action(self, "A propos", None, self._show_about))

    def _connect(self) -> None:
        self.palette_widget.entryActivated.connect(self._add_at_view_centre)
        self.palette_widget.entryOpened.connect(self.show_entry_card)
        self.scene.itemCardRequested.connect(self.show_item_card)
        self.palette_widget.defaultTransportsChanged.connect(self.scene.set_default_transports)
        self.palette_widget.defaultTransportsChanged.connect(self._store_transports)
        self.palette_widget.filtersChanged.connect(self._store_filters)
        self.scene.selectionSummaryChanged.connect(self._show_hint)
        self.scene.selectionChanged.connect(self._canvas_selection_changed)
        self.table_panel.selectionChangedTo.connect(self._table_selection_changed)
        self.diagnostics_panel.targetPicked.connect(self.reveal)
        self.diagnostics_panel.fixRequested.connect(self.apply_fix)
        self.document.reportChanged.connect(self._show_report_summary)
        self.document.identityChanged.connect(self.refresh_title)
        self.scene.set_default_transports(*self.palette_widget.default_transports())

    # -------------------------------------------------------------- selection

    def _canvas_selection_changed(self) -> None:
        if self._syncing_selection:
            return
        self._syncing_selection = True
        try:
            self.table_panel.show_selection([item.node.id for item in self.scene.selected_nodes()])
        finally:
            self._syncing_selection = False

    def _table_selection_changed(self, node_ids: Sequence[str]) -> None:
        if self._syncing_selection:
            return
        self._syncing_selection = True
        try:
            self.scene.select_nodes(node_ids)
        finally:
            self._syncing_selection = False

    def reveal(self, node_id: str, edge_id: str) -> None:
        """Select and centre what a diagnostic is talking about."""
        target = self.scene.select_target(node_id, edge_id)
        if target is not None:
            self.view.centerOn(target)

    def apply_fix(self, node_id: str, edge_id: str) -> None:
        """The one-click fix a diagnostic offers, carried out on the right object."""
        if edge_id and self.scene.upgrade_line(edge_id):
            return
        if node_id:
            self.scene.adjust_node_if_machine(node_id)

    # ------------------------------------------------------- document actions

    def new_factory(self) -> None:
        if not self.confirm_discard():
            return
        self.document.reset()
        self.statusBar().showMessage("Nouvelle usine.", 4000)

    def open_file(self, path: Path | None = None) -> bool:
        if not self.confirm_discard():
            return False
        if path is None:
            chosen, _ = QFileDialog.getOpenFileName(self, "Ouvrir une usine", "", FILE_FILTER)
            if not chosen:
                return False
            path = Path(chosen)
        try:
            loaded = self.document.open(path)
        except FactoryFileError as exc:
            QMessageBox.warning(self, "Ouverture impossible", str(exc))
            return False
        self.remember_recent(path)
        self.fit_to_factory()
        self._report_warnings(loaded.warnings, path.name)
        return True

    def save(self) -> bool:
        if self.document.path is None:
            return self.save_as()
        if self.document.is_partial:
            choice = ask_partial_save(
                self, self.document.path.name, self.document.partial_description()
            )
            if choice is PartialSaveChoice.CANCEL:
                return False
            if choice is PartialSaveChoice.SAVE_AS:
                return self.save_as()
            logger.warning("écrasement demande d'un fichier ouvert partiellement")
        return self._write(self.document.path)

    def save_as(self) -> bool:
        suggestion = str(self.document.path or f"{self.document.display_name}{FILE_SUFFIX}")
        chosen, _ = QFileDialog.getSaveFileName(
            self, "Enregistrer l'usine", suggestion, FILE_FILTER
        )
        if not chosen:
            return False
        path = Path(chosen).with_suffix(FILE_SUFFIX)
        return self._write(path)

    def _write(self, path: Path) -> bool:
        try:
            self.document.save_as(path, exporters.thumbnail_bytes(self.scene))
        except OSError as exc:
            QMessageBox.warning(self, "Enregistrement impossible", f"{path.name} : {exc.strerror}")
            return False
        self.remember_recent(path)
        self.statusBar().showMessage(f"Usine enregistrée dans {path.name}.", 4000)
        return True

    def confirm_discard(self) -> bool:
        """Ask before throwing away unsaved work. True means "go ahead"."""
        if not self.document.is_modified:
            return True
        answer = QMessageBox.question(
            self,
            "Modifications non enregistrées",
            f"« {self.document.display_name} » a été modifiée. Enregistrer avant de continuer ?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if answer == QMessageBox.StandardButton.Save:
            return self.save()
        return answer == QMessageBox.StandardButton.Discard

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.confirm_discard():
            event.accept()
        else:
            event.ignore()

    # ----------------------------------------------------------- share codes

    def copy_code(self) -> str:
        code = self.document.share_code()
        QGuiApplication.clipboard().setText(code)
        dialog = ShareCodeDialog("Code de partage", code, read_only=True, parent=self)
        dialog.exec()
        self.statusBar().showMessage("Code copié dans le presse-papiers.", 4000)
        return code

    def import_code(self, code: str | None = None) -> bool:
        if code is None:
            if not self.confirm_discard():
                return False
            dialog = ShareCodeDialog("Importer depuis un code", parent=self)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return False
            code = dialog.code()
        try:
            loaded = factory_file.decode_share_code(code)
        except FactoryFileError as exc:
            QMessageBox.warning(self, "Code refusé", str(exc))
            return False
        warnings = list(loaded.warnings)
        self.document.adopt(loaded.graph, warnings=warnings)
        self.fit_to_factory()
        self._report_warnings(warnings, "le code importé")
        return True

    def _report_warnings(self, warnings: Sequence[str], subject: str) -> None:
        if not warnings:
            return
        QMessageBox.information(
            self, "Usine ouverte avec des réserves", f"{subject} :\n\n" + "\n\n".join(warnings)
        )

    # --------------------------------------------------------------- exports

    def export_png(self, path: Path | None = None) -> bool:
        if path is None:
            chosen, _ = QFileDialog.getSaveFileName(
                self, "Exporter le canvas", f"{self.document.display_name}.png", "Image PNG (*.png)"
            )
            if not chosen:
                return False
            path = Path(chosen)
        if not exporters.export_png(self.scene, path):
            QMessageBox.information(self, "Rien a exporter", "L'usine est vide.")
            return False
        self.statusBar().showMessage(f"Canvas exporté dans {path.name}.", 4000)
        return True

    def export_pdf(self, path: Path | None = None, *, include_totals: bool = True) -> bool:
        if path is None:
            dialog = PdfOptionsDialog(self)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return False
            include_totals = dialog.include_totals()
            chosen, _ = QFileDialog.getSaveFileName(
                self, "Exporter en PDF", f"{self.document.display_name}.pdf", "Document PDF (*.pdf)"
            )
            if not chosen:
                return False
            path = Path(chosen)
        written = exporters.export_pdf(
            self.scene,
            path,
            self.document.report,
            self.game_data,
            include_totals=include_totals,
        )
        if written:
            self.statusBar().showMessage(f"Usine exportée dans {path.name}.", 4000)
        return written

    # ----------------------------------------------------------- preferences

    def apply_preferences(self) -> None:
        """Push what was stored into the widgets that show it."""
        self.palette_widget.apply_stored(
            self.preferences.default_belt,
            self.preferences.default_pipe,
            show_alternates=self.preferences.show_alternates,
            show_events=self.preferences.show_events,
        )
        deployed = self.preferences.deployed_rendering
        self.deployed_action.setChecked(deployed)
        self.scene.set_deployed_rendering(deployed, self.preferences.deployed_ceiling)
        palette = self.preferences.item_palette
        self.scene.set_item_palette(palette)
        self.palette_widget.set_item_palette(palette)
        self.refresh_recent_menu()

    def toggle_deployed_rendering(self) -> None:
        """The Affichage menu and the preferences box set the same one setting."""
        enabled = self.deployed_action.isChecked()
        self.preferences.deployed_rendering = enabled
        self.scene.set_deployed_rendering(enabled, self.preferences.deployed_ceiling)

    def edit_preferences(self) -> bool:
        """Open the box and, if it is accepted, act on what changed."""
        before = self.preferences.effective_icon_directory
        dialog = PreferencesDialog(
            self.preferences, self.game_data, len(self.icons.index), parent=self
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        if self.preferences.effective_icon_directory != before:
            self.reload_icons()
        self.apply_preferences()
        self.statusBar().showMessage("Préférences enregistrées.", 4000)
        return True

    def reload_icons(self) -> None:
        """Index the icon folders again and hand the result to everything drawing.

        Immediate rather than "restart to apply": someone who has just exported four
        hundred files with FModel wants to know now whether they landed in the right
        place, and being told to restart is being told to find out later.
        """
        self.icons = IconProvider.from_default_roots(
            user_directory=self.preferences.effective_icon_directory
        )
        self.palette_widget.set_icons(self.icons)
        self.scene.set_icons(self.icons)
        self._refresh_catalogue_summary()
        logger.info("%d fichier(s) d'icône indexe(s)", len(self.icons.index))

    def _store_transports(self, belt: str, pipe: str) -> None:
        self.preferences.default_belt = belt
        self.preferences.default_pipe = pipe

    def _store_filters(self, alternates: bool, events: bool) -> None:
        self.preferences.show_alternates = alternates
        self.preferences.show_events = events

    def focus_search(self) -> None:
        """Ctrl+F: show the palette if it was hidden, and put the caret in the box."""
        self.palette_dock.show()
        self.palette_widget.search.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.palette_widget.search.selectAll()

    def show_help(self) -> None:
        """The gestures, and the shortcuts as this window really binds them."""
        HelpDialog(shortcut_rows(self.documented_actions()), self).exec()

    def documented_actions(self) -> list[QAction]:
        """Every action worth listing in the help, in menu order.

        Read off the menus this window really built, rather than from a list kept
        alongside them: a list kept alongside them is a list that stops matching.
        """
        actions: list[QAction] = []
        for menu in self.menus:
            actions.extend(action for action in menu.actions() if not action.isSeparator())
        return actions

    # --------------------------------------------------------- recent files

    def recent_files(self) -> list[Path]:
        return self.preferences.recent_files()

    def remember_recent(self, path: Path) -> None:
        self.preferences.remember_recent(path)
        self.refresh_recent_menu()

    def refresh_recent_menu(self) -> None:
        self.recent_menu.clear()
        entries = self.recent_files()
        if not entries:
            empty = self.recent_menu.addAction("Aucun fichier récent")
            empty.setEnabled(False)
            return
        for path in entries:
            action = self.recent_menu.addAction(path.name)
            action.setToolTip(str(path))
            action.triggered.connect(lambda _checked=False, target=path: self.open_file(target))
        self.recent_menu.addSeparator()
        self.recent_menu.addAction("Oublier la liste", self.forget_recent)

    def forget_recent(self) -> None:
        self.preferences.forget_recent()
        self.refresh_recent_menu()

    # ----------------------------------------------------------------- misc

    def _add_at_view_centre(self, entry: PaletteEntry) -> None:
        """Double-click in the palette drops the node in the middle of the view."""
        self.scene.add_entry(entry, self.view.mapToScene(self.view.viewport().rect().center()))

    # -------------------------------------------------------------- item card

    def show_item_card(self, item_class: str) -> None:
        """Open the card for one item, reusing the one window.

        One instance, hidden rather than destroyed when closed: the reader's trail
        through the catalogue survives dismissing it, which is the whole point of
        having a back button.
        """
        if self.item_card is None:
            self.item_card = ItemCard(self.game_data, self.icons, self)
            self.item_card.placeRequested.connect(self.place_recipe)
        self.item_card.show_item(item_class)
        self.item_card.show()
        self.item_card.raise_()
        self.item_card.activateWindow()

    def show_entry_card(self, entry: PaletteEntry) -> None:
        """The card for whatever a palette entry is about, when it is about an item."""
        subject = entry.subject_item(self.game_data)
        if subject is None:
            self.statusBar().showMessage(f"Aucune fiche pour « {entry.label} ».", 4000)
            return
        self.show_item_card(subject)

    def place_recipe(self, recipe_class: str) -> bool:
        """The card's "place on the canvas" button, resolved against the palette."""
        for entry in self.entries:
            if entry.kind is EntryKind.RECIPE and entry.class_name == recipe_class:
                self._add_at_view_centre(entry)
                self.statusBar().showMessage(f"{entry.label} pose au centre de la vue.", 4000)
                return True
        logger.debug("recette absente de la palette : %s", recipe_class)
        return False

    def _adjust_selection(self) -> None:
        for item in self.scene.selected_nodes():
            self.scene.adjust_node_if_machine(item.node.id)

    def fit_to_factory(self) -> None:
        """Frame the whole factory, without magnifying a small one past legibility.

        ``fitInView`` does not know about the wheel's zoom limit, so a factory of two
        nodes would otherwise be blown up to eight times its size.
        """
        rect = self.scene.itemsBoundingRect()
        if rect.isEmpty():
            return
        self.view.fitInView(rect.adjusted(-40, -40, 40, 40), Qt.AspectRatioMode.KeepAspectRatio)
        scale = self.view.transform().m11()
        if scale > MAX_SCALE:
            self.view.resetTransform()
            self.view.scale(MAX_SCALE, MAX_SCALE)
            self.view.centerOn(rect.center())

    def refresh_title(self) -> None:
        marker = " •" if self.document.is_modified else ""
        # Spelled out rather than iconic: this is the state where a reflex Ctrl+S
        # costs somebody their nodes, so it says what it means.
        partial = " — OUVERTURE PARTIELLE" if self.document.is_partial else ""
        self.setWindowTitle(
            f"{self.document.display_name}{marker}{partial} — SatisPlanner {__version__} "
            f"— Satisfactory {db.GAME_VERSION}"
        )

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
        parts = [f"{len(report.nodes)} nœud(s)", f"{len(report.edges)} ligne(s)"]
        if not report.converged:
            parts.append("Résolution NON Convergée")
        if not report.is_sustainable:
            parts.append("débits non tenables : un tampon se vide")
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
        self.catalogue_label = QLabel(self.catalogue_summary(), self)
        self.catalogue_label.setStyleSheet(f"color: {theme.TEXT_MUTED};")
        self.statusBar().addPermanentWidget(self.catalogue_label)

    def catalogue_summary(self) -> str:
        return (
            f"{len(self.game_data.recipes)} recettes, {len(self.game_data.items)} items — "
            f"{len(self.icons.index)} icône(s) indexée(s), le reste est dessiné"
        )

    def _refresh_catalogue_summary(self) -> None:
        self.catalogue_label.setText(self.catalogue_summary())

    def _show_about(self) -> None:
        log_path = logging_setup.current_log_path()
        journal = f"Journal : {log_path}<br><br>" if log_path is not None else ""
        QMessageBox.about(
            self,
            "A propos de SatisPlanner",
            f"<b>SatisPlanner {__version__}</b><br>"
            f"Données de jeu : Satisfactory {db.GAME_VERSION} "
            f"(schéma de base {db.SCHEMA_VERSION}, schéma de fichier "
            f"{DOCUMENT_SCHEMA_VERSION})<br><br>"
            "Planificateur d'usines théoriques. L'outil raisonne en <b>débits</b>, pas en "
            "géométrie 3D : ni distances, ni élévations, ni hauteur de refoulement des "
            f"pompes.<br><br>{journal}"
            "Satisfactory, ses données et ses icônes sont la propriété de "
            "Coffee Stain Studios. Aucun logo ni élément de marque du jeu n'est "
            "reproduit dans cette application.",
        )


def _action(
    window: QMainWindow,
    text: str,
    shortcut: QKeySequence.StandardKey | str | None,
    slot: Callable[[], object],
) -> QAction:
    """One action. Shortcuts are given as a standard key where one exists -- so that
    Windows and any other platform each get their own convention -- and as text only
    where the application invents the binding.

    The action is invoked with **no argument**, and that is the whole point of the
    ``lambda``. ``QAction.triggered`` carries a ``checked`` flag, which Qt hands to
    any slot with room for a positional parameter; several methods here are gestures
    that also accept an optional path or code, so ``Ouvrir...`` called
    ``open_file(False)`` and died on ``False.is_file()`` -- as did importing a share
    code and both exports. Swallowing the flag once, here, is the fix rather than
    four call sites remembering to write ``lambda _checked=False: ...``: this is the
    only place in the application where ``triggered`` is connected, so it is the only
    place the trap can be sprung. A checkable action reads its own state through
    ``isChecked()`` and needs nothing passed to it either.
    """
    action = QAction(text, window)
    if isinstance(shortcut, QKeySequence.StandardKey):
        action.setShortcut(shortcut)
    elif shortcut is not None:
        action.setShortcut(QKeySequence(shortcut))
    action.triggered.connect(lambda _checked=False: slot())
    return action
