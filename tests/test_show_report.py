"""The console demonstration script, exercised on the fixture factories.

It is the phase 2 deliverable, so it gets tested rather than only demonstrated.
"""

import logging
import sys
from pathlib import Path

import pytest

from satisplanner.core import engine
from satisplanner.core.models import GameData
from tests.conftest import GRAPH_DIR, load_graph

TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import show_report  # noqa: E402  (needs the path insertion above)


def test_the_report_prints_every_section(
    game_data: GameData, caplog: pytest.LogCaptureFixture
) -> None:
    report = engine.solve(load_graph("plastic_chain"), game_data)
    with caplog.at_level(logging.INFO):
        show_report.show(report, game_data)
    output = caplog.text

    for section in ("NOEUDS", "LIGNES", "BILAN", "LISTE DE COURSES", "DIAGNOSTICS"):
        assert section in output
    assert "Raffinerie" in output
    assert "Résidus de pétrole lourd" in output
    assert "160.0 MW" in output
    assert "l'usine est nominale" in output


def test_the_report_shows_diagnostics_when_there_are_any(
    game_data: GameData, caplog: pytest.LogCaptureFixture
) -> None:
    report = engine.solve(load_graph("blocked_byproduct"), game_data)
    with caplog.at_level(logging.INFO):
        show_report.show(report, game_data)
    assert "ERREUR" in caplog.text
    assert "totalement bloquée" in caplog.text


def test_the_cli_needs_a_database(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Without the generated database the script says what to run, and does not crash."""
    with caplog.at_level(logging.ERROR):
        code = show_report.main(
            [str(GRAPH_DIR / "iron_plate.json"), "--database", str(tmp_path / "absent.sqlite")]
        )
    assert code == 2
    assert "satisplanner.data.build" in caplog.text


def test_every_fixture_factory_can_be_rendered(
    game_data: GameData, caplog: pytest.LogCaptureFixture
) -> None:
    """A blunt guard against a formatting crash on some corner of the report."""
    for path in sorted(GRAPH_DIR.glob("*.json")):
        report = engine.solve(load_graph(path.stem), game_data)
        with caplog.at_level(logging.INFO):
            show_report.show(report, game_data)
        assert caplog.text
        caplog.clear()
