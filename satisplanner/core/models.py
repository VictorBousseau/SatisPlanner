"""Domain models.

Phase 1 introduces only the vocabulary that the data layer needs to share with
the domain. The graph, recipe and building models land in phase 2.
"""

from enum import StrEnum


class ItemForm(StrEnum):
    """Physical form of an item, which decides its unit and its transport.

    Solids are counted in items/min and travel on conveyors; liquids and gases
    are counted in m3/min and travel in pipes.
    """

    SOLID = "solid"
    LIQUID = "liquid"
    GAS = "gas"

    @property
    def is_fluid(self) -> bool:
        """True for liquids and gases, i.e. everything measured in m3."""
        return self is not ItemForm.SOLID
