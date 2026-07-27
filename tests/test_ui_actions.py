"""The menus and the toolbar, taken the way a user takes them: by triggering them.

Every other interface test calls the window's methods -- ``window.open_file(path)``
-- which is a fine way to check what opening a file does and no way at all to check
that the menu entry reaches it. It did not: ``QAction.triggered`` carries a
``checked`` flag, Qt hands it to any slot with room for a positional argument, and
four File actions had exactly that room. ``Ouvrir...`` called ``open_file(False)``
and died in ``factory_file.load`` on ``False.is_file()``, with the whole suite green.

So the tests here start at :meth:`QAction.trigger` and nowhere earlier. The dialogs
an action would open are the one thing stubbed, because a modal box has nobody to
click it -- and the stub is what proves the action got that far, since reaching the
dialog means it was called with no argument rather than with a stray boolean.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtWidgets import QDialog, QFileDialog, QMessageBox
from pytestqt.qtbot import QtBot

from satisplanner.core.graph import FactoryGraph, MachineNode
from satisplanner.core.models import GameData
from satisplanner.data import factory_file
from satisplanner.ui.main_window import MainWindow, _action
from tests.conftest import temporary_settings


class OpenedDialogs(list[str]):
    """The dialogs an action reached, in order. Reaching one is the good outcome."""


@pytest.fixture
def window(qtbot: QtBot, game_data: GameData, tmp_path: Path) -> Iterator[MainWindow]:
    del qtbot
    built = MainWindow(game_data, settings=temporary_settings(tmp_path))
    yield built
    built.document.undo_stack.setClean()
    built.scene.dispose()
    built.close()
    built.deleteLater()


@pytest.fixture
def cancelled_dialogs(monkeypatch: pytest.MonkeyPatch) -> OpenedDialogs:
    """Every modal box notes that it was reached and answers "cancelled".

    Cancelling rather than raising, for two reasons. An exception thrown inside a
    slot never comes back out of ``trigger()`` -- Qt catches it -- so it could not
    be asserted on here anyway. And cancelling is a path a user takes constantly,
    which makes this the realistic stub rather than merely the convenient one.
    """
    opened = OpenedDialogs()

    def cancel_file(name: str) -> object:
        def stub(*_args: object, **_kwargs: object) -> tuple[str, str]:
            opened.append(name)
            return "", ""

        return staticmethod(stub)

    def note(name: str) -> object:
        def stub(*_args: object, **_kwargs: object) -> object:
            opened.append(name)
            return QMessageBox.StandardButton.Ok

        return staticmethod(stub)

    def reject(_self: QDialog) -> QDialog.DialogCode:
        opened.append(type(_self).__name__)
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(QFileDialog, "getOpenFileName", cancel_file("getOpenFileName"))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", cancel_file("getSaveFileName"))
    monkeypatch.setattr(QMessageBox, "warning", note("warning"))
    monkeypatch.setattr(QMessageBox, "information", note("information"))
    monkeypatch.setattr(QMessageBox, "question", note("question"))
    monkeypatch.setattr(QDialog, "exec", reject)
    return opened


def saved_factory(path: Path, game_data: GameData) -> FactoryGraph:
    """A one-node factory on disk, for the open path to actually find something."""
    graph = FactoryGraph(nodes=[MachineNode(id="machine1", recipe_class="Recipe_IngotIron_C")])
    del game_data
    factory_file.save(path, graph, None)
    return graph


def test_the_open_entry_of_the_file_menu_opens_a_file(
    window: MainWindow, tmp_path: Path, game_data: GameData, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The gesture, end to end: click Ouvrir, choose a file, see it on the canvas.

    The regression test for the defect this file was written for. It starts at
    ``trigger()``, so the ``checked`` flag Qt sends really is sent.
    """
    path = tmp_path / "usine.sfp"
    saved = saved_factory(path, game_data)
    monkeypatch.setattr(
        QFileDialog, "getOpenFileName", staticmethod(lambda *_a, **_k: (str(path), ""))
    )

    window.open_action.trigger()

    assert [node.id for node in window.document.graph.nodes] == [node.id for node in saved.nodes]
    assert window.document.path == path
    assert window.scene.nodes.keys() == {"machine1"}, "le canvas doit montrer l'usine ouverte"
    assert window.document.is_modified is False, "un fichier qui vient d'être ouvert est propre"


def test_the_open_entry_survives_being_cancelled(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Choosing nothing in the box leaves the document exactly as it was."""
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *_a, **_k: ("", "")))

    window.open_action.trigger()

    assert window.document.path is None
    assert not window.document.graph.nodes


def test_an_action_calls_its_slot_with_no_argument(window: MainWindow) -> None:
    """The guard on the class of defect rather than on its four instances.

    ``_action`` is the only place in the application where ``triggered`` is
    connected, so this one assertion covers every menu entry and every toolbar
    button there will ever be -- including the ones written after this test.
    """
    seen: list[tuple[object, ...]] = []
    action = _action(window, "Essai", None, lambda *received: seen.append(received))

    action.trigger()

    assert seen == [()], "une action ne doit rien passer a son slot, pas même l'état coche"


def test_a_checkable_action_still_reaches_its_slot(window: MainWindow) -> None:
    """Dropping the flag must not stop a checkable entry from working.

    ``Machines deployees`` is the one action that cares about being on or off, and
    it reads that from itself rather than from the signal -- which is why swallowing
    the argument is safe.
    """
    before = window.scene.deployed_rendering()

    window.deployed_action.trigger()

    assert window.scene.deployed_rendering() is not before
    assert window.deployed_action.isChecked() is not before


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("open_action", "getOpenFileName"),
        ("import_code_action", "ShareCodeDialog"),
        ("export_png_action", "getSaveFileName"),
        ("export_pdf_action", "PdfOptionsDialog"),
    ],
)
def test_the_file_actions_that_take_an_optional_argument_get_none(
    window: MainWindow, cancelled_dialogs: OpenedDialogs, name: str, expected: str
) -> None:
    """The four that were broken, named, so a fix that only half lands is caught.

    Each of these accepts an optional path or code and is meant to open a chooser
    when it is not given one. Reaching **that** chooser is the assertion, and the
    name of it is checked rather than merely that some box appeared: with the defect
    in place, ``export_png(False)`` skipped its chooser and went straight to an
    "l'usine est vide" box, which a laxer test would have accepted as a pass.
    """
    window.document.reset(
        FactoryGraph(nodes=[MachineNode(id="machine1", recipe_class="Recipe_IngotIron_C")])
    )

    getattr(window, name).trigger()

    assert expected in cancelled_dialogs, (
        f"« {name} » devait ouvrir {expected}, boites atteintes : {list(cancelled_dialogs)}"
    )
