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
    OVERFLOW_BRANCH,
    PASS_THROUGH_KINDS,
    Edge,
    ExternalSourceNode,
    FactoryGraph,
    GeneratorNode,
    MachineNode,
    MergerNode,
    Node,
    NodeKind,
    OutputNode,
    ResourceNode,
    ResourceWellNode,
    SplitterNode,
    StorageNode,
    WaterExtractorNode,
    attachment_building,
    branch_carries,
    branch_filter,
    condensation_order,
    draw_of,
    extra_buildings,
    generator_input_rates,
    machine_building,
    pass_through_item,
    storage_item,
    unit_count,
)
from satisplanner.core.models import AttachmentRole, GameData, SplitterMode
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
    # A splitter or a merger: it keeps nothing, and its figures are read as
    # "what went through" rather than "how hard it worked".
    passes_through: bool = False

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
        # Everything below is settled before the first iteration and never changes
        # again during this solve. Recomputing any of it per round -- sorting the
        # edges, grouping them by item, asking the graph which lines reach a buffer
        # -- was pure repetition: the topology cannot move while the fixed point is
        # being found, because the fixed point is about flows and not about shape.
        self._sorted_nodes = graph.sorted_nodes()
        self._sorted_edges = graph.sorted_edges()
        self.out_edges: dict[str, list[Edge]] = {node.id: [] for node in graph.nodes}
        self.in_edges: dict[str, list[Edge]] = {node.id: [] for node in graph.nodes}
        for edge in self._sorted_edges:
            self.out_edges[edge.source].append(edge)
            self.in_edges[edge.target].append(edge)
        self._edges_by_item: dict[str, list[Edge]] = {}
        for edge in self._sorted_edges:
            self._edges_by_item.setdefault(edge.item_class, []).append(edge)
        self._items_in_order = sorted(self._edges_by_item)
        self._storage_items: dict[str, str | None] = {
            node.id: storage_item(node, graph)
            for node in self._sorted_nodes
            if isinstance(node, StorageNode)
        }
        self._line_items: dict[str, str | None] = {
            node.id: pass_through_item(node, graph)
            for node in self._sorted_nodes
            if isinstance(node, SplitterNode | MergerNode)
        }
        # Branches a filter has closed, and branches that only take refusals.
        # Both are settled by the document alone, so both are settled once.
        self._closed = frozenset(
            edge.id
            for edge in self._sorted_edges
            if not branch_carries(graph.node(edge.source), edge.target, edge.item_class)
        )
        self._overflow = frozenset(
            edge.id
            for edge in self._sorted_edges
            if edge.id not in self._closed
            and branch_filter(graph.node(edge.source), edge.target) == OVERFLOW_BRANCH
        )
        self._drained = self._draining_ports()
        self._deferred = self._conduits_to_sinks()
        self.limits: dict[str, float] = {
            edge.id: self._capacity(edge) if self.options.enforce_line_capacity else math.inf
            for edge in graph.edges
        }
        self.converged = False
        self.iterations = 0
        # True as soon as one line has refused to carry what was offered to it. It
        # is what says whether the uncapped companion run would answer anything
        # different from this one -- see :func:`_solve_pair`.
        self.line_bound = False
        # Per item, what each producer still had on offer after the last allocation.
        self.leftovers: dict[str, dict[str, float]] = {}
        self._states_in_order: list[_NodeState] = []
        self._prepare()

    # ------------------------------------------------------------------ setup

    def _prepare(self) -> None:
        for node in self._sorted_nodes:
            state = _NodeState(node=node)
            state.nominal_out = self._nominal_out(node)
            state.nominal_in = self._nominal_in(node)
            state.absorbs_without_limit = isinstance(node, OutputNode | StorageNode)
            state.passes_through = isinstance(node, SplitterNode | MergerNode)
            state.blocked_products = self._blocked_products(node)
            self.states[node.id] = state
        # ``_sorted_nodes`` is already in identifier order, so this list is too.
        self._states_in_order = [self.states[node.id] for node in self._sorted_nodes]
        self._size_pass_throughs()
        for state in self._states_in_order:
            # Optimistic start: every constraint assumed satisfied. A splitter needs
            # this more than anything else in the graph, and both halves of it: it
            # keeps nothing, so its intake is capped by what it pushed last round
            # and its offer is what it took last round, and there is no nameplate to
            # pin either. Start one of the two at nothing and the pair either
            # oscillates with a period of two -- the offer chasing the refusal --
            # or settles on "a tree of splitters carries zero", which is as
            # self-consistent as a stopped recycling loop and just as wrong.
            state.ratio_in = dict.fromkeys(state.nominal_in, 1.0)
            state.ratio_out = dict.fromkeys(state.nominal_out, 1.0)
        self._seed_storage_intake()

    def _seed_storage_intake(self) -> None:
        """Let a buffer start the round believing its suppliers will deliver.

        A buffer with an unlimited absorber downstream has no figure to match, so it
        offers its own intake -- and its intake, before anything has been allocated,
        is nothing. Every other node in the graph starts optimistic and descends;
        this one started at zero and climbed, which was harmless for as long as
        nothing downstream reacted to it. A splitter reacts to it: it is a conduit,
        so what it may take in is what it managed to push out, and a supply that
        climbs from below while that is being decided sets the two chasing each
        other with a period of two. Two rounds later the buffer is offering plenty
        and the splitter is refusing it because of a shortage that ended.

        Seeded with what the suppliers say they make, which is a nameplate and not a
        guess, and which the very first allocation replaces with the truth.
        """
        for state in self._states_in_order:
            if not isinstance(state.node, StorageNode):
                continue
            item = self._storage_items[state.node.id]
            if item is None:
                continue
            arriving = sum(
                rate
                for edge in self.in_edges[state.node.id]
                if edge.item_class == item
                for rate in (self.states[edge.source].nominal_out.get(item, 0.0),)
                if math.isfinite(rate)
            )
            state.inflow = {item: arriving}

    def _size_pass_throughs(self) -> None:
        """Give every splitter and merger a nameplate, and nothing else changes.

        A splitter is then an ordinary node to the iteration, which is the whole
        design: what it offers is its rating times its intake ratio, and what it can
        absorb is its rating times its output ratio. Write ``i`` and ``o`` for its
        intake and its outflow and the fixed point says ``i <= o`` and ``o <= i``, so
        **conservation is a consequence and not a rule bolted on**: a splitter
        cannot leak, and back pressure crosses it because its own intake is capped
        by what it managed to push out last round.

        That leaves the rating itself. It has to be a figure no flow can reach --
        the game's splitter moves more than the fastest belt, so it is never the
        bottleneck -- and it has to *be* a figure, because those two ratios are
        quotients and infinity is not one. Two bounds say so, and the smaller wins:

        * everything the factory makes of the item plus everything it asks for,
          since nothing arrives here that was neither produced nor wanted;
        * what its own incoming lines can carry, read from the ceilings **this
          solve** is enforcing -- so the uncapped companion run keeps the first
          bound alone, exactly as it ignores every other transport limit.

        The second is what keeps the first round from flooding: a rating of ten
        thousand offered down a belt that carries seven hundred would report a
        transport ceiling as binding, and buy a companion run to be told it was not.
        """
        ceiling: dict[str, float] = {}
        for state in self._states_in_order:
            if state.passes_through:
                continue
            for rates in (state.nominal_out, state.nominal_in):
                for item, rate in rates.items():
                    if math.isfinite(rate):
                        ceiling[item] = ceiling.get(item, 0.0) + rate
        for state in self._states_in_order:
            if not state.passes_through:
                continue
            carried = self._line_items[state.node.id]
            if carried is None:
                continue
            arriving = sum(
                self.limits[edge.id]
                for edge in self.in_edges[state.node.id]
                if edge.item_class == carried
            )
            rating = min(ceiling.get(carried, 0.0), arriving)
            state.nominal_out = {carried: rating}
            state.nominal_in = {carried: rating}

    def _draining_ports(self) -> set[tuple[str, str]]:
        """``(node, item)`` pairs the item can still leave by.

        Without splitters this was "is there an outgoing line for it", and the answer
        is unchanged wherever there are none. With them the question grows a step: a
        line into a splitter that leads nowhere is no more of a route out than no
        line at all, and a machine behind one is as blocked as a machine behind
        nothing. Solved backwards from whatever really absorbs, which also settles
        a ring of splitters closed on itself -- it drains nothing, and says so.
        """
        drained: set[tuple[str, str]] = set()
        settled = False
        while not settled:
            settled = True
            for edge in self._sorted_edges:
                port = (edge.source, edge.item_class)
                if port in drained or edge.id in self._closed:
                    continue
                target = self.graph.node(edge.target)
                if not isinstance(target, SplitterNode | MergerNode) or (
                    edge.target,
                    edge.item_class,
                ) in drained:
                    drained.add(port)
                    settled = False
        return drained

    def _nominal_out(self, node: Node) -> dict[str, float]:
        """Production at full speed, per item and per minute.

        "Full speed" means the node's own clock: throughput is strictly proportional
        to it, so a Mk.3 miner on a pure node at 250 % puts out 480 x 2.5 = 1200 a
        minute. Only the power bill departs from proportionality, and that is handled
        where power is computed.
        """
        match node:
            case ResourceNode():
                extractor = self.game_data.extractor(node.extractor_class)
                rate = extractor.rate(node.purity) * node.count * node.clock_speed
                return {node.item_class: rate}
            case ResourceWellNode():
                # One well, several satellites, and each satellite its own purity:
                # the output is a sum over the tally, not a rate times a count.
                extractor = self.game_data.extractor(node.extractor_class)
                rate = sum(
                    extractor.rate(purity) * count
                    for purity, count in sorted(node.satellites.items())
                )
                return {node.item_class: rate * node.clock_speed}
            case WaterExtractorNode():
                extractor = self.game_data.extractor(node.extractor_class)
                item = extractor.item_class
                rate = extractor.rate_per_minute * node.count * node.clock_speed
                return {item: rate} if item else {}
            case ExternalSourceNode():
                return {node.item_class: node.rate_per_minute}
            case MachineNode():
                rates = self.game_data.recipe(node.recipe_class).product_rates()
                scale = node.machine_count * node.clock_speed
                return {item: rate * scale for item, rate in sorted(rates.items())}
            case GeneratorNode():
                # A generator puts out power, which no line carries and which the
                # solver therefore never allocates. Its production is accounted for
                # in the report, not in the flows.
                return {}
            case StorageNode():
                # Filled in at every iteration: a buffer hands downstream what it
                # asks for, drawing on its stock if its own intake is not enough.
                item = self._storage_items[node.id]
                return {item: 0.0} if item else {}
            case OutputNode():
                return {}
            case SplitterNode() | MergerNode():
                # Rated by :meth:`_size_pass_throughs`, which needs every other
                # node's nameplate to be known first.
                return {}

    def _nominal_in(self, node: Node) -> dict[str, float]:
        """Consumption at full speed, per item and per minute."""
        match node:
            case MachineNode():
                rates = self.game_data.recipe(node.recipe_class).ingredient_rates()
                scale = node.machine_count * node.clock_speed
                return {item: rate * scale for item, rate in sorted(rates.items())}
            case GeneratorNode():
                # Fuel and make-up water, both ordinary inputs on ordinary lines.
                return generator_input_rates(node, self.game_data)
            case StorageNode() | OutputNode():
                item = (
                    self._storage_items[node.id]
                    if isinstance(node, StorageNode)
                    else node.item_class
                )
                # Unlimited absorption; the figure is only a placeholder.
                return {item: math.inf} if item else {}
            case _:
                return {}

    def _conduits_to_sinks(self) -> frozenset[str]:
        """Splitters and mergers with nothing but unlimited absorbers behind them.

        These are served last, with the sinks, and the reason is that they are not
        consumers at all. A machine's ceiling says how much of the material is
        *wanted*, which is why a machine is served before a flare; a splitter's says
        only how much fits through it. Left in the first round it would take a whole
        node's output and hand it to two exits, while three more exits drawn beside
        it -- one line each, no splitter needed -- got nothing.

        Falsified rather than proved, so that a ring of splitters closed on itself
        stays in the set: it absorbs nothing either way, and is reported blocked.
        """
        conduits = {
            node.id for node in self._sorted_nodes if isinstance(node, SplitterNode | MergerNode)
        }
        settled = False
        while not settled:
            settled = True
            for node_id in sorted(conduits):
                for edge in self.out_edges[node_id]:
                    if edge.id in self._closed:
                        continue
                    target = self.graph.node(edge.target)
                    if isinstance(target, OutputNode | StorageNode) or edge.target in conduits:
                        continue
                    conduits.discard(node_id)
                    settled = False
                    break
        return frozenset(conduits)

    def _blocked_products(self, node: Node) -> tuple[str, ...]:
        """Products with no route out, decided on the topology alone.

        A machine whose output cannot leave stops entirely -- in game its output
        buffer fills and it shuts down. Buffers and outputs, including a deliberate
        discard, all count as valid routes; a splitter counts only if something is
        hanging off it, which is what :meth:`_draining_ports` works out.

        A splitter with nowhere to send what it carries is blocked in exactly the
        same sense, and saying so is what stops the machine feeding it from being
        reported as merely under back pressure.
        """
        if isinstance(node, SplitterNode | MergerNode):
            item = self._line_items[node.id]
            if item is None or (node.id, item) in self._drained:
                return ()
            return (item,)
        if not isinstance(node, MachineNode):
            return ()
        products = self.game_data.recipe(node.recipe_class).product_rates()
        return tuple(item for item in sorted(products) if (node.id, item) not in self._drained)

    # -------------------------------------------------------------- iteration

    def run(self) -> FactoryReport:
        """Iterate to the fixed point, then assemble the report."""
        self.iterate()
        return self._report()

    def edge_rates(self) -> dict[str, float]:
        """What each line carries, without assembling a report around it.

        The companion run is read for exactly this and nothing else -- no node
        solutions, no shopping list, no power -- so building the rest of a report
        for it was a third of the run spent on figures nobody would ever look at.
        Rounded here the way :meth:`_report` rounds, so the two agree to the digit.
        """
        return {edge.id: round(self.flows[edge.id], 9) for edge in self._sorted_edges}

    def iterate(self) -> None:
        """Descend to the fixed point. Leaves the answer in the solver's state."""
        # The decomposition says nothing the iteration uses -- that is global, and
        # deliberately so -- and exists purely to describe the graph in the log. It
        # is Tarjan over every node and every edge, four times per solve, which on a
        # five-hundred node factory was a fifth of the whole calculation spent
        # writing a sentence nobody was reading. It is now computed only when
        # somebody has actually asked for debug output.
        if logger.isEnabledFor(logging.DEBUG):
            components = condensation_order(self.graph)
            logger.debug(
                "%d nœud(s), %d composante(s) dont %d cyclique(s)",
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
                "point fixe non convergent après %d itérations", constants.MAX_ITERATIONS
            )

    def _step(self, *, damped: bool) -> float:
        """One Jacobi sweep. Returns the largest change of the round."""
        self._refresh_storage_supply()
        supplies, caps = self._offers()
        self.flows = self._allocate_all(supplies, caps)
        self._accumulate_flows()
        # A buffer's offer is part of the state this iteration is solving for, so a
        # round in which it is still moving is not a round that has settled. See
        # :meth:`_storage_offer_drift`.
        return max(self._update_ratios(damped=damped), self._storage_offer_drift())

    def _storage_offer(self, state: _NodeState) -> dict[str, float]:
        """What one buffer puts on the market, from what is known right now.

        A buffer offers what downstream asks for, drawing on its stock if its own
        intake is not enough. For a downstream node that absorbs without limit --
        another buffer, an output, a flare -- there is no figure to match, so the
        buffer passes on its own intake and nothing more. It never invents material
        for a sink.

        That last rule is what makes this quantity part of the fixed point rather
        than a constant: it reads the intake, and the intake is only known once the
        round's flows have been allocated. Hence :meth:`_storage_offer_drift`.
        """
        item = self._storage_items[state.node.id]
        if item is None:
            return {}
        asked = 0.0
        passes_through = False
        for edge in self.out_edges[state.node.id]:
            if edge.item_class != item:
                continue
            demand, reaches_sink = self._reachable_demand(
                edge.target, item, frozenset({state.node.id})
            )
            asked += demand
            passes_through = passes_through or reaches_sink
        if passes_through:
            # The intake counts **once**, however many sinks are hanging off this
            # buffer: two containers side by side share what arrives, they do not
            # each conjure up a copy of it. And it is the greater of the two figures
            # rather than their sum, because a route that absorbs without limit takes
            # what the real consumers left and no more.
            asked = max(asked, state.inflow.get(item, 0.0))
        return {item: asked}

    def _reachable_demand(
        self, node_id: str, item: str, seen: frozenset[str]
    ) -> tuple[float, bool]:
        """What lies downstream of here really wants, and whether a sink is among it.

        A splitter is looked **through**. Its own appetite is the rating
        :meth:`_size_pass_throughs` gave it, which is a ceiling nobody reaches, and a
        buffer that read that as demand would empty itself into a fitting. What is
        behind the fitting is the question, so that is what is asked -- and if a sink
        is behind it, the rule that a buffer never invents material for a sink
        applies exactly as it does when the sink is wired straight to it.

        ``seen`` closes a ring of splitters: it contributes its own demand once and
        does not come round again.
        """
        state = self.states[node_id]
        if state.absorbs_without_limit:
            return 0.0, True
        if not state.passes_through:
            return state.nominal_in.get(item, 0.0) * state.ratio_excluding(item), False
        total = 0.0
        sink = False
        for edge in self.out_edges[node_id]:
            if edge.item_class != item or edge.target in seen or edge.id in self._closed:
                continue
            demand, reaches_sink = self._reachable_demand(edge.target, item, seen | {node_id})
            total += demand
            sink = sink or reaches_sink
        return total, sink

    def _refresh_storage_supply(self) -> None:
        """Adopt each buffer's offer for this round.

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
            offer = self._storage_offer(state)
            state.nominal_out = offer
            if not offer:
                continue
            item, asked = next(iter(offer.items()))
            state.ratio_out = {item: 1.0}
            if not self.options.buffers_supply:
                # Absorption stays unlimited (``absorbs_without_limit`` bypasses this
                # figure on the intake side); what it constrains is the offer.
                state.nominal_in = {item: asked}

    def _storage_offer_drift(self) -> float:
        """How far any buffer's offer has moved away from the one it just used.

        Without this the iteration could stop while a buffer was still climbing, and
        it did. A buffer feeding only unlimited absorbers offers exactly its own
        intake; at the first round that intake is nil, because nothing has been
        allocated yet, so the offer is nil too. Every *ratio* in that factory is
        already at its final value -- the supplier is sending everything it has, the
        container is swallowing everything it is given -- so the residual read zero
        and the answer was frozen at the one figure that had not had its turn yet: a
        buffer taking in 240 a minute and passing on none of it.

        The fix is not to iterate a few more times for luck, nor to guess a first
        offer. It is that the offer is one of the unknowns, and a convergence test
        that cannot see an unknown is not a convergence test. Measured without
        mutating anything: the round is over, this only decides whether there is
        another one.
        """
        drift = 0.0
        for state in self._sorted_states():
            if not isinstance(state.node, StorageNode):
                continue
            fresh = self._storage_offer(state)
            for item, asked in fresh.items():
                drift = max(drift, abs(asked - state.nominal_out.get(item, 0.0)))
        return drift

    def _offers(self) -> tuple[dict[str, dict[str, float]], dict[str, dict[str, float]]]:
        """Per item: what each producer can send, and what each consumer can take."""
        supplies: dict[str, dict[str, float]] = {}
        caps: dict[str, dict[str, float]] = {}
        # The states are walked in identifier order, which is what decides the
        # order of every producer list here. Sorting one node's own items on top
        # of that decided nothing: the allocation walks items in its own sorted
        # order and re-sorts producers and consumers before it adds anything up.
        for state in self._sorted_states():
            offered = state.input_ratio()
            for item, nominal in state.nominal_out.items():
                supplies.setdefault(item, {})[state.node.id] = nominal * offered
            for item, nominal in state.nominal_in.items():
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
        self.leftovers = {}
        for item in self._items_in_order:
            # A branch a filter has closed is left out of the allocation entirely.
            # Nothing goes down it, so it neither takes a share nor holds one back.
            item_edges = [
                edge for edge in self._edges_by_item[item] if edge.id not in self._closed
            ]
            item_supplies = supplies.get(item, {})
            item_flows, bound = allocate(
                item_supplies,
                caps.get(item, {}),
                item_edges,
                self.limits,
                self._deferred,
                self._overflow,
            )
            self.line_bound = self.line_bound or bound
            flows.update(item_flows)
            sent: dict[str, float] = {}
            for edge in item_edges:
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
        # No sort: this loop takes a maximum and writes each ratio into a slot of
        # its own, and neither depends on the order the items come in.
        for state in self._sorted_states():
            for item, nominal in state.nominal_in.items():
                if not math.isfinite(nominal) or nominal <= FLOW_EPSILON:
                    fresh = 1.0  # nothing required: no constraint
                else:
                    fresh = min(1.0, state.inflow.get(item, 0.0) / nominal)
                residual = max(residual, self._blend(state.ratio_in, item, fresh, damped=damped))
            for item, nominal in state.nominal_out.items():
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
        """Every state in identifier order. Settled once: no state is ever added."""
        return self._states_in_order

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
            power_production_mw=round(sum(node.power_produced_mw for node in nodes), 9),
            power_production_by_building=self._power_by_building(nodes, produced=True),
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
        ratio = 1.0 if state.passes_through and not state.is_blocked else state.ratio()
        machine_count = unit_count(node)
        clock = getattr(node, "clock_speed", 1.0)
        building = self._building_of(node)
        extra = extra_buildings(node, self.game_data)
        power = 0.0
        if building is not None and machine_count is not None:
            # Not proportional: the game raises the draw to the building's own
            # exponent, which is why 250 % costs about 3.36 times and not 2.5. And
            # the nameplate itself may come from the recipe rather than from the
            # building -- see :func:`draw_of`.
            power = draw_of(node, building, clock, self.game_data) * machine_count
        # A node that puts down a second building pays for it too. Today that is
        # the well's pressuriser, which is where all of a well's current goes: the
        # satellites are declared at zero, so leaving this out would make a well
        # free and overclocking one free as well.
        power += sum(
            self.game_data.building(class_name).power_at(clock) * units
            for class_name, units in sorted(extra.items())
        )
        shards = 0
        if machine_count is not None and clock > 1.0:
            shards = sum(self.game_data.shards_for(clock, machine_count).values())
        # Production, unlike consumption, follows the operating ratio: a generator
        # short of coal burns less and puts out less, whereas a machine standing
        # idle still draws its share. The asymmetry is the physical one.
        produced = 0.0
        if isinstance(node, GeneratorNode):
            # A generator loaded with a fuel its building does not accept burns
            # nothing and therefore produces nothing. Only a hand-edited or foreign
            # file can reach that state, and it is diagnosed rather than crashed on.
            generator = self.game_data.generators.get(node.generator_class)
            if generator is not None and generator.accepts(node.fuel_class):
                produced = generator.power_mw * node.count * ratio
        # A splitter has no nameplate and cannot be starved: it is a fitting, and the
        # rating :meth:`_size_pass_throughs` gave it is a ceiling nobody reaches, not
        # a rate it was built for. Writing it on the node would be a number pretending
        # to be a capacity, which is the same reason a buffer shows none.
        limiting = (
            (LimitingFactor.BLOCKED if state.is_blocked else LimitingFactor.NONE)
            if state.passes_through
            else state.limiting_factor(spare)
        )
        return NodeSolution(
            node_id=node.id,
            kind=node.kind,
            label=node.label or self._default_label(node),
            ratio=ratio,
            limiting=limiting,
            inputs=_clean(state.inflow),
            outputs=_clean(state.outflow),
            nominal_inputs={} if state.passes_through else _nameplate(state.nominal_in),
            nominal_outputs={} if state.passes_through else _nameplate(state.nominal_out),
            blocked_products=state.blocked_products,
            starved_items=() if state.passes_through else state.starved_items(spare),
            building_class=building,
            extra_buildings=dict(sorted(extra.items())),
            machine_count=machine_count,
            useful_machine_count=None if machine_count is None else machine_count * ratio,
            clock_speed=clock,
            power_shards=shards,
            power_mw=round(power, 9),
            power_produced_mw=round(produced, 9),
        )

    def _building_of(self, node: Node) -> str | None:
        match node:
            case MachineNode():
                return machine_building(node, self.game_data)
            case ResourceNode() | ResourceWellNode() | WaterExtractorNode():
                # For a well this is the satellite. The pressuriser is the other
                # half, and it comes through ``extra_buildings``.
                return node.extractor_class
            case GeneratorNode():
                return node.generator_class
            case StorageNode():
                return node.storage_class
            case SplitterNode() | MergerNode():
                return attachment_building(node, self._line_items[node.id], self.game_data)
            case _:
                return None

    def _default_label(self, node: Node) -> str:
        """A readable French name, taken from the game labels."""
        match node:
            case MachineNode():
                return self.game_data.recipe(node.recipe_class).display_name_fr
            case ResourceNode() | ResourceWellNode() | ExternalSourceNode() | OutputNode():
                return self.game_data.item(node.item_class).display_name_fr
            case WaterExtractorNode():
                return self.game_data.building(node.extractor_class).display_name_fr
            case GeneratorNode():
                return self.game_data.building(node.generator_class).display_name_fr
            case StorageNode():
                return self.game_data.building(node.storage_class).display_name_fr
            case SplitterNode() | MergerNode():
                building = self._building_of(node)
                if building is None:
                    # Nothing on its lines yet, so no building to name it after.
                    return "Répartiteur" if isinstance(node, SplitterNode) else "Groupeur"
                return self.game_data.building(building).display_name_fr

    def _buffer_solution(self, state: _NodeState) -> BufferSolution:
        node = state.node
        assert isinstance(node, StorageNode)
        item_class = self._storage_items[node.id]
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
            if not isinstance(
                state.node,
                ResourceNode | ResourceWellNode | WaterExtractorNode | ExternalSourceNode,
            ):
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

    def _power_by_building(
        self, nodes: Iterable[NodeSolution], *, produced: bool = False
    ) -> dict[str, float]:
        """Draw -- or output -- per building class.

        Idle machines still count on the consumption side: they are still built and
        still plugged in. Idle generators do not count on the production side, for
        the same physical reason: a generator with no fuel produces nothing.
        """
        totals: dict[str, float] = {}
        for solution in nodes:
            power = solution.power_produced_mw if produced else solution.power_mw
            if solution.building_class is None or power <= 0:
                continue
            totals[solution.building_class] = totals.get(solution.building_class, 0.0) + power
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
        attachments: dict[str, int] = {}
        shards: dict[str, int] = {}
        for solution in nodes:
            if solution.building_class is None:
                continue
            count = math.ceil(solution.machine_count or 1.0)
            # A fitting somebody drew is counted where it stands; a fitting nobody
            # drew is worked out from the lines below. Either way it ends up under
            # the same heading, because either way it is a building to put down.
            basket = attachments if solution.kind in PASS_THROUGH_KINDS else buildings
            basket[solution.building_class] = basket.get(solution.building_class, 0) + count
            for class_name, units in solution.extra_buildings.items():
                buildings[class_name] = buildings.get(class_name, 0) + units
            if basket is attachments:
                continue
            if solution.clock_speed > 1.0 and solution.machine_count is not None:
                for item, needed in self.game_data.shards_for(
                    solution.clock_speed, solution.machine_count
                ).items():
                    shards[item] = shards.get(item, 0) + needed

        if not self.graph.is_faithful:
            for class_name, units in self._implied_attachments().items():
                attachments[class_name] = attachments.get(class_name, 0) + units

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
            attachments=dict(sorted(attachments.items())),
            power_shards=dict(sorted(shards.items())),
        )

    def _implied_attachments(self) -> dict[str, int]:
        """Fittings a simple-mode document needs but does not draw.

        Two lines leaving one node with the same item means the player has to split
        that output, and two arriving means a merge. The count follows from the
        number of lines alone -- a splitter serves three, so each unit adds two.

        This is where the two modes give different totals, and the difference is
        real rather than an inconsistency. Here the count is what the drawing
        *implies*, worked out per port, in the cheapest chaining there is. In the
        faithful mode it is what somebody actually placed, and a factory drawn by
        hand may well use more: a tree built for symmetry, or a fitting kept where
        a machine count later dropped. A rise on switching to faithful is therefore
        the drawing telling you something the deduction could not.

        The standard splitter is always the one costed. A smart or a programmable
        one cannot be implied -- nothing in a simple document says a branch was
        meant to be filtered, and there would be nothing to read it off.
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
                    item = self.game_data.items.get(item_class)
                    if item is None:
                        continue
                    # A pipe junction has no mode at all, so asking it for a
                    # standard one would come back empty and price nothing.
                    mode = (
                        SplitterMode.STANDARD
                        if role is AttachmentRole.SPLIT and not item.form.is_fluid
                        else None
                    )
                    attachment = self.game_data.attachment_for(item.form, role, mode)
                    if attachment is None:
                        continue
                    units = attachment.units_for(count)
                    if units:
                        totals[attachment.class_name] = (
                            totals.get(attachment.class_name, 0) + units
                        )
        return dict(sorted(totals.items()))


def allocate(
    supplies: Mapping[str, float],
    caps: Mapping[str, float],
    edges: Sequence[Edge],
    limits: Mapping[str, float] | None = None,
    deferred: frozenset[str] = frozenset(),
    overflow: frozenset[str] = frozenset(),
) -> tuple[dict[str, float], bool]:
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

    ``deferred`` names consumers that have a real appetite and are served last all
    the same. There is one kind: a splitter with nothing but sinks behind it. It is
    a conduit, so its own ceiling says nothing about what wants the material, and
    letting it queue ahead of an exit drawn beside it would starve the exit to fill
    a fitting. Its ceiling is still enforced when its turn comes -- unlike a real
    sink's, which is not one.

    ``overflow`` names **lines** rather than consumers: the branches of a splitter
    set to take only what the other branches refused. It is the same idea and it is
    deliberately the same mechanism -- a second round over what is left -- because
    "surplus" is a place in the queue and nothing else. That is what lets the
    residue go to the recycler until it is full and the rest to the flare, without
    a rule of its own anywhere in the solver.

    ``limits`` caps each line by its transport tier. Deterministic: producers,
    consumers and edges are all walked in sorted order.

    Returns the flows and **whether a line ever bound** -- whether a share was
    clipped by a transport ceiling. That second value is what lets the caller skip
    the uncapped companion run: if no ceiling was ever reached, taking the
    ceilings away could not have changed a single figure.
    """
    flows = {edge.id: 0.0 for edge in edges}
    if not edges:
        return flows, False

    ordered = sorted(edges, key=lambda edge: edge.id)
    remaining_supply = {node_id: max(supplies.get(node_id, 0.0), 0.0) for node_id in supplies}
    remaining_line = {
        edge.id: math.inf if limits is None else limits.get(edge.id, math.inf) for edge in ordered
    }
    # One ceiling dictionary for both rounds, so that a branch served late into a
    # consumer already partly filled sees what is left of it rather than all of it.
    remaining_cap = dict(caps)
    held_back = {node for node, cap in caps.items() if not math.isfinite(cap)} | set(deferred)
    now = [edge for edge in ordered if edge.target not in held_back and edge.id not in overflow]
    last = [edge for edge in ordered if edge.target in held_back or edge.id in overflow]

    bound = _water_fill(flows, now, remaining_supply, remaining_cap, remaining_line)
    if last:
        # Second pass: what nobody could take goes to the unlimited absorbers, to
        # the conduits that lead only to them, and to the overflow branches.
        bound |= _water_fill(flows, last, remaining_supply, remaining_cap, remaining_line)
    return flows, bound


def _water_fill(
    flows: dict[str, float],
    edges: Sequence[Edge],
    remaining_supply: dict[str, float],
    remaining_cap: dict[str, float],
    remaining_line: dict[str, float],
) -> bool:
    """Move as much as possible from supplies to caps, redistributing what bounces.

    A consumer that absorbs without limit is passed a ceiling of infinity, which
    every step below then handles on its own: it never runs out, it never scales an
    offer down, and a clip in front of it is never harmless. There used to be a flag
    saying all of that a second time, and it had to be right in four places.

    Returns whether a transport ceiling ever changed what got delivered.

    That is a narrower question than "was an offer ever clipped", and the
    difference is the ordinary case of an over-sized deposit. A Mk.3 miner on a
    pure node at 250 % puts 3 600 ore a minute on a belt that carries 270, feeding
    a smelter that wants 120: the offer is clipped, and the smelter still receives
    its 120, exactly as it would down an infinite belt. Counting that as a binding
    line would mean re-solving the whole factory to discover nothing had changed,
    on the most common layout there is.

    A clip is therefore only reported when it can have changed a figure: when the
    line ran out of room while there was still material and somewhere to put it,
    or when the consumer at the far end could have taken more than the clipped
    offer -- including any consumer sharing its intake with another line, where
    clipping one offer changes how the others are scaled.
    """
    bound = False
    for _ in range(MAX_ALLOCATION_ROUNDS):
        active = []
        for edge in edges:
            if (
                remaining_supply.get(edge.source, 0.0) <= FLOW_EPSILON
                or remaining_cap.get(edge.target, 0.0) <= FLOW_EPSILON
            ):
                continue
            if remaining_line[edge.id] <= FLOW_EPSILON:
                # There is material and room for it, and the line is full: that is
                # the ceiling binding, in its most visible form.
                bound = True
                continue
            active.append(edge)
        if not active:
            return bound

        # Step 1: each producer offers an equal share per line, never more than the
        # line can still carry. What a share does not use comes back next round.
        offers: dict[str, float] = {}
        clipped: set[str] = set()
        by_source: dict[str, list[Edge]] = {}
        for edge in active:
            by_source.setdefault(edge.source, []).append(edge)
        for source, group in sorted(by_source.items()):
            share = remaining_supply[source] / len(group)
            for edge in group:
                ceiling = remaining_line[edge.id]
                if ceiling < share - FLOW_EPSILON:
                    clipped.add(edge.id)
                offers[edge.id] = min(share, ceiling)

        # Step 2: each consumer accepts what it can, scaling every offer alike.
        moved = 0.0
        # Clips that provably delivered the same as an infinite line would have.
        harmless: set[str] = set()
        by_target: dict[str, list[Edge]] = {}
        for edge in active:
            if offers.get(edge.id, 0.0) > FLOW_EPSILON:
                by_target.setdefault(edge.target, []).append(edge)
        for target, group in sorted(by_target.items()):
            offered = sum(offers[edge.id] for edge in group)
            if offered <= FLOW_EPSILON:
                continue
            capacity = remaining_cap.get(target, 0.0)
            factor = min(1.0, capacity / offered)
            if len(group) == 1 and capacity <= offers[group[0].id] + FLOW_EPSILON:
                # One line into this consumer, and it is the consumer's appetite
                # that runs out first: it takes all it can hold either way.
                harmless.add(group[0].id)
            for edge in group:
                accepted = offers[edge.id] * factor
                if accepted <= 0:
                    continue
                flows[edge.id] += accepted
                remaining_supply[edge.source] -= accepted
                remaining_line[edge.id] -= accepted
                remaining_cap[target] -= accepted
                moved += accepted
        # Anything clipped and not shown harmless -- including an offer too small
        # to have reached step 2 at all -- has to be assumed to have mattered.
        bound = bound or bool(clipped - harmless)
        if moved <= FLOW_EPSILON:
            return bound
    return bound


def _clean(values: Mapping[str, float]) -> dict[str, float]:
    """Drop float noise and sort, so that two runs compare equal."""
    return {
        key: round(value, 9) for key, value in sorted(values.items()) if abs(value) > FLOW_EPSILON
    }


def _nameplate(values: Mapping[str, float]) -> dict[str, float]:
    """The same, keeping the zeros and dropping what is not a number.

    Two differences from :func:`_clean`, and both matter to a reader.

    A nominal of zero is kept: a machine set to zero machines has a nameplate, and
    it says zero. Dropping it would make the port look as though it had no rating
    rather than a rating of nothing.

    An infinite nominal is dropped. A buffer and an exit take whatever arrives, and
    a port with no ceiling has no second figure to show beside the first -- writing
    one would turn "unlimited" into a quantity, and would not survive being written
    to JSON either.
    """
    return {key: round(value, 9) for key, value in sorted(values.items()) if math.isfinite(value)}


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
    """One diagnosed answer, plus the uncapped companion its line findings need.

    The companion answers "what would this line carry if it were big enough?", and
    on a factory where no line is too small the answer is "exactly what it already
    carries". That is not a guess: the solver reports whether a transport ceiling
    ever got in the way of an allocation, and if none ever did, the two runs would
    have executed identically instruction for instruction -- the ceilings are the
    only thing that differs between them. So the capped run goes first now, and the
    companion is paid for only when the answer can actually differ.

    Every figure is the same either way. What changes is that a well-sized factory
    stops paying for a second fixed point to be told what it already knew.
    """
    solver = _Solver(graph, game_data, SolveOptions(buffers_supply=buffers_supply))
    capped = solver.run()
    if solver.line_bound:
        companion = _Solver(
            graph,
            game_data,
            SolveOptions(enforce_line_capacity=False, buffers_supply=buffers_supply),
        )
        companion.iterate()
        desired = companion.edge_rates()
    else:
        desired = {solution.edge_id: solution.rate_per_minute for solution in capped.edges}
    report = _with_desired_rates(capped, desired)
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
    return report.with_flows(nodes, edges)


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
        msg = f"le nœud {node_id} n'est pas une machine"
        raise TypeError(msg)
    rates = game_data.recipe(node.recipe_class).ingredient_rates()
    if not rates:
        return node.machine_count

    probe = graph.model_copy(deep=True)
    probed = probe.node(node_id)
    assert isinstance(probed, MachineNode)
    probed.machine_count = max(node.machine_count, 1.0) * PROBE_FACTOR

    solution = solve(probe, game_data).node(node_id)
    # Divided by what one machine consumes **at this node's clock**: the answer is a
    # number of machines, and an overclocked machine eats more of everything.
    clock = node.clock_speed
    return min(
        solution.inputs.get(item, 0.0) / (rate * clock)
        for item, rate in sorted(rates.items())
        if rate > 0
    )
