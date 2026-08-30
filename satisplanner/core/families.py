"""Which family an item belongs to, so a factory can be read by what flows in it.

A family is a **way of looking**, not a property of the item: nothing here reaches
the solver, and two readers with different palettes get identical reports. It lives
in ``core`` all the same, because deciding that crude oil is a fluid is catalogue
knowledge and has nothing to do with Qt.

The order the families are tried in is the whole of the logic, and it is deliberate.
Crude oil, water and nitrogen are *both* raw resources and fluids; they are shown as
fluids, because what a reader wants to know at a glance is what carries them -- a
pipe, not a belt -- and because a blue oil well next to a grey iron deposit says
something true about the factory.
"""

from enum import StrEnum

from satisplanner.core import constants
from satisplanner.core.i18n import _
from satisplanner.core.models import Item


class ItemFamily(StrEnum):
    """The five groups an item can fall into. ``OTHER`` is not a failure."""

    FLUID = "fluid"
    RAW = "raw"
    INGOT = "ingot"
    SPACE_ELEVATOR = "space_elevator"
    # Everything in between: plates, rods, screws, circuit boards, motors. It is by
    # far the largest family, and giving it a name of its own rather than treating it
    # as "unclassified" is what lets a user colour it on purpose.
    OTHER = "other"


def family_label(family: ItemFamily) -> str:
    """How a family reads in the preferences box, in the language in force."""
    match family:
        case ItemFamily.FLUID:
            return _("Fluides")
        case ItemFamily.RAW:
            return _("Ressources brutes")
        case ItemFamily.INGOT:
            return _("Lingots")
        case ItemFamily.SPACE_ELEVATOR:
            return _("Produits finis")
        case ItemFamily.OTHER:
            return _("Pièces intermédiaires")


def family_of(item: Item) -> ItemFamily:
    """The family this item belongs to, decided in order of precedence."""
    if item.form.is_fluid:
        return ItemFamily.FLUID
    if item.is_raw_resource:
        return ItemFamily.RAW
    if item.class_name in constants.INGOT_CLASSES:
        return ItemFamily.INGOT
    if item.class_name in constants.SPACE_ELEVATOR_CLASSES:
        return ItemFamily.SPACE_ELEVATOR
    return ItemFamily.OTHER
