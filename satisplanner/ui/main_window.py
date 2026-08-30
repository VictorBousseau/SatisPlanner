"""Main window: palette on the left, the open factories in the middle, panels right.

This is where the application becomes an application rather than a canvas: a document
has a name, a place on disk and a modified state, closing it asks before throwing
work away, and the three panels are kept in step with the same report the canvas
draws from.

Several factories are open at once, one per tab. Each tab owns its document, its
scene and its view -- so its zoom, its framing and its selection are its own -- while
the palette, the three panels and the preferences are shared and rebound as the tabs
change. That split is deliberate: three panels per open factory would be three times
the work on every edit, for two of which nobody is looking.

The rebinding lives in exactly one method, :meth:`MainWindow._activate`, which also
sets the active undo stack. It has to be one method: a window where the panels
follow the tabs and the undo history follows something else is a window where
``Annuler`` eventually undoes an edit made in another factory.

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

from PySide6.QtCore import QRectF, QSettings, Qt
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QCloseEvent,
    QGuiApplication,
    QKeySequence,
    QUndoGroup,
)
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
    QTabWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from satisplanner import __version__, logging_setup
from satisplanner.core import formatting, i18n, interface
from satisplanner.core.graph import SCHEMA_VERSION as DOCUMENT_SCHEMA_VERSION
from satisplanner.core.graph import AttachmentMode, FactoryGraph
from satisplanner.core.i18n import Language, _
from satisplanner.core.models import GameData
from satisplanner.core.planner import PlanError
from satisplanner.core.results import FactoryReport, Severity
from satisplanner.data import db, factory_file, module_file
from satisplanner.data.factory_file import FILE_FILTER, FILE_SUFFIX, FactoryFileError
from satisplanner.data.module_file import FactoryModule, ModuleError
from satisplanner.ui import clipboard, edits, exporters, theme
from satisplanner.ui.binding import DocumentBinding
from satisplanner.ui.canvas import MAX_SCALE, FactoryScene, FactoryView
from satisplanner.ui.catalogue import EntryKind, PaletteEntry, build_entries
from satisplanner.ui.diagnostics_panel import DiagnosticsPanel
from satisplanner.ui.document import FactoryDocument
from satisplanner.ui.document_tab import DocumentTab
from satisplanner.ui.generate import GenerateDialog
from satisplanner.ui.help_dialog import HelpDialog, shortcut_rows
from satisplanner.ui.icon_provider import IconProvider
from satisplanner.ui.item_card import ItemCard
from satisplanner.ui.localisation import (
    install_translations,
)
from satisplanner.ui.modules import ModuleLibraryDialog, SaveModuleDialog
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
        self.edit.setPlaceholderText(_("Collez ici un code commençant par SFP1:"))
        self.edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)

        buttons = QDialogButtonBox(self)
        if read_only:
            copy = buttons.addButton(_("Copier le code"), QDialogButtonBox.ButtonRole.ActionRole)
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
    box.setWindowTitle(_("Cette usine n'a pas été ouverte en entier"))
    box.setText(
        _(
            "« {name} » contenait des éléments que ce catalogue ne connait pas.\n"
            "Enregistrer par-dessus le fichier d'origine les supprimerait "
            "définitivement."
        ).format(name=name)
    )
    box.setInformativeText(description)
    save_as = box.addButton(_("Enregistrer sous..."), QMessageBox.ButtonRole.AcceptRole)
    overwrite = box.addButton(
        _("Écraser {name}").format(name=name), QMessageBox.ButtonRole.DestructiveRole
    )
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
        self.setWindowTitle(_("Export PDF"))
        self.totals = QCheckBox(_("Inclure le tableau des totaux et les diagnostics"), self)
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
    """Application shell around the factories that are open."""

    def __init__(
        self,
        game_data: GameData | None = None,
        settings: QSettings | None = None,
        module_directory: Path | None = None,
    ) -> None:
        super().__init__()
        # Injectable so a test never writes to the developer's own settings.
        self.preferences = Preferences(settings)
        # The language before anything is built, because everything built after it
        # reads it: the palette entries carry the game's word for each recipe, and
        # a dialog's buttons come from Qt's own catalogue. On a first launch this is
        # the system's language, so an English speaker is not left guessing how to
        # get out of French.
        i18n.set_language(self.preferences.language)
        install_translations(i18n.language())
        self.game_data = game_data if game_data is not None else load_catalogue()
        self.icons = IconProvider.from_default_roots(
            user_directory=self.preferences.effective_icon_directory
        )
        self.entries: list[PaletteEntry] = build_entries(self.game_data)
        # Injectable for the same reason as the settings: a test must not write
        # into the developer's own library.
        self.module_directory = module_directory
        # Built on first use and kept: see show_item_card and show_module_library.
        self.item_card: ItemCard | None = None
        self.library: ModuleLibraryDialog | None = None
        # Tabs that were opened from the library, and which module each holds, so
        # that re-saving one lands back on the file it came from.
        self.editing_module: dict[DocumentTab, FactoryModule] = {}
        self._syncing_selection = False
        # In creation order, which is menu order, which is the order the help lists.
        self.menus: list[QMenu] = []
        # One history per open factory, and one group so that Annuler always means
        # "undo in the factory being looked at".
        self.undo_group = QUndoGroup(self)
        # Everything that belongs to the active document, connected and disconnected
        # as one piece. Filled and emptied only by _activate.
        self._binding = DocumentBinding()
        self._active: DocumentTab | None = None

        self.resize(1700, 980)
        self.tabs = QTabWidget(self)
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.setDocumentMode(True)
        self.tabs.currentChanged.connect(self._current_changed)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.setCentralWidget(self.tabs)

        # The first factory exists before the panels, because they are built around a
        # document; it joins the tab bar last, once there is something to bind it to.
        first = self._new_tab()

        self.palette_widget = PaletteWidget(self.game_data, self.icons, self.entries, self)
        self.table_panel = NodeTablePanel(first.document, self)
        self.totals_panel = TotalsPanel(first.document, self)
        self.diagnostics_panel = DiagnosticsPanel(first.document, self)
        self.panels = (self.table_panel, self.totals_panel, self.diagnostics_panel)

        self._build_docks()
        self._build_actions()
        self._connect()
        self._show_catalogue_summary()

        self._adopt(first)
        self.apply_preferences()

    # ------------------------------------------------------------------- tabs

    @property
    def current_tab(self) -> DocumentTab:
        """The factory being looked at. There is always exactly one."""
        assert self._active is not None, "la fenêtre a toujours une usine ouverte"
        return self._active

    @property
    def document(self) -> FactoryDocument:
        return self.current_tab.document

    @property
    def scene(self) -> FactoryScene:
        return self.current_tab.scene

    @property
    def view(self) -> FactoryView:
        return self.current_tab.view

    def open_tabs(self) -> list[DocumentTab]:
        """Every open factory, in tab-bar order."""
        found = []
        for index in range(self.tabs.count()):
            widget = self.tabs.widget(index)
            if isinstance(widget, DocumentTab):
                found.append(widget)
        return found

    def _new_tab(self) -> DocumentTab:
        """Build a factory and its canvas, without putting them on screen yet."""
        tab = DocumentTab(self.game_data, self.icons, self.entries, self)
        self.undo_group.addStack(tab.document.undo_stack)
        # Its own title follows its own name and modified state for as long as it
        # exists -- which is not the same lifetime as being the active document, so
        # this connection is not one of _activate's.
        tab.document.identityChanged.connect(self.refresh_title)
        # So that a tab always has a report to show and switching to it never has to
        # ask the engine for one. An empty factory costs microseconds.
        tab.document.solve_now()
        return tab

    def _adopt(self, tab: DocumentTab) -> DocumentTab:
        """Put a built tab on screen and make it the active one."""
        index = self.tabs.addTab(tab, tab.title())
        self.tabs.setCurrentIndex(index)
        # Adding the very first tab makes it current without changing the index, so
        # ``currentChanged`` may not have fired. Activating twice is free: _activate
        # returns at once when the document has not changed.
        self._activate(tab)
        return tab

    def new_tab(self) -> DocumentTab:
        """A blank factory in a new tab, made active."""
        return self._adopt(self._new_tab())

    def select_tab(self, tab: DocumentTab) -> None:
        self.tabs.setCurrentIndex(self.tabs.indexOf(tab))

    def next_tab(self) -> None:
        """Ctrl+Tab, wrapping round rather than stopping at the last one."""
        if self.tabs.count() > 1:
            self.tabs.setCurrentIndex((self.tabs.currentIndex() + 1) % self.tabs.count())

    def close_current_tab(self) -> bool:
        return self.close_tab(self.tabs.currentIndex())

    def close_tab(self, index: int) -> bool:
        """Close one factory, asking first if it would lose work.

        The tab is brought to the front before the question is asked, and only
        then: "« Usine » a été modifiée" is a question about a factory and should
        be answered while looking at it, but closing a background tab that has
        nothing to lose has no reason to move the view at all.
        """
        tab = self.tabs.widget(index)
        if not isinstance(tab, DocumentTab):
            return False
        if tab.document.is_modified:
            self.tabs.setCurrentIndex(index)
            if not self.confirm_discard():
                return False
        if self.tabs.count() == 1:
            # The window always has a factory in it. Closing the only one leaves a
            # blank one rather than a window with nothing to edit and half its menus
            # pointing at nothing. The replacement is put in place *first*, so the
            # active document is never momentarily absent.
            self.new_tab()
        self.tabs.removeTab(index)
        self.editing_module.pop(tab, None)
        self.undo_group.removeStack(tab.document.undo_stack)
        tab.dispose()
        tab.deleteLater()
        return True

    def _reusable_tab(self) -> DocumentTab | None:
        """The active tab when it is a blank one nobody has touched.

        Opening a file into it rather than beside it is what stops the window
        filling up with the empty document it started with.
        """
        tab = self._active
        return tab if tab is not None and tab.is_pristine else None

    def _current_changed(self, index: int) -> None:
        widget = self.tabs.widget(index)
        self._activate(widget if isinstance(widget, DocumentTab) else None)

    def _activate(self, tab: DocumentTab | None) -> None:
        """The one and only place that knows the active document changed.

        It undoes the previous document's connections, makes the new one's, and
        hands the undo group the matching stack. Those three have to happen
        together: panels that follow the tabs and an undo history that follows
        something else drift apart silently, and the first sign of it is
        ``Annuler`` undoing an edit made in a factory that is no longer on screen.

        Nothing here asks the engine for anything. The document being shown was
        solved when it was created and after every edit since; the panels redisplay
        that report, they do not request a new one. Switching tabs is not an edit.
        """
        if tab is self._active:
            return
        self._binding.unbind()
        self._active = tab
        if tab is None:
            return

        document, scene = tab.document, tab.scene
        self._binding.bind(scene.itemCardRequested, self.show_item_card)
        self._binding.bind(scene.selectionSummaryChanged, self._show_hint)
        self._binding.bind(scene.selectionChanged, self._canvas_selection_changed)
        self._binding.bind(document.reportChanged, self._show_report_summary)
        for panel in self.panels:
            panel.set_document(document)
        self.undo_group.setActiveStack(document.undo_stack)

        self.refresh_title()
        # The mode belongs to the document, so the ticked entry follows the tabs
        # exactly as the panels do.
        self.refresh_attachment_mode()
        if document.report is not None:
            self._show_report_summary(document.report)
        # The table is shared, so it shows the selection of whichever canvas is in
        # front -- including an empty one.
        self._canvas_selection_changed()

    def dispose(self) -> None:
        """Let go of every open factory. For the tests; a running window closes.

        The active document is unbound first, so that nothing is listening to a
        scene while that scene is being taken apart.
        """
        self._binding.unbind()
        for tab in self.open_tabs():
            tab.document.undo_stack.setClean()
            tab.dispose()

    # ------------------------------------------------------------------ layout

    def _build_docks(self) -> None:
        self.palette_dock = self._dock(_("Palette"), "palette", self.palette_widget, PALETTE_WIDTH)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.palette_dock)

        self.table_dock = self._dock(_("Tableau"), "tableau", self.table_panel, PANEL_WIDTH)
        self.totals_dock = self._dock(_("Totaux"), "totaux", self.totals_panel, PANEL_WIDTH)
        self.diagnostics_dock = self._dock(
            _("Diagnostics"), "diagnostics", self.diagnostics_panel, PANEL_WIDTH
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
        self._build_generator_actions()
        self._build_attachment_mode_actions()
        self._build_module_actions()
        self._build_language_actions()
        self._build_help_actions()

    def _build_file_actions(self) -> None:
        self.new_action = _action(
            self, _("Nouvel onglet"), QKeySequence.StandardKey.New, self.new_tab
        )
        # Two bindings for one gesture: Ctrl+N is what "new document" means
        # everywhere, Ctrl+T is what "new tab" means everywhere, and with tabs they
        # are the same gesture. The help page lists both.
        self.new_action.setShortcuts(
            [QKeySequence(QKeySequence.StandardKey.New), QKeySequence("Ctrl+T")]
        )
        self.open_action = _action(
            self, _("Ouvrir..."), QKeySequence.StandardKey.Open, self.open_file
        )
        self.close_tab_action = _action(
            self, _("Fermer l'onglet"), "Ctrl+W", self.close_current_tab
        )
        self.next_tab_action = _action(self, _("Onglet suivant"), "Ctrl+Tab", self.next_tab)
        self.save_action = _action(
            self, _("Enregistrer"), QKeySequence.StandardKey.Save, self.save
        )
        self.save_as_action = _action(
            self, _("Enregistrer sous..."), QKeySequence.StandardKey.SaveAs, self.save_as
        )
        self.copy_code_action = _action(
            self, _("Copier le code de partage"), "Ctrl+Shift+C", self.copy_code
        )
        self.import_code_action = _action(
            self, _("Importer depuis un code..."), "Ctrl+Shift+V", self.import_code
        )
        self.export_png_action = _action(
            self, _("Exporter en PNG..."), "Ctrl+Shift+E", self.export_png
        )
        self.export_pdf_action = _action(
            self, _("Exporter en PDF..."), QKeySequence.StandardKey.Print, self.export_pdf
        )
        self.preferences_action = _action(
            self, _("Préférences..."), "Ctrl+,", self.edit_preferences
        )
        self.quit_action = _action(self, _("Quitter"), QKeySequence.StandardKey.Quit, self.close)

        menu = self.menuBar().addMenu(_("&Fichier"))
        self.menus.append(menu)
        menu.addAction(self.new_action)
        menu.addAction(self.open_action)
        self.recent_menu = menu.addMenu(_("Fichiers récents"))
        menu.addSeparator()
        menu.addAction(self.next_tab_action)
        menu.addAction(self.close_tab_action)
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

        toolbar = QToolBar(_("Fichier"), self)
        toolbar.setObjectName("toolbar_fichier")
        toolbar.setMovable(False)
        toolbar.addAction(self.new_action)
        toolbar.addAction(self.open_action)
        toolbar.addAction(self.save_action)
        self.addToolBar(toolbar)

    def _build_edit_actions(self) -> None:
        toolbar = QToolBar(_("Édition"), self)
        toolbar.setObjectName("toolbar_edition")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        # From the group rather than from one stack: the action stays the same
        # object across a tab change and follows whichever history is active.
        self.undo_action = self.undo_group.createUndoAction(self, _("Annuler"))
        self.undo_action.setShortcut(QKeySequence.StandardKey.Undo)
        self.redo_action = self.undo_group.createRedoAction(self, _("Refaire"))
        self.redo_action.setShortcut(QKeySequence.StandardKey.Redo)
        toolbar.addAction(self.undo_action)
        toolbar.addAction(self.redo_action)
        toolbar.addSeparator()

        # Every canvas action goes through the window rather than being bound to one
        # scene: the actions are built once and the canvas underneath them changes
        # with the tab. ``self.scene`` reads the active one at the moment of the
        # click, which is the only moment at which the answer is known.
        self.delete_action = _action(
            self,
            _("Supprimer"),
            QKeySequence.StandardKey.Delete,
            lambda: self.scene.delete_selection(),
        )
        self.select_all_action = _action(
            self,
            _("Tout sélectionner"),
            QKeySequence.StandardKey.SelectAll,
            lambda: self.scene.select_all(),
        )
        self.copy_action = _action(
            self, _("Copier"), QKeySequence.StandardKey.Copy, lambda: self.scene.copy_selection()
        )
        self.cut_action = _action(
            self, _("Couper"), QKeySequence.StandardKey.Cut, lambda: self.scene.cut_selection()
        )
        self.paste_action = _action(
            self, _("Coller"), QKeySequence.StandardKey.Paste, lambda: self.scene.paste()
        )
        self.duplicate_action = _action(
            self, _("Dupliquer"), "Ctrl+D", lambda: self.scene.duplicate_selection()
        )
        self.adjust_action = _action(self, _("Ajuster ce nœud"), "Ctrl+E", self._adjust_selection)
        self.adjust_action.setToolTip(
            _("Dimensionne le nœud sélectionné à ce que ses intrants permettent (calcul local)")
        )
        toolbar.addAction(self.delete_action)
        toolbar.addAction(self.adjust_action)

        menu = self.menuBar().addMenu(_("&Édition"))
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
        # Through the window for the same reason as the edit actions: the view is
        # the active tab's, and which tab that is changes.
        self.zoom_in_action = _action(
            self, _("Zoom avant"), QKeySequence.StandardKey.ZoomIn, lambda: self.view.zoom_in()
        )
        self.zoom_out_action = _action(
            self, _("Zoom arrière"), QKeySequence.StandardKey.ZoomOut, lambda: self.view.zoom_out()
        )
        self.reset_zoom_action = _action(
            self, _("Zoom 100 %"), "Ctrl+0", lambda: self.view.reset_zoom()
        )
        self.fit_action = _action(self, _("Tout afficher"), "Ctrl+Shift+F", self.fit_to_factory)
        self.search_action = _action(
            self, _("Rechercher dans la palette"), "Ctrl+F", self.focus_search
        )
        self.deployed_action = _action(
            self, _("Machines déployées"), "Ctrl+M", self.toggle_deployed_rendering
        )
        self.deployed_action.setCheckable(True)

        toolbar = self.findChild(QToolBar, "toolbar_edition")
        if toolbar is not None:
            toolbar.addSeparator()
            toolbar.addAction(self.reset_zoom_action)
            toolbar.addAction(self.fit_action)

        menu = self.menuBar().addMenu(_("&Affichage"))
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

    def _build_generator_actions(self) -> None:
        self.generate_action = _action(
            self, _("Générer une usine depuis un objectif..."), "Ctrl+G", self.generate_factory
        )
        menu = self.menuBar().addMenu(_("&Générer"))
        self.menus.append(menu)
        menu.addAction(self.generate_action)

    def generate_factory(self) -> DocumentTab | None:
        """Ask for a target and open the factory it implies, in a tab of its own.

        In its own tab because generating is not editing: the factory the user was
        looking at stays exactly where it was, and the generated one arrives as an
        ordinary document -- savable, editable, undoable -- rather than as anything
        that would have to be turned back into one.
        """
        dialog = GenerateDialog(self.game_data, self.preferences, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        try:
            # In the mode of the document it is generated from: what comes out is an
            # ordinary factory, and an ordinary factory obeys its own document.
            graph, notes = dialog.generate(self.document.graph.attachment_mode)
        except PlanError as exc:
            QMessageBox.warning(self, _("Objectif irréalisable"), str(exc))
            return None
        tab = self._reusable_tab() or self.new_tab()
        self.select_tab(tab)
        tab.document.adopt(graph)
        tab.document.solve_now()
        self.refresh_title()
        QMessageBox.information(self, _("Usine générée"), _paragraphs(notes))
        self.statusBar().showMessage(notes[0], 8000)
        return tab

    def _build_language_actions(self) -> None:
        """Two exclusive entries, in a menu of their own, next to the help.

        A menu **and** the preferences box, both calling :meth:`set_language`, for
        the reason every other setting has only one home and this one has two: it is
        the first thing somebody who cannot read the interface goes looking for, and
        the preferences dialog they would have to open is itself in a language they
        cannot read. A top-level menu bearing the word "Language" is findable
        without reading anything else.
        """
        menu = self.menuBar().addMenu("&Langue / Language")
        self.menus.append(menu)
        self.language_actions: dict[Language, QAction] = {}
        group = QActionGroup(self)
        group.setExclusive(True)
        for language in Language:
            action = QAction(language.english_name, self)
            action.setCheckable(True)
            action.setActionGroup(group)
            action.triggered.connect(
                lambda _checked=False, chosen=language: self.set_language(chosen)
            )
            menu.addAction(action)
            self.language_actions[language] = action
        self.refresh_language_actions()

    def refresh_language_actions(self) -> None:
        """Tick the language in force, and say plainly where the translation stands.

        The English entry carries its own coverage until the catalogue is finished.
        Somebody who switches and finds French sentences under English item names
        must have been told first: an announced gap is a state of the work, an
        unannounced one looks like a defect.
        """
        current = i18n.language()
        for language, action in self.language_actions.items():
            action.setChecked(language is current)
        english = self.language_actions[Language.ENGLISH]
        translated, translatable = i18n.coverage()
        # Announced unless the catalogue is provably complete. "Nothing is wrapped
        # yet" and "half of it is translated" both need saying; only "all of it"
        # does not.
        if not (translatable and translated == translatable):
            share = f" — {translated / translatable:.0%}" if translatable else ""
            english.setText(f"English (traduction en cours{share})")
            english.setStatusTip(
                _(
                    "Les noms du jeu et les nombres sont en anglais ; les phrases de "
                    "l'interface sont encore en français."
                )
            )
        else:
            english.setText(Language.ENGLISH.english_name)
            english.setStatusTip("")

    def set_language(self, language: Language | str) -> None:
        """Switch the whole interface, without restarting and without losing anything.

        Nothing here touches a document: the graphs, the undo stacks and the
        selections are exactly where they were. What is rebuilt is what *displays*
        them -- the palette entries, whose labels come from the catalogue, and the
        reports, whose node labels the engine writes at solve time. Re-solving is
        the honest way to get those: a report is a value computed in a language, and
        keeping the old one would leave French labels under an English interface.

        Immediate rather than "restart to apply", for the same reason the icon
        folder is: being told to restart is being told to find out later.
        """
        wanted = Language(language)
        if wanted is i18n.language():
            return
        i18n.set_language(wanted)
        self.preferences.language = wanted
        install_translations(wanted)

        # The palette holds one entry per recipe, labelled with the game's word for
        # it. Rebuilt rather than relabelled: the entries are also what the canvas
        # decodes a drop with, and two lists would be two truths.
        self.entries = build_entries(self.game_data)
        self.palette_widget.set_entries(self.entries)
        for tab in self.open_tabs():
            tab.scene.set_entries(self.entries)
            tab.scene.rebuild()
            tab.document.solve_now()
        self.retranslate()
        self._refresh_catalogue_summary()
        logger.info("langue de l'interface : %s", wanted.value)

    def retranslate(self) -> None:
        """Put back every label that was written once, when the window was built.

        A palette entry and a diagnostic are *recomputed* on a switch, so they follow
        the language for free. A menu title, an action and a dock title are not: they
        were set from a string at construction and nothing ever reads that string
        again. Without this the catalogue can be complete and the menu bar still say
        « Fichier », which is the difference between a translated catalogue and a
        translated application.

        The menus are **rebuilt** rather than relabelled, because the build methods
        are where the words live: relabelling would mean writing all sixty of them a
        second time, in a list that starts matching and stops. The cost is that every
        action is a new object, so everything derived from one is restored just
        below -- the ticks, the recent files, the mode of the factory in front.
        """
        direct = Qt.FindChildOption.FindDirectChildrenOnly
        for toolbar in self.findChildren(QToolBar, options=direct):
            self.removeToolBar(toolbar)
            toolbar.deleteLater()
        self.menuBar().clear()
        for menu in self.menus:
            menu.deleteLater()
        self.menus.clear()
        for action in self.findChildren(QAction, options=direct):
            self.removeAction(action)
            action.setParent(None)
            action.deleteLater()

        self._build_actions()
        self._label_docks()
        self.palette_widget.retranslate()
        self.table_panel.retranslate()
        self.diagnostics_panel.retranslate()

        # Everything a rebuilt action had to be told, and could not carry over.
        self.deployed_action.setChecked(self.preferences.deployed_rendering)
        self.refresh_attachment_mode()
        self.refresh_language_actions()
        self.refresh_recent_menu()

    def _label_docks(self) -> None:
        """The four dock titles, which are set here and nowhere else.

        The only labels written twice in this window: a dock is created once and
        outlives every rebuild, so its title cannot come from the build methods.
        """
        for dock, title in (
            (self.palette_dock, _("Palette")),
            (self.table_dock, _("Tableau")),
            (self.totals_dock, _("Totaux")),
            (self.diagnostics_dock, _("Diagnostics")),
        ):
            dock.setWindowTitle(title)

    def _build_attachment_mode_actions(self) -> None:
        """Two exclusive entries rather than one toggle.

        A toggle says what will happen next, which is the wrong thing to read on a
        setting that changes the figures: what a user needs to see at a glance is
        which mode this factory is *in*, and a checked entry says that even when
        nobody remembers what the other one was called.
        """
        menu = self.menuBar().addMenu(_("&Raccords"))
        self.menus.append(menu)
        self.attachment_mode_actions: dict[AttachmentMode, QAction] = {}
        group = QActionGroup(self)
        group.setExclusive(True)
        for mode, label, hint in (
            (
                AttachmentMode.SIMPLE,
                _("Mode simple (raccords déduits)"),
                _(
                    "Un port porte autant de lignes qu'on veut ; les raccords sont "
                    "comptés dans la liste de courses sans être dessinés."
                ),
            ),
            (
                AttachmentMode.FAITHFUL,
                _("Mode fidèle (raccords explicites)"),
                _(
                    "Un port porte une ligne, comme dans le jeu : au-delà il faut un "
                    "répartiteur ou un groupeur, et on le pose."
                ),
            ),
        ):
            action = QAction(label, self)
            action.setCheckable(True)
            action.setStatusTip(hint)
            action.setActionGroup(group)
            action.triggered.connect(
                lambda _checked=False, chosen=mode: self.set_attachment_mode(chosen)
            )
            menu.addAction(action)
            self.attachment_mode_actions[mode] = action

    def refresh_attachment_mode(self) -> None:
        """Tick the entry the document being looked at is actually in.

        Called from :meth:`_activate` and after a bascule, and never at build time:
        the menus are put together before the first tab exists.
        """
        if self._active is None:
            return
        current = self.document.graph.attachment_mode
        for mode, action in self.attachment_mode_actions.items():
            action.setChecked(mode is current)

    def set_attachment_mode(self, mode: AttachmentMode) -> bool:
        """Switch the current factory between the two modes, reporting what moved."""
        change = edits.set_attachment_mode(self.document, mode)
        if change.refusal is not None:
            QMessageBox.warning(self, _("Bascule refusée"), change.refusal)
            self.refresh_attachment_mode()
            return False
        self.refresh_attachment_mode()
        if change.notes:
            QMessageBox.information(self, _("Raccords"), _paragraphs(list(change.notes)))
            self.statusBar().showMessage(change.notes[0], 8000)
        return True

    def _build_module_actions(self) -> None:
        self.save_module_action = _action(
            self, _("Enregistrer la sélection comme module..."), "Ctrl+Shift+M", self.save_as_module
        )
        self.library_action = _action(
            self, _("Bibliothèque de modules..."), "Ctrl+B", self.show_module_library
        )
        menu = self.menuBar().addMenu(_("&Modules"))
        self.menus.append(menu)
        menu.addAction(self.save_module_action)
        menu.addAction(self.library_action)

    def _build_help_actions(self) -> None:
        self.help_action = _action(
            self, _("Gestes et raccourcis"), QKeySequence.StandardKey.HelpContents, self.show_help
        )
        menu = self.menuBar().addMenu(_("&Aide"))
        self.menus.append(menu)
        menu.addAction(self.help_action)
        menu.addSeparator()
        menu.addAction(_action(self, _("À propos"), None, self._show_about))

    def _connect(self) -> None:
        """What is wired once and for good: the shared widgets to the window.

        Nothing here mentions a document. Everything that follows the active one is
        in :meth:`_activate`, and keeping the two apart is what makes it possible to
        say that the rebinding is complete.
        """
        self.palette_widget.entryActivated.connect(self._add_at_view_centre)
        self.palette_widget.entryOpened.connect(self.show_entry_card)
        self.palette_widget.defaultTransportsChanged.connect(self._store_transports)
        self.palette_widget.filtersChanged.connect(self._store_filters)
        self.table_panel.selectionChangedTo.connect(self._table_selection_changed)
        self.diagnostics_panel.targetPicked.connect(self.reveal)
        self.diagnostics_panel.fixRequested.connect(self.apply_fix)

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

    def open_file(self, path: Path | None = None) -> bool:
        """Open a factory in its own tab, next to the ones already open.

        The file is read **before** a tab is chosen for it: a file that cannot be
        opened must not leave an empty tab behind as evidence that something was
        attempted.
        """
        if path is None:
            chosen, _filter = QFileDialog.getOpenFileName(
                self, _("Ouvrir une usine"), "", FILE_FILTER
            )
            if not chosen:
                return False
            path = Path(chosen)
        try:
            loaded = factory_file.load(path)
        except FactoryFileError as exc:
            QMessageBox.warning(self, _("Ouverture impossible"), str(exc))
            return False
        tab = self._reusable_tab() or self.new_tab()
        self.select_tab(tab)
        warnings = list(loaded.warnings)
        tab.document.adopt(loaded.graph, path, warnings)
        self.remember_recent(path)
        self.fit_to_factory()
        self._report_warnings(warnings, path.name)
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
        chosen, _filter = QFileDialog.getSaveFileName(
            self, _("Enregistrer l'usine"), suggestion, FILE_FILTER
        )
        if not chosen:
            return False
        path = Path(chosen).with_suffix(FILE_SUFFIX)
        return self._write(path)

    def _write(self, path: Path) -> bool:
        try:
            self.document.save_as(path, exporters.thumbnail_bytes(self.scene))
        except OSError as exc:
            QMessageBox.warning(
                self, _("Enregistrement impossible"), f"{path.name} : {exc.strerror}"
            )
            return False
        self.remember_recent(path)
        self.statusBar().showMessage(
            _("Usine enregistrée dans {file}.").format(file=path.name), 4000
        )
        return True

    def confirm_discard(self) -> bool:
        """Ask before throwing away unsaved work. True means "go ahead"."""
        if not self.document.is_modified:
            return True
        answer = QMessageBox.question(
            self,
            _("Modifications non enregistrées"),
            _("« {name} » a été modifiée. Enregistrer avant de continuer ?").format(
                name=self.document.display_name
            ),
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if answer == QMessageBox.StandardButton.Save:
            return self.save()
        return answer == QMessageBox.StandardButton.Discard

    def closeEvent(self, event: QCloseEvent) -> None:
        """Ask about every open factory: none forgotten, none asked twice.

        Each tab is brought to the front before its own question, so the name in
        the box is the factory on screen. Saving makes a document clean, and a
        clean document is not asked about, which is what stops the second pass
        from repeating the first. Cancelling anywhere stops the whole closing --
        the factories already saved stay saved, and nothing has been discarded.
        """
        for index in range(self.tabs.count()):
            self.tabs.setCurrentIndex(index)
            if not self.confirm_discard():
                event.ignore()
                return
        event.accept()

    # ----------------------------------------------------------- share codes

    def copy_code(self) -> str:
        code = self.document.share_code()
        QGuiApplication.clipboard().setText(code)
        dialog = ShareCodeDialog(_("Code de partage"), code, read_only=True, parent=self)
        dialog.exec()
        self.statusBar().showMessage(_("Code copié dans le presse-papiers."), 4000)
        return code

    def import_code(self, code: str | None = None) -> bool:
        """A shared factory arrives in its own tab, like an opened file."""
        if code is None:
            dialog = ShareCodeDialog(_("Importer depuis un code"), parent=self)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return False
            code = dialog.code()
        try:
            loaded = factory_file.decode_share_code(code)
        except FactoryFileError as exc:
            QMessageBox.warning(self, _("Code refusé"), str(exc))
            return False
        tab = self._reusable_tab() or self.new_tab()
        self.select_tab(tab)
        warnings = list(loaded.warnings)
        tab.document.adopt(loaded.graph, warnings=warnings)
        self.fit_to_factory()
        self._report_warnings(warnings, _("le code importé"))
        return True

    def _report_warnings(self, warnings: Sequence[str], subject: str) -> None:
        if not warnings:
            return
        QMessageBox.information(
            self,
            _("Usine ouverte avec des réserves"),
            f"{subject} :\n\n" + "\n\n".join(warnings),
        )

    # --------------------------------------------------------------- modules

    def save_as_module(self) -> bool:
        """Lift the selection out and put it in the library under a name.

        The payload is the same share code a copy produces, so the library never
        has to learn a second format -- and inherits its migration, its refusals
        and its handling of node kinds that do not exist yet.
        """
        edited = self.editing_module.get(self.current_tab)
        node_ids = [item.node.id for item in self.scene.selected_nodes()]
        if not node_ids and edited is not None:
            # A tab opened from the library **is** the module: re-saving it needs no
            # selection, which is what makes the edit round trip one gesture.
            node_ids = [node.id for node in self.document.graph.nodes]
        if not node_ids:
            QMessageBox.information(
                self,
                _("Aucune sélection"),
                _("Sélectionnez le morceau d'usine à enregistrer comme module."),
            )
            return False
        piece = clipboard.selection_graph(self.document.graph, node_ids)

        suggestion = edited.name if edited is not None else self._suggested_module_name(piece)
        dialog = SaveModuleDialog(suggestion, self)
        if edited is not None:
            dialog.description.setPlainText(edited.description)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False

        face = interface.interface_of(piece, self.game_data)
        module = FactoryModule(
            name=dialog.chosen_name(),
            share_code=factory_file.encode_share_code(piece),
            description=dialog.chosen_description(),
            inputs=face.inputs,
            outputs=face.outputs,
            thumbnail=exporters.thumbnail_bytes(self.scene, self._selection_bounds()),
        )
        # The same name over the module being edited replaces it; any other name
        # makes a second one. That is the whole of "sous le même nom ou un autre".
        replacing = (
            edited.path
            if edited is not None and edited.path is not None and edited.name == module.name
            else None
        )
        try:
            saved = module_file.save_module(module, self.module_directory, replacing=replacing)
        except ModuleError as exc:
            QMessageBox.warning(self, _("Module non enregistré"), str(exc))
            return False
        self.editing_module[self.current_tab] = saved
        if self.library is not None:
            self.library.reload()
        self.statusBar().showMessage(
            _("Module « {name} » enregistré.").format(name=saved.name), 4000
        )
        return True

    def _selection_bounds(self) -> QRectF:
        """The rectangle around what is selected, so the thumbnail shows the module."""
        bounds = QRectF()
        for item in self.scene.selected_nodes():
            bounds = bounds.united(item.sceneBoundingRect())
        return bounds.adjusted(-20, -20, 20, 20) if not bounds.isEmpty() else bounds

    def _suggested_module_name(self, piece: FactoryGraph) -> str:
        """What it makes and how much of it -- the name a reader would have typed."""
        face = interface.interface_of(piece, self.game_data)
        if not face.outputs:
            return ""
        item_class, rate = max(face.outputs.items(), key=lambda pair: pair[1])
        item = self.game_data.items.get(item_class)
        name = item.name if item else item_class
        # The item first and the rate after, rather than "40 plaque de fer/min":
        # the game's labels are singular, and inventing a plural rule for French
        # to make one suggested name read better is not a trade worth making.
        return f"{name} {formatting.number(rate)}/min"

    def show_module_library(self) -> ModuleLibraryDialog:
        """Open the library, and keep it open: inserting three in a row is the point."""
        if self.library is None:
            self.library = ModuleLibraryDialog(self.game_data, self.module_directory, self)
            self.library.insertRequested.connect(self.insert_module)
            self.library.openRequested.connect(self.open_module_in_tab)
            self.library.newFromRequested.connect(self.new_from_module)
        self.library.reload()
        self.library.show()
        self.library.raise_()
        self.library.activateWindow()
        return self.library

    def insert_module(self, module: FactoryModule) -> bool:
        """Drop a copy of the module in the middle of the view, as one undo step."""
        graph = self._module_graph(module)
        if graph is None:
            return False
        centre = self.view.mapToScene(self.view.viewport().rect().center())
        inserted = self.scene.insert_graph(
            graph, centre, _("insertion du module « {name} »").format(name=module.name)
        )
        if inserted:
            self.statusBar().showMessage(
                _(
                    "Module « {name} » inséré. C'est une copie : il ne suivra pas "
                    "les modifications du module."
                ).format(name=module.name),
                6000,
            )
        return inserted

    def open_module_in_tab(self, module: FactoryModule) -> DocumentTab | None:
        """Edit the module itself: its own tab, so the factory stays where it is."""
        graph = self._module_graph(module)
        if graph is None:
            return None
        tab = self._reusable_tab() or self.new_tab()
        self.select_tab(tab)
        tab.document.adopt(graph)
        self.editing_module[tab] = module
        self.refresh_title()
        self.fit_to_factory()
        self.statusBar().showMessage(
            _("Module « {name} » ouvert. Ctrl+Maj+M le réenregistre.").format(
                name=module.name
            ),
            6000,
        )
        return tab

    def new_from_module(self, module: FactoryModule) -> DocumentTab | None:
        """Start a factory on this module: the same thing, without the link back."""
        tab = self.open_module_in_tab(module)
        if tab is not None:
            self.editing_module.pop(tab, None)
            self.statusBar().showMessage(
                _("Nouvelle usine depuis « {name} ». C'est une copie.").format(
                    name=module.name
                ),
                6000,
            )
        return tab

    def _module_graph(self, module: FactoryModule) -> FactoryGraph | None:
        try:
            return module.graph()
        except ModuleError as exc:
            QMessageBox.warning(self, _("Module illisible"), str(exc))
            return None

    # --------------------------------------------------------------- exports

    def export_png(self, path: Path | None = None) -> bool:
        if path is None:
            chosen, _filter = QFileDialog.getSaveFileName(
                self,
                _("Exporter le canvas"),
                f"{self.document.display_name}.png",
                _("Image PNG (*.png)"),
            )
            if not chosen:
                return False
            path = Path(chosen)
        if not exporters.export_png(self.scene, path):
            QMessageBox.information(self, _("Rien à exporter"), _("L'usine est vide."))
            return False
        self.statusBar().showMessage(
            _("Canvas exporté dans {file}.").format(file=path.name), 4000
        )
        return True

    def export_pdf(self, path: Path | None = None, *, include_totals: bool = True) -> bool:
        if path is None:
            dialog = PdfOptionsDialog(self)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return False
            include_totals = dialog.include_totals()
            chosen, _filter = QFileDialog.getSaveFileName(
                self,
                _("Exporter en PDF"),
                f"{self.document.display_name}.pdf",
                _("Document PDF (*.pdf)"),
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
            self.statusBar().showMessage(
                _("Usine exportée dans {file}.").format(file=path.name), 4000
            )
        return written

    # ----------------------------------------------------------- preferences

    def apply_preferences(self) -> None:
        """Push what was stored into the widgets that show it.

        Onto **every** open canvas, not only the one in front: a preference is a
        property of the application, and finding the old colours on the tab next
        door would be a bug rather than a feature.
        """
        self.palette_widget.apply_stored(
            self.preferences.default_belt,
            self.preferences.default_pipe,
            show_alternates=self.preferences.show_alternates,
            show_events=self.preferences.show_events,
        )
        deployed = self.preferences.deployed_rendering
        self.deployed_action.setChecked(deployed)
        palette = self.preferences.item_palette
        belt, pipe = self.palette_widget.default_transports()
        for tab in self.open_tabs():
            tab.scene.set_deployed_rendering(deployed, self.preferences.deployed_ceiling)
            tab.scene.set_item_palette(palette)
            tab.scene.set_default_transports(belt, pipe)
        self.palette_widget.set_item_palette(palette)
        self.refresh_recent_menu()

    def toggle_deployed_rendering(self) -> None:
        """The Affichage menu and the preferences box set the same one setting."""
        enabled = self.deployed_action.isChecked()
        self.preferences.deployed_rendering = enabled
        for tab in self.open_tabs():
            tab.scene.set_deployed_rendering(enabled, self.preferences.deployed_ceiling)

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
        # After the rest: switching language rebuilds the palette entries the
        # preferences have just been pushed into.
        self.set_language(dialog.chosen_language())
        self.apply_preferences()
        self.statusBar().showMessage(_("Préférences enregistrées."), 4000)
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
        for tab in self.open_tabs():
            tab.scene.set_icons(self.icons)
        self._refresh_catalogue_summary()
        logger.info("%d fichier(s) d'icône indexé(s)", len(self.icons.index))

    def _store_transports(self, belt: str, pipe: str) -> None:
        self.preferences.default_belt = belt
        self.preferences.default_pipe = pipe
        for tab in self.open_tabs():
            tab.scene.set_default_transports(belt, pipe)

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
            empty = self.recent_menu.addAction(_("Aucun fichier récent"))
            empty.setEnabled(False)
            return
        for path in entries:
            action = self.recent_menu.addAction(path.name)
            action.setToolTip(str(path))
            action.triggered.connect(lambda _checked=False, target=path: self.open_file(target))
        self.recent_menu.addSeparator()
        self.recent_menu.addAction(_("Oublier la liste"), self.forget_recent)

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
            self.statusBar().showMessage(
                _("Aucune fiche pour « {entry} ».").format(entry=entry.label), 4000
            )
            return
        self.show_item_card(subject)

    def place_recipe(self, recipe_class: str) -> bool:
        """The card's "place on the canvas" button, resolved against the palette."""
        for entry in self.entries:
            if entry.kind is EntryKind.RECIPE and entry.class_name == recipe_class:
                self._add_at_view_centre(entry)
                self.statusBar().showMessage(
                    _("{entry} pose au centre de la vue.").format(entry=entry.label), 4000
                )
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
        """The window's title, and every tab's, from the documents themselves.

        All of them at once rather than only the active one: an identity change is
        rare, there are a handful of tabs, and a routine that refreshes everything
        cannot leave one stale.
        """
        for index, tab in enumerate(self.open_tabs()):
            edited = self.editing_module.get(tab)
            self.tabs.setTabText(index, tab.title(edited.name if edited else None))
            self.tabs.setTabToolTip(index, tab.tooltip())
        if self._active is None:
            return
        document = self._active.document
        marker = " •" if document.is_modified else ""
        # Spelled out rather than iconic: this is the state where a reflex Ctrl+S
        # costs somebody their nodes, so it says what it means.
        partial = _(" — OUVERTURE PARTIELLE") if document.is_partial else ""
        self.setWindowTitle(
            f"{document.display_name}{marker}{partial} — SatisPlanner {__version__} "
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
                _("Glissez une recette ou un gisement depuis la palette pour commencer.")
            )
            return
        errors = len(report.by_severity(Severity.ERROR))
        warnings = len(report.by_severity(Severity.WARNING))
        parts = [
            _("{count} nœud(s)").format(count=len(report.nodes)),
            _("{count} ligne(s)").format(count=len(report.edges)),
        ]
        if not report.converged:
            parts.append(_("Résolution NON Convergée"))
        if not report.is_sustainable:
            parts.append(_("débits non tenables : un tampon se vide"))
        if errors:
            parts.append(_("{count} erreur(s)").format(count=errors))
        if warnings:
            parts.append(_("{count} avertissement(s)").format(count=warnings))
        if not errors and not warnings and report.converged:
            parts.append(_("usine nominale"))
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
        return _(
            "{recipes} recettes, {items} items — {icons} icône(s) indexée(s), "
            "le reste est dessiné"
        ).format(
            recipes=len(self.game_data.recipes),
            items=len(self.game_data.items),
            icons=len(self.icons.index),
        )

    def _refresh_catalogue_summary(self) -> None:
        self.catalogue_label.setText(self.catalogue_summary())

    def _show_about(self) -> None:
        log_path = logging_setup.current_log_path()
        journal = (
            _("Journal : {path}<br><br>").format(path=log_path)
            if log_path is not None
            else ""
        )
        QMessageBox.about(
            self,
            _("À propos de SatisPlanner"),
            f"<b>SatisPlanner {__version__}</b><br>"
            + _(
                "Données de jeu : Satisfactory {game} (schéma de base {database}, "
                "schéma de fichier {document})<br>"
            ).format(
                game=db.GAME_VERSION,
                database=db.SCHEMA_VERSION,
                document=DOCUMENT_SCHEMA_VERSION,
            )
            # Said here because nothing else on screen distinguishes "I have no
            # icons" from "the generated fallback is working as designed", and
            # somebody installing on a second machine reads the second as the first.
            + _("Icônes : {status}<br><br>").format(status=self.icons.status.sentence())
            + _(
                "Planificateur d'usines théoriques. L'outil raisonne en <b>débits</b>, "
                "pas en géométrie 3D : ni distances, ni élévations, ni hauteur de "
                "refoulement des pompes.<br><br>"
            )
            + journal
            + _(
                "Satisfactory, ses données et ses icônes sont la propriété de "
                "Coffee Stain Studios. Aucun logo ni élément de marque du jeu n'est "
                "reproduit dans cette application."
            ),
        )


def _paragraphs(lines: list[str]) -> str:
    """The generation report as one message, a blank line between each finding."""
    return "\n\n".join(lines)


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
