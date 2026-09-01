"""Catalogue questions about one item: who makes it, who eats it, what it costs.

Everything an item card needs to say, computed without a graph and without Qt. None
of it feeds the solver -- a factory's cost comes from its own resolved report, not
from a recipe tree -- so this module is deliberately separate from :mod:`engine`.

The raw cost is the one piece with judgement in it. Expanding a recipe tree down to
ore answers "what would one of these cost me, roughly", and it is **indicative on
purpose**:

* only standard recipes are followed, so an alternate that halves the ore is ignored;
* byproducts are not credited back, so a recipe that also yields fuel looks dearer
  than it is;
* nothing is shared between branches: two ingredients that both need iron each pay
  their own way, which is right for a cost and wrong for a plan.

A real factory answers all three properly, because it is solved. This is a lookup,
and the card says so in as many words.
"""

import logging
from dataclasses import dataclass, field
from typing import Final

from satisplanner.core.models import GameData, Generator, Recipe

logger = logging.getLogger(__name__)

# Below this a computed amount is noise from repeated division, not a quantity.
COST_EPSILON: Final = 1e-9


def producers(game_data: GameData, item_class: str) -> list[Recipe]:
    """Recipes that make this item: the standard ones first, then the alternates.

    Within each group the order is by label, so the card reads the way a wiki
    page does rather than the way a database happened to be filled.
    """
    made = [
        recipe
        for recipe in game_data.recipes.values()
        if any(slot.item_class == item_class for slot in recipe.products)
    ]
    return sorted(made, key=lambda recipe: (recipe.is_alternate, recipe.name))


def consumers(game_data: GameData, item_class: str) -> list[Recipe]:
    """Recipes that use this item as an ingredient, in the same order."""
    used = [
        recipe
        for recipe in game_data.recipes.values()
        if any(slot.item_class == item_class for slot in recipe.ingredients)
    ]
    return sorted(used, key=lambda recipe: (recipe.is_alternate, recipe.name))


def generator_sources(game_data: GameData, item_class: str) -> list[tuple[Generator, str]]:
    """Generators that leave this item behind, with the fuel that does it.

    Uranium waste has no recipe in the game at all: it falls out of a nuclear plant
    burning a rod. Until the plant was in the catalogue this had to be written down
    as a frozen label on the item; now it is read from the generators themselves,
    and it stops being true the moment the data stops saying it.
    """
    found = [
        (generator, fuel.item_class)
        for generator in game_data.generators.values()
        for fuel in generator.fuels
        if fuel.byproduct_class == item_class
    ]
    return sorted(found, key=lambda pair: (pair[0].class_name, pair[1]))


def unavailable_producers(game_data: GameData, item_class: str) -> list[Recipe]:
    """Recipes that make this item and that no node can place, in the same order.

    Reads the second mapping, never the first. Nothing computed from these ever
    reaches a factory: they exist so a card can name what the game has and this
    version does not, which is the difference between a scope and a hole.
    """
    made = [
        recipe
        for recipe in game_data.unavailable_recipes.values()
        if any(slot.item_class == item_class for slot in recipe.products)
    ]
    return sorted(made, key=lambda recipe: (recipe.is_alternate, recipe.name))


def unavailable_consumers(game_data: GameData, item_class: str) -> list[Recipe]:
    """Recipes that eat this item and that no node can place, in the same order."""
    used = [
        recipe
        for recipe in game_data.unavailable_recipes.values()
        if any(slot.item_class == item_class for slot in recipe.ingredients)
    ]
    return sorted(used, key=lambda recipe: (recipe.is_alternate, recipe.name))


def standard_recipe(game_data: GameData, item_class: str) -> Recipe | None:
    """The non-alternate recipe for this item, or ``None`` when there is none.

    An item with several standard recipes is not something the game currently has;
    should one appear, the first by label wins and the answer stays deterministic
    rather than depending on dictionary order.
    """
    for recipe in producers(game_data, item_class):
        if not recipe.is_alternate:
            return recipe
    return None


def output_per_cycle(recipe: Recipe, item_class: str) -> float:
    """How much of ``item_class`` one cycle of ``recipe`` yields."""
    return sum(slot.amount_per_cycle for slot in recipe.products if slot.item_class == item_class)


@dataclass(frozen=True)
class RawCost:
    """What one unit of an item costs in raw resources, and how sure we are.

    ``cycle`` is empty on a normal answer. When it is not, ``amounts`` is meaningless
    and must not be shown: the expansion was abandoned rather than looped, and the
    chain that closed on itself is named so the reader can see why.
    """

    item_class: str
    amounts: dict[str, float] = field(default_factory=dict)
    cycle: tuple[str, ...] = ()

    @property
    def is_complete(self) -> bool:
        return not self.cycle

    @property
    def cycle_description(self) -> str:
        return " → ".join(self.cycle)


class _CycleError(Exception):
    """Raised through the recursion to abandon the whole expansion at once."""

    def __init__(self, chain: tuple[str, ...]) -> None:
        super().__init__(" -> ".join(chain))
        self.chain = chain


def raw_cost(game_data: GameData, item_class: str) -> RawCost:
    """Raw resources for one unit of ``item_class``, expanding standard recipes.

    Recursion stops on anything the catalogue cannot take further: a raw resource, or
    an item with no standard recipe -- an alternate-only product, or one that simply
    is not craftable. Those count as themselves, which is the honest answer.
    """
    try:
        amounts = _expand(game_data, item_class, 1.0, ())
    except _CycleError as found:
        logger.debug("coût brut abandonné, cycle : %s", found)
        return RawCost(item_class=item_class, cycle=found.chain)
    return RawCost(
        item_class=item_class,
        amounts={
            name: round(value, 9) for name, value in sorted(amounts.items()) if value > COST_EPSILON
        },
    )


def _expand(
    game_data: GameData, item_class: str, quantity: float, chain: tuple[str, ...]
) -> dict[str, float]:
    if item_class in chain:
        raise _CycleError((*chain, item_class))

    item = game_data.items.get(item_class)
    expandable = item is not None and not item.is_raw_resource
    recipe = standard_recipe(game_data, item_class) if expandable else None
    produced = 0.0 if recipe is None else output_per_cycle(recipe, item_class)
    if recipe is None or produced <= COST_EPSILON:
        return {item_class: quantity}

    deeper = (*chain, item_class)
    totals: dict[str, float] = {}
    for slot in recipe.ingredients:
        share = quantity * slot.amount_per_cycle / produced
        for name, value in _expand(game_data, slot.item_class, share, deeper).items():
            totals[name] = totals.get(name, 0.0) + value
    return totals
