"""Shared fixtures: the parsed dataset extracted from the game's locale files.

``tests/fixtures/docs_*.json`` are real slices of the game files, produced by
``tools/extract_fixture.py`` and kept in their original encoding (UTF-16 LE with a
BOM) so that the tests exercise the same decoding path as production.
"""

from pathlib import Path

import pytest

from satisplanner.core.graph import FactoryGraph
from satisplanner.core.models import GameData, Recipe, RecipeSlot
from satisplanner.data.docs_parser import (
    GameDataset,
    french_labels,
    parse_dataset,
    read_locale,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures"
REFERENCE_FIXTURE = FIXTURE_DIR / "docs_en-US.json"
FRENCH_FIXTURE = FIXTURE_DIR / "docs_fr.json"
GRAPH_DIR = FIXTURE_DIR / "graphs"


@pytest.fixture(scope="session")
def dataset() -> GameDataset:
    """The fixture slice, parsed exactly as the CLI would parse the real files."""
    return parse_dataset(
        read_locale(REFERENCE_FIXTURE),
        french_labels(read_locale(FRENCH_FIXTURE)),
        source_file=REFERENCE_FIXTURE.name,
        french_file=FRENCH_FIXTURE.name,
    )


@pytest.fixture(scope="session")
def recipes(dataset: GameDataset) -> dict[str, Recipe]:
    return {recipe.class_name: recipe for recipe in dataset.recipes}


@pytest.fixture(scope="session")
def game_data(dataset: GameDataset) -> GameData:
    """The catalogue as injected into the engine."""
    return dataset.to_game_data()


def load_graph(name: str) -> FactoryGraph:
    """Load one of the JSON factory fixtures from ``tests/fixtures/graphs``."""
    return FactoryGraph.model_validate_json(
        (GRAPH_DIR / f"{name}.json").read_text(encoding="utf-8")
    )


def slot_of(slots: tuple[RecipeSlot, ...], item_class: str) -> RecipeSlot:
    """The slot for one item, failing the test loudly if the recipe has no such slot."""
    for slot in slots:
        if slot.item_class == item_class:
            return slot
    available = [slot.item_class for slot in slots]
    msg = f"{item_class} absent de la recette ; presents : {available}"
    raise AssertionError(msg)
