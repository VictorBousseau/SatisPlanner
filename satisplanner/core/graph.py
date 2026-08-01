"""The factory graph: nodes, typed edges, and the decomposition the solver needs.

Design points that matter downstream:

* An edge carries **one item** and **one transport**. A solid can only travel on a
  belt and a fluid only in a pipe; :meth:`FactoryGraph.connect` refuses anything
  else at construction time rather than letting the solver discover it.
* A **port carries one line per building on the node**, and a line that has no port
  left is refused the same way -- see :func:`port_line_budget`. Getting more lines
  out of a node is what a splitter is for, and a splitter is a node like any other.
* The graph is plain data and serialises to JSON through pydantic. It holds no
  reference to the game catalogue, which is passed in wherever it is needed.
* Everything that iterates does so in a **sorted** order, so that two graphs built
  in different orders produce identical results.
"""

import math
from collections.abc import Callable
from enum import StrEnum
from typing import Annotated, Any, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from satisplanner.core import constants
from satisplanner.core.models import AttachmentRole, GameData, ItemForm, Purity, UnknownClassError

# 2 added a clock speed to extractors and machines. Reading a version 1 document
# needs no conversion -- the field defaults to 100 % -- but the bump is what makes
# a V1 build refuse a V1.1 file with a sentence instead of a validation error.
# 3 added the generator node. Same reasoning: an older document simply has none,
# but a document that has one must not be opened by a build that cannot draw it.
# 4 added the per-node deployed-rendering override, which is display state but
# belongs to the document all the same -- exactly as a position does.
# 5 made splitters and mergers nodes of their own, and with them the rule that a
# port carries as many lines as there are buildings on the node. A document from
# before it has both to gain: the migration inserts what the layout implied.
SCHEMA_VERSION: Final = 5

# A machine has at most four input ports and two output ports.
MAX_MACHINE_INPUTS: Final = 4
MAX_MACHINE_OUTPUTS: Final = 2


class GraphError(Exception):
    """The graph would become inconsistent: the operation is refused.

    Deliberately not a ``ValueError``: pydantic converts those raised inside a
    validator into a ``ValidationError``, which would mean callers had to catch two
    different exceptions for the same rule depending on whether the graph was being
    loaded or edited. Anything else propagates untouched.
    """


class NodeKind(StrEnum):
    RESOURCE = "resource"
    WATER_EXTRACTOR = "water_extractor"
    EXTERNAL_SOURCE = "external_source"
    MACHINE = "machine"
    GENERATOR = "generator"
    STORAGE = "storage"
    OUTPUT = "output"
    SPLITTER = "splitter"
    MERGER = "merger"


class _NodeBase(BaseModel):
    # Assignment is validated, not just construction. Every edit in the application
    # goes through a command that sets an attribute by name, so without this a clock
    # of 400 % would be refused when a file declares it and accepted when a widget
    # writes it. The bounds belong to the field, so this is where they are enforced.
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    id: str
    label: str | None = None
    position: tuple[float, float] = (0.0, 0.0)
    # Show one thumbnail per built machine on this node, overriding the global
    # preference. ``None`` means "follow it". Purely a way of drawing the node: it
    # changes no rate and adds nothing to the shopping list. It lives in the
    # document rather than in the settings for the same reason a position does --
    # it is a property of *this* node in *this* factory, and a user who deployed
    # one bank of generators expects to find it deployed when they reopen the file.
    show_deployed: bool | None = None


def _clock_field() -> Any:
    """Clock speed, as a fraction: 1.0 is 100 %, the game's own range 1 % to 250 %.

    Declared here rather than validated against the catalogue because a graph is
    plain data and never holds one. A function rather than a shared ``Field``
    object, so that three models never end up sharing one descriptor.
    """
    return Field(default=1.0, ge=constants.MIN_CLOCK_SPEED, le=constants.MAX_CLOCK_SPEED)


class ResourceNode(_NodeBase):
    """A solid or crude oil deposit, worked by one or more extractors."""

    kind: Literal[NodeKind.RESOURCE] = NodeKind.RESOURCE
    item_class: str
    extractor_class: str
    purity: Purity = Purity.NORMAL
    count: float = Field(default=1.0, gt=0)
    clock_speed: float = _clock_field()


class WaterExtractorNode(_NodeBase):
    """A water extractor: fixed output, no node purity.

    The item comes from the extractor's own declaration in the game data, so no
    game class name is hard-coded in the domain layer.
    """

    kind: Literal[NodeKind.WATER_EXTRACTOR] = NodeKind.WATER_EXTRACTOR
    extractor_class: str
    count: float = Field(default=1.0, gt=0)
    clock_speed: float = _clock_field()


class ExternalSourceNode(_NodeBase):
    """An import from outside the modelled factory, at a rate the user states."""

    kind: Literal[NodeKind.EXTERNAL_SOURCE] = NodeKind.EXTERNAL_SOURCE
    item_class: str
    rate_per_minute: float = Field(default=0.0, ge=0)


class MachineNode(_NodeBase):
    """A production machine running one recipe.

    ``machine_count`` is an **input**: the user states how many machines are built,
    decimals allowed. The engine reports back how many of them are actually useful.
    ``building_class`` is normally left empty and derived from the recipe; it exists
    so that a mismatch introduced by the UI can be diagnosed rather than ignored.
    """

    kind: Literal[NodeKind.MACHINE] = NodeKind.MACHINE
    recipe_class: str
    machine_count: float = Field(default=1.0, ge=0)
    building_class: str | None = None
    clock_speed: float = _clock_field()


class GeneratorNode(_NodeBase):
    """A bank of generators burning one chosen fuel.

    ``fuel_class`` is a choice among the fuels the building declares, because a fuel
    generator runs on fuel or on turbofuel and the two do not have the same appetite
    at all. It is validated against the catalogue where the catalogue is available --
    a graph never holds one -- and shown on the node, because it changes every number.

    There is deliberately **no clock field**. The game raises a generator's output by
    an exponent of its own, distinct from the consumption exponent that prices an
    overclocked machine; modelling one with the other would invent figures. Absent is
    therefore more honest than present and pinned to 100 %.
    """

    kind: Literal[NodeKind.GENERATOR] = NodeKind.GENERATOR
    generator_class: str
    fuel_class: str
    count: float = Field(default=1.0, gt=0)


class StorageNode(_NodeBase):
    """A buffer. ``item_class`` may be left empty and inferred from the edges."""

    kind: Literal[NodeKind.STORAGE] = NodeKind.STORAGE
    storage_class: str
    item_class: str | None = None
    initial_content: float = Field(default=0.0, ge=0)


class OutputNode(_NodeBase):
    """Where a product leaves the factory.

    ``is_sink`` marks a deliberate discard (flare, AWESOME Sink). Both kinds absorb
    without limit; the flag only changes how the report accounts for the flow.
    """

    kind: Literal[NodeKind.OUTPUT] = NodeKind.OUTPUT
    item_class: str
    is_sink: bool = False


class _PassThroughNode(_NodeBase):
    """A splitter or a merger: one item in, the same item out, nothing kept.

    ``item_class`` may be left empty and read off the lines, exactly as a buffer's
    content is. A splitter is placed on a belt that already carries something, and
    making the user name the item again would be asking twice.

    There is no building class field. Which building this is follows from the item's
    form and the job: a solid splits in a conveyor splitter and a fluid in a pipe
    junction, and the junction does both jobs because it has four ports. A field
    would only let a document disagree with itself.
    """

    item_class: str | None = None


class SplitterNode(_PassThroughNode):
    """One line in, up to three out, split equally between the ones connected."""

    kind: Literal[NodeKind.SPLITTER] = NodeKind.SPLITTER


class MergerNode(_PassThroughNode):
    """Up to three lines in, one out."""

    kind: Literal[NodeKind.MERGER] = NodeKind.MERGER


Node = Annotated[
    ResourceNode
    | WaterExtractorNode
    | ExternalSourceNode
    | MachineNode
    | GeneratorNode
    | StorageNode
    | OutputNode
    | SplitterNode
    | MergerNode,
    Field(discriminator="kind"),
]

# Node kinds that can absorb an incoming flow. A generator is a consumer and only
# that: what it produces is power, and power does not travel on a line.
CONSUMER_KINDS: Final = frozenset(
    {
        NodeKind.MACHINE,
        NodeKind.GENERATOR,
        NodeKind.STORAGE,
        NodeKind.OUTPUT,
        NodeKind.SPLITTER,
        NodeKind.MERGER,
    }
)
# Node kinds that can emit a flow.
PRODUCER_KINDS: Final = frozenset(
    {
        NodeKind.RESOURCE,
        NodeKind.WATER_EXTRACTOR,
        NodeKind.EXTERNAL_SOURCE,
        NodeKind.MACHINE,
        NodeKind.STORAGE,
        NodeKind.SPLITTER,
        NodeKind.MERGER,
    }
)
# Kinds that keep nothing: what arrives leaves, and the node is neither a source
# nor a sink of anything.
PASS_THROUGH_KINDS: Final = frozenset({NodeKind.SPLITTER, NodeKind.MERGER})


# Keys the identifier indexes live under, in the graph's own instance dictionary.
_NODE_INDEX: Final = "_node_index"
_EDGE_INDEX: Final = "_edge_index"
_IN_INDEX: Final = "_incoming_index"
_OUT_INDEX: Final = "_outgoing_index"


def _indexed[T](
    graph: "FactoryGraph",
    key: str,
    entries: list[T],
    identifier: Callable[[T], str],
) -> dict[str, T]:
    """An identifier map for ``entries``, rebuilt only when it has gone stale.

    A graph is read back one identifier at a time -- once per node item on the
    canvas, once per cell the table paints, once per undo command -- and walking
    the list each time is what made a five-hundred node factory painful. Unlike a
    report, though, a graph is edited in place, so the map cannot simply be
    computed once. It is kept beside two things that say whether it still holds:

    * **the list object itself**, held rather than merely identified, so that it
      cannot be freed and have its address handed to a different list;
    * **the length it had** when the map was made.

    Between them they catch every way this project changes a graph. Assigning a
    filtered list -- ``remove_node``, ``RemoveCommand`` -- changes the object;
    appending or extending -- ``add_node``, ``connect``, ``PasteCommand`` --
    changes the length. Setting a field on a node changes neither, and neither
    does it change what the map says, because an identifier is never reassigned:
    pasting renames its copies before they are ever added.

    The cache lives in the instance dictionary, which is where
    ``functools.cached_property`` puts its own, and deliberately **not** in a
    pydantic private attribute: ``__pydantic_private__`` takes part in equality,
    so two identical graphs would compare unequal purely for having been read
    from in a different order. The instance dictionary does not, because pydantic
    falls back to comparing declared fields when the raw dictionaries differ.
    """
    cached = graph.__dict__.get(key)
    if cached is not None and cached[0] is entries and cached[1] == len(entries):
        found: dict[str, T] = cached[2]
        return found
    fresh = {identifier(entry): entry for entry in entries}
    graph.__dict__[key] = (entries, len(entries), fresh)
    return fresh


class Edge(BaseModel):
    """A conveyor or a pipe carrying one item between two nodes."""

    model_config = ConfigDict(extra="forbid")

    id: str
    source: str
    target: str
    item_class: str
    transport_class: str


class FactoryGraph(BaseModel):
    """A serialisable factory layout."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = SCHEMA_VERSION
    nodes: list[Node] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_references(self) -> Self:
        """Identifiers must be unique and every edge must join two known nodes."""
        seen: set[str] = set()
        for node in self.nodes:
            if node.id in seen:
                msg = f"identifiant de nœud en doublon : {node.id}"
                raise GraphError(msg)
            seen.add(node.id)

        edge_ids: set[str] = set()
        for edge in self.edges:
            if edge.id in edge_ids:
                msg = f"identifiant d'arête en doublon : {edge.id}"
                raise GraphError(msg)
            edge_ids.add(edge.id)
            for endpoint in (edge.source, edge.target):
                if endpoint not in seen:
                    msg = f"l'arête {edge.id} référence un nœud inconnu : {endpoint}"
                    raise GraphError(msg)
            if edge.source == edge.target:
                msg = f"l'arête {edge.id} boucle sur le nœud {edge.source}"
                raise GraphError(msg)
        return self

    # ---------------------------------------------------------------- lookups

    def node_map(self) -> dict[str, Node]:
        """Every node by identifier. A copy: callers are free to keep it."""
        return dict(self._nodes_by_id())

    def _nodes_by_id(self) -> dict[str, Node]:
        return _indexed(self, _NODE_INDEX, self.nodes, lambda node: node.id)

    def _edges_by_id(self) -> dict[str, Edge]:
        return _indexed(self, _EDGE_INDEX, self.edges, lambda edge: edge.id)

    def node(self, node_id: str) -> Node:
        found = self._nodes_by_id().get(node_id)
        if found is None:
            msg = f"nœud inconnu : {node_id}"
            raise GraphError(msg)
        return found

    def edge(self, edge_id: str) -> Edge:
        found = self._edges_by_id().get(edge_id)
        if found is None:
            msg = f"arête inconnue : {edge_id}"
            raise GraphError(msg)
        return found

    def outgoing(self, node_id: str) -> list[Edge]:
        """Lines leaving this node, in the graph's own order. A fresh list."""
        return list(self._incidence(_OUT_INDEX, lambda edge: edge.source).get(node_id, ()))

    def incoming(self, node_id: str) -> list[Edge]:
        """Lines arriving at this node, in the graph's own order. A fresh list."""
        return list(self._incidence(_IN_INDEX, lambda edge: edge.target).get(node_id, ()))

    def _incidence(self, key: str, endpoint: Callable[[Edge], str]) -> dict[str, list[Edge]]:
        """Lines grouped by one of their ends, cached like the identifier maps.

        Asking "what reaches this node?" used to walk every line in the factory,
        and the diagnostics ask it several times per node. Guarded exactly as
        :func:`_indexed` is, and for the same reasons.
        """
        cached = self.__dict__.get(key)
        if cached is not None and cached[0] is self.edges and cached[1] == len(self.edges):
            grouped: dict[str, list[Edge]] = cached[2]
            return grouped
        fresh: dict[str, list[Edge]] = {}
        for edge in self.edges:
            fresh.setdefault(endpoint(edge), []).append(edge)
        self.__dict__[key] = (self.edges, len(self.edges), fresh)
        return fresh

    def sorted_nodes(self) -> list[Node]:
        return sorted(self.nodes, key=lambda node: node.id)

    def sorted_edges(self) -> list[Edge]:
        return sorted(self.edges, key=lambda edge: edge.id)

    # ---------------------------------------------------------------- editing

    def add_node(self, node: Node) -> Node:
        if node.id in self._nodes_by_id():
            msg = f"identifiant de nœud en doublon : {node.id}"
            raise GraphError(msg)
        self.nodes.append(node)
        return node

    def connect(
        self,
        source: str,
        target: str,
        item_class: str,
        transport_class: str,
        game_data: GameData,
        edge_id: str | None = None,
    ) -> Edge:
        """Create an edge after checking it makes physical sense.

        Refused at construction: an unknown class, a form that does not match the
        transport, a producer that does not make the item, a consumer that cannot
        take it, or a machine port budget already full.
        """
        edge = Edge(
            id=edge_id or self._next_edge_id(),
            source=source,
            target=target,
            item_class=item_class,
            transport_class=transport_class,
        )
        check_edge(self, edge, game_data)
        if edge.id in self._edges_by_id():
            msg = f"identifiant d'arête en doublon : {edge.id}"
            raise GraphError(msg)
        self.edges.append(edge)
        return edge

    def remove_node(self, node_id: str) -> None:
        """Remove a node and every edge attached to it."""
        self.nodes = [node for node in self.nodes if node.id != node_id]
        self.edges = [edge for edge in self.edges if node_id not in (edge.source, edge.target)]

    def remove_edge(self, edge_id: str) -> None:
        self.edges = [edge for edge in self.edges if edge.id != edge_id]

    def _next_edge_id(self) -> str:
        return f"e{len(self.edges) + 1}"


# --------------------------------------------------------------------------- #
# Edge legality
# --------------------------------------------------------------------------- #


def node_output_items(node: Node, game_data: GameData) -> set[str]:
    """Items this node can emit."""
    match node:
        case SplitterNode() | MergerNode():
            # Whatever it was put on. An empty set means "not decided yet", which
            # is what lets the first line connected settle it.
            return {node.item_class} if node.item_class else set()
        case ResourceNode():
            return {node.item_class}
        case WaterExtractorNode():
            extracted = game_data.extractor(node.extractor_class).item_class
            return {extracted} if extracted else set()
        case ExternalSourceNode():
            return {node.item_class}
        case MachineNode():
            return set(game_data.recipe(node.recipe_class).product_rates())
        case GeneratorNode():
            # It produces power, and power is not a flow on a line.
            return set()
        case StorageNode():
            # A buffer passes through whatever reaches it.
            return {node.item_class} if node.item_class else set()
        case OutputNode():
            return set()


def node_input_items(node: Node, game_data: GameData) -> set[str] | None:
    """Items this node can absorb, or ``None`` when it accepts anything."""
    match node:
        case SplitterNode() | MergerNode():
            return {node.item_class} if node.item_class else None
        case MachineNode():
            return set(game_data.recipe(node.recipe_class).ingredient_rates())
        case GeneratorNode():
            return set(generator_input_rates(node, game_data))
        case StorageNode():
            return {node.item_class} if node.item_class else None
        case OutputNode():
            return {node.item_class}
        case _:
            return set()


def check_edge(graph: FactoryGraph, edge: Edge, game_data: GameData) -> None:
    """Raise :class:`GraphError` if this edge could not exist in the game."""
    source = graph.node(edge.source)
    target = graph.node(edge.target)
    item = game_data.item(edge.item_class)

    try:
        matches = game_data.transport_form_matches(edge.transport_class, item.form)
    except UnknownClassError as exc:
        raise GraphError(str(exc)) from exc
    if not matches:
        expected = "une tuyauterie" if item.form.is_fluid else "un convoyeur"
        msg = (
            f"{item.display_name_fr} est {_form_label(item.form)} : il faut {expected}, "
            f"pas {edge.transport_class}"
        )
        raise GraphError(msg)

    if source.kind not in PRODUCER_KINDS:
        msg = f"le nœud {source.id} ne produit rien"
        raise GraphError(msg)
    if target.kind not in CONSUMER_KINDS:
        msg = f"le nœud {target.id} ne consomme rien"
        raise GraphError(msg)

    produced = node_output_items(source, game_data)
    if produced and edge.item_class not in produced:
        msg = f"le nœud {source.id} ne produit pas {item.display_name_fr}"
        raise GraphError(msg)

    accepted = node_input_items(target, game_data)
    if accepted is not None and edge.item_class not in accepted:
        msg = f"le nœud {target.id} ne consomme pas {item.display_name_fr}"
        raise GraphError(msg)

    _check_port_budget(graph, edge, game_data)
    _check_single_item(graph, edge, game_data)
    _check_line_budget(graph, edge, game_data)


def _check_port_budget(graph: FactoryGraph, edge: Edge, game_data: GameData) -> None:
    """A machine has at most 4 distinct inputs and 2 distinct outputs."""
    target = graph.node(edge.target)
    if isinstance(target, MachineNode):
        items = {other.item_class for other in graph.incoming(target.id)} | {edge.item_class}
        if len(items) > MAX_MACHINE_INPUTS:
            msg = f"le nœud {target.id} dépassé {MAX_MACHINE_INPUTS} entrées distinctes"
            raise GraphError(msg)

    source = graph.node(edge.source)
    if isinstance(source, MachineNode):
        items = {other.item_class for other in graph.outgoing(source.id)} | {edge.item_class}
        if len(items) > MAX_MACHINE_OUTPUTS:
            msg = f"le nœud {source.id} dépassé {MAX_MACHINE_OUTPUTS} sorties distinctes"
            raise GraphError(msg)
    _ = game_data  # kept for symmetry with check_edge's signature


def _check_single_item(graph: FactoryGraph, edge: Edge, game_data: GameData) -> None:
    """A splitter or a merger carries one item, whether or not it was named.

    The declared item is already handled by :func:`node_output_items`; this is the
    inferred case, which no set of allowed classes can express: a splitter left to
    follow its lines has no item until one arrives, and from then on it has that one.
    """
    for node_id in (edge.source, edge.target):
        node = graph.node(node_id)
        if not isinstance(node, SplitterNode | MergerNode) or node.item_class:
            continue
        carried = pass_through_item(node, graph)
        if carried is not None and carried != edge.item_class:
            name = game_data.item(carried).display_name_fr
            msg = f"le nœud {node_id} transporte déjà {name} : une ligne ne porte qu'un item"
            raise GraphError(msg)


def port_line_budget(node: Node, *, is_output: bool) -> int | None:
    """How many lines one port of this node may carry, or ``None`` for no limit.

    In the game a port carries one line. A node standing for eight smelters has
    eight of them, so it has eight output lines, and that is why the budget is
    ``ceil(count)`` rather than one: eight smelters feeding eight consumers need no
    splitter at all. It is counted per item **and** per direction, because a
    manufacturer has one port per slot of its recipe.

    The three kinds that have no ``count`` are decided rather than guessed:

    * a **buffer** gets one, because one container is one building and the game
      gives it one input and one output;
    * an **exit** and an **import** get none, because they are the boundary of what
      is being modelled and not buildings at all -- demanding a merger in front of
      an abstraction would be ceremony with nothing behind it in game;
    * a **splitter** and a **merger** get the branch count on their many side and
      one on the other, which is the shape of the building.
    """
    match node:
        case OutputNode() | ExternalSourceNode():
            return None
        case SplitterNode():
            return constants.ATTACHMENT_BRANCHES if is_output else 1
        case MergerNode():
            return 1 if is_output else constants.ATTACHMENT_BRANCHES
        case _:
            count = unit_count(node)
            if count is None:
                return 1
            # A machine count may be zero while it is being typed. Refusing every
            # line at that instant would delete work over a keystroke, so the floor
            # is one port -- the smallest thing that can exist at all.
            return max(1, math.ceil(count))


def _check_line_budget(graph: FactoryGraph, edge: Edge, game_data: GameData) -> None:
    """Refuse a line the node has no port left for, and say what to insert."""
    for node_id, is_output in ((edge.source, True), (edge.target, False)):
        node = graph.node(node_id)
        budget = port_line_budget(node, is_output=is_output)
        if budget is None:
            continue
        neighbours = graph.outgoing(node_id) if is_output else graph.incoming(node_id)
        # A line already in the graph under this identifier is the one being
        # replaced -- changing a belt's tier is not asking for a second belt.
        used = sum(
            1
            for other in neighbours
            if other.item_class == edge.item_class and other.id != edge.id
        )
        if used < budget:
            continue
        item = game_data.item(edge.item_class).display_name_fr
        side = "sortie" if is_output else "entrée"
        remedy = "un répartiteur" if is_output else "un groupeur"
        msg = (
            f"le nœud {node_id} n'a que {budget} port(s) de {side} pour {item} "
            f"et ils sont pris : insérez {remedy}"
        )
        raise GraphError(msg)


def _form_label(form: ItemForm) -> str:
    return {ItemForm.SOLID: "solide", ItemForm.LIQUID: "liquide", ItemForm.GAS: "gazeux"}[form]


# --------------------------------------------------------------------------- #
# Decomposition
# --------------------------------------------------------------------------- #


def adjacency(graph: FactoryGraph) -> dict[str, list[str]]:
    """Successors of each node, sorted, duplicates removed."""
    result: dict[str, set[str]] = {node.id: set() for node in graph.nodes}
    for edge in graph.edges:
        result[edge.source].add(edge.target)
    return {node_id: sorted(targets) for node_id, targets in sorted(result.items())}


def strongly_connected_components(graph: FactoryGraph) -> list[tuple[str, ...]]:
    """Tarjan's algorithm, iterative and deterministic.

    Returns components as sorted tuples. Recursion is avoided on purpose: a long
    production line would otherwise be able to blow the interpreter's stack.
    """
    successors = adjacency(graph)
    index_of: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    components: list[tuple[str, ...]] = []
    counter = 0

    for root in sorted(successors):
        if root in index_of:
            continue
        # Each frame is (node, iterator position among its successors).
        work: list[tuple[str, int]] = [(root, 0)]
        index_of[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)

        while work:
            node, position = work[-1]
            children = successors[node]
            if position < len(children):
                work[-1] = (node, position + 1)
                child = children[position]
                if child not in index_of:
                    index_of[child] = low[child] = counter
                    counter += 1
                    stack.append(child)
                    on_stack.add(child)
                    work.append((child, 0))
                elif child in on_stack:
                    low[node] = min(low[node], index_of[child])
                continue

            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index_of[node]:
                component: list[str] = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                components.append(tuple(sorted(component)))

    return components


def is_cyclic(component: tuple[str, ...], graph: FactoryGraph) -> bool:
    """True if the component really is a cycle and not a single node passed through."""
    if len(component) > 1:
        return True
    only = component[0]
    return any(edge.source == only and edge.target == only for edge in graph.edges)


def condensation_order(graph: FactoryGraph) -> list[tuple[str, ...]]:
    """Strongly connected components in topological order, deterministically.

    Ties are broken on the component's smallest node identifier so that the result
    never depends on how the graph was assembled.
    """
    components = strongly_connected_components(graph)
    owner = {node_id: index for index, component in enumerate(components) for node_id in component}

    successors: dict[int, set[int]] = {index: set() for index in range(len(components))}
    incoming_count = dict.fromkeys(range(len(components)), 0)
    for edge in graph.edges:
        source_component, target_component = owner[edge.source], owner[edge.target]
        if source_component != target_component:
            successors[source_component].add(target_component)
    for source_component, targets in successors.items():
        for target in targets:
            incoming_count[target] += 1
        _ = source_component

    ready = sorted(
        (index for index, count in incoming_count.items() if count == 0),
        key=lambda index: components[index],
    )
    order: list[tuple[str, ...]] = []
    while ready:
        current = ready.pop(0)
        order.append(components[current])
        for target in sorted(successors[current], key=lambda index: components[index]):
            incoming_count[target] -= 1
            if incoming_count[target] == 0:
                ready.append(target)
        ready.sort(key=lambda index: components[index])

    if len(order) != len(components):  # pragma: no cover - Tarjan makes this impossible
        msg = "le graphe condensé contient un cycle, ce qui est impossible"
        raise GraphError(msg)
    return order


def storage_item(node: StorageNode, graph: FactoryGraph) -> str | None:
    """The item a buffer holds: declared, or inferred from a single incoming item."""
    if node.item_class:
        return node.item_class
    items = {edge.item_class for edge in graph.incoming(node.id)}
    if len(items) == 1:
        return next(iter(items))
    return None


def pass_through_item(node: Node, graph: FactoryGraph) -> str | None:
    """The item a splitter or a merger carries: declared, or read off its lines.

    Both ends are looked at, unlike a buffer, whose content is what fills it and so
    is read off what arrives. A splitter has no content: it is a fitting on a line,
    and the line is as much the one leaving as the one arriving. Ambiguity -- two
    different items on its lines -- reads as undecided, which is what the diagnostic
    then reports.
    """
    if not isinstance(node, SplitterNode | MergerNode):
        return None
    if node.item_class:
        return node.item_class
    items = {edge.item_class for edge in graph.incoming(node.id)}
    items |= {edge.item_class for edge in graph.outgoing(node.id)}
    return next(iter(items)) if len(items) == 1 else None


def unit_count(node: Node) -> float | None:
    """How many buildings this node stands for, or ``None`` when it is not a bank.

    A machine node counts machines, an extractor counts extractors, a generator
    counts generators. A buffer, an import and an exit are one thing each and have
    no such number -- and ``None`` is what says so, rather than a misleading 1.
    """
    match node:
        case MachineNode():
            return node.machine_count
        case ResourceNode() | WaterExtractorNode() | GeneratorNode():
            return node.count
        case _:
            return None


def machine_building(node: MachineNode, game_data: GameData) -> str:
    """The building a machine node runs in: its own, or the recipe's."""
    return node.building_class or game_data.recipe(node.recipe_class).building_class


def attachment_building(node: Node, item_class: str | None, game_data: GameData) -> str | None:
    """Which building a splitter or a merger is, from the form of what it carries.

    A solid splits in a conveyor splitter and merges in a conveyor merger; a fluid
    does both in the same pipe junction, which has four ports and no opinion about
    which of them is the trunk. ``None`` while the item is still undecided: there is
    nothing to build yet, and naming a building would be picking one at random.
    """
    if not isinstance(node, SplitterNode | MergerNode) or item_class is None:
        return None
    role = AttachmentRole.SPLIT if isinstance(node, SplitterNode) else AttachmentRole.MERGE
    item = game_data.items.get(item_class)
    if item is None:
        return None
    attachment = game_data.attachment_for(item.form, role)
    return None if attachment is None else attachment.class_name


def generator_input_rates(node: GeneratorNode, game_data: GameData) -> dict[str, float]:
    """What this bank of generators burns per minute, by item, at full load.

    Fuel and make-up water alike: the water of a coal generator is an input on a
    pipe, subject to the same capacity and back pressure as anything else, and a
    generator that does not get it is short of an input like any machine.

    Empty when the building does not accept the chosen fuel, which is how a file
    written against another game version degrades: no consumption, no production,
    and a diagnostic rather than an exception.
    """
    generator = game_data.generator(node.generator_class)
    fuel = generator.fuel(node.fuel_class)
    if fuel is None:
        return {}
    return {item: rate * node.count for item, rate in sorted(fuel.input_rates().items())}
