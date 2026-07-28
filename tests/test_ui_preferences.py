"""Settings that survive a restart, and the box that changes them.

The interface parts go through what a user touches -- a checkbox in the palette, the
Preferences action -- and never through the setter behind it. The modal box is the
documented exception: its ``exec`` is intercepted, but everything it does on the way
out is the real code.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QDialog
from pytestqt.qtbot import QtBot

from satisplanner.core.models import GameData, ItemForm
from satisplanner.ui.catalogue import EntryKind, transport_choices
from satisplanner.ui.main_window import MainWindow
from satisplanner.ui.preferences import Preferences, PreferencesDialog
from tests.conftest import temporary_settings


@pytest.fixture
def store(tmp_path: Path) -> QSettings:
    return temporary_settings(tmp_path)


@pytest.fixture
def window(qtbot: QtBot, game_data: GameData, store: QSettings) -> Iterator[MainWindow]:
    """See ``test_ui_smoke`` for why this is not handed to ``qtbot.addWidget``."""
    del qtbot
    built = MainWindow(game_data, settings=store)
    yield built
    built.dispose()
    built.close()
    built.deleteLater()


def place(window: MainWindow, kind: EntryKind, class_name: str) -> str:
    before = {node.id for node in window.document.graph.nodes}
    entry = next(e for e in window.entries if e.kind is kind and e.class_name == class_name)
    window.palette_widget.entryActivated.emit(entry)
    (created,) = {node.id for node in window.document.graph.nodes} - before
    return created


# --------------------------------------------------------------------- store


def test_defaults_are_the_documented_ones(store: QSettings) -> None:
    preferences = Preferences(store)
    assert preferences.show_alternates is True
    assert preferences.show_events is False, "le contenu d'événement est masque par défaut"
    assert preferences.max_recent_files == 8
    assert preferences.icon_directory is None
    assert preferences.effective_icon_directory.name == "icons"


def test_a_false_boolean_comes_back_false(store: QSettings, tmp_path: Path) -> None:
    """``QSettings`` returns "false" as a string, and ``bool("false")`` is ``True``.

    This is the whole reason the typed accessors exist, so it is checked through a
    second store reading the same file rather than through the object that wrote it.
    """
    Preferences(store).show_alternates = False
    store.sync()

    reopened = Preferences(temporary_settings(tmp_path))
    assert reopened.show_alternates is False


def test_recent_files_are_bounded_and_ordered(store: QSettings, tmp_path: Path) -> None:
    preferences = Preferences(store)
    preferences.max_recent_files = 3
    for index in range(5):
        preferences.remember_recent(tmp_path / f"usine{index}.sfp")
    assert [path.name for path in preferences.recent_files()] == [
        "usine4.sfp",
        "usine3.sfp",
        "usine2.sfp",
    ]

    preferences.remember_recent(tmp_path / "usine3.sfp")
    names = [path.name for path in preferences.recent_files()]
    assert names[0] == "usine3.sfp"
    assert names.count("usine3.sfp") == 1, "pas de doublon"


def test_lowering_the_limit_shrinks_the_list(store: QSettings, tmp_path: Path) -> None:
    """A preference that visibly does nothing is worse than no preference."""
    preferences = Preferences(store)
    for index in range(6):
        preferences.remember_recent(tmp_path / f"u{index}.sfp")
    preferences.max_recent_files = 2
    assert len(preferences.recent_files()) == 2


# -------------------------------------------------------------------- dialog


def test_the_box_writes_nothing_until_it_is_accepted(
    qtbot: QtBot, store: QSettings, game_data: GameData
) -> None:
    preferences = Preferences(store)
    dialog = PreferencesDialog(preferences, game_data)
    qtbot.addWidget(dialog)
    dialog.alternates.setChecked(False)
    dialog.max_recent.setValue(2)
    dialog.reject()
    assert preferences.show_alternates is True
    assert preferences.max_recent_files == 8

    dialog.alternates.setChecked(False)
    dialog.accept()
    assert preferences.show_alternates is False


# -------------------------------------------------------------------- window


def test_a_filter_toggled_in_the_palette_is_still_off_next_time(
    window: MainWindow, store: QSettings, game_data: GameData
) -> None:
    """The real path: the user clicks the checkbox, and the setting follows."""
    window.palette_widget.events.setChecked(True)
    window.palette_widget.alternates.setChecked(False)
    assert Preferences(store).show_events is True
    assert Preferences(store).show_alternates is False

    reopened = MainWindow(game_data, settings=store)
    try:
        assert reopened.palette_widget.events.isChecked() is True
        assert reopened.palette_widget.alternates.isChecked() is False
        assert not any(entry.is_alternate for entry in reopened.palette_widget.visible_entries())
    finally:
        reopened.dispose()
        reopened.close()
        reopened.deleteLater()


def test_the_stored_tier_is_what_a_new_line_is_built_with(
    window: MainWindow, store: QSettings, game_data: GameData
) -> None:
    second_belt = transport_choices(game_data, ItemForm.SOLID)[1][0]
    window.palette_widget.belt_tier.setCurrentIndex(1)
    assert Preferences(store).default_belt == second_belt

    mine = place(window, EntryKind.EXTRACTOR, "Desc_OreIron_C")
    smelter = place(window, EntryKind.RECIPE, "Recipe_IngotIron_C")
    assert window.scene.connect_nodes(mine, smelter, "Desc_OreIron_C") is None
    (edge,) = window.document.graph.edges
    assert edge.transport_class == second_belt


def test_choosing_an_icon_folder_indexes_it_at_once(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Someone who has just run FModel wants to know now whether it worked."""
    folder = tmp_path / "mes_icones"
    folder.mkdir()
    # A one-pixel PNG, enough for the index: it resolves by file name.
    (folder / "Desc_Inexistant_256.png").write_bytes(
        bytes.fromhex(
            "89504e470d0a1a0a0000000d494844520000000100000001080600000"
            "01f15c4890000000a49444154789c6300010000050001"
            "0d0a2db40000000049454e44ae426082"
        )
    )
    before = len(window.icons.index)

    def accept_with_folder(dialog: PreferencesDialog) -> int:
        dialog.icon_directory.setText(str(folder))
        dialog.accept()
        return int(dialog.result())

    monkeypatch.setattr(PreferencesDialog, "exec", accept_with_folder)
    window.preferences_action.trigger()

    assert window.preferences.icon_directory == folder
    assert len(window.icons.index) == before + 1
    assert (
        window.icons.index.resolve("Desc_Inexistant_256.png") == folder / "Desc_Inexistant_256.png"
    )
    # The canvas got the new provider too, not just the palette.
    assert window.scene.icons is window.icons


def test_cancelling_the_box_changes_nothing(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(PreferencesDialog, "exec", lambda _d: int(QDialog.DialogCode.Rejected))
    before = window.icons
    assert window.edit_preferences() is False
    assert window.icons is before


def test_ctrl_f_puts_the_caret_in_the_search_box(window: MainWindow) -> None:
    """Asserted on the window's focus widget, not on ``hasFocus``.

    ``hasFocus`` is also about which window the operating system considers active,
    which under a test runner opening a dozen windows is nobody's business.
    """
    window.show()
    window.table_panel.filter.setFocus()
    window.search_action.trigger()
    assert window.focusWidget() is window.palette_widget.search
