"""Copy, cut, paste and duplicate, through the real clipboard.

Three properties carry the feature and each has a test of its own: a line that left
the selection is **not** copied, a paste is **one** undo step, and a clipboard that
holds something else does **nothing at all** -- no box, no message, no exception.

The payload is the share code, which means a paste between two windows is the same
code path as a paste inside one, and the "two windows" test builds a second document
to prove it rather than asserting the MIME type and hoping.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QMimeData
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from satisplanner.core.graph import GeneratorNode, MachineNode, ResourceNode
from satisplanner.core.models import GameData, Purity
from satisplanner.ui import clipboard
from satisplanner.ui.catalogue import EntryKind
from satisplanner.ui.main_window import MainWindow
from tests.conftest import temporary_settings


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


@pytest.fixture(autouse=True)
def _clean_clipboard() -> Iterator[None]:
    """The system clipboard is shared with the developer's own machine."""
    yield
    board = QApplication.clipboard()
    if board is not None:
        board.clear()


def place(window: MainWindow, kind: EntryKind, class_name: str, **extra: str) -> str:
    before = {node.id for node in window.document.graph.nodes}
    entry = next(
        e
        for e in window.entries
        if e.kind is kind
        and e.class_name == class_name
        and all(getattr(e, key) == value for key, value in extra.items())
    )
    window.palette_widget.entryActivated.emit(entry)
    (created,) = {node.id for node in window.document.graph.nodes} - before
    return created


def a_small_chain(window: MainWindow) -> tuple[str, str, str]:
    """Deposit -> smelter -> exit, with the exit deliberately left out of copies."""
    deposit = place(
        window, EntryKind.EXTRACTOR, "Desc_OreIron_C", extractor_class="Build_MinerMk3_C"
    )
    smelter = place(window, EntryKind.RECIPE, "Recipe_IngotIron_C")
    exit_node = place(window, EntryKind.OUTPUT, "Desc_IronIngot_C")
    assert window.scene.connect_nodes(deposit, smelter, "Desc_OreIron_C") is None
    assert window.scene.connect_nodes(smelter, exit_node, "Desc_IronIngot_C") is None
    return deposit, smelter, exit_node


# --------------------------------------------------------------- what is copied


def test_a_line_leaving_the_selection_is_not_copied(window: MainWindow) -> None:
    """The rule of the whole feature: the copy is the selection, and it comes unwired.

    Keeping the line would mean either inventing the node at the far end or leaving
    a dangling reference, and both are worse than the honest answer.
    """
    deposit, smelter, _ = a_small_chain(window)
    piece = clipboard.selection_graph(window.document.graph, [deposit, smelter])

    assert {node.id for node in piece.nodes} == {deposit, smelter}
    assert len(piece.edges) == 1, "la ligne interne survit"
    assert piece.edges[0].source == deposit and piece.edges[0].target == smelter


def test_a_single_node_is_copied_without_any_line(window: MainWindow) -> None:
    deposit, _, _ = a_small_chain(window)
    piece = clipboard.selection_graph(window.document.graph, [deposit])
    assert len(piece.nodes) == 1
    assert piece.edges == []


def test_every_parameter_rides_along(window: MainWindow) -> None:
    """A copy of an overclocked pure deposit is an overclocked pure deposit."""
    deposit = place(
        window, EntryKind.EXTRACTOR, "Desc_OreIron_C", extractor_class="Build_MinerMk3_C"
    )
    generator = place(window, EntryKind.GENERATOR, "Build_GeneratorCoal_C")
    assert window.scene.set_purity(deposit, Purity.PURE)
    assert window.scene.set_clock_speed(deposit, 2.5)
    assert window.scene.set_extractor(deposit, "Build_MinerMk2_C")
    assert window.scene.set_fuel(generator, "Desc_PetroleumCoke_C")
    window.scene.set_deployed(generator, True)

    window.scene.select_nodes([deposit, generator])
    assert window.scene.copy_selection()
    before = set(window.document.graph.node_map())
    assert window.scene.paste()
    created = set(window.document.graph.node_map()) - before
    new_deposit = next(node_id for node_id in created if node_id.startswith("gisement"))
    new_generator = next(node_id for node_id in created if node_id.startswith("generateur"))

    copied_deposit = window.document.graph.node(new_deposit)
    assert isinstance(copied_deposit, ResourceNode)
    assert copied_deposit.purity is Purity.PURE
    assert copied_deposit.clock_speed == 2.5
    assert copied_deposit.extractor_class == "Build_MinerMk2_C"

    copied_generator = window.document.graph.node(new_generator)
    assert isinstance(copied_generator, GeneratorNode)
    assert copied_generator.fuel_class == "Desc_PetroleumCoke_C"
    assert copied_generator.show_deployed is True


# ------------------------------------------------------------------ the paste


def test_a_paste_gives_new_names_and_a_new_place(window: MainWindow) -> None:
    deposit, smelter, _ = a_small_chain(window)
    window.scene.select_nodes([deposit, smelter])
    assert window.scene.copy_selection()

    before = dict(window.document.graph.node_map())
    assert window.scene.paste()
    created = set(window.document.graph.node_map()) - set(before)

    assert len(created) == 2
    assert not created & set(before), "aucun identifiant reutilise"
    for node_id in created:
        origin = before[deposit] if "gisement" in node_id else before[smelter]
        assert window.document.graph.node(node_id).position != origin.position
    # The internal line came with them, and it is a new line.
    assert len(window.document.graph.edges) == 3


def test_a_paste_is_one_undo_step(window: MainWindow) -> None:
    deposit, smelter, _ = a_small_chain(window)
    window.scene.select_nodes([deposit, smelter])
    window.scene.copy_selection()

    before = len(window.document.graph.nodes)
    depth = window.document.undo_stack.count()
    assert window.scene.paste()
    assert window.document.undo_stack.count() == depth + 1, "une seule commande"

    window.document.undo_stack.undo()
    assert len(window.document.graph.nodes) == before
    window.document.undo_stack.redo()
    assert len(window.document.graph.nodes) == before + 2


def test_a_paste_selects_what_it_created(window: MainWindow) -> None:
    """So that pasting twice in a row does not duplicate the original again."""
    deposit, _, _ = a_small_chain(window)
    window.scene.select_nodes([deposit])
    window.scene.copy_selection()
    window.scene.paste()

    selected = {item.node.id for item in window.scene.selected_nodes()}
    assert len(selected) == 1
    assert deposit not in selected


def test_pasted_identifiers_stay_readable(window: MainWindow) -> None:
    """``gisement1`` pasted becomes ``gisement2``, not a hash."""
    deposit, _, _ = a_small_chain(window)
    window.scene.select_nodes([deposit])
    window.scene.copy_selection()
    before = set(window.document.graph.node_map())
    window.scene.paste()
    (created,) = set(window.document.graph.node_map()) - before
    assert created.startswith("gisement")


# --------------------------------------------------------------- cut and Ctrl+D


def test_cutting_copies_then_deletes(window: MainWindow) -> None:
    deposit, smelter, _ = a_small_chain(window)
    window.scene.select_nodes([deposit, smelter])
    assert window.scene.cut_selection()

    remaining = set(window.document.graph.node_map())
    assert deposit not in remaining and smelter not in remaining
    assert window.scene.paste()
    assert len(window.document.graph.nodes) == 3


def test_duplicating_leaves_the_clipboard_alone(window: MainWindow) -> None:
    """Ctrl+D is frequent; losing what one was carrying to use it would be a poor trade."""
    deposit, smelter, _ = a_small_chain(window)
    window.scene.select_nodes([smelter])
    assert window.scene.copy_selection()

    window.scene.select_nodes([deposit])
    assert window.scene.duplicate_selection()

    still_there = clipboard.read()
    assert still_there is not None
    assert [node.id for node in still_there.nodes] == [smelter]


def test_duplicating_nothing_does_nothing(window: MainWindow) -> None:
    a_small_chain(window)
    window.scene.clearSelection()
    assert window.scene.duplicate_selection() is False
    assert window.scene.copy_selection() is False


# -------------------------------------------------------- between two windows


def test_a_selection_travels_between_two_windows(
    window: MainWindow, game_data: GameData, tmp_path: Path
) -> None:
    """The reason the payload goes through the system clipboard at all."""
    deposit, smelter, _ = a_small_chain(window)
    window.scene.select_nodes([deposit, smelter])
    assert window.scene.copy_selection()

    other = MainWindow(game_data, settings=temporary_settings(tmp_path / "autre"))
    try:
        assert other.scene.paste()
        assert len(other.document.graph.nodes) == 2
        assert len(other.document.graph.edges) == 1
    finally:
        other.document.undo_stack.setClean()
        other.scene.dispose()
        other.close()
        other.deleteLater()


# ------------------------------------------------------- a foreign clipboard


def test_a_clipboard_full_of_text_is_ignored_in_silence(window: MainWindow) -> None:
    """Someone who copied a URL and pressed Ctrl+V by reflex has made no mistake."""
    board = QApplication.clipboard()
    assert board is not None
    board.setText("https://example.invalid/quelque-chose")

    assert window.scene.paste() is False
    assert window.document.graph.nodes == []


def test_an_empty_clipboard_is_ignored(window: MainWindow) -> None:
    board = QApplication.clipboard()
    assert board is not None
    board.clear()
    assert window.scene.paste() is False


def test_a_corrupt_payload_is_refused_without_a_trace(window: MainWindow) -> None:
    """Our own MIME type, holding rubbish: still no exception and still no box."""
    board = QApplication.clipboard()
    assert board is not None
    payload = QMimeData()
    payload.setData(clipboard.CLIPBOARD_MIME, b"SFP1:ceci-n-est-pas-du-zlib")
    board.setMimeData(payload)

    assert clipboard.read() is None
    assert window.scene.paste() is False
    assert window.document.graph.nodes == []


def test_a_code_from_a_newer_schema_is_refused_quietly(window: MainWindow) -> None:
    """The share-code decoder already refuses it in French; here it must be silent."""
    del window
    import base64
    import json
    import zlib

    envelope = {
        "manifest": {"schema_version": 99, "game_version": "1.2"},
        "graph": {"schema_version": 99, "nodes": [], "edges": []},
    }
    packed = base64.urlsafe_b64encode(zlib.compress(json.dumps(envelope).encode("utf-8"))).decode(
        "ascii"
    )
    assert clipboard.decode(f"SFP1:{packed}") is None


def test_the_clipboard_carries_a_share_code(window: MainWindow) -> None:
    """One serialisation format for the whole application, not two."""
    deposit, _, _ = a_small_chain(window)
    code = clipboard.encode(window.document.graph, [deposit])
    assert code is not None
    assert code.startswith("SFP1:")
    restored = clipboard.decode(code)
    assert restored is not None
    node = restored.node(deposit)
    assert isinstance(node, ResourceNode)


def test_copying_leaves_a_readable_code_behind(window: MainWindow) -> None:
    """A clipboard holds one thing: ours was always going to replace the text.

    Since it cannot be avoided, what is left behind is the share code rather than
    nothing -- so a paste into a chat window carries the factory.
    """
    board = QApplication.clipboard()
    assert board is not None
    board.setText("un texte quelconque")

    deposit, _, _ = a_small_chain(window)
    window.scene.select_nodes([deposit])
    assert window.scene.copy_selection()
    assert board.text().startswith("SFP1:")
    # ...and reading still goes through the private type, not through the text.
    assert clipboard.read() is not None


def test_a_pasted_machine_is_a_real_node_the_engine_solves(window: MainWindow) -> None:
    """Not a picture: the copy takes part in the calculation like everything else."""
    deposit, smelter, _ = a_small_chain(window)
    window.scene.select_nodes([smelter])
    window.scene.copy_selection()
    before = set(window.document.graph.node_map())
    window.scene.paste()
    (created,) = set(window.document.graph.node_map()) - before

    report = window.document.solve_now()
    solution = report.node(created)
    assert isinstance(window.document.graph.node(created), MachineNode)
    assert solution.building_class == "Build_SmelterMk1_C"
    assert deposit in window.document.graph.node_map()
