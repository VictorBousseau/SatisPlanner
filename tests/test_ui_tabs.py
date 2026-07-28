"""Several factories open at once, and the one place where that goes wrong.

Most of what a tab bar does is visible the moment it breaks: a title that does not
follow the file, a close that loses work, a paste that lands in the wrong factory.
Those are tested here the way a user meets them -- through the actions and the tab
widget, never by calling the method underneath.

One defect in this lot is **not** visible, and it is the reason this file exists.
The three panels are shared and rebound whenever the active tab changes. A rebinding
that connects the new document without disconnecting the old one breaks nothing: the
panel keeps showing exactly the right figures, it simply computes them twice, then
three times, then four, once per tab ever visited. Nobody sees it on a small factory
and everybody feels it on a large one, months later.

So the test for it does not check what a panel displays -- that would pass with the
defect in place. It counts how many times the panel is asked to redraw.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtWidgets import QMessageBox
from pytestqt.qtbot import QtBot

from satisplanner.core.graph import (
    ExternalSourceNode,
    FactoryGraph,
    MachineNode,
    OutputNode,
)
from satisplanner.core.models import GameData
from satisplanner.data import factory_file
from satisplanner.ui.diagnostics_panel import DiagnosticsPanel
from satisplanner.ui.document import SOLVE_DELAY_MS
from satisplanner.ui.document_tab import DocumentTab
from satisplanner.ui.main_window import MainWindow
from satisplanner.ui.table_panel import NodeTableModel
from satisplanner.ui.totals_panel import TotalsPanel
from tests.conftest import temporary_settings

ORE = "Desc_OreIron_C"
INGOT = "Desc_IronIngot_C"
BELT = "Build_ConveyorBeltMk3_C"


@pytest.fixture
def window(qtbot: QtBot, game_data: GameData, tmp_path: Path) -> Iterator[MainWindow]:
    """See ``test_ui_smoke`` for why this is not handed to ``qtbot.addWidget``."""
    del qtbot
    built = MainWindow(game_data, settings=temporary_settings(tmp_path))
    yield built
    built.dispose()
    built.close()
    built.deleteLater()


def a_factory(game_data: GameData, supplied: float = 90.0) -> FactoryGraph:
    """Source, smelter, exit: small, and it really produces something."""
    graph = FactoryGraph(
        nodes=[
            ExternalSourceNode(id="entree1", item_class=ORE, rate_per_minute=supplied),
            MachineNode(id="machine1", recipe_class="Recipe_IngotIron_C", machine_count=3),
            OutputNode(id="sortie1", item_class=INGOT),
        ]
    )
    graph.connect("entree1", "machine1", ORE, BELT, game_data)
    graph.connect("machine1", "sortie1", INGOT, BELT, game_data)
    return graph


def machine_count(tab: DocumentTab) -> float:
    """The one figure these tests edit, read back with its type known."""
    node = tab.document.node("machine1")
    assert isinstance(node, MachineNode)
    return node.machine_count


def three_tabs(window: MainWindow) -> list[DocumentTab]:
    """The tab the window started with, and two more, each with a factory in it."""
    tabs = [window.current_tab, window.new_tab(), window.new_tab()]
    for tab in tabs:
        tab.document.reset(a_factory(window.game_data))
    return tabs


def answer(monkeypatch: pytest.MonkeyPatch, button: QMessageBox.StandardButton) -> list[str]:
    """Every "save before closing?" box answers the same way, and says it was asked."""
    asked: list[str] = []

    def stub(_parent: object, _title: str, text: str, *_args: object) -> object:
        asked.append(text)
        return button

    monkeypatch.setattr(QMessageBox, "question", staticmethod(stub))
    return asked


# --------------------------------------------------------------------------- #
# The rebinding, which is the part that fails silently
# --------------------------------------------------------------------------- #


def test_switching_between_three_tabs_leaves_each_panel_refreshing_once(
    qtbot: QtBot, game_data: GameData, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The test the whole design of :meth:`MainWindow._activate` exists for.

    Switch back and forth between three factories several times, then make one
    edit, and count. A connection left behind at any of those switches shows up
    here as a second, third or fourth refresh -- and nowhere else, because the
    panel would still be displaying the right thing.

    The counters are installed on the classes *before* the window is built, so
    that the connections themselves are made to the counting version. Patching
    afterwards would count nothing: Qt holds the bound method it was given.
    """
    counts = {"totaux": 0, "diagnostics": 0, "tableau": 0}
    for name, owner, method in (
        ("totaux", TotalsPanel, "show_report"),
        ("diagnostics", DiagnosticsPanel, "show_report"),
        ("tableau", NodeTableModel, "refresh"),
    ):
        original = getattr(owner, method)

        def counting(self, *args, _name=name, _original=original, **kwargs):  # type: ignore[no-untyped-def]
            counts[_name] += 1
            return _original(self, *args, **kwargs)

        monkeypatch.setattr(owner, method, counting)

    built = MainWindow(game_data, settings=temporary_settings(tmp_path))
    try:
        tabs = three_tabs(built)
        for _ in range(3):
            for tab in tabs:
                built.select_tab(tab)
        qtbot.wait(SOLVE_DELAY_MS * 2)

        counts.update(totaux=0, diagnostics=0, tableau=0)
        built.scene.set_quantity("machine1", 5.0)
        built.document.solve_now()

        assert counts["totaux"] == 1, f"panneau des totaux redessine {counts['totaux']} fois"
        assert counts["diagnostics"] == 1, f"diagnostics redessines {counts['diagnostics']} fois"
        # Two is the honest number for the table and not a tolerated extra: the
        # shape of the factory changed, then its figures did, and the table shows
        # both. A duplicated connection would make it four.
        assert counts["tableau"] == 2, f"tableau redessine {counts['tableau']} fois"
    finally:
        built.dispose()
        built.close()
        built.deleteLater()


def test_the_binding_is_the_same_size_after_every_switch(window: MainWindow) -> None:
    """The structural companion to the test above, stated on the mechanism itself."""
    tabs = three_tabs(window)
    size = len(window._binding)
    assert size > 0, "le document actif doit être branché a quelque chose"
    for _ in range(4):
        for tab in tabs:
            window.select_tab(tab)
            assert len(window._binding) == size


def test_changing_tab_never_runs_the_engine(window: MainWindow, qtbot: QtBot) -> None:
    """The report of the factory being shown is already computed.

    The wait is longer than the quiet period before a recomputation, so a
    resolution merely deferred would be caught too.
    """
    tabs = three_tabs(window)
    qtbot.wait(SOLVE_DELAY_MS * 2)
    solved: list[object] = []
    for tab in tabs:
        tab.document.reportChanged.connect(solved.append)

    for _ in range(3):
        for tab in tabs:
            window.select_tab(tab)
    qtbot.wait(SOLVE_DELAY_MS * 3)

    assert solved == [], "changer d'onglet a déclenché une résolution"


def test_an_edit_solves_the_edited_factory_and_no_other(window: MainWindow, qtbot: QtBot) -> None:
    """Six factories open, one edit, one resolution. Not six."""
    tabs = [window.current_tab, *(window.new_tab() for _ in range(5))]
    for tab in tabs:
        tab.document.reset(a_factory(window.game_data))
    qtbot.wait(SOLVE_DELAY_MS * 2)
    solved: list[str] = []
    for index, tab in enumerate(tabs):
        tab.document.reportChanged.connect(lambda _report, name=str(index): solved.append(name))

    window.select_tab(tabs[2])
    window.scene.set_quantity("machine1", 4.0)
    window.document.solve_now()

    assert solved == ["2"], f"résolutions déclenchées : {solved}"


def test_annuler_undoes_in_the_factory_being_looked_at(window: MainWindow) -> None:
    """The undo group, checked through the menu entry rather than through a stack."""
    first, second = window.current_tab, window.new_tab()
    for tab in (first, second):
        tab.document.reset(a_factory(window.game_data))
    window.select_tab(first)
    window.scene.set_quantity("machine1", 7.0)
    window.select_tab(second)
    window.scene.set_quantity("machine1", 9.0)

    window.undo_action.trigger()

    assert machine_count(second) == 3.0
    assert machine_count(first) == 7.0, "annuler dans un onglet ne doit rien défaire dans un autre"


# --------------------------------------------------------------------------- #
# What a tab is
# --------------------------------------------------------------------------- #


def test_a_new_window_has_exactly_one_factory(window: MainWindow) -> None:
    assert window.tabs.count() == 1
    assert window.tabs.tabText(0) == "Usine sans titre"
    assert window.document is window.current_tab.document


def test_the_new_entry_opens_a_tab_and_leaves_the_others_alone(window: MainWindow) -> None:
    window.document.reset(a_factory(window.game_data))

    window.new_action.trigger()

    assert window.tabs.count() == 2
    assert window.tabs.currentIndex() == 1
    assert not window.document.graph.nodes, "le nouvel onglet est vide"
    assert len(window.open_tabs()[0].document.graph.nodes) == 3, "l'autre usine est intacte"


def test_the_new_entry_answers_to_both_its_shortcuts(window: MainWindow) -> None:
    """Ctrl+N because it is a new document, Ctrl+T because it is a new tab."""
    keys = {sequence.toString() for sequence in window.new_action.shortcuts()}
    assert keys == {"Ctrl+N", "Ctrl+T"}


def test_each_tab_keeps_its_own_framing_and_its_own_selection(window: MainWindow) -> None:
    """Half the point of tabs: coming back finds the factory where it was left."""
    first, second = window.current_tab, window.new_tab()
    for tab in (first, second):
        tab.document.reset(a_factory(window.game_data))

    window.select_tab(first)
    window.view.zoom_in()
    window.scene.select_nodes(["machine1"])
    zoom = window.view.transform().m11()

    window.select_tab(second)
    assert window.view.transform().m11() != zoom
    assert not window.scene.selected_nodes()

    window.select_tab(first)
    assert window.view.transform().m11() == zoom
    assert [item.node.id for item in window.scene.selected_nodes()] == ["machine1"]


def test_a_tab_title_carries_the_file_name_and_the_modified_mark(
    window: MainWindow, tmp_path: Path
) -> None:
    path = tmp_path / "fonderie.sfp"
    factory_file.save(path, a_factory(window.game_data), None)

    window.open_file(path)
    assert window.tabs.tabText(0) == "fonderie"
    assert str(path) in window.tabs.tabToolTip(0)

    window.scene.set_quantity("machine1", 4.0)
    assert window.tabs.tabText(0) == "fonderie •", "un onglet modifie doit le dire"


# --------------------------------------------------------------------------- #
# Opening, closing, and not losing anything on the way
# --------------------------------------------------------------------------- #


def test_opening_a_file_takes_over_the_blank_starting_tab(
    window: MainWindow, tmp_path: Path
) -> None:
    """Otherwise every session begins with an empty tab nobody asked for."""
    path = tmp_path / "usine.sfp"
    factory_file.save(path, a_factory(window.game_data), None)

    assert window.open_file(path)

    assert window.tabs.count() == 1
    assert window.document.path == path


def test_opening_a_second_file_opens_a_second_tab(window: MainWindow, tmp_path: Path) -> None:
    first, second = tmp_path / "une.sfp", tmp_path / "deux.sfp"
    for path in (first, second):
        factory_file.save(path, a_factory(window.game_data), None)

    window.open_file(first)
    window.open_file(second)

    assert [tab.document.path for tab in window.open_tabs()] == [first, second]
    assert window.document.path == second, "le fichier ouvert est celui qu'on regarde"


def test_a_file_that_cannot_be_opened_leaves_no_tab_behind(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty tab as the only trace of a failed opening would be a poor answer."""
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *_a, **_k: None))
    broken = tmp_path / "casse.sfp"
    broken.write_text("ceci n'est pas une usine", encoding="utf-8")

    assert window.open_file(broken) is False
    assert window.tabs.count() == 1


def test_the_partial_state_belongs_to_its_own_tab(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One factory opened in part must not put the other under suspicion.

    The tab is too narrow for the window title's spelled-out warning, so it
    carries a sign and the sentence moves to the tooltip -- where the names of
    what was dropped are still readable.
    """
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *_a, **_k: None))
    graph = a_factory(window.game_data)
    graph.add_node(MachineNode(id="disparue", recipe_class="Recipe_Supprimee_C"))
    path = tmp_path / "de_quelqu_un_d_autre.sfp"
    factory_file.save(path, graph)
    intact = tmp_path / "intacte.sfp"
    factory_file.save(intact, a_factory(window.game_data))

    window.open_file(path)
    window.open_file(intact)

    incomplete, whole = window.open_tabs()
    assert incomplete.document.is_partial and not whole.document.is_partial
    assert window.tabs.tabText(0).startswith("⚠")
    assert "Recipe_Supprimee_C" in window.tabs.tabToolTip(0)
    assert not window.tabs.tabText(1).startswith("⚠")
    assert "PARTIELLE" not in window.windowTitle(), "l'onglet regarde est le complet"


def test_a_recent_file_opens_in_its_own_tab(window: MainWindow, tmp_path: Path) -> None:
    path = tmp_path / "recente.sfp"
    factory_file.save(path, a_factory(window.game_data), None)
    window.document.reset(a_factory(window.game_data))
    window.remember_recent(path)

    entry = next(item for item in window.recent_menu.actions() if item.text() == path.name)
    entry.trigger()

    assert window.tabs.count() == 2
    assert window.document.path == path


def test_closing_a_modified_tab_asks_first(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, second = window.current_tab, window.new_tab()
    second.document.reset(a_factory(window.game_data))
    window.scene.set_quantity("machine1", 6.0)
    asked = answer(monkeypatch, QMessageBox.StandardButton.Cancel)

    assert window.close_tab(1) is False
    assert window.tabs.count() == 2, "annuler garde l'onglet"
    assert len(asked) == 1

    answer(monkeypatch, QMessageBox.StandardButton.Discard)
    assert window.close_tab(1) is True
    assert window.open_tabs() == [first]


def test_closing_an_unmodified_tab_asks_nothing(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    window.new_tab()
    asked = answer(monkeypatch, QMessageBox.StandardButton.Cancel)

    assert window.close_current_tab() is True
    assert window.tabs.count() == 1
    assert asked == []


def test_closing_the_only_tab_leaves_a_blank_one(window: MainWindow) -> None:
    """The window always has a factory in it, so no menu ever points at nothing."""
    window.document.reset(a_factory(window.game_data))
    window.document.undo_stack.setClean()

    window.close_tab_action.trigger()

    assert window.tabs.count() == 1
    assert not window.document.graph.nodes
    assert window.document.path is None


def test_the_next_tab_entry_wraps_round(window: MainWindow) -> None:
    tabs = three_tabs(window)
    window.select_tab(tabs[0])

    for expected in (tabs[1], tabs[2], tabs[0]):
        window.next_tab_action.trigger()
        assert window.current_tab is expected


def test_closing_the_window_asks_about_every_modified_factory_once(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """None forgotten, none asked twice.

    Three factories, two of them modified: exactly two questions, naming the two
    that would lose work.
    """
    tabs = three_tabs(window)
    for tab in tabs:
        tab.document.undo_stack.setClean()
    for tab, path in zip(tabs[:2], ("une", "deux"), strict=True):
        window.select_tab(tab)
        tab.document._path = Path(f"{path}.sfp")
        window.scene.set_quantity("machine1", 8.0)
    asked = answer(monkeypatch, QMessageBox.StandardButton.Discard)

    window.close()

    assert len(asked) == 2, f"questions posées : {asked}"
    assert "une" in asked[0] and "deux" in asked[1]


def test_cancelling_at_the_second_factory_keeps_the_window_open(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    tabs = three_tabs(window)
    for tab in tabs:
        window.select_tab(tab)
        window.scene.set_quantity("machine1", 8.0)
    answers = iter([QMessageBox.StandardButton.Discard, QMessageBox.StandardButton.Cancel])
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *_a: next(answers)))

    window.close()

    assert window.isVisible() is False or window.tabs.count() == 3
    assert window.tabs.count() == 3, "rien ne doit être ferme quand on annule"
    for tab in tabs:
        tab.document.undo_stack.setClean()


# --------------------------------------------------------------------------- #
# What the tabs must not break
# --------------------------------------------------------------------------- #


def test_copying_in_one_tab_and_pasting_in_another(window: MainWindow) -> None:
    """It goes through the clipboard, so it should be free. Checked, not assumed."""
    source, target = window.current_tab, window.new_tab()
    source.document.reset(a_factory(window.game_data))

    window.select_tab(source)
    window.scene.select_all()
    assert window.scene.copy_selection()

    window.select_tab(target)
    assert window.scene.paste()

    assert len(target.document.graph.nodes) == 3
    assert len(target.document.graph.edges) == 2, "les lignes internes suivent les nœuds"
    assert len(source.document.graph.nodes) == 3, "l'usine d'origine est intacte"


def test_a_preference_reaches_every_open_canvas(window: MainWindow) -> None:
    """A preference belongs to the application, not to the tab in front."""
    tabs = three_tabs(window)
    before = window.scene.deployed_rendering()

    window.deployed_action.trigger()

    assert all(tab.scene.deployed_rendering() is not before for tab in tabs)


def test_the_error_box_no_longer_speaks_of_one_factory(window: MainWindow) -> None:
    """The wording could not survive several factories being open at once."""
    del window
    from satisplanner.logging_setup import build_report

    message = build_report(ValueError("essai")).message
    assert "l'usine en cours" not in message.lower()
    assert "usines ouvertes" in message
