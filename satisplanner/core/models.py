"""Domain models: the game catalogue, as the engine sees it.

These models are the contract between the data layer and the engine. The data
layer builds them (from the game files or from the SQLite database) and hands them
over; the engine only ever receives them by injection and never reads a database.

Every quantity is already normalised: items/min for solids, m3/min for fluids.
"""

import math
from collections.abc import Iterable, Mapping
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field

from satisplanner.core import constants


class UnknownClassError(LookupError):
    """A class name is absent from the game catalogue."""


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


class Purity(StrEnum):
    """Purity of a resource node, which multiplies the extractor's base rate."""

    IMPURE = "impure"
    NORMAL = "normal"
    PURE = "pure"

    @property
    def multiplier(self) -> float:
        return _PURITY_MULTIPLIERS[self]


_PURITY_MULTIPLIERS: Mapping[Purity, float] = {
    Purity.IMPURE: constants.IMPURE_MULTIPLIER,
    Purity.NORMAL: constants.NORMAL_MULTIPLIER,
    Purity.PURE: constants.PURE_MULTIPLIER,
}


class RecipeAvailability(StrEnum):
    """Whether a node can be placed for a recipe, and if not, what stops it.

    The catalogue keeps **every** recipe the game has, including the ones no node
    can place. Dropping them at parse time was cheaper but made an absence
    indistinguishable from a gap in the data: an item card showed one alternate
    recipe and said nothing about the standard one, so the reader concluded the
    catalogue was wrong when it was merely narrower than the game.

    Only :data:`PLACEABLE` recipes ever reach the engine -- they live in a separate
    mapping (:attr:`GameData.unavailable_recipes`) rather than behind a flag any
    caller could forget to test.
    """

    PLACEABLE = "placeable"
    # The machine exists in the game and this version does not model it yet.
    # Temporary by nature: a value that should disappear as the scope widens.
    MACHINE_OUT_OF_SCOPE = "machine"
    # Crafted by hand at the workbench or the equipment workshop. Permanent:
    # these will never be factory nodes, because they are not machines.
    HAND_CRAFTED = "hand"


class BuildingKind(StrEnum):
    """What a building does, as far as the planner is concerned."""

    MANUFACTURER = "manufacturer"
    EXTRACTOR = "extractor"
    GENERATOR = "generator"
    BELT = "belt"
    PIPE = "pipe"
    STORAGE = "storage"
    ATTACHMENT = "attachment"


class AttachmentRole(StrEnum):
    """What a conveyor attachment or pipe junction does to a line."""

    SPLIT = "split"
    MERGE = "merge"


class SplitterMode(StrEnum):
    """Which of the three splitters this is, which is a choice of building.

    They share a shape -- one line in, three out -- and differ in what may be
    written on a branch. Standard says nothing; smart filters one branch; the
    programmable one filters every branch. The order below is the order they
    unlock in, and it is the order a menu shows them in.
    """

    STANDARD = "standard"
    SMART = "smart"
    PROGRAMMABLE = "programmable"


class _Row(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Item(_Row):
    class_name: str
    display_name: str
    display_name_fr: str
    # The game's own blurb, French when the locale has one. Empty when the data has
    # none: an item card shows nothing rather than something written here.
    description_fr: str = ""
    form: ItemForm
    stack_size: float
    icon_file: str | None
    sink_points: int
    is_raw_resource: bool
    is_event: bool = False
    # `mEnergyValue`, normalised: MJ per item for a solid, MJ per m3 for a fluid.
    # Zero for everything that cannot be burnt. This is what a generator's fuel
    # consumption is derived from, and the litre factor is already applied.
    energy_mj: float = 0.0
    # French name of the building this item drops out of as a byproduct, when that
    # building is out of scope and **no recipe at all** produces the item. Uranium
    # waste is the case: it has no recipe in the game because it falls out of the
    # nuclear plant, which this version cannot place. Empty for everything else,
    # including the items that are simply picked up off the ground -- naming the
    # source is what keeps "no recipe" from reading as "gathered in the world".
    byproduct_of_fr: str = ""


class RecipeSlot(_Row):
    """One ingredient or one product of a recipe, for a single machine."""

    item_class: str
    amount_per_cycle: float  # already in items or m3
    rate_per_minute: float


class Recipe(_Row):
    class_name: str
    display_name: str
    display_name_fr: str
    building_class: str
    cycle_seconds: float
    is_alternate: bool
    involves_fluid: bool
    ingredients: tuple[RecipeSlot, ...]
    products: tuple[RecipeSlot, ...]
    is_event: bool = False
    availability: RecipeAvailability = RecipeAvailability.PLACEABLE
    # What one machine running this recipe draws, when the **building** declares
    # nothing. Three machines are like that -- the Converter, the Particle
    # Accelerator and the Quantum Encoder -- and the game writes their draw here
    # as ``constant`` and ``factor``: the consumption swings between ``constant``
    # and ``constant + factor`` over a cycle instead of sitting still.
    #
    # Both are zero for every other recipe, and that is the whole shape of the
    # rule: a fixed nameplate on the building is the **particular** case, and a
    # draw that belongs to the machine-and-recipe pair is the general one.
    power_constant_mw: float = 0.0
    power_factor_mw: float = 0.0
    # French name of the machine, carried **only** when that machine is absent from
    # the catalogue. A placeable recipe leaves it empty and the name is read from
    # ``buildings``, so the two can never drift apart for the rows that have both.
    building_name_fr: str = ""

    @property
    def is_placeable(self) -> bool:
        """Whether a node can be placed for this recipe."""
        return self.availability is RecipeAvailability.PLACEABLE

    @property
    def has_own_power(self) -> bool:
        """Whether the draw is written on this recipe rather than on the building."""
        return self.power_constant_mw > 0 or self.power_factor_mw > 0

    @property
    def power_mw(self) -> float:
        """The draw of one machine running this recipe, in steady state.

        **The midpoint**, and the choice is worth stating because the data gives a
        range and no nominal. The consumption oscillates between ``constant`` and
        ``constant + factor``; this engine solves a steady state and has no notion
        of time, so a single figure is the only thing it can hold. Any oscillation
        symmetric about its middle -- and two parameters with no third to describe
        a shape say symmetric -- averages to that middle over a full period, so the
        midpoint is the mean draw rather than a point picked out of the interval.
        Taking the maximum would size every power plant for a peak no factory
        sustains; taking the minimum would under-report a real bill.

        Zero when the building has a nameplate of its own, which is where the
        figure then comes from.
        """
        return self.power_constant_mw + self.power_factor_mw / 2.0

    @property
    def power_range_mw(self) -> tuple[float, float]:
        """The two ends of the swing, for a reader who wants to see them."""
        return self.power_constant_mw, self.power_constant_mw + self.power_factor_mw

    @property
    def product_count(self) -> int:
        """Number of distinct products; more than one means a byproduct."""
        return len(self.products)

    def ingredient_rates(self) -> dict[str, float]:
        """Consumption per machine and per minute, by item."""
        return {slot.item_class: slot.rate_per_minute for slot in self.ingredients}

    def product_rates(self) -> dict[str, float]:
        """Production per machine and per minute, by item."""
        return {slot.item_class: slot.rate_per_minute for slot in self.products}


class Building(_Row):
    class_name: str
    display_name: str
    display_name_fr: str
    kind: BuildingKind
    power_mw: float
    icon_file: str | None
    # `mPowerConsumptionExponent`. Overclocking costs power as a **power law**, not
    # in proportion: at 250 % a machine draws 2.5 ** 1.321929, about 3.36 times its
    # nominal figure. The exponent is per building and is read, never assumed --
    # the game uses 1.321929 for everything that produces and 1.6 elsewhere.
    power_exponent: float = 1.0

    def power_at(self, clock_speed: float, nominal_mw: float | None = None) -> float:
        """Draw of one such building running at ``clock_speed`` (1.0 = 100 %).

        ``nominal_mw`` replaces the nameplate for a building that has none: the
        three variable-power machines declare zero and let the recipe say. The
        exponent is the building's either way -- overclocking is priced by the
        machine, not by what it happens to be making.
        """
        if clock_speed <= 0:
            return 0.0
        base = self.power_mw if nominal_mw is None else nominal_mw
        return float(base * clock_speed**self.power_exponent)


class PowerShard(_Row):
    """A consumable that raises a building's maximum clock speed.

    Only the overclocking kind is kept. The game declares a second shard type for
    production amplification -- the Somersloop -- which follows a different formula
    and belongs to V2.
    """

    class_name: str
    # `mExtraPotential`: 0.5, so one shard buys 50 % more clock.
    extra_potential: float

    def shards_for(self, clock_speed: float) -> int:
        """Shards one building needs to run at ``clock_speed``."""
        if clock_speed <= 1.0 or self.extra_potential <= 0:
            return 0
        needed = (clock_speed - 1.0) / self.extra_potential
        # Rounded up, but not by float noise: 200 % is exactly two shards, and
        # (2.0 - 1.0) / 0.5 lands a hair above 2 often enough to matter.
        return int(-(-round(needed, 9) // 1))


class Extractor(_Row):
    class_name: str
    item_class: str | None  # None = any node of the allowed form
    allowed_form: ItemForm
    rate_per_minute: float
    has_purity: bool
    # The building that has to be put down beside this extractor for it to work at
    # all. Only a resource-well satellite has one -- the pressuriser -- and it is
    # what makes such an extractor impossible to place on its own. Empty for every
    # extractor that stands alone, which is all the others.
    activator_class: str | None = None
    # What this extractor may be put on, when the game restricts it to a list. Empty
    # means the form alone decides, which is the case of every solid miner.
    allowed_items: tuple[str, ...] = ()

    @property
    def needs_activator(self) -> bool:
        """Whether this extractor is a satellite rather than a building of its own."""
        return self.activator_class is not None

    def rate(self, purity: Purity) -> float:
        """Base rate at 100 % clock speed, adjusted for node purity if relevant."""
        if not self.has_purity:
            return self.rate_per_minute
        return self.rate_per_minute * purity.multiplier


class GeneratorFuel(_Row):
    """One fuel a generator accepts, with everything burning it costs per minute.

    The rates are derived at parse time from the generator's power and the fuel's
    energy value, exactly as a recipe's rates are derived from its cycle time: the
    domain layer reasons in per-minute figures and never in joules.
    """

    item_class: str
    # Burnt at nominal power, in items/min for a solid and m3/min for a fluid.
    rate_per_minute: float
    # Make-up water, which is a real input on a pipe and not an accounting line:
    # the coal generator names it per fuel entry in the game files.
    supplemental_class: str | None = None
    supplemental_per_minute: float = 0.0

    def input_rates(self) -> dict[str, float]:
        """Everything one such generator swallows per minute, by item."""
        rates = {self.item_class: self.rate_per_minute}
        if self.supplemental_class and self.supplemental_per_minute > 0:
            rates[self.supplemental_class] = self.supplemental_per_minute
        return rates


class Generator(_Row):
    """A building that burns something and puts power on the grid.

    Its clock is fixed at 100 %: the game raises a generator's *production* by an
    exponent of its own, distinct from the consumption exponent that prices an
    overclocked machine, and conflating the two would invent numbers. Overclocking
    generators is therefore a subject in itself and stays out of this version --
    which is why :class:`~satisplanner.core.graph.GeneratorNode` has no clock field
    at all rather than one pinned to 1.0 by convention.
    """

    class_name: str
    # `mPowerProduction`: what one unit puts on the grid at 100 %.
    power_mw: float
    fuels: tuple[GeneratorFuel, ...]

    def fuel(self, item_class: str) -> GeneratorFuel | None:
        for candidate in self.fuels:
            if candidate.item_class == item_class:
                return candidate
        return None

    def accepts(self, item_class: str) -> bool:
        return self.fuel(item_class) is not None

    @property
    def default_fuel(self) -> str | None:
        """The fuel a freshly placed generator starts on: the game's own first."""
        return self.fuels[0].item_class if self.fuels else None


class Belt(_Row):
    class_name: str
    tier: int
    items_per_minute: float


class Pipe(_Row):
    class_name: str
    tier: int
    cubic_metres_per_minute: float


class Attachment(_Row):
    """A splitter, a merger, or a pipe junction.

    These are nodes on the canvas like any other, placed where the player would
    place them: a port carries one line, so getting a second one out of it takes a
    building, and a building somebody has to put down is a building they should be
    able to see. Their throughput is deliberately not modelled -- a splitter moves
    2000 items/min while the fastest conveyor tops out at 1200, so it can never be
    the bottleneck -- which is why this row carries no rate.
    """

    class_name: str
    form: ItemForm
    roles: tuple[AttachmentRole, ...]
    # Lines one unit serves on its many-line side: 3 for a conveyor splitter (one
    # input, three outputs) and 3 for a pipe junction (four ports, one used as the
    # trunk). Mirrored by ``core.constants.ATTACHMENT_BRANCHES``, which is what the
    # graph validates against when there is no catalogue to hand.
    branches: int
    # Which of the three splitters this is, or ``None`` for anything that is not
    # one. A pipe junction is ``None`` and not ``STANDARD``: the game has no
    # filtering junction, and saying "standard" would suggest there is a smart one
    # to upgrade to.
    splitter_mode: SplitterMode | None = None

    def units_for(self, lines: int) -> int:
        """How many of these it takes to serve ``lines`` lines off one port.

        A splitter turns one line into three, so chaining a second one onto a
        branch buys two more: the count is ``ceil((lines - 1) / (branches - 1))``.
        One line needs none.

        This is the **simple mode's** answer, and it is deliberately not the same
        question as the faithful mode's. There the fittings are nodes somebody drew
        and the list counts what is on the canvas; here nobody drew them, and the
        list says how many the drawing implies. How they would be chained is
        geometry, which is out of scope -- so the count is as far as this can
        honestly go, and it is exactly as far as it went before the fittings
        became nodes.
        """
        if lines <= 1 or self.branches <= 1:
            return 0
        return math.ceil((lines - 1) / (self.branches - 1))


class BuildingCost(_Row):
    """What one of a building costs to put down, from the game's own build recipe.

    Buildings are crafted like everything else: the game declares a recipe whose
    ``mProducedIn`` is the build gun and whose single product is the building. This
    is that recipe, kept apart from :class:`Recipe` because it has none of what
    makes one -- no machine, no cycle, and therefore no rate per minute. A build
    cost is a quantity, not a flow, and giving it a cycle time would invite it to
    be divided by one.

    ``amounts`` is per **unit built**: one smelter, or one unit of length of belt.
    What a length is, is geometry, and geometry is out of scope -- which is why the
    shopping list prices machines and leaves lines blank.
    """

    class_name: str  # the Build_* class this is the cost of
    recipe_class: str  # the Recipe_* it was read from, so the figure can be traced
    amounts: dict[str, float]  # item class -> quantity for one unit


class Storage(_Row):
    class_name: str
    form: ItemForm
    slots: int | None  # solids: capacity depends on the stored item's stack size
    capacity_m3: float | None  # fluids

    def capacity_for(self, item: Item) -> float:
        """Capacity in the item's own unit: items for solids, m3 for fluids."""
        if item.form.is_fluid:
            return self.capacity_m3 or 0.0
        return (self.slots or 0) * item.stack_size


class GameData(BaseModel):
    """The injected catalogue. Lookups fail loudly rather than returning None."""

    model_config = ConfigDict(frozen=True)

    items: dict[str, Item] = Field(default_factory=dict)
    recipes: dict[str, Recipe] = Field(default_factory=dict)
    buildings: dict[str, Building] = Field(default_factory=dict)
    extractors: dict[str, Extractor] = Field(default_factory=dict)
    generators: dict[str, Generator] = Field(default_factory=dict)
    belts: dict[str, Belt] = Field(default_factory=dict)
    pipes: dict[str, Pipe] = Field(default_factory=dict)
    storages: dict[str, Storage] = Field(default_factory=dict)
    attachments: dict[str, Attachment] = Field(default_factory=dict)
    power_shards: dict[str, PowerShard] = Field(default_factory=dict)
    # Build costs, by Build_* class. Empty on a catalogue built before schema 6,
    # and the shopping list says so rather than showing a total of nothing.
    building_costs: dict[str, BuildingCost] = Field(default_factory=dict)
    # Recipes the game has and no node can place. They exist so that a card can
    # say *why* something is missing; they are deliberately kept out of
    # ``recipes`` so that no computation can reach them by accident.
    unavailable_recipes: dict[str, Recipe] = Field(default_factory=dict)

    @classmethod
    def from_rows(
        cls,
        *,
        items: Iterable[Item] = (),
        recipes: Iterable[Recipe] = (),
        buildings: Iterable[Building] = (),
        extractors: Iterable[Extractor] = (),
        generators: Iterable[Generator] = (),
        belts: Iterable[Belt] = (),
        pipes: Iterable[Pipe] = (),
        storages: Iterable[Storage] = (),
        attachments: Iterable[Attachment] = (),
        power_shards: Iterable[PowerShard] = (),
        building_costs: Iterable[BuildingCost] = (),
        unavailable_recipes: Iterable[Recipe] = (),
    ) -> Self:
        return cls(
            items={row.class_name: row for row in items},
            recipes={row.class_name: row for row in recipes},
            buildings={row.class_name: row for row in buildings},
            extractors={row.class_name: row for row in extractors},
            generators={row.class_name: row for row in generators},
            belts={row.class_name: row for row in belts},
            pipes={row.class_name: row for row in pipes},
            storages={row.class_name: row for row in storages},
            attachments={row.class_name: row for row in attachments},
            power_shards={row.class_name: row for row in power_shards},
            building_costs={row.class_name: row for row in building_costs},
            unavailable_recipes={row.class_name: row for row in unavailable_recipes},
        )

    def item(self, class_name: str) -> Item:
        return _get(self.items, class_name, "item")

    def recipe(self, class_name: str) -> Recipe:
        return _get(self.recipes, class_name, "recette")

    def building(self, class_name: str) -> Building:
        return _get(self.buildings, class_name, "bâtiment")

    def extractor(self, class_name: str) -> Extractor:
        return _get(self.extractors, class_name, "extracteur")

    def generator(self, class_name: str) -> Generator:
        return _get(self.generators, class_name, "générateur")

    def storage(self, class_name: str) -> Storage:
        return _get(self.storages, class_name, "stockage")

    def transport_capacity(self, class_name: str) -> float:
        """Throughput of a belt or a pipe, in the unit of what it carries."""
        if class_name in self.belts:
            return self.belts[class_name].items_per_minute
        if class_name in self.pipes:
            return self.pipes[class_name].cubic_metres_per_minute
        msg = f"transport inconnu : {class_name}"
        raise UnknownClassError(msg)

    def transport_form_matches(self, class_name: str, form: ItemForm) -> bool:
        """A solid needs a belt, a fluid needs a pipe."""
        if class_name in self.belts:
            return not form.is_fluid
        if class_name in self.pipes:
            return form.is_fluid
        msg = f"transport inconnu : {class_name}"
        raise UnknownClassError(msg)

    def transports_for(self, form: ItemForm) -> list[Belt] | list[Pipe]:
        """Every transport able to carry this form, sorted by increasing capacity."""
        if form.is_fluid:
            return sorted(self.pipes.values(), key=lambda pipe: pipe.cubic_metres_per_minute)
        return sorted(self.belts.values(), key=lambda belt: belt.items_per_minute)

    def smallest_transport_for(self, form: ItemForm, rate: float) -> Belt | Pipe | None:
        """Cheapest tier whose capacity covers ``rate``, or None if none is enough."""
        for transport in self.transports_for(form):
            if self.transport_capacity(transport.class_name) >= rate:
                return transport
        return None

    def overclock_shard(self) -> PowerShard | None:
        """The shard used to overclock, or ``None`` on a catalogue without one.

        There is exactly one in the game, but the lookup is by content rather than
        by class name so that nothing here hard-codes a game identifier.
        """
        shards = sorted(self.power_shards.values(), key=lambda shard: shard.class_name)
        return shards[0] if shards else None

    def shards_for(self, clock_speed: float, buildings: float) -> dict[str, int]:
        """Shards needed to run ``buildings`` machines at ``clock_speed``.

        Counted per whole building, as they are placed: half a machine at 250 % is
        still a machine with three shards in it.
        """
        shard = self.overclock_shard()
        if shard is None:
            return {}
        each = shard.shards_for(clock_speed)
        total = each * math.ceil(buildings) if each else 0
        return {shard.class_name: total} if total else {}

    def attachment_for(
        self,
        form: ItemForm,
        role: AttachmentRole,
        mode: SplitterMode | None = None,
    ) -> Attachment | None:
        """The splitter, merger or junction this form uses for that role.

        Solids and fluids use different hardware, and a pipe junction does both
        jobs, so the lookup is by form and role rather than by class name.

        ``mode`` picks between the three conveyor splitters and must be ``None``
        for anything else -- a merger has no modes and a pipe junction has none
        either, so asking for a smart one returns nothing rather than the ordinary
        one. That is the answer a caller wants: the game has no filtering junction,
        and quietly handing back the plain one would price a building that cannot
        do the job.
        """
        candidates = [
            attachment
            for attachment in self.attachments.values()
            if attachment.form.is_fluid is form.is_fluid
            and role in attachment.roles
            and attachment.splitter_mode is mode
        ]
        return min(candidates, key=lambda a: a.class_name) if candidates else None


def _get[T](mapping: dict[str, T], class_name: str, label: str) -> T:
    try:
        return mapping[class_name]
    except KeyError:
        msg = f"{label} inconnu(e) dans les données du jeu : {class_name}"
        raise UnknownClassError(msg) from None
