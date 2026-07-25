"""Result types shared by the solver and the diagnostics.

They live in their own module so that ``engine`` can call ``validation`` without a
circular import: the dependency order is ``results <- validation <- engine``.

Every mapping is built sorted by key, and every sequence sorted by identifier, so
that two runs on the same factory produce byte-identical reports whatever order the
graph was assembled in.
"""

import math
from enum import StrEnum
from typing import Final

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
    NOT_CONVERGED = "not_converged"


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
    blocked_products: tuple[str, ...] = ()
    # Items this node is genuinely short of: it asked for more and the suppliers had
    # nothing left. An item that is short only because the node is idle for another
    # reason is not listed -- that is a consequence, not a cause.
    starved_items: tuple[str, ...] = ()
    building_class: str | None = None
    machine_count: float | None = None  # what the user set
    useful_machine_count: float | None = None  # what the inputs actually feed
    power_mw: float = 0.0

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
    """What one conveyor or pipe carries, and how close to its limit it runs."""

    edge_id: str
    source: str
    target: str
    item_class: str
    transport_class: str
    rate_per_minute: float
    capacity_per_minute: float

    @property
    def saturation(self) -> float:
        """Fraction of the line's capacity in use; above 1 the line cannot cope."""
        if self.capacity_per_minute <= 0:
            return 0.0
        return self.rate_per_minute / self.capacity_per_minute

    @property
    def is_saturated(self) -> bool:
        return self.rate_per_minute > self.capacity_per_minute + FLOW_EPSILON


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

    @property
    def total_buildings(self) -> int:
        return sum(self.buildings.values())


class FactoryReport(_Result):
    """The complete steady-state answer for one factory."""

    converged: bool
    iterations: int
    nodes: tuple[NodeSolution, ...] = ()
    edges: tuple[EdgeSolution, ...] = ()
    buffers: tuple[BufferSolution, ...] = ()

    # Category 1: raw solids consumed per minute, by ore.
    raw_solids: dict[str, float] = Field(default_factory=dict)
    # Category 2: fluids consumed, plus the byproduct balance.
    raw_fluids: dict[str, float] = Field(default_factory=dict)
    byproducts: tuple[ByproductBalance, ...] = ()
    # Category 3: power draw.
    power_total_mw: float = 0.0
    power_by_building: dict[str, float] = Field(default_factory=dict)

    final_outputs: dict[str, float] = Field(default_factory=dict)
    discarded_outputs: dict[str, float] = Field(default_factory=dict)
    shopping_list: ShoppingList = ShoppingList()
    diagnostics: tuple[Diagnostic, ...] = ()

    def node(self, node_id: str) -> NodeSolution:
        for solution in self.nodes:
            if solution.node_id == node_id:
                return solution
        msg = f"aucun resultat pour le noeud {node_id}"
        raise KeyError(msg)

    def edge(self, edge_id: str) -> EdgeSolution:
        for solution in self.edges:
            if solution.edge_id == edge_id:
                return solution
        msg = f"aucun resultat pour l'arete {edge_id}"
        raise KeyError(msg)

    def has_errors(self) -> bool:
        return any(item.severity is Severity.ERROR for item in self.diagnostics)

    def by_severity(self, severity: Severity) -> tuple[Diagnostic, ...]:
        return tuple(item for item in self.diagnostics if item.severity is severity)

    def with_diagnostics(self, diagnostics: tuple[Diagnostic, ...]) -> "FactoryReport":
        return self.model_copy(update={"diagnostics": diagnostics})
