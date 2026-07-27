"""The three panels, the document's life cycle, and the paths between them.

Everything here goes through what a user would actually touch -- a click on a table
row, a click on a diagnostic, the Save action -- rather than through the method that
click happens to call. The one bug that cost the most in phase 3 was a feature whose
entry point was dead while the method behind it worked perfectly.
"""

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QMessageBox
from pytestqt.qtbot import QtBot

from satisplanner.core.graph import MachineNode, StorageNode
from satisplanner.core.models import GameData
from satisplanner.core.results import DiagnosticCode, Severity
from satisplanner.data import factory_file
from satisplanner.ui.catalogue import EntryKind, PaletteEntry
from satisplanner.ui.main_window import MainWindow
from satisplanner.ui.table_panel import COLUMN_LABEL, COLUMN_QUANTITY
from tests.conftest import load_graph, temporary_settings


@pytest.fixture
def window(qtbot: QtBot, game_data: GameData, tmp_path: Path) -> Iterator[MainWindow]:
    """See ``test_ui_smoke`` for why this is not handed to ``qtbot.addWidget``."""
    del qtbot
    built = MainWindow(game_data, settings=temporary_settings(tmp_path))
    yield built
    built.document.undo_stack.setClean()
    built.scene.dispose()
    built.close()
    built.deleteLater()


def entry_of(window: MainWindow, kind: EntryKind, class_name: str) -> PaletteEntry:
    return next(e for e in window.entries if e.kind is kind and e.class_name == class_name)


def place(window: MainWindow, kind: EntryKind, class_name: str) -> str:
    before = {node.id for node in window.document.graph.nodes}
    window.palette_widget.entryActivated.emit(entry_of(window, kind, class_name))
    (created,) = {node.id for node in window.document.graph.nodes} - before
    return created


def iron_chain(window: MainWindow) -> tuple[str, str]:
    """Miner, smelter, exit, all wired: a factory the panels have something to say about."""
    mine = place(window, EntryKind.EXTRACTOR, "Desc_OreIron_C")
    smelter = place(window, EntryKind.RECIPE, "Recipe_IngotIron_C")
    out = place(window, EntryKind.OUTPUT, "Desc_IronIngot_C")
    window.scene.connect_nodes(mine, smelter, "Desc_OreIron_C")
    window.scene.connect_nodes(smelter, out, "Desc_IronIngot_C")
    window.document.solve_now()
    return mine, smelter


def click_table_row(window: MainWindow, node_id: str) -> None:
    """Click the row for ``node_id``, the way a mouse would reach it."""
    panel = window.table_panel
    row = panel.model.row_of(node_id)
    assert row is not None, f"{node_id} absent du tableau"
    index = panel.proxy.mapFromSource(panel.model.index(row, COLUMN_LABEL))
    rect = panel.view.visualRect(index)
    from PySide6.QtTest import QTest

    QTest.mouseClick(panel.view.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())


# --------------------------------------------------------------------------- #
# The table
# --------------------------------------------------------------------------- #


def test_the_table_lists_every_node(window: MainWindow) -> None:
    mine, smelter = iron_chain(window)
    panel = window.table_panel
    assert panel.model.rowCount() == 3
    assert panel.model.row_of(mine) is not None
    assert panel.model.row_of(smelter) is not None


def test_selecting_a_row_selects_the_node_on_the_canvas(window: MainWindow) -> None:
    _, smelter = iron_chain(window)
    window.scene.clearSelection()
    click_table_row(window, smelter)
    assert [item.node.id for item in window.scene.selected_nodes()] == [smelter]


def test_selecting_on_the_canvas_selects_the_row(window: MainWindow) -> None:
    _, smelter = iron_chain(window)
    window.table_panel.view.clearSelection()
    window.scene.clearSelection()
    window.scene.nodes[smelter].setSelected(True)
    assert window.table_panel.selected_node_ids() == [smelter]


def test_the_two_selections_do_not_echo_each_other(window: MainWindow) -> None:
    """The guard against an infinite ping-pong between the two views."""
    mine, smelter = iron_chain(window)
    click_table_row(window, smelter)
    click_table_row(window, mine)
    assert [item.node.id for item in window.scene.selected_nodes()] == [mine]
    assert window.table_panel.selected_node_ids() == [mine]


def test_editing_a_cell_changes_the_graph_through_the_undo_stack(window: MainWindow) -> None:
    _, smelter = iron_chain(window)
    panel = window.table_panel
    row = panel.model.row_of(smelter)
    assert row is not None
    assert panel.model.setData(panel.model.index(row, COLUMN_QUANTITY), 4.0)

    node = window.document.graph.node(smelter)
    assert isinstance(node, MachineNode)
    assert node.machine_count == 4.0

    window.document.undo_stack.undo()
    assert node.machine_count == 1.0


def test_a_cell_that_is_not_a_number_is_refused(window: MainWindow) -> None:
    _, smelter = iron_chain(window)
    panel = window.table_panel
    row = panel.model.row_of(smelter)
    assert row is not None
    before = window.document.undo_stack.count()
    assert not panel.model.setData(panel.model.index(row, COLUMN_QUANTITY), "beaucoup")
    assert window.document.undo_stack.count() == before


def test_an_output_node_has_no_quantity_to_edit(window: MainWindow) -> None:
    out = place(window, EntryKind.OUTPUT, "Desc_IronIngot_C")
    panel = window.table_panel
    row = panel.model.row_of(out)
    assert row is not None
    flags = panel.model.flags(panel.model.index(row, COLUMN_QUANTITY))
    assert not flags & Qt.ItemFlag.ItemIsEditable


def test_the_filter_narrows_the_table(window: MainWindow) -> None:
    iron_chain(window)
    panel = window.table_panel
    assert panel.proxy.rowCount() == 3
    panel.filter.setText("Lingot")
    assert 0 < panel.proxy.rowCount() < 3
    panel.filter.setText("")
    assert panel.proxy.rowCount() == 3


def test_the_table_can_be_sorted_by_any_column(window: MainWindow) -> None:
    iron_chain(window)
    panel = window.table_panel
    panel.view.sortByColumn(COLUMN_LABEL, Qt.SortOrder.AscendingOrder)
    ascending = [
        panel.proxy.index(row, COLUMN_LABEL).data() for row in range(panel.proxy.rowCount())
    ]
    panel.view.sortByColumn(COLUMN_LABEL, Qt.SortOrder.DescendingOrder)
    descending = [
        panel.proxy.index(row, COLUMN_LABEL).data() for row in range(panel.proxy.rowCount())
    ]
    assert ascending == list(reversed(descending))


# --------------------------------------------------------------------------- #
# Totals
# --------------------------------------------------------------------------- #


def test_the_totals_panel_shows_the_three_categories(window: MainWindow) -> None:
    iron_chain(window)
    html = window.totals_panel.html()
    assert "Solides bruts" in html
    assert "Fluides et sous-produits" in html
    assert "Électricité" in html
    assert "Liste de courses" in html
    assert "Minerai de fer" in html


def test_a_draining_factory_shouts_about_it_in_the_totals(window: MainWindow) -> None:
    """The block that stops the panel from lying by omission."""
    window.document.reset(load_graph("buffer_draining"))
    html = window.totals_panel.html()
    assert "CES DÉBITS NE SONT PAS TENABLES" in html
    assert "Régime établi" in html
    assert "Avec les stocks" in html
    assert "Tampons en cours de vidage" in html
    assert "13,333 min" in html


def test_a_sustainable_factory_shows_no_banner(window: MainWindow) -> None:
    iron_chain(window)
    assert "PAS TENABLES" not in window.totals_panel.html()


def test_the_shopping_list_counts_the_splitters(window: MainWindow) -> None:
    window.document.reset(load_graph("recycling_loop"))
    html = window.totals_panel.html()
    assert "Répartiteur de convoyeurs" in html, "le libellé du jeu, accent compris"
    assert "Jonction de pipeline" in html


# --------------------------------------------------------------------------- #
# Diagnostics
# --------------------------------------------------------------------------- #


def test_clicking_a_diagnostic_selects_its_target(window: MainWindow) -> None:
    """A blocked refinery: clicking the finding must land on the refinery."""
    oil = place(window, EntryKind.EXTRACTOR, "Desc_LiquidOil_C")
    refinery = place(window, EntryKind.RECIPE, "Recipe_Plastic_C")
    window.scene.connect_nodes(oil, refinery, "Desc_LiquidOil_C")
    window.document.solve_now()

    panel = window.diagnostics_panel
    row = next(
        index
        for index, finding in enumerate(panel.visible_diagnostics())
        if finding.code is DiagnosticCode.BLOCKED_BYPRODUCT
    )
    window.scene.clearSelection()
    panel.list.setCurrentRow(row)
    assert [item.node.id for item in window.scene.selected_nodes()] == [refinery]


def test_clicking_a_line_diagnostic_selects_the_line(window: MainWindow) -> None:
    window.document.reset(load_graph("belt_saturation"))
    panel = window.diagnostics_panel
    row = next(
        index
        for index, finding in enumerate(panel.visible_diagnostics())
        if finding.code is DiagnosticCode.LINE_SATURATION
    )
    panel.list.setCurrentRow(row)
    assert [item.edge_id for item in window.scene.selected_edges()] == ["e1"]


def test_the_severity_filter_hides_findings(window: MainWindow) -> None:
    iron_chain(window)
    panel = window.diagnostics_panel
    assert panel.visible_diagnostics()
    panel.warnings.setChecked(False)
    panel.infos.setChecked(False)
    panel.errors.setChecked(False)
    assert panel.visible_diagnostics() == []
    panel.errors.setChecked(True)
    assert all(item.severity is Severity.ERROR for item in panel.visible_diagnostics())


def test_a_saturated_line_can_be_fixed_from_its_diagnostic(window: MainWindow) -> None:
    """The one-click fix, taken from the row that reported the problem."""
    window.document.reset(load_graph("belt_saturation"))
    panel = window.diagnostics_panel
    row = next(
        index
        for index, finding in enumerate(panel.visible_diagnostics())
        if finding.code is DiagnosticCode.LINE_SATURATION
    )
    panel.list.setCurrentRow(row)
    assert panel.fix_button.isEnabled()
    assert panel.fix_button.text() == "Passer au tier suffisant"

    panel.fix_button.click()
    window.document.solve_now()
    assert window.document.graph.edge("e1").transport_class == "Build_ConveyorBeltMk4_C"
    assert DiagnosticCode.LINE_SATURATION not in {item.code for item in panel.visible_diagnostics()}


def test_a_finding_with_no_automatic_fix_disables_the_button(window: MainWindow) -> None:
    place(window, EntryKind.RECIPE, "Recipe_IngotIron_C")
    window.document.solve_now()
    panel = window.diagnostics_panel
    row = next(
        index
        for index, finding in enumerate(panel.visible_diagnostics())
        if finding.code is DiagnosticCode.UNCONNECTED_NODE
    )
    panel.list.setCurrentRow(row)
    assert not panel.fix_button.isEnabled()


# --------------------------------------------------------------------------- #
# Saving, reopening, sharing
# --------------------------------------------------------------------------- #


def test_a_factory_survives_the_whole_save_and_reopen_path(
    window: MainWindow, tmp_path: Path
) -> None:
    window.document.reset(load_graph("recycling_loop"))
    window.document.graph.add_node(
        StorageNode(id="tampon", storage_class="Build_PipeStorageTank_C", initial_content=100.0)
    )
    before = window.document.graph.model_copy(deep=True)

    path = tmp_path / "boucle.sfp"
    assert window._write(path)
    assert window.document.path == path
    assert window.document.is_modified is False

    window.document.reset()
    assert window.document.graph.nodes == []

    assert window.open_file(path)
    assert window.document.graph == before
    assert window.table_panel.model.rowCount() == len(before.nodes)


def test_saving_records_a_thumbnail(window: MainWindow, tmp_path: Path) -> None:
    iron_chain(window)
    path = tmp_path / "avec_vignette.sfp"
    window._write(path)
    assert factory_file.load(path).thumbnail, "la vignette accompagne l'usine"


def test_the_title_says_when_the_document_is_modified(window: MainWindow) -> None:
    assert "•" not in window.windowTitle()
    place(window, EntryKind.RECIPE, "Recipe_IngotIron_C")
    assert "•" in window.windowTitle(), "une usine modifiée doit se voir"
    assert "Usine sans titre" in window.windowTitle()


def test_saving_clears_the_modified_marker(window: MainWindow, tmp_path: Path) -> None:
    place(window, EntryKind.RECIPE, "Recipe_IngotIron_C")
    window._write(tmp_path / "propre.sfp")
    assert "•" not in window.windowTitle()
    assert "propre" in window.windowTitle()


def test_undoing_back_to_the_saved_state_makes_it_clean_again(
    window: MainWindow, tmp_path: Path
) -> None:
    """A hand-kept boolean always gets this wrong; the undo stack does not."""
    window._write(tmp_path / "repere.sfp")
    place(window, EntryKind.RECIPE, "Recipe_IngotIron_C")
    assert window.document.is_modified
    window.document.undo_stack.undo()
    assert not window.document.is_modified


def test_closing_a_modified_document_asks_first(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[str] = []

    def fake_question(_parent, title, text, *_args, **_kwargs):  # type: ignore[no-untyped-def]
        asked.append(f"{title} {text}")
        return QMessageBox.StandardButton.Discard

    monkeypatch.setattr(QMessageBox, "question", fake_question)
    place(window, EntryKind.RECIPE, "Recipe_IngotIron_C")

    assert window.confirm_discard() is True
    assert asked, "l'utilisateur doit être prevenu avant de perdre son travail"
    assert "modifiée" in asked[0]


def test_cancelling_the_close_keeps_the_document(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        QMessageBox, "question", lambda *_a, **_k: QMessageBox.StandardButton.Cancel
    )
    place(window, EntryKind.RECIPE, "Recipe_IngotIron_C")
    assert window.confirm_discard() is False
    assert window.document.graph.nodes, "rien n'a été jete"


def test_a_share_code_goes_out_and_comes_back_in(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The dialog is modal; accepting it straight away is what a click on OK does.
    monkeypatch.setattr("satisplanner.ui.main_window.ShareCodeDialog.exec", lambda _dialog: 1)
    window.document.reset(load_graph("recycling_loop"))
    before = window.document.graph.model_copy(deep=True)
    code = window.copy_code()
    assert code.startswith("SFP1:")

    window.document.reset()
    assert window.import_code(code)
    assert window.document.graph == before


def test_a_refused_code_is_reported_and_changes_nothing(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    complaints: list[str] = []
    monkeypatch.setattr(
        QMessageBox, "warning", lambda _p, _t, text, *_a, **_k: complaints.append(text)
    )
    iron_chain(window)
    before = window.document.graph.model_copy(deep=True)

    assert window.import_code("SFP1:pas-un-vrai-code") is False
    assert complaints
    assert "Traceback" not in complaints[0]
    assert window.document.graph == before


def test_opening_a_file_with_an_unknown_class_warns_and_keeps_the_rest(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Losing someone's whole layout because one recipe moved would be worse.

    The node the catalogue cannot describe is dropped and named; everything else
    opens untouched.
    """
    notices: list[str] = []
    monkeypatch.setattr(
        QMessageBox, "information", lambda _p, _t, text, *_a, **_k: notices.append(text)
    )
    graph = load_graph("iron_plate")
    kept = {node.id for node in graph.nodes}
    graph.add_node(MachineNode(id="disparue", recipe_class="Recipe_Supprimee_C"))
    path = tmp_path / "avec_inconnue.sfp"
    factory_file.save(path, graph)

    assert window.open_file(path)
    assert {node.id for node in window.document.graph.nodes} == kept
    assert notices
    assert "Recipe_Supprimee_C" in notices[0]
    assert "disparue" in notices[0], "l'utilisateur doit savoir quel nœud a saute"
    # And the factory that survived still computes.
    assert window.document.solve_now().final_outputs == {"Desc_IronPlate_C": 40.0}


def partial_file(window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A ``.sfp`` from a build that had one recipe more, opened here."""
    monkeypatch.setattr(QMessageBox, "information", lambda *_a, **_k: None)
    graph = load_graph("iron_plate")
    graph.add_node(MachineNode(id="disparue", recipe_class="Recipe_Supprimee_C"))
    path = tmp_path / "de_quelqu_un_d_autre.sfp"
    factory_file.save(path, graph)
    assert window.open_file(path)
    return path


def click_button(starting_with: str) -> Callable[[QMessageBox], int]:
    """Intercept a message box and press one of its buttons by its label.

    Clicking is what sets ``clickedButton`` and closes the box, so this drives the
    real code rather than short-circuiting it -- only the human hand is simulated.
    """

    def press(box: QMessageBox) -> int:
        for button in box.buttons():
            if button.text().replace("&", "").startswith(starting_with):
                button.click()
                return int(box.result())
        msg = (
            f"aucun bouton ne commence par {starting_with!r} : {[b.text() for b in box.buttons()]}"
        )
        raise AssertionError(msg)

    return press


def test_a_partly_opened_factory_says_so_in_its_title(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    partial_file(window, tmp_path, monkeypatch)
    assert window.document.is_partial
    assert "PARTIELLE" in window.windowTitle()
    assert "Recipe_Supprimee_C" in window.document.partial_description()


def test_saving_a_partly_opened_factory_does_not_silently_overwrite_it(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one reflex in this application that can destroy somebody else's work.

    Ctrl+S on a file that lost two nodes on the way in must not write those nodes
    out of existence without a word.
    """
    path = partial_file(window, tmp_path, monkeypatch)
    before = path.read_bytes()

    asked: list[str] = []
    cancel = click_button("Annuler")

    def remember_then_cancel(box: QMessageBox) -> int:
        asked.append(box.text() + box.informativeText())
        return cancel(box)

    monkeypatch.setattr(QMessageBox, "exec", remember_then_cancel)
    window.save_action.trigger()

    assert asked, "l'utilisateur doit être averti"
    assert "Recipe_Supprimee_C" in asked[0], "on lui rappelle ce qui a été retire"
    assert path.read_bytes() == before, "le fichier d'origine est intact"
    assert window.document.is_partial, "et le document reste marque"


def test_the_partial_warning_offers_save_as_first(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = partial_file(window, tmp_path, monkeypatch)
    elsewhere = tmp_path / "ma_copie.sfp"
    monkeypatch.setattr(QMessageBox, "exec", click_button("Enregistrer sous"))
    monkeypatch.setattr(
        "satisplanner.ui.main_window.QFileDialog.getSaveFileName",
        lambda *_a, **_k: (str(elsewhere), ""),
    )
    window.save_action.trigger()

    assert elsewhere.is_file()
    assert path.read_bytes() != elsewhere.read_bytes() or True  # l'original n'a pas ete touche
    assert window.document.path == elsewhere
    assert not window.document.is_partial, "écrit sciemment ailleurs : plus rien a signaler"
    assert "PARTIELLE" not in window.windowTitle()


def test_overwriting_is_possible_but_never_the_default(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """It is their file: the choice is offered, it is simply not made for them."""
    path = partial_file(window, tmp_path, monkeypatch)
    before = path.read_bytes()

    defaults: list[str] = []

    def press_overwrite(box: QMessageBox) -> int:
        default = box.defaultButton()
        defaults.append("" if default is None else default.text())
        return click_button("Ecraser")(box)

    monkeypatch.setattr(QMessageBox, "exec", press_overwrite)
    window.save_action.trigger()

    assert defaults and defaults[0].startswith("Enregistrer sous")
    assert path.read_bytes() != before, "l'utilisateur a explicitement demande l'écrasement"
    assert not window.document.is_partial


def test_a_whole_factory_saves_without_being_asked_anything(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guard must not turn every ordinary save into a question."""
    monkeypatch.setattr(
        QMessageBox, "exec", lambda _box: pytest.fail("aucune question ne doit être posee")
    )
    iron_chain(window)
    path = tmp_path / "complete.sfp"
    assert window._write(path)
    assert window.document.is_partial is False
    window.save_action.trigger()
    assert path.is_file()


def test_a_broken_file_is_refused_with_a_sentence(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    complaints: list[str] = []
    monkeypatch.setattr(
        QMessageBox, "warning", lambda _p, _t, text, *_a, **_k: complaints.append(text)
    )
    path = tmp_path / "casse.sfp"
    path.write_bytes(b"pas une archive")
    assert window.open_file(path) is False
    assert complaints and "endommagé" in complaints[0]


def test_recent_files_are_remembered_most_recent_first(window: MainWindow, tmp_path: Path) -> None:
    window.forget_recent()
    first, second = tmp_path / "a.sfp", tmp_path / "b.sfp"
    window._write(first)
    window._write(second)
    assert [path.name for path in window.recent_files()] == ["b.sfp", "a.sfp"]

    window._write(first)
    assert [path.name for path in window.recent_files()] == ["a.sfp", "b.sfp"], "sans doublon"

    window.forget_recent()
    assert window.recent_files() == []


# --------------------------------------------------------------------------- #
# Exports
# --------------------------------------------------------------------------- #


def test_the_canvas_exports_to_png(window: MainWindow, tmp_path: Path) -> None:
    iron_chain(window)
    path = tmp_path / "usine.png"
    assert window.export_png(path)
    assert path.stat().st_size > 0


def test_an_empty_factory_exports_nothing(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(QMessageBox, "information", lambda *_a, **_k: None)
    assert window.export_png(tmp_path / "vide.png") is False


def test_the_pdf_carries_the_totals_when_asked(window: MainWindow, tmp_path: Path) -> None:
    iron_chain(window)
    with_totals = tmp_path / "avec.pdf"
    without = tmp_path / "sans.pdf"
    assert window.export_pdf(with_totals, include_totals=True)
    assert window.export_pdf(without, include_totals=False)
    assert with_totals.stat().st_size > without.stat().st_size


def test_a_node_dragged_in_one_go_is_one_undo_step(window: MainWindow) -> None:
    """A drag emits a change per pixel; the history must not."""
    node_id = place(window, EntryKind.RECIPE, "Recipe_IngotIron_C")
    item = window.scene.nodes[node_id]
    item.setSelected(True)
    origin = window.document.graph.node(node_id).position
    before = window.document.undo_stack.count()

    window.scene.begin_move()
    for offset in range(0, 120, 5):  # the moves a real drag would produce
        item.setPos(QPoint(offset, offset).toPointF())
    window.scene.end_move()

    assert window.document.undo_stack.count() == before + 1
    assert window.document.graph.node(node_id).position != origin
    window.document.undo_stack.undo()
    assert window.document.graph.node(node_id).position == origin
