"""Icons: the three backends, and above all the generative fallback.

Running with no icons at all is the normal case -- the game's art is never
redistributed -- so the fallback is tested as a feature rather than as a safety net.
"""

from pathlib import Path

from PySide6.QtGui import QColor, QImage, QPixmap
from pytestqt.qtbot import QtBot

from satisplanner.core.models import GameData
from satisplanner.data.icons import IconIndex
from satisplanner.ui.icon_provider import IconProvider, initials, stable_hue


def _write_icon(directory: Path, name: str, colour: QColor) -> Path:
    """A real 8x8 PNG, so the provider goes through QPixmap as it would in production."""
    directory.mkdir(parents=True, exist_ok=True)
    image = QImage(8, 8, QImage.Format.Format_ARGB32)
    image.fill(colour)
    path = directory / name
    assert image.save(str(path))
    return path


# --------------------------------------------------------------------------- #
# The fallback
# --------------------------------------------------------------------------- #


def test_a_hue_is_stable_across_runs() -> None:
    """Python's own hash is salted per process; an icon must not change colour."""
    assert stable_hue("Desc_OreIron_C") == stable_hue("Desc_OreIron_C")
    assert 0 <= stable_hue("Desc_OreIron_C") <= 359
    assert stable_hue("Desc_OreIron_C") != stable_hue("Desc_OreCopper_C")


def test_initials_skip_the_short_words() -> None:
    assert initials("Minerai de fer") == "MF"
    assert initials("Plastique") == "P"
    assert initials("Résidus de pétrole lourd") == "RP"
    assert initials("") == "?"


def test_an_item_without_a_file_is_drawn_rather_than_missing(
    qtbot: QtBot, game_data: GameData
) -> None:
    del qtbot
    provider = IconProvider(IconIndex())  # no roots at all: everything is generated
    item = game_data.item("Desc_OreIron_C")
    icon = provider.for_item(item)

    assert not icon.isNull(), "l'absence d'icône n'est pas une degradation"
    assert provider.was_generated(item.class_name)
    assert provider.generated_count == 1
    pixmap = icon.pixmap(64, 64)
    assert not pixmap.isNull()
    assert pixmap.toImage().pixelColor(32, 32).alpha() > 0, "le carre doit être peint"


def test_two_classes_get_different_generated_icons(qtbot: QtBot) -> None:
    del qtbot
    provider = IconProvider(IconIndex())
    first = provider.generate("Desc_OreIron_C", "Minerai de fer").toImage()
    second = provider.generate("Desc_OreCopper_C", "Minerai de cuivre").toImage()
    assert first.pixelColor(4, 4) != second.pixelColor(4, 4)


def test_the_same_class_is_generated_identically_twice(qtbot: QtBot) -> None:
    del qtbot
    provider = IconProvider(IconIndex())
    first = provider.generate("Desc_OreIron_C", "Minerai de fer").toImage()
    second = provider.generate("Desc_OreIron_C", "Minerai de fer").toImage()
    assert first == second


# --------------------------------------------------------------------------- #
# The file backends
# --------------------------------------------------------------------------- #


def test_a_file_backend_wins_over_the_fallback(
    qtbot: QtBot, tmp_path: Path, game_data: GameData
) -> None:
    del qtbot
    item = game_data.item("Desc_OreIron_C")
    assert item.icon_file is not None
    _write_icon(tmp_path, item.icon_file, QColor("#123456"))

    provider = IconProvider(IconIndex([tmp_path]))
    icon = provider.for_item(item)
    assert not provider.was_generated(item.class_name)
    assert icon.pixmap(8, 8).toImage().pixelColor(4, 4) == QColor("#123456")


def test_the_embedded_root_wins_over_the_user_one(
    qtbot: QtBot, tmp_path: Path, game_data: GameData
) -> None:
    """First root wins, so a user export never shadows what the package ships."""
    del qtbot
    item = game_data.item("Desc_OreIron_C")
    assert item.icon_file is not None
    embedded, user = tmp_path / "embedded", tmp_path / "user"
    _write_icon(embedded, item.icon_file, QColor("#112233"))
    _write_icon(user, item.icon_file, QColor("#445566"))

    provider = IconProvider(IconIndex([embedded, user]))
    assert provider.for_item(item).pixmap(8, 8).toImage().pixelColor(4, 4) == QColor("#112233")


def test_an_unreadable_file_falls_back_instead_of_crashing(
    qtbot: QtBot, tmp_path: Path, game_data: GameData
) -> None:
    del qtbot
    item = game_data.item("Desc_OreIron_C")
    assert item.icon_file is not None
    (tmp_path / item.icon_file).write_bytes(b"ceci n'est pas une image")

    provider = IconProvider(IconIndex([tmp_path]))
    assert not provider.for_item(item).isNull()
    assert provider.was_generated(item.class_name)


def test_an_icon_is_resolved_once_and_cached(qtbot: QtBot, game_data: GameData) -> None:
    del qtbot
    provider = IconProvider(IconIndex())
    item = game_data.item("Desc_OreIron_C")
    assert provider.for_item(item) is provider.for_item(item)


def test_buildings_go_through_the_same_path(qtbot: QtBot, game_data: GameData) -> None:
    """The whole building set has no exported icon, and that is the expected state."""
    del qtbot
    provider = IconProvider(IconIndex())
    for building in game_data.buildings.values():
        assert not provider.for_building(building).isNull()
    assert provider.generated_count == len(game_data.buildings)


def test_the_generated_pixmap_honours_the_requested_size(qtbot: QtBot) -> None:
    del qtbot
    pixmap: QPixmap = IconProvider(IconIndex(), size=32).generate("X", "Test")
    assert pixmap.deviceIndependentSize().toSize().width() == 32
