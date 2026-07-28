"""What a piece of factory takes in and gives out, on its own.

A module is a selection lifted out of a factory. Its **interface** is its unconnected
ports: the inputs nobody feeds and the outputs nobody takes. Those are what a reader
has to wire up after inserting it, and what the label on it should describe.

The figures are obtained by resolving the module **fed and drained**: an external
source on every open input at exactly what the port asks for, an exit on every open
output. Both halves are needed and neither is decoration:

* an input left dangling supplies nothing, so a module lifted from the middle of a
  chain -- the common case, and the whole point of modules -- would label itself
  zero;
* an output left dangling **blocks its machine entirely** in this engine, exactly as
  a byproduct with nowhere to go does, so the module would label itself zero for the
  other reason.

What the figures still carry is everything internal: a Mk.1 belt inside the module
that saturates saturates here too, and the label says so, because that belt is part
of the design. The artificial lines added at the edges are not: they are laid at the
fastest tier and split across as many as the rate needs, so that the thing being
measured is the module and never the wire the measurement was taken through.

**It is a label, not a promise.** Inserted into a factory that starves it, the module
will do less, and nothing here pretends otherwise.
"""

import logging
import math
from dataclasses import dataclass, field

from satisplanner.core import engine
from satisplanner.core.graph import (
    ExternalSourceNode,
    FactoryGraph,
    OutputNode,
)
from satisplanner.core.models import GameData, UnknownClassError

logger = logging.getLogger(__name__)

# Below this a rate is float dust rather than a port.
PORT_EPSILON = 1e-9


@dataclass(frozen=True)
class ModulePort:
    """One unconnected port: what has to be wired after an insertion."""

    node_id: str
    item_class: str
    is_output: bool
    # What the port asks for or offers at the nameplate, per minute.
    nominal_rate: float


@dataclass(frozen=True)
class ModuleInterface:
    """The throughputs of a module resolved on its own, by item."""

    inputs: dict[str, float] = field(default_factory=dict)
    outputs: dict[str, float] = field(default_factory=dict)
    ports: tuple[ModulePort, ...] = ()

    @property
    def is_closed(self) -> bool:
        """True for a module that needs nothing and offers nothing outward."""
        return not self.ports


def open_ports(graph: FactoryGraph, game_data: GameData) -> tuple[ModulePort, ...]:
    """Every port of the graph that no edge of the graph touches.

    Read off the **nameplate** rather than off a resolution: what a machine's ports
    are does not depend on whether anything is currently reaching them. A node with
    no nameplate at all -- a buffer, an exit -- has no port here, and that is right:
    it absorbs whatever arrives, so there is nothing to promise about it.
    """
    report = engine.solve(graph, game_data)
    incoming: set[tuple[str, str]] = {(edge.target, edge.item_class) for edge in graph.edges}
    outgoing: set[tuple[str, str]] = {(edge.source, edge.item_class) for edge in graph.edges}

    ports: list[ModulePort] = []
    for solution in report.nodes:
        for rates, is_output, wired in (
            (solution.nominal_inputs, False, incoming),
            (solution.nominal_outputs, True, outgoing),
        ):
            for item_class, rate in sorted(rates.items()):
                if rate <= PORT_EPSILON or (solution.node_id, item_class) in wired:
                    continue
                ports.append(
                    ModulePort(
                        node_id=solution.node_id,
                        item_class=item_class,
                        is_output=is_output,
                        nominal_rate=rate,
                    )
                )
    return tuple(ports)


def interface_of(graph: FactoryGraph, game_data: GameData) -> ModuleInterface:
    """Resolve the module fed and drained, and report what crosses its boundary."""
    ports = open_ports(graph, game_data)
    if not graph.nodes:
        return ModuleInterface()

    fed, feeders = _fed_and_drained(graph, ports, game_data)
    report = engine.solve(fed, game_data)

    inputs: dict[str, float] = {}
    for edge_id, item_class in feeders:
        try:
            rate = report.edge(edge_id).rate_per_minute
        except KeyError:  # an edge the solver dropped; nothing crossed it
            continue
        if rate > PORT_EPSILON:
            inputs[item_class] = inputs.get(item_class, 0.0) + rate

    return ModuleInterface(
        inputs=_clean(inputs),
        # Everything reaching an exit, the module's own included: an exit a user put
        # there is part of the interface just as much as one added here.
        outputs=_clean(dict(report.final_outputs)),
        ports=ports,
    )


def _fed_and_drained(
    graph: FactoryGraph, ports: tuple[ModulePort, ...], game_data: GameData
) -> tuple[FactoryGraph, list[tuple[str, str]]]:
    """A copy of the graph with a source on every open input and an exit on every
    open output. Returns it and the identifiers of the feeding edges."""
    fed = graph.model_copy(deep=True)
    feeders: list[tuple[str, str]] = []
    for index, port in enumerate(ports):
        transport = _fastest_transport(port.item_class, game_data)
        capacity = game_data.transport_capacity(transport)
        # As many lines as the rate needs, which is what a player would lay: one
        # artificial belt running at its limit would measure the belt, not the module.
        lines = max(1, math.ceil(port.nominal_rate / capacity)) if capacity > 0 else 1
        share = port.nominal_rate / lines
        for line in range(lines):
            helper = f"_interface{index}_{line}"
            if port.is_output:
                fed.add_node(OutputNode(id=helper, item_class=port.item_class))
                fed.connect(port.node_id, helper, port.item_class, transport, game_data)
                continue
            fed.add_node(
                ExternalSourceNode(id=helper, item_class=port.item_class, rate_per_minute=share)
            )
            edge = fed.connect(helper, port.node_id, port.item_class, transport, game_data)
            feeders.append((edge.id, port.item_class))
    return fed, feeders


def _fastest_transport(item_class: str, game_data: GameData) -> str:
    """The highest tier that can carry this item, so the wire is never the limit."""
    form = game_data.item(item_class).form
    if form.is_fluid:
        tiers = [(pipe.tier, pipe.class_name) for pipe in game_data.pipes.values()]
    else:
        tiers = [(belt.tier, belt.class_name) for belt in game_data.belts.values()]
    if not tiers:
        msg = f"aucun transport pour la forme {form.value} dans ce catalogue"
        raise UnknownClassError(msg)
    return max(tiers)[1]


def _clean(rates: dict[str, float]) -> dict[str, float]:
    return {
        item_class: round(rate, 9)
        for item_class, rate in sorted(rates.items())
        if rate > PORT_EPSILON
    }


__all__ = ["ModuleInterface", "ModulePort", "interface_of", "open_ports"]
