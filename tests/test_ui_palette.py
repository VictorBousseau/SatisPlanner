"""Palette: what it offers, what the toggles hide, and what a drag carries."""

import pytest
from pytestqt.qtbot import QtBot

from satisplanner.core.graph import (
    MachineNode,
    Node,
    OutputNode,
    ResourceNode,
    StorageNode,
)
from satisplanner.core.models import GameData
from satisplanner.ui.catalogue import (
    EntryKind,
    PaletteEntry,
    build_entries,
    fold,
    machine_choices,
)
from satisplanner.ui.icon_provider import IconProvider
from satisplanner.ui.palette import ANY_MACHINE, PaletteWidget, decode_entry, encode_entry


@pytest.fixture
def entries(game_data: GameData) -> list[PaletteEntry]:
    return build_entries(game_data)


@pytest.fixture
def palette(qtbot: QtBot, game_data: GameData) -> PaletteWidget:
    widget = PaletteWidget(game_data, IconProvider(), build_entries(game_data))
    qtbot.addWidget(widget)
    return widget


# --------------------------------------------------------------------------- #
# Catalogue
# --------------------------------------------------------------------------- #


def test_folding_ignores_case_and_accents() -> None:
    assert fold("Résidus de Pétrole Lourd") == "residus de petrole lourd"
    assert fold("Vis") == "vis"


def test_search_matches_every_word_independently(entries: list[PaletteEntry]) -> None:
    """ "plaque fer" finds the recipe whatever order the words come in."""
    plate = next(e for e in entries if e.class_name == "Recipe_IronPlate_C")
    assert plate.matches("plaque")
    assert plate.matches("plaque fer")
    assert plate.matches("fer plaque")
    assert not plate.matches("plaque cuivre")


def test_every_entry_kind_is_offered(entries: list[PaletteEntry]) -> None:
    kinds = {entry.kind for entry in entries}
    assert kinds == set(EntryKind), "chaque type de noeud doit etre posable"


def _made(entries: list[PaletteEntry], kind: EntryKind, class_name: str) -> Node:
    entry = next(e for e in entries if e.kind is kind and e.class_name == class_name)
    return entry.make_node("n1", (10.0, 20.0))


def test_an_entry_builds_the_node_it_stands_for(entries: list[PaletteEntry]) -> None:
    assert isinstance(_made(entries, EntryKind.RECIPE, "Recipe_IngotIron_C"), MachineNode)
    assert isinstance(_made(entries, EntryKind.EXTRACTOR, "Desc_OreIron_C"), ResourceNode)
    assert isinstance(_made(entries, EntryKind.STORAGE, "Build_StorageContainerMk1_C"), StorageNode)
    assert _made(entries, EntryKind.RECIPE, "Recipe_IngotIron_C").position == (10.0, 20.0)

    flare = _made(entries, EntryKind.SINK, "Desc_HeavyOilResidue_C")
    assert isinstance(flare, OutputNode)
    assert flare.is_sink is True, "un rejet assume absorbe sans compter dans la production"


def test_a_miner_is_offered_once_per_ore(entries: list[PaletteEntry]) -> None:
    """A miner takes any solid node, so the palette lists the ore, not the drill."""
    mk1 = [
        entry
        for entry in entries
        if entry.kind is EntryKind.EXTRACTOR and entry.extractor_class == "Build_MinerMk1_C"
    ]
    ores = {entry.class_name for entry in mk1}
    assert {"Desc_OreIron_C", "Desc_Coal_C", "Desc_OreCopper_C"} <= ores
    assert "Desc_LiquidOil_C" not in ores, "le petrole n'est pas un solide"


def test_the_water_extractor_names_its_own_resource(entries: list[PaletteEntry]) -> None:
    pumps = [entry for entry in entries if entry.kind is EntryKind.WATER_EXTRACTOR]
    assert [entry.class_name for entry in pumps] == ["Build_WaterPump_C"]


def test_machine_choices_are_the_production_buildings(game_data: GameData) -> None:
    labels = dict(machine_choices(game_data))
    assert labels["Build_SmelterMk1_C"] == "Fonderie"
    assert labels["Build_FoundryMk1_C"] == "Fonderie avancée"
    assert "Build_ConveyorBeltMk1_C" not in labels


# --------------------------------------------------------------------------- #
# Filters
# --------------------------------------------------------------------------- #


def test_event_content_is_hidden_by_default(palette: PaletteWidget) -> None:
    assert palette.events.isChecked() is False
    assert not any(entry.is_event for entry in palette.visible_entries())
    assert any(entry.is_event for entry in palette.entries), "mais il est bien dans la base"

    palette.events.setChecked(True)
    assert any(entry.is_event for entry in palette.visible_entries())


def test_alternate_recipes_can_be_hidden(palette: PaletteWidget) -> None:
    assert palette.alternates.isChecked() is True
    assert any(entry.is_alternate for entry in palette.visible_entries())

    palette.alternates.setChecked(False)
    assert not any(entry.is_alternate for entry in palette.visible_entries())


def test_filtering_by_machine_keeps_only_its_recipes(palette: PaletteWidget) -> None:
    index = palette.machine.findData("Build_OilRefinery_C")
    assert index > 0
    palette.machine.setCurrentIndex(index)
    visible = palette.visible_entries()
    assert visible
    assert all(entry.machine_class == "Build_OilRefinery_C" for entry in visible)

    palette.machine.setCurrentIndex(palette.machine.findData(ANY_MACHINE))
    assert len(palette.visible_entries()) > len(visible)


def test_searching_narrows_the_list(palette: PaletteWidget) -> None:
    everything = len(palette.visible_entries())
    palette.search.setText("petrole")
    narrowed = palette.visible_entries()
    assert 0 < len(narrowed) < everything
    assert all(entry.matches("petrole") for entry in narrowed)


# --------------------------------------------------------------------------- #
# Drag payload
# --------------------------------------------------------------------------- #


def test_a_drag_payload_round_trips_through_the_catalogue(entries: list[PaletteEntry]) -> None:
    for entry in (entries[0], entries[len(entries) // 2], entries[-1]):
        assert decode_entry(encode_entry(entry), entries) == entry


def test_a_malformed_payload_is_ignored_rather_than_trusted(entries: list[PaletteEntry]) -> None:
    assert decode_entry(b"n'importe quoi", entries) is None
    assert decode_entry(b"recipe\tRecipe_Inexistante_C\t", entries) is None
    assert decode_entry(b"\xff\xfe\x00", entries) is None


def test_the_default_tiers_are_announced(palette: PaletteWidget) -> None:
    belt, pipe = palette.default_transports()
    assert belt == "Build_ConveyorBeltMk1_C"
    assert pipe == "Build_Pipeline_C"

    seen: list[tuple[str, str]] = []
    palette.defaultTransportsChanged.connect(lambda b, p: seen.append((b, p)))
    palette.belt_tier.setCurrentIndex(palette.belt_tier.findData("Build_ConveyorBeltMk4_C"))
    assert seen[-1] == ("Build_ConveyorBeltMk4_C", "Build_Pipeline_C")
