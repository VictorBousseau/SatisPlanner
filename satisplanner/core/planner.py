"""Building a factory backwards from what it should produce.

"Two heavy modular frames a minute" is a target; this turns it into a factory. The
method is a plain recursive expansion of the recipes and nothing more -- no solver,
no optimisation, no search. Given the same target, the same catalogue and the same
recipe choices, it produces the same factory to the byte.

Three decisions shape everything below.

**Intermediates are shared.** A chain that needs iron plates in two places gets one
bank of constructors sized for both, not two banks. It is what a player builds, it
is what makes the picture readable, and it changes no total: the raw resources a
factory eats are linear in what it makes, so sharing moves machines around without
moving a single ore.

**The expansion stops exactly where the item card's raw cost stops** -- on a raw
resource, and on anything the catalogue cannot take further. That is not a
coincidence to be maintained by hand: it is what makes
``tests/test_planner`` able to check this module against
:mod:`satisplanner.core.breakdown`, which computes the same quantity by a
completely different route. An item with no recipe becomes an **import**, because
that is the honest thing to draw for something the factory does not make.

**What the tool cannot know is drawn and flagged.** A raw resource becomes a
deposit, and a deposit has a purity and a miner that depend on the map -- which is
the one thing no catalogue holds. They are placed at a stated default, counted in
:attr:`Plan.to_settle`, and the generation report says so rather than letting a
figure that was never verified pass for one that was.
"""

import logging
import math
import unicodedata
from dataclasses import dataclass, field
from typing import Final

from satisplanner.core import breakdown, formatting
from satisplanner.core.attachments import materialise
from satisplanner.core.graph import (
    AttachmentMode,
    Edge,
    ExternalSourceNode,
    FactoryGraph,
    MachineNode,
    Node,
    OutputNode,
    ResourceNode,
    ResourceWellNode,
    StorageNode,
    WaterExtractorNode,
    port_line_budget,
)
from satisplanner.core.i18n import _
from satisplanner.core.models import Extractor, GameData, ItemForm, Purity, Recipe

logger = logging.getLogger(__name__)

# Below this a computed rate is float dust from repeated division.
RATE_EPSILON: Final = 1e-9

# Layout. The columns are wider than the clearance ``core.attachments`` keeps, so
# that the splitters it inserts afterwards have a lane of their own between two
# banks instead of being stacked below the factory.
COLUMN_WIDTH: Final = 700.0
ROW_HEIGHT: Final = 260.0

# What a generated deposit is placed at. Neither is a guess about the map -- there
# is nothing to guess from -- they are the neutral value and the first miner, put
# there so the factory resolves, and reported as needing a decision.
DEFAULT_PURITY: Final = Purity.NORMAL


class PlanError(Exception):
    """The target cannot be built. The message is French and shown as it is."""


@dataclass(frozen=True)
class Step:
    """One item of the plan: how much is wanted, and what makes it.

    ``machines`` is exact and fractional. Rounding is a separate pass, on purpose:
    the two variants the specification asks for are the same tree read twice.
    """

    item_class: str
    # What the rest of the factory asks of this item, per minute.
    rate_per_minute: float
    recipe_class: str | None = None
    machines: float = 0.0
    # What those machines really put out. Equal to the demand in the exact
    # variant; more than it wherever a whole building was rounded up to.
    produced_per_minute: float = 0.0
    # Distance from the raw resources, which is what the layout puts in columns.
    level: int = 0

    @property
    def is_source(self) -> bool:
        """True for what the factory does not make: a deposit or an import."""
        return self.recipe_class is None

    @property
    def surplus_per_minute(self) -> float:
        """What nobody downstream asked for. Nought unless something was rounded."""
        return max(self.produced_per_minute - self.rate_per_minute, 0.0)


@dataclass(frozen=True)
class Plan:
    """A whole factory, decided but not yet drawn."""

    target: str
    rate_per_minute: float
    steps: tuple[Step, ...] = ()
    # Raw resources per minute, which is the figure the item card can be held
    # against, and the byproducts that will need an exit of their own.
    raw: dict[str, float] = field(default_factory=dict)
    imports: dict[str, float] = field(default_factory=dict)
    byproducts: dict[str, float] = field(default_factory=dict)
    # Deposits whose purity and miner the tool had no way of knowing.
    to_settle: tuple[str, ...] = ()

    def step(self, item_class: str) -> Step:
        for step in self.steps:
            if step.item_class == item_class:
                return step
        msg = f"aucune étape pour {item_class}"
        raise KeyError(msg)


def recipe_for(game_data: GameData, item_class: str, choices: dict[str, str]) -> Recipe | None:
    """The recipe this plan makes ``item_class`` with, or ``None`` to stop here.

    The standard one unless the user pinned another, which is where the alternates
    come in: **by decision, never by calculation**. A pinned class that does not
    make the item is refused rather than ignored -- a choice that silently does
    nothing is worse than one that says it cannot be honoured.
    """
    item = game_data.items.get(item_class)
    if item is None or item.is_raw_resource:
        return None
    pinned = choices.get(item_class)
    if pinned is None:
        return breakdown.standard_recipe(game_data, item_class)
    recipe = game_data.recipes.get(pinned)
    if recipe is None:
        msg = f"recette inconnue pour {_name(game_data, item_class)} : {pinned}"
        raise PlanError(msg)
    if breakdown.output_per_cycle(recipe, item_class) <= 0:
        msg = (
            f"la recette « {recipe.name} » ne produit pas "
            f"{_name(game_data, item_class)}"
        )
        raise PlanError(msg)
    return recipe


def plan(
    game_data: GameData,
    item_class: str,
    rate_per_minute: float,
    choices: dict[str, str] | None = None,
    *,
    rounded: bool = False,
) -> Plan:
    """Expand ``item_class`` at ``rate_per_minute`` into everything it needs.

    ``rounded`` is the buildable variant, and it is **not** the exact answer with
    each figure rounded at the end. A bank rounded up eats more than the exact plan
    gave it, so whatever feeds it has to be sized for the rounded appetite -- else
    the factory that was meant to be buildable as it stands comes out starved from
    one end to the other. The ceiling is therefore applied **as the demand
    descends**, which is the same single pass down the same tree.
    """
    if rate_per_minute <= RATE_EPSILON:
        msg = _("un objectif se donne en objets par minute, et il doit être positif.")
        raise PlanError(msg)
    if item_class not in game_data.items:
        msg = _("objet inconnu : {item}").format(item=item_class)
        raise PlanError(msg)
    picked = dict(choices or {})
    if recipe_for(game_data, item_class, picked) is None:
        msg = _(
            "{item} ne se fabrique pas : aucune recette ne le produit, il n'y a donc "
            "rien à construire."
        ).format(item=_name(game_data, item_class))
        raise PlanError(msg)

    order = _dependency_order(game_data, item_class, picked)
    demand: dict[str, float] = {item_class: rate_per_minute}
    steps: list[Step] = []
    byproducts: dict[str, float] = {}
    for name in order:
        wanted = demand.get(name, 0.0)
        recipe = recipe_for(game_data, name, picked)
        if recipe is None or wanted <= RATE_EPSILON:
            continue
        per_minute = recipe.product_rates()[name]
        machines = wanted / per_minute
        if rounded:
            machines = float(math.ceil(machines - RATE_EPSILON))
        for ingredient, rate in recipe.ingredient_rates().items():
            demand[ingredient] = demand.get(ingredient, 0.0) + rate * machines
        for product, rate in recipe.product_rates().items():
            if product != name:
                byproducts[product] = byproducts.get(product, 0.0) + rate * machines
        steps.append(
            Step(
                item_class=name,
                rate_per_minute=wanted,
                recipe_class=recipe.class_name,
                machines=machines,
                produced_per_minute=machines * per_minute,
            )
        )

    made = {step.item_class for step in steps}
    sources = {name: rate for name, rate in demand.items() if name not in made}
    steps.extend(
        Step(item_class=name, rate_per_minute=rate)
        for name, rate in sorted(sources.items())
        if rate > RATE_EPSILON
    )
    levelled = _levelled(game_data, steps, picked)
    raw = {
        name: rate
        for name, rate in sorted(sources.items())
        if rate > RATE_EPSILON and _is_raw(game_data, name)
    }
    return Plan(
        target=item_class,
        rate_per_minute=rate_per_minute,
        steps=levelled,
        raw=raw,
        imports={
            name: rate
            for name, rate in sorted(sources.items())
            if rate > RATE_EPSILON and not _is_raw(game_data, name)
        },
        byproducts={
            name: round(rate, 9) for name, rate in sorted(byproducts.items()) if rate > RATE_EPSILON
        },
        to_settle=tuple(sorted(raw)),
    )


def _is_raw(game_data: GameData, item_class: str) -> bool:
    item = game_data.items.get(item_class)
    return item is not None and item.is_raw_resource


def _dependency_order(
    game_data: GameData, target: str, choices: dict[str, str]
) -> list[str]:
    """Every item the target needs, each after everything that needs it.

    Depth-first over the chosen recipes, which is also where a loop is caught: a
    user who pins recycled plastic and recycled rubber together has asked for a
    factory that makes each out of the other, and the honest answer is to name the
    ring rather than to expand it forever.
    """
    order: list[str] = []
    done: set[str] = set()
    open_chain: list[str] = []

    def walk(name: str) -> None:
        if name in done:
            return
        if name in open_chain:
            ring = " → ".join(_name(game_data, step) for step in (*open_chain, name))
            msg = _(
                "ces recettes se fabriquent l'une l'autre : {ring}. Choisissez une "
                "recette différente pour l'une d'elles."
            ).format(ring=ring)
            raise PlanError(msg)
        recipe = recipe_for(game_data, name, choices)
        if recipe is not None:
            open_chain.append(name)
            for ingredient in sorted(recipe.ingredient_rates()):
                walk(ingredient)
            open_chain.pop()
        done.add(name)
        order.append(name)

    walk(target)
    # Deepest first while walking, so the parents come out last; the demand has to
    # be accumulated the other way round.
    return list(reversed(order))


def _levelled(
    game_data: GameData, steps: list[Step], choices: dict[str, str]
) -> tuple[Step, ...]:
    """Give every step a column: sources at nought, each item after its ingredients."""
    levels: dict[str, int] = {}
    by_item = {step.item_class: step for step in steps}
    for step in reversed(steps):  # sources last in ``steps``, ingredients before users
        recipe = None if step.is_source else recipe_for(game_data, step.item_class, choices)
        if recipe is None:
            levels[step.item_class] = 0
            continue
        below = [levels.get(name, 0) for name in recipe.ingredient_rates() if name in by_item]
        levels[step.item_class] = 1 + max(below, default=0)
    ordered = sorted(steps, key=lambda step: (levels[step.item_class], step.item_class))
    return tuple(
        Step(
            item_class=step.item_class,
            rate_per_minute=round(step.rate_per_minute, 9),
            recipe_class=step.recipe_class,
            machines=round(step.machines, 9),
            produced_per_minute=round(
                step.produced_per_minute if step.recipe_class else step.rate_per_minute, 9
            ),
            level=levels[step.item_class],
        )
        for step in ordered
    )


# --------------------------------------------------------------------------- #
# Drawing the plan
# --------------------------------------------------------------------------- #


def build(
    game_data: GameData, made: Plan, mode: AttachmentMode = AttachmentMode.SIMPLE
) -> FactoryGraph:
    """Turn a plan into an ordinary factory: nodes, lines and fittings.

    Ordinary is the word that matters. What comes back is a
    :class:`~satisplanner.core.graph.FactoryGraph` like any other -- editable,
    savable, and subject to every rule the rest of the application enforces.

    ``mode`` is the mode of the document the user is generating into, and a
    generated factory obeys its own document's rule rather than one of its own. In
    the faithful mode the fittings its ports need are put in by the very same
    :func:`~satisplanner.core.attachments.materialise` the bascule runs: a
    generated factory that broke the port rule would be an admission that the rule
    is decorative. In the simple mode there is nothing to put in, and drawing
    fittings nobody asked for would be the same admission from the other side.

    A container goes wherever the plan makes more than it consumes, which only
    happens once something has been rounded up. The surplus is not routed by hand
    either: a container absorbs without limit and is therefore served last, so what
    reaches it is exactly what the consumers could not take.
    """
    graph = FactoryGraph(attachment_mode=mode)
    _place_nodes(game_data, made, graph)
    _lay_lines(game_data, made, graph)
    if graph.is_faithful:
        materialise(graph)
    return graph


def _place_nodes(game_data: GameData, made: Plan, graph: FactoryGraph) -> None:
    depth = max((step.level for step in made.steps), default=0)
    rows: dict[int, int] = {}
    for step in made.steps:
        row = rows.get(step.level, 0)
        rows[step.level] = row + 1
        position = (step.level * COLUMN_WIDTH, row * ROW_HEIGHT)
        graph.add_node(_node_for(game_data, step, position))

    # Exits: the target and every byproduct. A byproduct with nowhere to go stops
    # its machine dead in this engine, so a generator that left one dangling would
    # produce a factory that computes zero -- see ``engine``'s blocked products.
    leaving = {made.target: made.rate_per_minute, **made.byproducts}
    for index, (item_class, _rate) in enumerate(sorted(leaving.items())):
        graph.add_node(
            OutputNode(
                id=f"sortie-{_slug(game_data, item_class)}",
                item_class=item_class,
                position=((depth + 1) * COLUMN_WIDTH, index * ROW_HEIGHT),
            )
        )


def _node_for(game_data: GameData, step: Step, position: tuple[float, float]) -> Node:
    # Never spelled out here: the identifier is what the lines are drawn between,
    # and two places inventing it independently is how a well ends up connected to
    # a node that does not exist.
    slug = node_id_for(game_data, step)
    if step.recipe_class is not None:
        return MachineNode(
            id=slug,
            recipe_class=step.recipe_class,
            machine_count=step.machines,
            position=position,
        )
    if not _is_raw(game_data, step.item_class):
        return ExternalSourceNode(
            id=slug,
            item_class=step.item_class,
            rate_per_minute=step.rate_per_minute,
            position=position,
        )
    extractor = _extractor_for(game_data, step.item_class)
    if extractor is None:
        msg = _("aucun extracteur ne travaille {item} dans ce catalogue.").format(
            item=_name(game_data, step.item_class)
        )
        raise PlanError(msg)
    each = extractor.rate(DEFAULT_PURITY)
    units = step.produced_per_minute / each if each > 0 else 1.0
    if extractor.needs_activator:
        # A satellite is a whole thing or it is nothing: there is no such object as
        # two thirds of one, even in the exact-ratio variant where machines are
        # allowed decimals. Rounding up leaves a surplus, which the diagnostics
        # report as a surplus -- and which the real purities usually change anyway.
        return ResourceWellNode(
            id=slug,
            item_class=step.item_class,
            extractor_class=extractor.class_name,
            satellites={DEFAULT_PURITY: max(1, math.ceil(units - RATE_EPSILON))},
            position=position,
        )
    if extractor.item_class is not None and not extractor.has_purity:
        return WaterExtractorNode(
            id=slug,
            extractor_class=extractor.class_name,
            count=max(units, RATE_EPSILON),
            position=position,
        )
    return ResourceNode(
        id=slug,
        item_class=step.item_class,
        extractor_class=extractor.class_name,
        purity=DEFAULT_PURITY,
        count=max(units, RATE_EPSILON),
        position=position,
    )


def _extractor_for(game_data: GameData, item_class: str) -> Extractor | None:
    """The building that works this resource, chosen the way the palette would.

    The slowest that can do the job, by class name, so the answer never depends on
    dictionary order. Which miner is really on the deposit is the user's to say --
    that is exactly what :attr:`Plan.to_settle` is for.

    A resource well comes last, however cheap its class name sorts. It is two
    buildings, a hundred and fifty megawatts and a spot on the map that has to have
    one, so it is the answer only where nothing else is: nitrogen, and nitrogen
    alone. Water has a pump and crude oil has a derrick, and a generator that sank
    a well for either would be answering a question nobody asked.
    """
    item = game_data.items.get(item_class)
    if item is None:
        return None
    candidates = [
        extractor
        for extractor in game_data.extractors.values()
        if extractor.allowed_form.is_fluid is item.form.is_fluid
        and extractor.item_class in (None, item_class)
        and item_class in (extractor.allowed_items or (item_class,))
    ]
    if not candidates:
        return None
    return min(
        candidates, key=lambda extractor: (extractor.needs_activator, extractor.class_name)
    )


def _lay_lines(game_data: GameData, made: Plan, graph: FactoryGraph) -> None:
    """One set of lines per item: from whoever makes it to whoever wants it."""
    producers = {step.item_class: node_id_for(game_data, step) for step in made.steps}
    for step in made.steps:
        if step.recipe_class is None:
            continue
        recipe = game_data.recipes[step.recipe_class]
        for ingredient in sorted(recipe.ingredient_rates()):
            source = producers.get(ingredient)
            if source is None:
                continue
            rate = recipe.ingredient_rates()[ingredient] * step.machines
            _connect(game_data, graph, source, producers[step.item_class], ingredient, rate)

    # The target and the byproducts leave the factory. The byproducts matter as
    # much as the target: one left dangling stops its machine dead -- that is the
    # engine's rule since phase 2 -- and a generator that forgot it would hand back
    # a factory computing zeroes.
    leaving = {made.target: made.rate_per_minute, **made.byproducts}
    for item_class, rate in sorted(leaving.items()):
        makers = _makers_of(game_data, made, item_class)
        exit_id = f"sortie-{_slug(game_data, item_class)}"
        for maker in makers:
            _connect(game_data, graph, producers[maker], exit_id, item_class, rate / len(makers))

    _add_containers(game_data, made, graph, producers)


def _makers_of(game_data: GameData, made: Plan, item_class: str) -> list[str]:
    """Every step whose recipe yields ``item_class``, as a headline product or not.

    Not only the step named after it: a byproduct has no step of its own, because
    nothing asked for it. It comes out of whichever recipe happens to make it, and
    that is the node the line has to leave from.
    """
    found: list[str] = []
    for step in made.steps:
        if step.recipe_class is None:
            continue
        if item_class in game_data.recipes[step.recipe_class].product_rates():
            found.append(step.item_class)
    return found


def _add_containers(
    game_data: GameData, made: Plan, graph: FactoryGraph, producers: dict[str, str]
) -> None:
    """A container wherever whole buildings make more than the plan asks for.

    Only there. The surplus is not routed by hand either: the container absorbs
    without limit and is served last, so what it receives is exactly what the
    consumers could not take -- which is the definition of a surplus.
    """
    depth = max((step.level for step in made.steps), default=0)
    row = 0
    for step in made.steps:
        if step.is_source or step.machines <= 0:
            continue
        surplus = step.surplus_per_minute
        if surplus <= RATE_EPSILON:
            continue
        item = game_data.item(step.item_class)
        storage = _storage_for(game_data, item.form)
        if storage is None:
            continue
        node_id = f"tampon-{_slug(game_data, step.item_class)}"
        graph.add_node(
            StorageNode(
                id=node_id,
                storage_class=storage,
                item_class=step.item_class,
                position=(
                    (depth + 1) * COLUMN_WIDTH,
                    (row + len(made.byproducts) + 2) * ROW_HEIGHT,
                ),
            )
        )
        row += 1
        _connect(game_data, graph, producers[step.item_class], node_id, step.item_class, surplus)


def _storage_for(game_data: GameData, form: ItemForm) -> str | None:
    """The largest container of the right kind, by class name for determinism."""
    candidates = [
        storage
        for storage in game_data.storages.values()
        if storage.form.is_fluid is form.is_fluid
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda storage: storage.class_name).class_name


def _connect(
    game_data: GameData, graph: FactoryGraph, source: str, target: str, item_class: str, rate: float
) -> None:
    """Lay as many lines as the rate needs, at the smallest tier that carries it.

    The smallest rather than the fastest: a generated factory that put Mk.6 belts
    everywhere would be telling the user to build something they cannot afford and
    do not need. Where no tier is enough the lines are doubled instead, which is
    what a player does, and never beyond the ports the two ends actually have.
    """
    form = game_data.item(item_class).form
    transports = game_data.transports_for(form)
    if not transports:
        msg = f"aucun transport pour {_name(game_data, item_class)}"
        raise PlanError(msg)
    chosen = game_data.smallest_transport_for(form, rate) or transports[-1]
    capacity = game_data.transport_capacity(chosen.class_name)
    wanted = max(1, math.ceil(rate / capacity)) if capacity > 0 else 1
    # The port budget bounds the doubling only where the document enforces it. In
    # the simple mode a port takes what it needs, and a rate that wants three belts
    # gets three -- which is the mode's whole promise.
    room = wanted
    if graph.is_faithful:
        room = min(
            port_line_budget(graph.node(source), is_output=True) or wanted,
            port_line_budget(graph.node(target), is_output=False) or wanted,
        )
    for _slot in range(max(1, min(wanted, room))):
        graph.edges.append(
            Edge(
                id=f"e{len(graph.edges) + 1}",
                source=source,
                target=target,
                item_class=item_class,
                transport_class=chosen.class_name,
            )
        )


def node_id_for(game_data: GameData, step: Step) -> str:
    """The identifier this plan gives the node that makes ``step``'s item.

    Public because a reader of the plan -- a test, a report -- has to be able
    to find the node a step became without guessing at the naming.
    """
    slug = _slug(game_data, step.item_class)
    if step.recipe_class is not None:
        return slug
    if not _is_raw(game_data, step.item_class):
        return f"entree-{slug}"
    extractor = _extractor_for(game_data, step.item_class)
    # A well is not a deposit and the identifier says so: "puits-azote" reads back
    # in a diagnostic, in the table and in the saved file as what it actually is.
    prefix = "puits" if extractor is not None and extractor.needs_activator else "gisement"
    return f"{prefix}-{slug}"


def _name(game_data: GameData, item_class: str) -> str:
    item = game_data.items.get(item_class)
    return item.name if item else item_class


def _slug(game_data: GameData, item_class: str) -> str:
    """A readable identifier from the item's name, in the language in force.

    Identifiers end up in the saved file, in every diagnostic and in the table, so
    "plaque-de-fer" is worth the six lines that fold the accents out of it. Falls
    back on the class name, which is already an identifier, if nothing survives.
    """
    folded = unicodedata.normalize("NFKD", _name(game_data, item_class).casefold())
    plain = "".join(char for char in folded if not unicodedata.combining(char))
    kept = "".join(char if char.isalnum() else "-" for char in plain)
    slug = "-".join(part for part in kept.split("-") if part)
    return slug or item_class.lower()


def report(game_data: GameData, made: Plan, *, rounded: bool) -> list[str]:
    """What the generator did, and above all what it could not know.

    Read once, on the dialog that produced the factory. The order is deliberate:
    what has to be decided by a human comes first, because a figure that was never
    verified must not be able to pass for one that was.
    """
    lines: list[str] = [
        _(
            "{rate} {of_item} : {workshops} atelier(s), {deposits} gisement(s), "
            "{imports} apport(s) extérieur(s)."
        ).format(
            rate=formatting.rate(made.rate_per_minute, game_data.item(made.target)),
            of_item=formatting.of(_name(game_data, made.target)),
            workshops=sum(1 for step in made.steps if not step.is_source),
            deposits=len(made.raw),
            imports=len(made.imports),
        )
    ]
    if made.to_settle:
        named = ", ".join(_name(game_data, item) for item in made.to_settle)
        lines.append(
            _(
                "À RÉGLER : {items}. Les gisements sont posés en pureté normale avec "
                "le premier extracteur venu, parce que ce qui se trouve sur votre "
                "carte n'est écrit nulle part dans les données du jeu. Corrigez la "
                "pureté et l'extracteur, puis le nombre suivra."
            ).format(items=named)
        )
    if made.imports:
        named = ", ".join(_name(game_data, item) for item in made.imports)
        lines.append(
            _("Apporté de l'extérieur, faute de recette dans le catalogue : {items}.").format(
                items=named
            )
        )
    if made.byproducts:
        of_item = _("{rate} de {item}")
        named = ", ".join(
            of_item.format(
                rate=formatting.rate(rate, game_data.item(item)),
                item=_name(game_data, item),
            )
            for item, rate in made.byproducts.items()
        )
        lines.append(
            _(
                "Sous-produit(s) évacué(s) vers une sortie : {items}. Sans issue, ils "
                "arrêteraient net la machine qui les fabrique."
            ).format(items=named)
        )
    if rounded:
        spare = [step for step in made.steps if step.surplus_per_minute > RATE_EPSILON]
        if spare:
            named = ", ".join(
                f"{_name(game_data, step.item_class)} "
                f"(+{formatting.rate(step.surplus_per_minute, game_data.item(step.item_class))})"
                for step in spare
            )
            lines.append(
                _(
                    "Arrondi au bâtiment entier : {count} surplus, chacun envoyé dans "
                    "un conteneur — {items}."
                ).format(count=len(spare), items=named)
            )
        else:
            lines.append(
                _("Arrondi au bâtiment entier : aucun surplus, tout tombe juste.")
            )
    else:
        fractional = [
            step
            for step in made.steps
            if not step.is_source and abs(step.machines - round(step.machines)) > RATE_EPSILON
        ]
        if fractional:
            named = ", ".join(
                f"{_name(game_data, step.item_class)} "
                f"{formatting.number(step.machines)} → {math.ceil(step.machines)}"
                for step in fractional
            )
            lines.append(
                _(
                    "Ratios exacts : {count} atelier(s) en nombre décimal, à construire "
                    "en entier — {items}."
                ).format(count=len(fractional), items=named)
            )
    return lines


__all__ = [
    "Plan",
    "PlanError",
    "Step",
    "build",
    "node_id_for",
    "plan",
    "recipe_for",
    "report",
]
