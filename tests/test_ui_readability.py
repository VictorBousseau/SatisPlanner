"""What Lot 2 changed on screen, checked the way a reader would check it.

Two things, and they are independent. A node shows what it achieves beside what it
was built to achieve; and a factory can be coloured by the nature of what flows
through it. Neither may move a single rate, which is the assertion that appears in
this file more than any other.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from pytestqt.qtbot import QtBot

from satisplanner.core import formatting
from satisplanner.core.families import ItemFamily
from satisplanner.core.graph import FactoryGraph
from satisplanner.core.models import GameData
from satisplanner.ui.item_colours import DEFAULT_FAMILY_COLOURS, ItemPalette, text_colour_on
from satisplanner.ui.main_window import MainWindow
from satisplanner.ui.preferences import Preferences, PreferencesDialog
from satisplanner.ui.table_panel import COLUMN_INPUTS, COLUMN_RATIO
from tests.conftest import load_graph, temporary_settings


@pytest.fixture
def window(qtbot: QtBot, game_data: GameData, tmp_path: Path) -> Iterator[MainWindow]:
    del qtbot
    built = MainWindow(game_data, settings=temporary_settings(tmp_path))
    yield built
    built.dispose()
    built.close()
    built.deleteLater()


def starved(window: MainWindow) -> FactoryGraph:
    """Three smelters on one Mk.1 miner: the reference under-fed factory."""
    graph = load_graph("deficit")
    window.document.reset(graph)
    window.document.solve_now()
    return graph


# --------------------------------------------------------------------------- #
# Real and nominal, side by side
# --------------------------------------------------------------------------- #


def test_an_under_fed_port_shows_both_figures(window: MainWindow) -> None:
    """The whole point of §3: "60 / 90/min", not "60/min" and a subtraction to do."""
    starved(window)

    smelter = window.scene.nodes["smelter"]
    assert smelter.port_rate("Desc_OreIron_C", is_output=False) == "60 / 90/min"


def test_a_nominal_port_shows_one_figure(window: MainWindow) -> None:
    """ "90 / 90" on every healthy port would hide the one port that is not."""
    starved(window)

    mine = window.scene.nodes["mine"]
    assert mine.port_rate("Desc_OreIron_C", is_output=True) == "60/min"


def test_a_port_with_no_nameplate_shows_one_figure(window: MainWindow) -> None:
    """An exit absorbs whatever arrives; there is no second number to write."""
    starved(window)

    exit_item = window.scene.nodes["out"]
    assert " / " not in exit_item.port_rate("Desc_IronIngot_C", is_output=False)


def test_the_table_shows_the_same_pair_as_the_node(window: MainWindow) -> None:
    """One number, written the same way in both places, is the whole of §3.4."""
    starved(window)
    model = window.table_panel.model
    row = model.row_of("smelter")
    assert row is not None

    cell = model.data(model.index(row, COLUMN_INPUTS), int(Qt.ItemDataRole.DisplayRole))

    assert "60 / 90" in cell, cell


def test_a_saturated_line_shows_what_it_would_carry(window: MainWindow) -> None:
    """On a line the second figure is the rate an infinite belt would move."""
    window.document.reset(load_graph("belt_saturation"))
    window.document.solve_now()

    labels = [item.label() for item in window.scene.edges.values()]
    assert any(" / " in label for label in labels), labels


# --------------------------------------------------------------------------- #
# Formatting
# --------------------------------------------------------------------------- #


def test_a_percentage_reads_the_same_everywhere(window: MainWindow) -> None:
    """Node, table and diagnostics quote one number written one way."""
    starved(window)
    report = window.document.report
    assert report is not None
    expected = formatting.percent(report.node("smelter").ratio)
    assert expected == "66,7 %"

    model = window.table_panel.model
    row = model.row_of("smelter")
    assert row is not None
    # Named rather than counted: a column added in the middle should not make this
    # test read the one next door and pass on the wrong cell.
    ratio_cell = model.data(model.index(row, COLUMN_RATIO), int(Qt.ItemDataRole.DisplayRole))
    assert ratio_cell == expected

    messages = [item.message for item in report.diagnostics]
    assert any(expected in message for message in messages), messages


def test_a_whole_rate_keeps_no_trailing_zeros(window: MainWindow) -> None:
    starved(window)
    assert formatting.number(30.0) == "30"
    assert formatting.rate(30.0, None) == "30/min"


# --------------------------------------------------------------------------- #
# Colours
# --------------------------------------------------------------------------- #


def test_a_node_takes_the_colour_of_what_it_is_about(window: MainWindow) -> None:
    starved(window)

    mine = window.scene.nodes["mine"]
    assert mine.background_colour() == DEFAULT_FAMILY_COLOURS[ItemFamily.RAW]
    smelter = window.scene.nodes["smelter"]
    assert smelter.background_colour() == DEFAULT_FAMILY_COLOURS[ItemFamily.INGOT]


def test_the_text_follows_the_colour_it_sits_on(window: MainWindow) -> None:
    starved(window)
    mine = window.scene.nodes["mine"]

    window.scene.set_item_palette(_palette_with(ItemFamily.RAW, "#f5f5f5"))

    assert mine.background_colour() == "#f5f5f5"
    assert mine.text_colour() == text_colour_on("#f5f5f5")


def test_the_left_palette_shows_the_same_colour_as_the_canvas(window: MainWindow) -> None:
    """It is where a reader goes looking for an item, so it is where the colour helps."""
    model = window.palette_widget.model
    entry = next(
        entry
        for entry in window.entries
        if entry.subject_item(window.game_data) == "Desc_OreIron_C"
    )

    assert model.entry_colour(entry) == DEFAULT_FAMILY_COLOURS[ItemFamily.RAW]

    window.palette_widget.set_item_palette(_palette_with(ItemFamily.RAW, "#123456"))
    assert model.entry_colour(entry) == "#123456"


def test_the_report_is_identical_whatever_the_palette(window: MainWindow) -> None:
    """The assertion this whole lot rests on: a colour is not an input to the engine."""
    starved(window)
    before = window.document.report
    assert before is not None

    window.scene.set_item_palette(_palette_with(ItemFamily.RAW, "#ff00ff"))
    after = window.document.solve_now()

    assert after.model_dump_json() == before.model_dump_json()


def test_a_chosen_colour_survives_a_restart(qtbot: QtBot, tmp_path: Path) -> None:
    """Written to the settings, not to the document, and still there next time."""
    del qtbot
    settings = temporary_settings(tmp_path)
    first = Preferences(settings)
    palette = ItemPalette()
    palette.set_family(ItemFamily.FLUID, "#0a1b2c")
    palette.set_item("Desc_IronIngot_C", "#ddeeff")
    first.item_palette = palette

    # A second Preferences over the same store is what a restart looks like.
    reopened = Preferences(temporary_settings(tmp_path)).item_palette

    assert reopened.families == {ItemFamily.FLUID: "#0a1b2c"}
    assert reopened.items == {"Desc_IronIngot_C": "#ddeeff"}


def test_a_palette_is_exported_and_read_back_through_the_box(
    qtbot: QtBot, game_data: GameData, tmp_path: Path
) -> None:
    """The two buttons, taken in turn, on the widget a user actually clicks."""
    del qtbot
    preferences = Preferences(temporary_settings(tmp_path))
    dialog = PreferencesDialog(preferences, game_data)
    dialog.colours.set_family(ItemFamily.INGOT, "#abcdef")
    path = tmp_path / "palette.json"

    assert dialog.export_palette_to(path)
    assert path.is_file()

    other = PreferencesDialog(preferences, game_data)
    assert other.colours.is_default()
    assert other.import_palette_from(path)

    assert other.colours.family_colour(ItemFamily.INGOT) == "#abcdef"
    dialog.deleteLater()
    other.deleteLater()


def test_importing_a_missing_file_says_so_rather_than_raising(
    qtbot: QtBot, game_data: GameData, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del qtbot
    from PySide6.QtWidgets import QMessageBox

    warned: list[str] = []
    monkeypatch.setattr(
        QMessageBox, "warning", staticmethod(lambda *args, **_k: warned.append(str(args[1])))
    )
    dialog = PreferencesDialog(Preferences(temporary_settings(tmp_path)), game_data)

    assert dialog.import_palette_from(tmp_path / "absente.json") is False
    assert warned == ["Import impossible"]
    dialog.deleteLater()


def _palette_with(family: ItemFamily, colour: str) -> ItemPalette:
    palette = ItemPalette()
    palette.set_family(family, colour)
    return palette
