"""The build CLI, driven against the fixture instead of a real game installation."""

import logging
from pathlib import Path

import pytest

from satisplanner.data import db
from satisplanner.data.build import main, scoped_items
from satisplanner.data.docs_parser import GameDataset
from tests.conftest import FIXTURE_DIR


def test_cli_builds_a_usable_database(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """--game-dir may point straight at a Docs-like directory, which the fixture is."""
    output = tmp_path / "game.sqlite"
    with caplog.at_level(logging.INFO):
        code = main(
            [
                "--game-dir",
                str(FIXTURE_DIR),
                "--output",
                str(output),
                "--icons-dir",
                str(tmp_path / "no-icons"),
            ]
        )
    assert code == 0
    assert output.is_file()
    with db.connect(output) as connection:
        assert db.read_recipes(connection)

    report = caplog.text
    assert "docs_en-US.json" in report
    assert "Recettes" in report
    assert "convoyeur Mk.6" in report


def test_cli_reports_a_missing_game_directory(tmp_path: Path) -> None:
    argv = ["--game-dir", str(tmp_path / "absent"), "--output", str(tmp_path / "x.sqlite")]
    assert main(argv) == 2


def test_cli_reports_missing_icons(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO):
        main(
            [
                "--game-dir",
                str(FIXTURE_DIR),
                "--output",
                str(tmp_path / "game.sqlite"),
                "--icons-dir",
                str(tmp_path / "empty"),
            ]
        )
    assert "sans icone" in caplog.text


def test_scope_excludes_equipment_but_keeps_raw_resources(dataset: GameDataset) -> None:
    scoped = {item.class_name for item in scoped_items(dataset)}
    assert "Desc_OreIron_C" in scoped
    assert "Desc_HeavyOilResidue_C" in scoped
    assert "Desc_Computer_C" in scoped
