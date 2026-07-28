"""What has to be manufactured before a factory can be built.

The shopping list says "ten smelters, three assemblers". This answers the question
that follows: ten smelters is how many iron rods and how much wire? Buildings are
crafted like everything else -- the game declares a recipe for each, produced in
the build gun -- so the figures are read from the catalogue and multiplied by the
counts the report already worked out. Nothing here is estimated.

**One level deep, on purpose.** A constructor costs two reinforced iron plates and
eight cables, and that is what the list says: it does not go on to say what the
plates cost in ore. The question is "what do I have to produce before I can build
this", and the answer to that is parts, not ore. The ore is the factory's own
consumption, which the report gives separately.

**Lines are not priced, and that is the honest answer.** A conveyor's cost is
declared per unit of length, and length is geometry -- the one thing this planner
has never claimed to know. Multiplying by an average run would produce a number
that looks like the others and is not one, so the lines are counted, named, and
left blank. See :class:`ConstructionMaterials.belt_lines`.
"""

import logging
from dataclasses import dataclass, field

from satisplanner.core.models import BuildingKind, GameData
from satisplanner.core.results import ShoppingList

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ConstructionMaterials:
    """The parts one factory has to be built out of, all buildings added together."""

    # Item class -> total quantity, in class-name order so the answer is stable.
    amounts: dict[str, float] = field(default_factory=dict)
    # Buildings the catalogue has no build recipe for, named rather than skipped:
    # a total that quietly omits a building is a total nobody can trust.
    unpriced: tuple[str, ...] = ()
    # Lines whose cost depends on a length nobody knows. Counted so the blank can
    # say how much is missing from it.
    belt_lines: int = 0
    pipe_lines: int = 0

    @property
    def is_complete(self) -> bool:
        """True when every building counted had a cost to add."""
        return not self.unpriced

    @property
    def line_count(self) -> int:
        return self.belt_lines + self.pipe_lines


def materials_for(shopping: ShoppingList, game_data: GameData) -> ConstructionMaterials:
    """Add up what every building on the shopping list costs to put down."""
    totals: dict[str, float] = {}
    unpriced: list[str] = []
    belts = sum(shopping.belts_by_tier.values())
    pipes = sum(shopping.pipes_by_tier.values())

    for building_class, count in sorted({**shopping.buildings, **shopping.attachments}.items()):
        kind = _kind_of(building_class, game_data)
        # A line can only ever be counted by its length, so it is moved to the
        # blank rather than priced -- wherever it was counted from.
        if kind is BuildingKind.BELT:
            belts += count
            continue
        if kind is BuildingKind.PIPE:
            pipes += count
            continue
        cost = game_data.building_costs.get(building_class)
        if cost is None:
            unpriced.append(building_class)
            logger.debug("aucune recette de construction pour %s", building_class)
            continue
        for item_class, amount in cost.amounts.items():
            totals[item_class] = totals.get(item_class, 0.0) + amount * count

    return ConstructionMaterials(
        amounts=dict(sorted(totals.items())),
        unpriced=tuple(unpriced),
        belt_lines=belts,
        pipe_lines=pipes,
    )


def _kind_of(building_class: str, game_data: GameData) -> BuildingKind | None:
    building = game_data.buildings.get(building_class)
    return building.kind if building is not None else None
