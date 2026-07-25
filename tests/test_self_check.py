"""The checklist the executable runs on itself.

Tested here for the same reason anything else is: it is code, it can rot, and the one
place it will be run for real is a machine with no way of noticing that it has.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from satisplanner.core.models import GameData
from satisplanner.ui import self_check
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


def test_the_whole_checklist_passes(window: MainWindow, tmp_path: Path) -> None:
    passed, text = self_check.run_self_check(window, tmp_path / "verification")
    assert passed, text
    assert "ECHEC" not in text


def test_it_produces_the_files_it_claims_to(window: MainWindow, tmp_path: Path) -> None:
    """A report saying the PDF was written is worth less than the PDF."""
    directory = tmp_path / "verification"
    self_check.run_self_check(window, directory)
    produced = {path.name for path in directory.iterdir()}
    assert {
        "verification.sfp",
        "verification.png",
        "verification.pdf",
        "code_de_partage.txt",
        self_check.REPORT_FILENAME,
    } <= produced
    assert (directory / "verification.pdf").stat().st_size > 1000


def test_a_failing_step_is_a_line_and_not_a_traceback(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The checklist must survive its own failures: that is when it is read."""

    def broken(_self: self_check.SelfCheck) -> str:
        msg = "l'export a echoue"
        raise RuntimeError(msg)

    monkeypatch.setattr(self_check.SelfCheck, "_pdf", broken)
    passed, text = self_check.run_self_check(window, tmp_path / "verification")

    assert passed is False
    assert "[ECHEC] Export PDF" in text
    assert "l'export a echoue" in text
    assert "Traceback" not in text
    # And the checks after the failure still ran.
    assert text.count("[OK   ]") == 7


def test_the_report_names_where_the_resources_came_from(window: MainWindow, tmp_path: Path) -> None:
    """The first thing to look at when a packaged run misbehaves."""
    _, text = self_check.run_self_check(window, tmp_path / "verification")
    assert "depuis les sources" in text
    assert "resources" in text
