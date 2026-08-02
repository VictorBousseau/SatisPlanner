"""The generator seen from the window: the dialog, the menu, and the preference.

The factory itself is pinned by ``tests/test_planner``; what is checked here is
that a user can actually reach it, that what comes back is an ordinary document,
and that the recipe choices survive being closed and reopened -- which is the whole
of "réutilisable" in the specification.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtWidgets import QDialog
from pytestqt.qtbot import QtBot

from satisplanner.core import engine
from satisplanner.core.graph import MachineNode
from satisplanner.core.models import GameData
from satisplanner.ui.generate import GenerateDialog, decode_choices, encode_choices
from satisplanner.ui.main_window import MainWindow
from satisplanner.ui.preferences import Preferences
from tests.conftest import temporary_settings


@pytest.fixture
def window(qtbot: QtBot, game_data: GameData, tmp_path: Path) -> Iterator[MainWindow]:
    del qtbot
    built = MainWindow(game_data, settings=temporary_settings(tmp_path))
    yield built
    built.dispose()
    built.close()
    built.deleteLater()


def _swallow(*_args: object, **_kwargs: object) -> None:
    """The message box the window shows once the factory is there."""


def _accept(_dialog: GenerateDialog) -> int:
    return int(QDialog.DialogCode.Accepted)


def _raw_ore(_dialog: GenerateDialog) -> str:
    return "Desc_OreIron_C"


def choose(dialog: GenerateDialog, item_class: str) -> None:
    """Pick a target the way a user does: type in the box, then take the row."""
    dialog.search.setText(dialog.game_data.item(item_class).display_name_fr)
    for row in range(dialog.items.count()):
        entry = dialog.items.item(row)
        if entry.data(0x0100) == item_class:  # Qt.ItemDataRole.UserRole
            dialog.items.setCurrentRow(row)
            return
    msg = f"{item_class} introuvable dans la liste"
    raise AssertionError(msg)


def test_the_dialog_generates_an_ordinary_factory(
    window: MainWindow, game_data: GameData
) -> None:
    dialog = GenerateDialog(game_data, window.preferences, window)
    choose(dialog, "Desc_IronPlateReinforced_C")
    dialog.rate.setValue(10.0)

    graph, notes = dialog.generate()
    report = engine.solve(graph, game_data)
    assert report.final_outputs == {"Desc_IronPlateReinforced_C": 10.0}
    assert report.diagnostics == ()
    assert notes and "À RÉGLER" in " ".join(notes)
    dialog.deleteLater()


def test_generating_opens_a_tab_that_can_be_edited_at_once(
    window: MainWindow, game_data: GameData, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No generated mode to leave: the tab is a document like any other.

    The dialog is answered by hand rather than shown, which is the only part of
    this the test cannot click; everything after it is the real path.
    """
    prepared: dict[str, object] = {}

    def answer(self: GenerateDialog) -> int:
        choose(self, "Desc_Cable_C")
        self.rate.setValue(60.0)
        prepared["dialog"] = self
        return int(QDialog.DialogCode.Accepted)

    monkeypatch.setattr(GenerateDialog, "exec", answer)
    monkeypatch.setattr(
        "satisplanner.ui.main_window.QMessageBox.information", _swallow
    )

    before = len(window.open_tabs())
    tab = window.generate_factory()
    assert tab is not None
    assert len(window.open_tabs()) >= before

    document = tab.document
    assert document.report is not None
    assert document.report.final_outputs == {"Desc_Cable_C": 60.0}

    # Ordinary means editable, and editable means undoable.
    machine = next(node for node in document.graph.nodes if isinstance(node, MachineNode))
    from satisplanner.ui import edits

    assert edits.set_quantity(document, machine.id, 99.0) is None
    assert document.undo_stack.count() == 1
    document.undo_stack.undo()


def test_a_target_that_cannot_be_built_is_refused_with_its_reason(
    window: MainWindow, game_data: GameData, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A raw ore is not a target, and the refusal says so rather than crashing."""
    seen: list[str] = []
    monkeypatch.setattr(GenerateDialog, "exec", _accept)
    monkeypatch.setattr(
        "satisplanner.ui.main_window.QMessageBox.warning",
        lambda _parent, _title, message: seen.append(message),
    )
    monkeypatch.setattr(GenerateDialog, "target", _raw_ore)

    assert window.generate_factory() is None
    assert seen and "ne se fabrique pas" in seen[0]


def test_the_recipe_choices_are_kept_between_two_dialogs(
    window: MainWindow, game_data: GameData
) -> None:
    """Saved as a preference, because it is one: it is not about any one factory."""
    dialog = GenerateDialog(game_data, window.preferences, window)
    dialog.choices["Desc_Plastic_C"] = "Recipe_Alternate_Plastic_1_C"
    choose(dialog, "Desc_Cable_C")
    dialog.accept()
    dialog.deleteLater()

    again = GenerateDialog(game_data, window.preferences, window)
    assert again.choices == {"Desc_Plastic_C": "Recipe_Alternate_Plastic_1_C"}
    again.deleteLater()


def test_an_unreadable_preference_is_worth_no_crash(tmp_path: Path) -> None:
    """The worst a corrupt entry can do is send the generator back to the standard."""
    assert decode_choices("") == {}
    assert decode_choices("{pas du json") == {}
    assert decode_choices('["une", "liste"]') == {}
    assert decode_choices(encode_choices({"a": "b"})) == {"a": "b"}

    preferences = Preferences(temporary_settings(tmp_path))
    preferences.recipe_choices = {"Desc_Plastic_C": "Recipe_Alternate_Plastic_1_C"}
    assert preferences.recipe_choices == {"Desc_Plastic_C": "Recipe_Alternate_Plastic_1_C"}
