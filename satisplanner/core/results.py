"""Result types shared by the solver and the diagnostics.

They live in their own module so that ``engine`` can call ``validation`` without a
circular import: the dependency order is ``results <- validation <- engine``.

Every mapping is built sorted by key, and every sequence sorted by identifier, so
that two runs on the same factory produce byte-identical reports whatever order the
graph was assembled in.
"""

import math
from enum import StrEnum
from functools import cached_property
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field

from satisplanner.core.graph import NodeKind

# Below this rate, in items or m3 per minute, a flow is considered zero. Chosen
# well under anything the game can display, and well above float noise.
FLOW_EPSILON: Final = 1e-9


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class DiagnosticCode(StrEnum):
    """Machine-readable reason, so the UI can group and filter without parsing text."""

    DEFICIT = "deficit"
    BLOCKED_BYPRODUCT = "blocked_byproduct"
    BACKPRESSURE = "backpressure"
    SURPLUS = "surplus"
    LINE_SATURATION = "line_saturation"
    INCOMPATIBLE_FORM = "incompatible_form"
    INCOMPATIBLE_RECIPE = "incompatible_recipe"
    UNCONNECTED_NODE = "unconnected_node"
    AMBIGUOUS_BUFFER = "ambiguous_buffer"
    BUFFER_FILLING = "buffer_filling"
    BUFFER_DRAINING = "buffer_draining"
    NOT_SUSTAINABLE = "not_sustainable"
    NOT_CONVERGED = "not_converged"
    POWER_DEFICIT = "power_deficit"


class _Result(BaseModel):
    model_config = ConfigDict(frozen=True)


class Diagnostic(_Result):
    """One finding about the factory, aimed at the user."""

    severity: Severity
    code: DiagnosticCode
    message: str  # French: this string is displayed as is
    node_id: str | None = None
    edge_id: str | None = None

    def __str__(self) -> str:
        target = self.node_id or self.edge_id or "-"
        return f"[{self.severity.value:9}] {target:16} {self.message}"


class LimitingFactor(StrEnum):
    """Why a node is not running at 100 %."""

    NONE = "none"
    INPUTS = "inputs"
    OUTPUTS = "outputs"
    # A conveyor or a pipe cannot carry what the node would send or receive. The
    # material exists and there is room for it: only the line is too small.
    LINE = "line"
    BLOCKED = "blocked"


class NodeSolution(_Result):
    """What one node actually does in steady state."""

    node_id: str
    kind: NodeKind
    label: str
    ratio: float  # operating rate, 0 to 1
    limiting: LimitingFactor
    inputs: dict[str, float] = Field(default_factory=dict)  # consumed, per item
    outputs: dict[str, float] = Field(default_factory=dict)  # produced, per item
    # What the node would consume and produce if nothing held it back: the recipe's
    # rates times the machine count times the clock, before any shortage is applied.
    #
    # Carried out of the solver rather than recovered from ``inputs / ratio``, which
    # is not the same quantity: at a ratio of zero it is not defined at all, and zero
    # is exactly when a reader most wants to know what the node was meant to do.
    #
    # A port with no nameplate has no entry -- a buffer or an exit absorbs whatever
    # reaches it, and writing an infinity there would be a number pretending to be a
    # capacity.
    nominal_inputs: dict[str, float] = Field(default_factory=dict)
    nominal_outputs: dict[str, float] = Field(default_factory=dict)
    blocked_products: tuple[str, ...] = ()
    # Items this node is genuinely short of: it asked for more and the suppliers had
    # nothing left. An item that is short only because the node is idle for another
    # reason is not listed -- that is a consequence, not a cause.
    starved_items: tuple[str, ...] = ()
    # Items the node cannot get enough of, or cannot get rid of, purely because a
    # line is too small. Filled in once the uncapped companion run is known.
    line_limited_items: tuple[str, ...] = ()
    building_class: str | None = None
    machine_count: float | None = None  # what the user set
    useful_machine_count: float | None = None  # what the inputs actually feed
    # 1.0 is 100 %. Throughput follows it exactly; power follows it raised to the
    # building's exponent, which is why the two are reported separately.
    clock_speed: float = 1.0
    # Shards this node needs, for the whole buildings it is made of.
    power_shards: int = 0
    power_mw: float = 0.0
    # What a generator puts on the grid, already scaled by its operating ratio: a
    # generator starved of fuel burns less and produces less. Zero everywhere else.
    power_produced_mw: float = 0.0

    @property
    def is_overclocked(self) -> bool:
        return self.clock_speed > 1.0

    @property
    def is_underclocked(self) -> bool:
        return self.clock_speed < 1.0

    @property
    def idle_machine_count(self) -> float:
        """Machines paid for but not producing."""
        if self.machine_count is None or self.useful_machine_count is None:
            return 0.0
        return self.machine_count - self.useful_machine_count

    @property
    def integer_machine_count(self) -> int:
        """The buildable count: you cannot build 4.33 assemblers."""
        return math.ceil(self.machine_count or 0.0)


class EdgeSolution(_Result):
    """What one conveyor or pipe carries, and how close to its limit it runs.

    ``rate_per_minute`` is what the line really moves and never exceeds its
    capacity, because a Mk.1 belt fed 480 items a minute carries 60 and backs the
    rest up. ``desired_rate_per_minute`` is what would flow if the line were
    infinite -- the figure that says which tier would actually be needed.
    """

    edge_id: str
    source: str
    target: str
    item_class: str
    transport_class: str
    rate_per_minute: float
    capacity_per_minute: float
    desired_rate_per_minute: float | None = None

    @property
    def demanded_rate(self) -> float:
        """What the factory wants to push through, capacity aside."""
        if self.desired_rate_per_minute is None:
            return self.rate_per_minute
        return self.desired_rate_per_minute

    @property
    def saturation(self) -> float:
        """Demanded flow over capacity; above 1 the line cannot cope."""
        if self.capacity_per_minute <= 0:
            return 0.0
        return self.demanded_rate / self.capacity_per_minute

    @property
    def is_saturated(self) -> bool:
        """The line is the binding constraint: more is wanted than it can carry."""
        return self.demanded_rate > self.capacity_per_minute + FLOW_EPSILON

    @property
    def is_at_capacity(self) -> bool:
        """Running at its ceiling, which may simply mean it fits exactly."""
        return (
            self.capacity_per_minute > 0
            and self.rate_per_minute >= self.capacity_per_minute - FLOW_EPSILON
        )

    @property
    def blocked_rate(self) -> float:
        """What the line refuses to carry."""
        return max(self.demanded_rate - self.rate_per_minute, 0.0)


class BufferState(StrEnum):
    FILLING = "filling"
    DRAINING = "draining"
    BALANCED = "balanced"


class BufferSolution(_Result):
    """A buffer's net balance, and how long before it fills up or runs dry."""

    node_id: str
    item_class: str | None
    inflow: float
    outflow: float
    capacity: float
    initial_content: float
    state: BufferState
    minutes_to_full: float | None = None
    minutes_to_empty: float | None = None

    @property
    def net(self) -> float:
        return self.inflow - self.outflow


class ByproductBalance(_Result):
    """Where a byproduct goes: back into the factory, out, or up in smoke."""

    item_class: str
    produced: float
    recycled: float  # consumed again by a machine
    stored: float
    exported: float  # sent to a normal output
    discarded: float  # sent to an output flagged as a deliberate discard

    @property
    def unaccounted(self) -> float:
        return self.produced - self.recycled - self.stored - self.exported - self.discarded


class ShoppingList(_Result):
    """What has to be built, counted in whole buildings."""

    buildings: dict[str, int] = Field(default_factory=dict)
    belts_by_tier: dict[int, int] = Field(default_factory=dict)
    pipes_by_tier: dict[int, int] = Field(default_factory=dict)
    # Splitters, mergers and pipe junctions, deduced from the lines that share a
    # node rather than placed by hand: they are never nodes on the canvas.
    attachments: dict[str, int] = Field(default_factory=dict)
    # Power shards, by item class, implied by the overclocked nodes. Consumables
    # rather than buildings, hence their own entry: they are not built, they are
    # produced and slotted in, and they do not belong in a building count.
    power_shards: dict[str, int] = Field(default_factory=dict)

    @property
    def total_buildings(self) -> int:
        return sum(self.buildings.values()) + sum(self.attachments.values())


class FactoryReport(_Result):
    """The complete steady-state answer for one factory.

    A report whose buffers are draining describes a factory that runs *for now*, on
    stock. That is a real and useful answer -- it is what the game shows -- but it
    is not the established regime, so :attr:`sustained` carries the same factory
    solved again with the buffers supplying nothing. The two sets of figures are
    meant to be read side by side: "with the stocks" and "once they run out".
    """

    converged: bool
    iterations: int
    nodes: tuple[NodeSolution, ...] = ()
    edges: tuple[EdgeSolution, ...] = ()
    buffers: tuple[BufferSolution, ...] = ()
    # Present only when this report is not sustainable; never nested twice, since a
    # buffer that supplies nothing can only fill up or stay level.
    sustained: "FactoryReport | None" = None

    # Category 1: raw solids consumed per minute, by ore.
    raw_solids: dict[str, float] = Field(default_factory=dict)
    # Category 2: fluids consumed, plus the byproduct balance.
    raw_fluids: dict[str, float] = Field(default_factory=dict)
    byproducts: tuple[ByproductBalance, ...] = ()
    # Category 3: power. Two numbers side by side, and nothing more.
    #
    # Electricity is a **counter, never a constraint**: a deficit is reported and
    # changes no rate anywhere. That is deliberate and it is the one place in the
    # project where an error-level finding does not translate into a reduced ratio.
    # In game a shortfall does not slow the factory down, it trips the whole grid
    # until somebody walks over and resets it; showing everything at zero would
    # teach nothing, and throttling part of it would be an invention.
    power_total_mw: float = 0.0
    power_by_building: dict[str, float] = Field(default_factory=dict)
    power_production_mw: float = 0.0
    power_production_by_building: dict[str, float] = Field(default_factory=dict)

    final_outputs: dict[str, float] = Field(default_factory=dict)
    discarded_outputs: dict[str, float] = Field(default_factory=dict)
    shopping_list: ShoppingList = ShoppingList()
    diagnostics: tuple[Diagnostic, ...] = ()

    # ---------------------------------------------------------------- lookups
    #
    # A report is read back one identifier at a time, once per node item on the
    # canvas and once per cell the table paints, which is a great many times for
    # something that used to walk the whole tuple each call. The two maps below
    # are built on first use and live for as long as the report does -- a frozen
    # object cannot change what they index, which is exactly what makes caching
    # them safe. Copies are the one exception, and :meth:`_copied` handles it.

    @cached_property
    def _by_node(self) -> dict[str, NodeSolution]:
        return {solution.node_id: solution for solution in self.nodes}

    @cached_property
    def _by_edge(self) -> dict[str, EdgeSolution]:
        return {solution.edge_id: solution for solution in self.edges}

    def node(self, node_id: str) -> NodeSolution:
        try:
            return self._by_node[node_id]
        except KeyError:
            msg = f"aucun résultat pour le noeud {node_id}"
            raise KeyError(msg) from None

    def edge(self, edge_id: str) -> EdgeSolution:
        try:
            return self._by_edge[edge_id]
        except KeyError:
            msg = f"aucun résultat pour l'arête {edge_id}"
            raise KeyError(msg) from None

    def _copied(self, **update: Any) -> "FactoryReport":
        """A copy with some fields replaced, and **never** a stale index.

        ``model_copy`` carries the whole instance dictionary across, cached
        properties included, so a copy that replaces ``nodes`` would otherwise
        keep an index of the solutions it has just thrown away. Rather than
        remember which copies are safe -- most of them are -- every copy drops
        both maps and lets them be built again on demand. The rule with no
        exceptions is the one that cannot be got wrong.
        """
        fresh = self.model_copy(update=update)
        fresh.__dict__.pop("_by_node", None)
        fresh.__dict__.pop("_by_edge", None)
        return fresh

    def has_errors(self) -> bool:
        return any(item.severity is Severity.ERROR for item in self.diagnostics)

    def by_severity(self, severity: Severity) -> tuple[Diagnostic, ...]:
        return tuple(item for item in self.diagnostics if item.severity is severity)

    @property
    def power_balance_mw(self) -> float:
        """Production minus consumption. Negative means the grid trips in game."""
        return round(self.power_production_mw - self.power_total_mw, 9)

    @property
    def has_generators(self) -> bool:
        return any(solution.kind is NodeKind.GENERATOR for solution in self.nodes)

    @property
    def has_power_deficit(self) -> bool:
        """True only when the factory generates and does not generate enough.

        A factory with no generator at all is not in deficit -- it is a factory
        powered from elsewhere, which is the normal way to draw one here. A factory
        whose only generator has run out of coal *is*, and that is why the test is
        on the presence of a generator rather than on a non-zero production.
        """
        return self.has_generators and self.power_balance_mw < -FLOW_EPSILON

    @property
    def is_sustainable(self) -> bool:
        """False as soon as one buffer has a negative net flow.

        A factory living off a stock is not in steady state: the headline figures
        stop being true the minute the tank runs dry.
        """
        return not self.draining_buffers

    @property
    def draining_buffers(self) -> tuple[BufferSolution, ...]:
        """Buffers being emptied, in report order, with their remaining autonomy."""
        return tuple(buffer for buffer in self.buffers if buffer.net < -FLOW_EPSILON)

    @property
    def shortest_autonomy_minutes(self) -> float | None:
        """Time until the first buffer runs dry, i.e. until these figures stop holding."""
        times = [
            buffer.minutes_to_empty
            for buffer in self.draining_buffers
            if buffer.minutes_to_empty is not None
        ]
        return min(times) if times else None

    def with_diagnostics(self, diagnostics: tuple[Diagnostic, ...]) -> "FactoryReport":
        return self._copied(diagnostics=diagnostics)

    def with_sustained(self, sustained: "FactoryReport") -> "FactoryReport":
        return self._copied(sustained=sustained)

    def with_flows(
        self, nodes: tuple[NodeSolution, ...], edges: tuple[EdgeSolution, ...]
    ) -> "FactoryReport":
        """The same report with its solved figures replaced.

        Used to fold the uncapped companion run in, which is the one copy that
        really does change what the lookups point at.
        """
        return self._copied(nodes=nodes, edges=edges)
