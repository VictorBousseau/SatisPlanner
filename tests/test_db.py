"""Database generation and reading, round-tripped through a real SQLite file."""

import hashlib
import sqlite3
from pathlib import Path

import pytest

from satisplanner.data import db
from satisplanner.data.docs_parser import GameDataset


@pytest.fixture
def database(dataset: GameDataset, tmp_path: Path) -> Path:
    path = tmp_path / "game.sqlite"
    db.build_database(dataset, path)
    return path


def test_meta_records_the_provenance(database: Path, dataset: GameDataset) -> None:
    with db.connect(database) as connection:
        meta = db.read_meta(connection)
    assert meta["schema_version"] == str(db.SCHEMA_VERSION)
    assert meta["game_version"] == db.GAME_VERSION
    assert meta["source_file"] == dataset.source_file


def test_every_table_of_the_specification_exists(database: Path) -> None:
    expected = {
        "items",
        "recipes",
        "recipe_ingredients",
        "recipe_products",
        "buildings",
        "extractors",
        "belts",
        "pipes",
    }
    with db.connect(database) as connection:
        tables = {
            row["name"]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    assert expected <= tables


def test_referential_integrity_holds(database: Path) -> None:
    with db.connect(database) as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_recipes_survive_the_round_trip(database: Path, dataset: GameDataset) -> None:
    with db.connect(database) as connection:
        reloaded = db.read_recipes(connection)
    assert reloaded == sorted(dataset.recipes, key=lambda recipe: recipe.class_name)


def test_items_survive_the_round_trip(database: Path, dataset: GameDataset) -> None:
    with db.connect(database) as connection:
        reloaded = db.read_items(connection)
    assert reloaded == list(dataset.items)


def test_transport_and_storage_survive_the_round_trip(database: Path, dataset: GameDataset) -> None:
    with db.connect(database) as connection:
        assert db.read_belts(connection) == sorted(dataset.belts, key=lambda b: b.tier)
        assert db.read_pipes(connection) == sorted(dataset.pipes, key=lambda p: p.tier)
        assert db.read_extractors(connection) == sorted(
            dataset.extractors, key=lambda e: e.class_name
        )
        assert db.read_storages(connection) == sorted(dataset.storages, key=lambda s: s.class_name)


def test_fluid_rates_are_stored_in_cubic_metres(database: Path) -> None:
    """A last line of defence against the litre bug reaching the database."""
    with db.connect(database) as connection:
        row = connection.execute(
            "SELECT amount_per_cycle, rate_per_minute FROM recipe_ingredients"
            " WHERE recipe_class = 'Recipe_Plastic_C' AND item_class = 'Desc_LiquidOil_C'"
        ).fetchone()
    assert (row["amount_per_cycle"], row["rate_per_minute"]) == (3.0, 30.0)


def test_rebuilding_is_reproducible(dataset: GameDataset, tmp_path: Path) -> None:
    """No timestamp anywhere: the committed artefact must not churn."""
    digests: list[str] = []
    for name in ("first.sqlite", "second.sqlite"):
        path = tmp_path / name
        db.build_database(dataset, path)
        digests.append(hashlib.sha256(path.read_bytes()).hexdigest())
    assert digests[0] == digests[1]


def test_the_database_opens_read_only(database: Path) -> None:
    with db.connect(database) as connection, pytest.raises(sqlite3.OperationalError):
        connection.execute("DELETE FROM items")


def test_rebuilding_over_an_existing_file_replaces_it(dataset: GameDataset, tmp_path: Path) -> None:
    path = tmp_path / "game.sqlite"
    path.write_bytes(b"not a database")
    db.build_database(dataset, path)
    with db.connect(path) as connection:
        assert db.read_meta(connection)["game_version"] == db.GAME_VERSION
