"""The help page, and the property that keeps it true.

The point of reading the shortcuts off the window's own actions is that the page
cannot drift from the application. That property is what is tested here: not "the
page mentions Ctrl+S" but "every shortcut this window binds is on the page".
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QMessageBox
from pytestqt.qtbot import QtBot

from satisplanner import __version__
from satisplanner.core.models import GameData
from satisplanner.data import db
from satisplanner.ui.help_dialog import (
    GESTURES,
    MODELLING_NOTES,
    HelpDialog,
    help_html,
    shortcut_rows,
)
from satisplanner.ui.main_window import MainWindow
from tests.conftest import temporary_settings


@pytest.fixture
def window(qtbot: QtBot, game_data: GameData, tmp_path: Path) -> Iterator[MainWindow]:
    """See ``test_ui_smoke`` for why this is not handed to ``qtbot.addWidget``."""
    del qtbot
    built = MainWindow(game_data, settings=temporary_settings(tmp_path))
    yield built
    built.dispose()
    built.close()
    built.deleteLater()


def test_every_bound_shortcut_reaches_the_page(window: MainWindow) -> None:
    bound = {
        action.shortcut().toString(QKeySequence.SequenceFormat.NativeText)
        for action in window.documented_actions()
        if not action.shortcut().isEmpty()
    }
    assert bound, "la fenêtre doit lier des raccourcis"

    page = help_html(shortcut_rows(window.documented_actions()))
    missing = [keys for keys in bound if keys not in page]
    assert not missing, f"raccourcis absents de l'aide : {missing}"


def test_the_usual_suspects_are_bound(window: MainWindow) -> None:
    rows = dict(shortcut_rows(window.documented_actions()))
    assert rows["Enregistrer"] == "Ctrl+S"
    assert rows["Ouvrir"] == "Ctrl+O"
    assert rows["Annuler"] == "Ctrl+Z"
    assert rows["Préférences"] == "Ctrl+,"
    assert rows["Gestes et raccourcis"] == "F1"


def test_menu_mnemonics_do_not_leak_into_the_page(window: MainWindow) -> None:
    page = help_html(shortcut_rows(window.documented_actions()))
    rendered = page.replace("&nbsp;", "").replace("&lt;", "").replace("&gt;", "")
    assert "&" not in rendered, "les & de menu ne sont pas du texte"


def test_the_canvas_gestures_are_all_described(window: MainWindow) -> None:
    """The part nobody can guess, and the only part written by hand."""
    page = help_html(shortcut_rows(window.documented_actions()))
    for gesture, effect in GESTURES:
        assert gesture in page
        assert effect in page
    assert "Clic milieu" in page
    assert "Molette" in page


def test_the_modelling_rules_are_stated_where_a_user_looks(window: MainWindow) -> None:
    """Rules nobody can deduce from the screen, and will otherwise assume wrongly."""
    page = help_html(shortcut_rows(window.documented_actions()))
    for note in MODELLING_NOTES:
        assert note in page
    assert "pureté d'un gisement s'applique" in page
    assert "deux nœuds" in page


def test_the_page_opens_from_the_menu(window: MainWindow, monkeypatch: pytest.MonkeyPatch) -> None:
    """The real path: the Aide action. The modal box is the documented exception."""
    seen: list[str] = []

    def capture(dialog: HelpDialog) -> int:
        seen.append(dialog.browser.toPlainText())
        return 0

    monkeypatch.setattr(HelpDialog, "exec", capture)
    window.help_action.trigger()
    assert seen and "Ctrl+S" in seen[0]


def test_about_names_both_versions(window: MainWindow, monkeypatch: pytest.MonkeyPatch) -> None:
    """Application version and game data version: they are not the same thing."""
    shown: list[str] = []
    monkeypatch.setattr(QMessageBox, "about", lambda _p, _t, text: shown.append(text))
    window._show_about()
    assert shown
    assert __version__ in shown[0]
    assert db.GAME_VERSION in shown[0]
    assert "Coffee Stain Studios" in shown[0]


def test_the_title_carries_the_versions(window: MainWindow) -> None:
    assert __version__ in window.windowTitle()
    assert db.GAME_VERSION in window.windowTitle()


def test_qt_speaks_french_too(window: MainWindow) -> None:
    """A French question over buttons reading Save and Cancel is half an interface."""
    box = QMessageBox(window)
    box.setStandardButtons(
        QMessageBox.StandardButton.Save
        | QMessageBox.StandardButton.Discard
        | QMessageBox.StandardButton.Cancel
    )
    labels = {button.text().replace("&", "") for button in box.buttons()}
    assert "Annuler" in labels
    assert "Enregistrer" in labels
    assert "Cancel" not in labels
