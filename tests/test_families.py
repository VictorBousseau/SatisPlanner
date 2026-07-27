"""Item families: what the data gives for free, and what had to be written by hand.

The hand-written lists in ``core/constants.py`` are the risk this file exists for.
Two families are read off ``Docs.json`` and cannot go stale; the other two are class
names typed out by a person, and a game update that renames one would otherwise cost
those items their colour in silence.
"""

import pytest

from satisplanner.core import constants
from satisplanner.core.families import FAMILY_LABELS, ItemFamily, family_of
from satisplanner.core.models import GameData
from satisplanner.data import db


@pytest.fixture(scope="module")
def shipped() -> GameData:
    """The database the application actually ships, not the test fixture.

    The hand-written class names have to be checked against the full catalogue: the
    fixture is a slice and would happily "pass" by not containing them.
    """
    return db.load_game_data_from_file(db.default_database_path())


HAND_WRITTEN = (
    ("INGOT_CLASSES", constants.INGOT_CLASSES),
    ("SPACE_ELEVATOR_CLASSES", constants.SPACE_ELEVATOR_CLASSES),
)


@pytest.mark.parametrize(("name", "classes"), HAND_WRITTEN)
def test_every_hand_written_class_exists_in_the_catalogue(
    shipped: GameData, name: str, classes: tuple[str, ...]
) -> None:
    """A renamed class must fail here rather than quietly lose its colour."""
    missing = [class_name for class_name in classes if class_name not in shipped.items]
    assert not missing, f"{name} cite des classes absentes du catalogue : {missing}"


def test_the_two_free_families_come_from_the_data_and_are_not_listed(
    shipped: GameData,
) -> None:
    """Nothing about raw resources or fluids is written down anywhere in the code."""
    listed = set(constants.INGOT_CLASSES) | set(constants.SPACE_ELEVATOR_CLASSES)
    from_data = {
        item.class_name
        for item in shipped.items.values()
        if item.is_raw_resource or item.form.is_fluid
    }
    assert not (listed & from_data), "une famille déduite des données ne doit pas être listee"
    assert len(from_data) > 20, "les deux familles gratuites doivent bien venir des données"


def test_a_fluid_that_is_also_a_raw_resource_is_shown_as_a_fluid(shipped: GameData) -> None:
    """Crude oil, water and nitrogen are both. What matters is that they need a pipe."""
    for class_name in ("Desc_LiquidOil_C", "Desc_Water_C", "Desc_NitrogenGas_C"):
        item = shipped.item(class_name)
        assert item.is_raw_resource, f"{class_name} doit bien être une ressource brute"
        assert family_of(item) is ItemFamily.FLUID, class_name


@pytest.mark.parametrize(
    ("class_name", "expected"),
    [
        ("Desc_OreIron_C", ItemFamily.RAW),
        ("Desc_Coal_C", ItemFamily.RAW),
        ("Desc_Water_C", ItemFamily.FLUID),
        ("Desc_HeavyOilResidue_C", ItemFamily.FLUID),
        ("Desc_IronIngot_C", ItemFamily.INGOT),
        ("Desc_SteelIngot_C", ItemFamily.INGOT),
        ("Desc_SpaceElevatorPart_1_C", ItemFamily.SPACE_ELEVATOR),
        ("Desc_IronPlate_C", ItemFamily.OTHER),
        ("Desc_Cement_C", ItemFamily.OTHER),
    ],
)
def test_items_land_in_the_family_a_player_would_expect(
    shipped: GameData, class_name: str, expected: ItemFamily
) -> None:
    assert family_of(shipped.item(class_name)) is expected


def test_every_family_has_a_french_label() -> None:
    """The preferences box shows these; a missing one would be a raw enum name."""
    assert set(FAMILY_LABELS) == set(ItemFamily)


def test_every_item_of_the_catalogue_lands_somewhere(shipped: GameData) -> None:
    """``OTHER`` is a family, not a hole: no item may fail to be classified."""
    for item in shipped.items.values():
        assert isinstance(family_of(item), ItemFamily), item.class_name
