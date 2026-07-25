"""Steady-state solver.

The whole calculation rests on one quantity per node: its **operating ratio**, the
fraction of its nominal throughput it actually achieves. For a machine the nominal
throughput is ``machine_count`` times the recipe's per-machine rates; for an
extractor it is the base rate times the node purity.

    ratio = min( satisfaction of every input, absorption of every output )

Both sides matter, and that is what makes this a fixed point rather than a single
pass: a machine's intake depends on what upstream sends, and what upstream can send
depends on how much this machine can swallow.

Two design decisions deserve to be spelled out.

**The iteration starts optimistic and descends.** Every ratio starts at 1 and the
sequence decreases until it settles. Starting from zero, as a first reading might
suggest, lands on a degenerate answer: in a recycling loop -- recycled plastic feeds
recycled rubber which feeds recycled plastic -- "everything stopped" is a perfectly
self-consistent state. The loop's real behaviour is the *largest* consistent state,
which is what a descending iteration finds. Damping is therefore off by default: it
would only slow a monotone descent down, and it is switched on late, as a rescue for
graphs where competing consumers make the sequence oscillate instead of descend.

**A byproduct with nowhere to go blocks its machine completely, and that is decided
on the topology alone**, before any number is computed. Deciding it on flows would
make the system bistable: "blocked, therefore nothing flows, therefore blocked" is
as self-consistent as the running state. Once a route exists, a route that only
absorbs part of the output no longer blocks anything -- it throttles, which is what
the game does on average when a machine stutters.

**Transport capacity is a constraint, not a remark.** A Mk.1 belt fed 480 items a
minute carries 60 and backs the rest up, so the flow on an edge is capped by its
tier and the shortfall propagates upstream as back pressure. To still be able to
say *which* tier would be needed, every solve is run twice: once with the caps and
once without. The uncapped run gives each line the rate it would carry if it were
infinite, which is what the diagnostics quote.

:func:`solve` therefore returns up to four fixed points in one report: the answer,
its uncapped companion, and -- when a buffer is being drained -- the same pair
solved again with the buffers supplying nothing, which is the regime that actually
holds once the stock is gone.
"""

import logging
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final

from satisplanner.core import constants, validation
from satisplanner.core.graph import (
    Edge,
    ExternalSourceNode,
    FactoryGraph,
    MachineNode,
    Node,
    NodeKind,
    OutputNode,
    ResourceNode,
    StorageNode,
    WaterExtractorNode,
    condensation_order,
    machine_building,
    storage_item,
)
from satisplanner.core.models import AttachmentRole, GameData
from satisplanner.core.results import (
    FLOW_EPSILON,
    BufferSolution,
    BufferState,
    ByproductBalance,
    EdgeSolution,
    FactoryReport,
    LimitingFactor,
    NodeSolution,
    ShoppingList,
)

logger = logging.getLogger(__name__)

# Iterations after which damping is switched on, as a rescue for oscillation.
DAMPING_AFTER: Final = 100
DAMPING_FACTOR: Final = 0.5

# Inner allocation loop: each round either saturates a line or a consumer, or moves
# everything left, so this bound is far above what any real factory needs.
MAX_ALLOCATION_ROUNDS: Final = 200

# Factor applied to a node's machine count when probing how much it could absorb.
PROBE_FACTOR: Final = 1e6


@dataclass(frozen=True)
class SolveOptions:
    """Switches that turn one solve into a different question about the same graph."""

    # False ignores every belt and pipe ceiling. Used for the companion run that
    # answers "what would this line carry if it were big enough?".
    enforce_line_capacity: bool = True
    # False forbids a buffer from handing out more than it receives, which gives the
    # regime that holds once its stock is exhausted.
    buffers_supply: bool = True


@dataclass
class _NodeState:
    """Everything the iteration keeps about one node."""

    node: Node
    nominal_out: dict[str, float] = field(default_factory=dict)
    nominal_in: dict[str, float] = field(default_factory=dict)
    ratio_in: dict[str, float] = field(default_factory=dict)
    ratio_out: dict[str, float] = field(default_factory=dict)
    inflow: dict[str, float] = field(default_factory=dict)
    outflow: dict[str, float] = field(default_factory=dict)
    blocked_products: tuple[str, ...] = ()
    absorbs_without_limit: bool = False

    @property
    def is_blocked(self) -> bool:
        return bool(self.blocked_products)

    def ratio(self) -> float:
        """Operating ratio: the tightest of every input and output constraint."""
        if self.is_blocked:
            return 0.0
        limits = [*self.ratio_in.values(), *self.ratio_out.values()]
        return min([1.0, *limits])

    def input_ratio(self) -> float:
        """Operating ratio as limited by supply alone.

        What a node puts on the market must be throttled by starvation only, never
        by how much of its previous offer came back unsold: doing both makes the
        offer and the refusal chase each other and the sequence oscillates with a
        period of two instead of settling.
        """
        if self.is_blocked:
            return 0.0
        return min([1.0, *self.ratio_in.values()])

    def ratio_excluding(self, item_class: str) -> float:
        """Operating ratio ignoring one input item.

        Used to size how much of that very item the node could take: capping its
        appetite with last round's intake of the same item would make a transient
        shortage permanent.
        """
        if self.is_blocked:
            return 0.0
        limits = [value for item, value in self.ratio_in.items() if item != item_class]
        limits.extend(self.ratio_out.values())
        return min([1.0, *limits])

    def starved_items(self, spare_upstream: Mapping[str, float]) -> tuple[str, ...]:
        """Inputs that fell short with nothing left upstream to make up the difference."""
        if self.is_blocked:
            return ()
        return tuple(
            item
            for item, nominal in sorted(self.nominal_in.items())
            if math.isfinite(nominal)
            and self.inflow.get(item, 0.0) < nominal - FLOW_EPSILON
            and spare_upstream.get(item, 0.0) <= FLOW_EPSILON
        )

    def limiting_factor(self, spare_upstream: Mapping[str, float]) -> LimitingFactor:
        """Why this node is throttled: starved of input, or held back by its output.

        At the fixed point the two look identical from the ratios alone -- a machine
        held back by a blocked byproduct ends up taking less input, so its input
        ratio drops too. What tells them apart is whether the material was there for
        the taking: if a supplier still had some left over and this node did not take
        it, then it is the output side that is holding it back.
        """
        if self.is_blocked:
            return LimitingFactor.BLOCKED
        if self.ratio() >= 1.0 - FLOW_EPSILON:
            return LimitingFactor.NONE
        if self.starved_items(spare_upstream):
            return LimitingFactor.INPUTS
        return LimitingFactor.OUTPUTS


class _Solver:
    """One solve. Holds the mutable iteration state; ``run`` returns the report."""

    def __init__(
        self, graph: FactoryGraph, game_data: GameData, options: SolveOptions | None = None
    ) -> None:
        self.graph = graph
        self.game_data = game_data
        self.options = options or SolveOptions()
        self.states: dict[str, _NodeState] = {}
        self.flows: dict[str, float] = {edge.id: 0.0 for edge in graph.edges}
        self.out_edges: dict[str, list[Edge]] = {node.id: [] for node in graph.nodes}
        self.in_edges: dict[str, list[Edge]] = {node.id: [] for node in graph.nodes}
        for edge in graph.sorted_edges():
            self.out_edges[edge.source].append(edge)
            self.in_edges[edge.target].append(edge)
        self.limits: dict[str, float] = {
            edge.id: self._capacity(edge) if self.options.enforce_line_capacity else math.inf
            for edge in graph.edges
        }
        self.converged = False
        self.iterations = 0
        # Per item, what each producer still had on offer after the last allocation.
        self.leftovers: dict[str, dict[str, float]] = {}
        self._prepare()

    # ------------------------------------------------------------------ setup

    def _prepare(self) -> None:
        for node in self.graph.sorted_nodes():
            state = _NodeState(node=node)
            state.nominal_out = self._nominal_out(node)
            state.nominal_in = self._nominal_in(node)
            state.absorbs_without_limit = isinstance(node, OutputNode | StorageNode)
            state.blocked_products = self._blocked_products(node)
            # Optimistic start: every constraint assumed satisfied.
            state.ratio_in = dict.fromkeys(state.nominal_in, 1.0)
            state.ratio_out = dict.fromkeys(state.nominal_out, 1.0)
            self.states[node.id] = state

    def _nominal_out(self, node: Node) -> dict[str, float]:
        """Production at full speed, per item and per minute."""
        match node:
            case ResourceNode():
                extractor = self.game_data.extractor(node.extractor_class)
                return {node.item_class: extractor.rate(node.purity) * node.count}
            case WaterExtractorNode():
                extractor = self.game_data.extractor(node.extractor_class)
                item = extractor.item_class
                return {item: extractor.rate_per_minute * node.count} if item else {}
            case ExternalSourceNode():
                return {node.item_class: node.rate_per_minute}
            case MachineNode():
                rates = self.game_data.recipe(node.recipe_class).product_rates()
                return {item: rate * node.machine_count for item, rate in sorted(rates.items())}
            case StorageNode():
                # Filled in at every iteration: a buffer hands downstream what it
                # asks for, drawing on its stock if its own intake is not enough.
                item = storage_item(node, self.graph)
                return {item: 0.0} if item else {}
            case OutputNode():
                return {}

    def _nominal_in(self, node: Node) -> dict[str, float]:
        """Consumption at full speed, per item and per minute."""
        match node:
            case MachineNode():
                rates = self.game_data.recipe(node.recipe_class).ingredient_rates()
                return {item: rate * node.machine_count for item, rate in sorted(rates.items())}
            case StorageNode() | OutputNode():
                item = (
                    storage_item(node, self.graph)
                    if isinstance(node, StorageNode)
                    else node.item_class
                )
                # Unlimited absorption; the figure is only a placeholder.
                return {item: math.inf} if item else {}
            case _:
                return {}

    def _blocked_products(self, node: Node) -> tuple[str, ...]:
        """Products with no route out, decided on the topology alone.

        A machine whose output cannot leave stops entirely -- in game its output
        buffer fills and it shuts down. Buffers and outputs, including a deliberate
        discard, all count as valid routes.
        """
        if not isinstance(node, MachineNode):
            return ()
        routed = {edge.item_class for edge in self.out_edges[node.id]}
        products = self.game_data.recipe(node.recipe_class).product_rates()
        return tuple(item for item in sorted(products) if item not in routed)

    # -------------------------------------------------------------- iteration

    def run(self) -> FactoryReport:
        """Iterate to the fixed point, then assemble the report."""
        # Computed for the diagnostics and to keep the decomposition honest even
        # though the iteration itself is global: a cycle-free graph converges in as
        # many rounds as it has layers.
        components = condensation_order(self.graph)
        logger.debug(
            "%d noeud(s), %d composante(s) dont %d cyclique(s)",
            len(self.graph.nodes),
            len(components),
            sum(1 for component in components if len(component) > 1),
        )

        for iteration in range(1, constants.MAX_ITERATIONS + 1):
            self.iterations = iteration
            residual = self._step(damped=iteration > DAMPING_AFTER)
            if residual < constants.CONVERGENCE_TOLERANCE:
                self.converged = True
                break
        else:
            logger.warning(
                "point fixe non convergent apres %d iterations", constants.MAX_ITERATIONS
            )

        return self._report()

    def _step(self, *, damped: bool) -> float:
        """One Jacobi sweep. Returns the largest ratio change of the round."""
        self._refresh_storage_supply()
        supplies, caps = self._offers()
        self.flows = self._allocate_all(supplies, caps)
        self._accumulate_flows()
        return self._update_ratios(damped=damped)

    def _refresh_storage_supply(self) -> None:
        """A buffer offers what downstream asks for; it can draw on its stock.

        For a downstream node that absorbs without limit -- another buffer, an output
        -- there is no figure to match, so the buffer passes on its own intake and
        nothing more. It never invents material for a sink.

        When buffers are forbidden from supplying, the buffer also becomes *required*
        to receive what it hands out -- ``nominal_in`` stops being infinite and
        matches the offer. Its satisfaction ratio then throttles the offer down to
        its real intake, through the same mechanism that starves a machine, and the
        iteration descends from the optimistic start instead of having to guess a
        first value.
        """
        for state in self._sorted_states():
            if not isinstance(state.node, StorageNode):
                continue
            item = storage_item(state.node, self.graph)
            if item is None:
                state.nominal_out = {}
                continue
            asked = 0.0
            for edge in self.out_edges[state.node.id]:
                downstream = self.states[edge.target]
                if downstream.absorbs_without_limit:
                    asked += state.inflow.get(item, 0.0)
                else:
                    asked += downstream.nominal_in.get(item, 0.0) * downstream.ratio_excluding(item)
            state.nominal_out = {item: asked}
            state.ratio_out = {item: 1.0}
            if not self.options.buffers_supply:
                # Absorption stays unlimited (``absorbs_without_limit`` bypasses this
                # figure on the intake side); what it constrains is the offer.
                state.nominal_in = {item: asked}

    def _offers(self) -> tuple[dict[str, dict[str, float]], dict[str, dict[str, float]]]:
        """Per item: what each producer can send, and what each consumer can take."""
        supplies: dict[str, dict[str, float]] = {}
        caps: dict[str, dict[str, float]] = {}
        for state in self._sorted_states():
            offered = state.input_ratio()
            for item, nominal in sorted(state.nominal_out.items()):
                supplies.setdefault(item, {})[state.node.id] = nominal * offered
            for item, nominal in sorted(state.nominal_in.items()):
                if state.absorbs_without_limit:
                    caps.setdefault(item, {})[state.node.id] = math.inf
                else:
                    caps.setdefault(item, {})[state.node.id] = nominal * state.ratio_excluding(item)
        return supplies, caps

    def _allocate_all(
        self,
        supplies: Mapping[str, Mapping[str, float]],
        caps: Mapping[str, Mapping[str, float]],
    ) -> dict[str, float]:
        flows = {edge.id: 0.0 for edge in self.graph.edges}
        by_item: dict[str, list[Edge]] = {}
        for edge in self.graph.sorted_edges():
            by_item.setdefault(edge.item_class, []).append(edge)

        self.leftovers = {}
        for item in sorted(by_item):
            item_supplies = supplies.get(item, {})
            item_flows = allocate(item_supplies, caps.get(item, {}), by_item[item], self.limits)
            flows.update(item_flows)
            sent: dict[str, float] = {}
            for edge in by_item[item]:
                sent[edge.source] = sent.get(edge.source, 0.0) + item_flows[edge.id]
            self.leftovers[item] = {
                producer: max(available - sent.get(producer, 0.0), 0.0)
                for producer, available in item_supplies.items()
            }
        return flows

    def _spare_upstream(self, node_id: str) -> dict[str, float]:
        """Material that this node's suppliers still had on offer and it did not take."""
        spare: dict[str, float] = {}
        for edge in self.in_edges[node_id]:
            available = self.leftovers.get(edge.item_class, {}).get(edge.source, 0.0)
            spare[edge.item_class] = spare.get(edge.item_class, 0.0) + available
        return spare

    def _accumulate_flows(self) -> None:
        for state in self.states.values():
            state.inflow = {}
            state.outflow = {}
        for edge in self.graph.sorted_edges():
            rate = self.flows[edge.id]
            source = self.states[edge.source]
            target = self.states[edge.target]
            source.outflow[edge.item_class] = source.outflow.get(edge.item_class, 0.0) + rate
            target.inflow[edge.item_class] = target.inflow.get(edge.item_class, 0.0) + rate

    def _update_ratios(self, *, damped: bool) -> float:
        """Recompute every satisfaction and absorption ratio; return the residual."""
        residual = 0.0
        for state in self._sorted_states():
            for item, nominal in sorted(state.nominal_in.items()):
                if not math.isfinite(nominal) or nominal <= FLOW_EPSILON:
                    fresh = 1.0  # nothing required: no constraint
                else:
                    fresh = min(1.0, state.inflow.get(item, 0.0) / nominal)
                residual = max(residual, self._blend(state.ratio_in, item, fresh, damped=damped))
            for item, nominal in sorted(state.nominal_out.items()):
                if nominal <= FLOW_EPSILON:
                    fresh = 1.0
                else:
                    fresh = min(1.0, state.outflow.get(item, 0.0) / nominal)
                residual = max(residual, self._blend(state.ratio_out, item, fresh, damped=damped))
        return residual

    @staticmethod
    def _blend(ratios: dict[str, float], item: str, fresh: float, *, damped: bool) -> float:
        previous = ratios.get(item, 1.0)
        updated = previous + DAMPING_FACTOR * (fresh - previous) if damped else fresh
        ratios[item] = updated
        return abs(updated - previous)

    def _sorted_states(self) -> list[_NodeState]:
        return [self.states[node_id] for node_id in sorted(self.states)]

    # ----------------------------------------------------------------- report

    def _report(self) -> FactoryReport:
        """The bare figures. Diagnostics are attached by :func:`solve`, which alone
        knows the companion run the line findings are phrased against."""
        nodes = tuple(self._node_solution(state) for state in self._sorted_states())
        edges = tuple(
            EdgeSolution(
                edge_id=edge.id,
                source=edge.source,
                target=edge.target,
                item_class=edge.item_class,
                transport_class=edge.transport_class,
                rate_per_minute=round(self.flows[edge.id], 9),
                capacity_per_minute=self._capacity(edge),
            )
            for edge in self.graph.sorted_edges()
        )
        buffers = tuple(
            self._buffer_solution(state)
            for state in self._sorted_states()
            if isinstance(state.node, StorageNode)
        )
        return FactoryReport(
            converged=self.converged,
            iterations=self.iterations,
            nodes=nodes,
            edges=edges,
            buffers=buffers,
            raw_solids=self._raw_inputs(fluids=False),
            raw_fluids=self._raw_inputs(fluids=True),
            byproducts=self._byproducts(nodes),
            power_total_mw=round(sum(node.power_mw for node in nodes), 9),
            power_by_building=self._power_by_building(nodes),
            final_outputs=self._outputs(discarded=False),
            discarded_outputs=self._outputs(discarded=True),
            shopping_list=self._shopping_list(nodes),
        )

    def _capacity(self, edge: Edge) -> float:
        try:
            return self.game_data.transport_capacity(edge.transport_class)
        except LookupError:
            return 0.0  # diagnosed by validation; not the solver's job to fail here

    def _node_solution(self, state: _NodeState) -> NodeSolution:
        node = state.node
        spare = self._spare_upstream(node.id)
        ratio = state.ratio()
        machine_count = getattr(node, "machine_count", None)
        if machine_count is None:
            machine_count = getattr(node, "count", None)
        building = self._building_of(node)
        power = 0.0
        if building is not None and machine_count is not None:
            power = self.game_data.building(building).power_mw * machine_count
        return NodeSolution(
            node_id=node.id,
            kind=node.kind,
            label=node.label or self._default_label(node),
            ratio=ratio,
            limiting=state.limiting_factor(spare),
            inputs=_clean(state.inflow),
            outputs=_clean(state.outflow),
            blocked_products=state.blocked_products,
            starved_items=state.starved_items(spare),
            building_class=building,
            machine_count=machine_count,
            useful_machine_count=None if machine_count is None else machine_count * ratio,
            power_mw=power,
        )

    def _building_of(self, node: Node) -> str | None:
        match node:
            case MachineNode():
                return machine_building(node, self.game_data)
            case ResourceNode() | WaterExtractorNode():
                return node.extractor_class
            case StorageNode():
                return node.storage_class
            case _:
                return None

    def _default_label(self, node: Node) -> str:
        """A readable French name, taken from the game labels."""
        match node:
            case MachineNode():
                return self.game_data.recipe(node.recipe_class).display_name_fr
            case ResourceNode() | ExternalSourceNode() | OutputNode():
                return self.game_data.item(node.item_class).display_name_fr
            case WaterExtractorNode():
                return self.game_data.building(node.extractor_class).display_name_fr
            case StorageNode():
                return self.game_data.building(node.storage_class).display_name_fr

    def _buffer_solution(self, state: _NodeState) -> BufferSolution:
        node = state.node
        assert isinstance(node, StorageNode)
        item_class = storage_item(node, self.graph)
        inflow = sum(state.inflow.values())
        outflow = sum(state.outflow.values())
        net = inflow - outflow
        capacity = 0.0
        if item_class is not None:
            capacity = self.game_data.storage(node.storage_class).capacity_for(
                self.game_data.item(item_class)
            )

        state_name = BufferState.BALANCED
        minutes_to_full: float | None = None
        minutes_to_empty: float | None = None
        if net > FLOW_EPSILON:
            state_name = BufferState.FILLING
            remaining = max(capacity - node.initial_content, 0.0)
            minutes_to_full = remaining / net
        elif net < -FLOW_EPSILON:
            state_name = BufferState.DRAINING
            minutes_to_empty = node.initial_content / abs(net)

        return BufferSolution(
            node_id=node.id,
            item_class=item_class,
            inflow=inflow,
            outflow=outflow,
            capacity=capacity,
            initial_content=node.initial_content,
            state=state_name,
            minutes_to_full=minutes_to_full,
            minutes_to_empty=minutes_to_empty,
        )

    def _raw_inputs(self, *, fluids: bool) -> dict[str, float]:
        """What actually enters the factory, split by form."""
        totals: dict[str, float] = {}
        for state in self._sorted_states():
            if not isinstance(state.node, ResourceNode | WaterExtractorNode | ExternalSourceNode):
                continue
            for item_class, rate in state.outflow.items():
                if self.game_data.item(item_class).form.is_fluid is fluids and rate > FLOW_EPSILON:
                    totals[item_class] = totals.get(item_class, 0.0) + rate
        return _clean(totals)

    def _byproducts(self, nodes: Sequence[NodeSolution]) -> tuple[ByproductBalance, ...]:
        """Balance of every secondary product: produced, recycled, stored, exported, flared."""
        secondary: set[str] = set()
        for node in self.graph.sorted_nodes():
            if not isinstance(node, MachineNode):
                continue
            recipe = self.game_data.recipe(node.recipe_class)
            if recipe.product_count > 1:
                # The first slot is the recipe's headline product; the rest are byproducts.
                secondary.update(slot.item_class for slot in recipe.products[1:])
        if not secondary:
            return ()

        produced: dict[str, float] = dict.fromkeys(secondary, 0.0)
        for solution in nodes:
            if solution.kind is not NodeKind.MACHINE:
                continue
            for item_class, rate in solution.outputs.items():
                if item_class in secondary:
                    produced[item_class] += rate

        recycled: dict[str, float] = dict.fromkeys(secondary, 0.0)
        stored: dict[str, float] = dict.fromkeys(secondary, 0.0)
        exported: dict[str, float] = dict.fromkeys(secondary, 0.0)
        discarded: dict[str, float] = dict.fromkeys(secondary, 0.0)
        for edge in self.graph.sorted_edges():
            if edge.item_class not in secondary:
                continue
            rate = self.flows[edge.id]
            target = self.states[edge.target].node
            match target:
                case MachineNode():
                    recycled[edge.item_class] += rate
                case StorageNode():
                    stored[edge.item_class] += rate
                case OutputNode():
                    bucket = discarded if target.is_sink else exported
                    bucket[edge.item_class] += rate
                case _:
                    pass

        return tuple(
            ByproductBalance(
                item_class=item_class,
                produced=round(produced[item_class], 9),
                recycled=round(recycled[item_class], 9),
                stored=round(stored[item_class], 9),
                exported=round(exported[item_class], 9),
                discarded=round(discarded[item_class], 9),
            )
            for item_class in sorted(secondary)
        )

    def _power_by_building(self, nodes: Iterable[NodeSolution]) -> dict[str, float]:
        """Draw per building class. Idle machines still count: they are still built."""
        totals: dict[str, float] = {}
        for solution in nodes:
            if solution.building_class is None or solution.power_mw <= 0:
                continue
            totals[solution.building_class] = (
                totals.get(solution.building_class, 0.0) + solution.power_mw
            )
        return _clean(totals)

    def _outputs(self, *, discarded: bool) -> dict[str, float]:
        totals: dict[str, float] = {}
        for state in self._sorted_states():
            node = state.node
            if not isinstance(node, OutputNode) or node.is_sink is not discarded:
                continue
            for item_class, rate in state.inflow.items():
                totals[item_class] = totals.get(item_class, 0.0) + rate
        return _clean(totals)

    def _shopping_list(self, nodes: Iterable[NodeSolution]) -> ShoppingList:
        buildings: dict[str, int] = {}
        for solution in nodes:
            if solution.building_class is None:
                continue
            count = math.ceil(solution.machine_count or 1.0)
            buildings[solution.building_class] = buildings.get(solution.building_class, 0) + count

        belts: dict[int, int] = {}
        pipes: dict[int, int] = {}
        for edge in self.graph.sorted_edges():
            if edge.transport_class in self.game_data.belts:
                tier = self.game_data.belts[edge.transport_class].tier
                belts[tier] = belts.get(tier, 0) + 1
            elif edge.transport_class in self.game_data.pipes:
                tier = self.game_data.pipes[edge.transport_class].tier
                pipes[tier] = pipes.get(tier, 0) + 1
        return ShoppingList(
            buildings=dict(sorted(buildings.items())),
            belts_by_tier=dict(sorted(belts.items())),
            pipes_by_tier=dict(sorted(pipes.items())),
            attachments=self._attachments(),
        )

    def _attachments(self) -> dict[str, int]:
        """Splitters, mergers and junctions implied by lines that share a node.

        Two lines leaving one node with the same item means the player has to split
        that output, and two arriving means a merge. The count follows from the
        number of lines alone -- a splitter serves three, so each unit adds two --
        which is as far as a throughput model can honestly go: how the units are
        physically chained is geometry, and geometry is out of scope.
        """
        totals: dict[str, int] = {}
        for node in self.graph.sorted_nodes():
            for role, edges in (
                (AttachmentRole.SPLIT, self.out_edges[node.id]),
                (AttachmentRole.MERGE, self.in_edges[node.id]),
            ):
                lines: dict[str, int] = {}
                for edge in edges:
                    lines[edge.item_class] = lines.get(edge.item_class, 0) + 1
                for item_class, count in sorted(lines.items()):
                    attachment = self.game_data.attachment_for(
                        self.game_data.item(item_class).form, role
                    )
                    if attachment is None:
                        continue
                    units = attachment.units_for(count)
                    if units:
                        totals[attachment.class_name] = totals.get(attachment.class_name, 0) + units
        return dict(sorted(totals.items()))


def allocate(
    supplies: Mapping[str, float],
    caps: Mapping[str, float],
    edges: Sequence[Edge],
    limits: Mapping[str, float] | None = None,
) -> dict[str, float]:
    """Share each producer's output between the consumers it is wired to.

    The rule is **max-min fairness**, which is what the game's own splitter does: it
    hands each output an equal turn and, when one of them is full, shares what is
    left equally between the others. In order:

    1. every producer splits what it has **into equal shares**, one per line -- not
       in proportion to what each consumer asks for;
    2. a consumer that cannot take its whole share, or a line too small to carry it,
       leaves the remainder on the table and the next round hands it to the others;
    3. consumers that absorb without limit (buffers, outputs, flares) are served
       **last**, with whatever nobody else could take. Treating their appetite as
       infinite in step 1 would let a flare starve a machine standing next to it.

    ``limits`` caps each line by its transport tier. Deterministic: producers,
    consumers and edges are all walked in sorted order.
    """
    flows = {edge.id: 0.0 for edge in edges}
    if not edges:
        return flows

    ordered = sorted(edges, key=lambda edge: edge.id)
    remaining_supply = {node_id: max(supplies.get(node_id, 0.0), 0.0) for node_id in supplies}
    remaining_line = {
        edge.id: math.inf if limits is None else limits.get(edge.id, math.inf) for edge in ordered
    }
    finite = {node: cap for node, cap in caps.items() if math.isfinite(cap)}
    unlimited = sorted(node for node, cap in caps.items() if not math.isfinite(cap))

    _water_fill(flows, ordered, remaining_supply, dict(finite), remaining_line)
    if unlimited:
        # Second pass: the leftovers go to the unlimited absorbers.
        leftovers = {
            node_id: value for node_id, value in remaining_supply.items() if value > FLOW_EPSILON
        }
        sink_edges = [edge for edge in ordered if edge.target in set(unlimited)]
        _water_fill(
            flows,
            sink_edges,
            leftovers,
            dict.fromkeys(unlimited, math.inf),
            remaining_line,
            unlimited_caps=True,
        )
        for node_id, value in leftovers.items():
            remaining_supply[node_id] = value
    return flows


def _water_fill(
    flows: dict[str, float],
    edges: Sequence[Edge],
    remaining_supply: dict[str, float],
    remaining_cap: dict[str, float],
    remaining_line: dict[str, float],
    *,
    unlimited_caps: bool = False,
) -> None:
    """Move as much as possible from supplies to caps, redistributing what bounces."""
    for _ in range(MAX_ALLOCATION_ROUNDS):
        active = [
            edge
            for edge in edges
            if remaining_supply.get(edge.source, 0.0) > FLOW_EPSILON
            and (unlimited_caps or remaining_cap.get(edge.target, 0.0) > FLOW_EPSILON)
            and remaining_line[edge.id] > FLOW_EPSILON
        ]
        if not active:
            return

        # Step 1: each producer offers an equal share per line, never more than the
        # line can still carry. What a share does not use comes back next round.
        offers: dict[str, float] = {}
        by_source: dict[str, list[Edge]] = {}
        for edge in active:
            by_source.setdefault(edge.source, []).append(edge)
        for source, group in sorted(by_source.items()):
            share = remaining_supply[source] / len(group)
            for edge in group:
                offers[edge.id] = min(share, remaining_line[edge.id])

        # Step 2: each consumer accepts what it can, scaling every offer alike.
        moved = 0.0
        by_target: dict[str, list[Edge]] = {}
        for edge in active:
            if offers.get(edge.id, 0.0) > FLOW_EPSILON:
                by_target.setdefault(edge.target, []).append(edge)
        for target, group in sorted(by_target.items()):
            offered = sum(offers[edge.id] for edge in group)
            if offered <= FLOW_EPSILON:
                continue
            capacity = remaining_cap.get(target, 0.0)
            factor = 1.0 if unlimited_caps else min(1.0, capacity / offered)
            for edge in group:
                accepted = offers[edge.id] * factor
                if accepted <= 0:
                    continue
                flows[edge.id] += accepted
                remaining_supply[edge.source] -= accepted
                remaining_line[edge.id] -= accepted
                if not unlimited_caps:
                    remaining_cap[target] -= accepted
                moved += accepted
        if moved <= FLOW_EPSILON:
            return


def _clean(values: Mapping[str, float]) -> dict[str, float]:
    """Drop float noise and sort, so that two runs compare equal."""
    return {
        key: round(value, 9) for key, value in sorted(values.items()) if abs(value) > FLOW_EPSILON
    }


def solve(graph: FactoryGraph, game_data: GameData) -> FactoryReport:
    """Compute the steady state of ``graph`` and return the complete report.

    When the answer only holds because a buffer is being drained, the report carries
    a second one under :attr:`FactoryReport.sustained`: the same factory with the
    buffers supplying nothing, which is the regime that survives the stock.
    """
    report = _solve_pair(graph, game_data, buffers_supply=True)
    if report.is_sustainable:
        return report
    combined = report.with_sustained(_solve_pair(graph, game_data, buffers_supply=False))
    # Diagnosed once more now that the established regime is known: the finding that
    # says "these figures are not sustainable" quotes what remains without the stock.
    return combined.with_diagnostics(tuple(validation.diagnose(graph, game_data, combined)))


def _solve_pair(graph: FactoryGraph, game_data: GameData, *, buffers_supply: bool) -> FactoryReport:
    """One diagnosed answer, plus the uncapped companion its line findings need."""
    uncapped = _Solver(
        graph,
        game_data,
        SolveOptions(enforce_line_capacity=False, buffers_supply=buffers_supply),
    ).run()
    report = _with_desired_rates(
        _Solver(graph, game_data, SolveOptions(buffers_supply=buffers_supply)).run(),
        {solution.edge_id: solution.rate_per_minute for solution in uncapped.edges},
    )
    return report.with_diagnostics(tuple(validation.diagnose(graph, game_data, report)))


def _with_desired_rates(report: FactoryReport, desired: Mapping[str, float]) -> FactoryReport:
    """Fold the uncapped companion into the report.

    Each line learns what it would carry if it were big enough, and each node learns
    which of its items a line is holding back. That last point cannot be decided by
    the capped run alone: a line running at exactly its ceiling because production
    happens to match it is not a bottleneck, and only the uncapped rate tells the
    two apart.
    """
    edges = tuple(
        solution.model_copy(update={"desired_rate_per_minute": desired.get(solution.edge_id)})
        for solution in report.edges
    )
    starving = {edge.target: set[str]() for edge in edges} | {
        edge.source: set[str]() for edge in edges
    }
    for edge in edges:
        if edge.is_saturated:
            starving[edge.target].add(edge.item_class)
            starving[edge.source].add(edge.item_class)
    nodes = tuple(
        _with_line_limits(solution, sorted(starving.get(solution.node_id, set())))
        for solution in report.nodes
    )
    return report.model_copy(update={"edges": edges, "nodes": nodes})


def _with_line_limits(solution: NodeSolution, saturated: Sequence[str]) -> NodeSolution:
    """Attribute a node's shortfall to its lines when that is what is holding it back.

    A genuine shortage wins: if nothing upstream had any of the item left over, the
    node is starved and the line's size is beside the point.
    """
    if not saturated or solution.starved_items:
        return solution
    if solution.limiting not in (LimitingFactor.INPUTS, LimitingFactor.OUTPUTS):
        return solution
    return solution.model_copy(
        update={"line_limited_items": tuple(saturated), "limiting": LimitingFactor.LINE}
    )


def suggest_machine_count(graph: FactoryGraph, game_data: GameData, node_id: str) -> float:
    """How many machines this node's available inputs would feed exactly.

    A local answer for one node, not a global optimisation: the rest of the graph is
    left as it is. The node is probed with a very large machine count so that it
    pulls everything upstream can spare, and the count is then read back from what
    actually arrives.
    """
    node = graph.node(node_id)
    if not isinstance(node, MachineNode):
        msg = f"le noeud {node_id} n'est pas une machine"
        raise TypeError(msg)
    rates = game_data.recipe(node.recipe_class).ingredient_rates()
    if not rates:
        return node.machine_count

    probe = graph.model_copy(deep=True)
    probed = probe.node(node_id)
    assert isinstance(probed, MachineNode)
    probed.machine_count = max(node.machine_count, 1.0) * PROBE_FACTOR

    solution = solve(probe, game_data).node(node_id)
    return min(
        solution.inputs.get(item, 0.0) / rate for item, rate in sorted(rates.items()) if rate > 0
    )
