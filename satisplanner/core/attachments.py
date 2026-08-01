"""Splitters and mergers: the trees a port needs, and the ones a document is missing.

A port carries one line. Everything here follows from that: a node with more lines
on one port than it has buildings could not be built in the game, and what a player
puts there is a **tree of splitters**. This module builds that tree, and puts it
into a document that predates the rule.

Two things it deliberately does not do.

**It does not preserve the old figures.** Before this, four lines leaving one machine
each carried a quarter. A real tree of splitters carries a quarter each only when
four can be reached by halving and thirding -- it can -- and carries a sixth, a
sixth, a sixth and a third when the count is five, which cannot. Rebuilding the old
equal shares by some artifice would put back exactly the abstraction being removed,
so the shares change where the arithmetic says they must, and :class:`PortChange`
carries the old and the new so the change can be shown rather than discovered.

**It does not choose a transport tier.** A document is lifted to the current schema
before any game data is looked at -- that is what makes a share code migrate on its
own -- so there is no catalogue here to say which belt is the faster. The trunk
therefore inherits the tier its own lines already use, taking the most common one,
and where that turns out too small for the sum it now carries, the ordinary line
diagnostic says so and names the tier to move to. Guessing would be worse: it is a
figure the user chose.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Final

from satisplanner.core import constants, formatting
from satisplanner.core.graph import (
    Edge,
    FactoryGraph,
    MergerNode,
    Node,
    SplitterNode,
    port_line_budget,
)

BRANCHES: Final = constants.ATTACHMENT_BRANCHES

# Clearance kept around an inserted node, in scene units. Not the canvas's own
# geometry -- the domain layer has never known how wide a box is drawn -- but the
# envelope a box cannot leave: the interface widens a node to fit its longest row
# and stops at 440, and the tallest -- a header, a subtitle over three lines and
# four item rows -- comes to a little under 170. Measured against the real drawn
# rectangles by ``tests/test_ui_canvas``, so the two cannot drift apart without a
# test saying so. The deployed band is deliberately not counted: it is a way of
# looking at a node, switched on and off, and not the size of anything.
CLEAR_X: Final = 460.0
CLEAR_Y: Final = 170.0
# How far a colliding node is pushed before the placement is tried again.
NUDGE_Y: Final = 190.0
# Smallest gap between two levels of a tree, and between the first level and the
# node it hangs off. A tree is drawn between its source and its targets when there
# is room for it and past them when there is not -- overshooting reads as a longer
# line, whereas crowding reads as two boxes on top of each other.
STEP_X: Final = CLEAR_X


@dataclass(frozen=True)
class Fan:
    """One attachment and what hangs off it. A leaf is the index of a line."""

    children: tuple["Fan | int", ...]

    @property
    def size(self) -> int:
        """How many lines this subtree ends in."""
        return sum(1 if isinstance(child, int) else child.size for child in self.children)

    def attachments(self) -> int:
        return 1 + sum(child.attachments() for child in self.children if isinstance(child, Fan))


def share_tree(count: int) -> "Fan | int":
    """The tree a player builds to reach ``count`` lines from one port.

    Halve or third as long as the count allows, which is what makes two, three,
    four, six, nine and their products come out exactly even. When neither divides
    -- five, seven, eleven -- fan out once into as many equal groups as the
    building has branches and carry on inside each: that is the shape a player ends
    up with, and the shares it gives are the shares the game gives.
    """
    return _fan(0, count)


def _fan(first: int, count: int) -> "Fan | int":
    if count <= 1:
        return first
    if count <= BRANCHES:
        return Fan(tuple(first + offset for offset in range(count)))
    for branch in (BRANCHES, 2):
        if count % branch == 0:
            size = count // branch
            return Fan(tuple(_fan(first + index * size, size) for index in range(branch)))
    start = first
    children: list[Fan | int] = []
    for size in even_groups(count, BRANCHES):
        children.append(_fan(start, size))
        start += size
    return Fan(tuple(children))


def even_groups(count: int, groups: int) -> list[int]:
    """``count`` split into ``groups`` parts as equal as they can be, biggest first."""
    base, extra = divmod(count, groups)
    return [base + (1 if index < extra else 0) for index in range(groups)]


def leaf_shares(tree: "Fan | int") -> dict[int, float]:
    """What fraction of the trunk each leaf ends up with.

    A splitter gives every branch it has connected an equal turn, so a leaf's share
    is the product of one over the branch count all the way down. It is the nominal
    share and not a promise: max-min fairness hands what one branch cannot take back
    to the others, exactly as the building does.
    """
    shares: dict[int, float] = {}
    _accumulate(tree, 1.0, shares)
    return shares


def _accumulate(tree: "Fan | int", share: float, shares: dict[int, float]) -> None:
    if isinstance(tree, int):
        shares[tree] = share
        return
    each = share / len(tree.children)
    for child in tree.children:
        _accumulate(child, each, shares)


# --------------------------------------------------------------------------- #
# What one port had to gain
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PortChange:
    """One port that had more lines than ports, and the tree that fixed it."""

    node_id: str
    item_class: str
    is_output: bool
    # Ports the node really has for this item, and lines that were on them.
    ports: int
    lines: int
    inserted: tuple[str, ...]
    # The nominal share of each line, in the order the lines were taken. Before,
    # every line had ``1 / lines``.
    shares: tuple[float, ...]

    @property
    def was_even(self) -> bool:
        """True when the tree gives the equal shares the old model assumed."""
        equal = 1.0 / self.lines
        return all(abs(share - equal) <= 1e-12 for share in self.shares)

    @property
    def role(self) -> str:
        return "répartiteur" if self.is_output else "groupeur"


@dataclass(frozen=True)
class Materialisation:
    """Everything a document gained, and everything it is worth being told."""

    changes: tuple[PortChange, ...] = ()

    @property
    def inserted(self) -> int:
        return sum(len(change.inserted) for change in self.changes)

    @property
    def splitters(self) -> int:
        return sum(len(change.inserted) for change in self.changes if change.is_output)

    @property
    def mergers(self) -> int:
        return sum(len(change.inserted) for change in self.changes if not change.is_output)

    @property
    def uneven(self) -> tuple[PortChange, ...]:
        """Ports whose lines no longer carry the same thing as each other."""
        return tuple(change for change in self.changes if not change.was_even)

    def notes(self) -> list[str]:
        """What to show the user on opening, in French, shortest useful form.

        Written as sentences rather than a count because the point is that the
        figures may have moved and where. The asymmetry between the two halves is
        stated once, here, so that nobody goes looking for a bug in the other one:
        a share is decided by the shape of its tree as soon as the source is what
        limits it, which is the normal state of a factory sized to fit, whereas a
        merge is decided by its shape only when the line leaving it is saturated,
        which is rare and already diagnosed on its own.
        """
        if not self.changes:
            return []
        notes = [
            f"{self.inserted} raccord(s) matérialisé(s) : {self.splitters} répartiteur(s) "
            f"et {self.mergers} groupeur(s). Ils étaient jusqu'ici déduits des lignes ; "
            f"ils sont maintenant des nœuds, comptés dans la liste de courses."
        ]
        uneven = self.uneven
        if not uneven:
            notes.append(
                "Tous les partages se font en arbre équilibré : les débits sont "
                "identiques à ceux d'avant conversion."
            )
            return notes
        notes.append(
            "Un partage en arbre réel ne donne des parts égales que lorsque le nombre "
            "de lignes se ramène à des moitiés et des tiers. Les ports suivants n'en "
            "font pas partie et leurs débits changent :"
        )
        notes.extend(f"    {sentence}" for sentence in (_describe(change) for change in uneven))
        if any(not change.is_output for change in uneven):
            notes.append(
                "Un groupage ne décide des débits que si la ligne qui en sort est "
                "saturée, ce qui est rare et déjà signalé à part ; un partage en décide "
                "dès que la source est la contrainte, ce qui est le cas courant."
            )
        return notes


def _describe(change: PortChange) -> str:
    before = formatting.percent(1.0 / change.lines)
    after = ", ".join(formatting.percent(share) for share in change.shares)
    side = "sortie" if change.is_output else "entrée"
    return (
        f"{change.node_id}, {side} de {change.lines} lignes sur {change.ports} port(s) : "
        f"{before} chacune auparavant, {after} désormais."
    )


# --------------------------------------------------------------------------- #
# Putting the trees in
# --------------------------------------------------------------------------- #


def materialise(graph: FactoryGraph) -> Materialisation:
    """Insert every splitter and merger the layout already implied. Edits in place.

    Deterministic to the identifier: ports are walked in node order then item
    order, lines are taken in the order their far end sits on the canvas, and the
    identifiers handed out follow. Two runs on the same document therefore produce
    the same document, which is what lets a share code be migrated on one machine
    and compared on another.
    """
    changes: list[PortChange] = []
    taken = {node.id for node in graph.nodes}
    for node in graph.sorted_nodes():
        for is_output in (True, False):
            for item_class, edges in _crowded_ports(graph, node, is_output=is_output):
                changes.append(_rewire(graph, node, item_class, edges, taken, is_output=is_output))
    return Materialisation(tuple(changes))


def _crowded_ports(
    graph: FactoryGraph, node: Node, *, is_output: bool
) -> list[tuple[str, list[Edge]]]:
    """Ports of this node carrying more lines than it has ports, in item order."""
    budget = port_line_budget(node, is_output=is_output)
    if budget is None:
        return []
    grouped: dict[str, list[Edge]] = {}
    for edge in graph.outgoing(node.id) if is_output else graph.incoming(node.id):
        grouped.setdefault(edge.item_class, []).append(edge)
    return [
        (item_class, sorted(edges, key=lambda edge: _far_end(graph, edge, is_output=is_output)))
        for item_class, edges in sorted(grouped.items())
        if len(edges) > budget
    ]


def _far_end(graph: FactoryGraph, edge: Edge, *, is_output: bool) -> tuple[float, float, str]:
    """Where the other end of this line sits, so a tree is built without crossings."""
    other = graph.node(edge.target if is_output else edge.source)
    return (other.position[1], other.position[0], edge.id)


def _rewire(
    graph: FactoryGraph,
    node: Node,
    item_class: str,
    edges: Sequence[Edge],
    taken: set[str],
    *,
    is_output: bool,
) -> PortChange:
    """Replace one crowded port's lines with the tree they should have gone through."""
    budget = port_line_budget(node, is_output=is_output)
    assert budget is not None  # _crowded_ports would not have returned it otherwise
    ports = min(budget, len(edges))
    trunk = _common_transport(edges)

    shares: dict[int, float] = {}
    inserted: list[str] = []
    start = 0
    occupied = [existing.position for existing in graph.nodes]
    for group in even_groups(len(edges), ports):
        block = list(edges[start : start + group])
        tree = share_tree(group)
        for leaf, share in leaf_shares(tree).items():
            shares[start + leaf] = share / ports
        planted = _plant(
            graph, node, item_class, block, tree, trunk, taken, occupied, is_output=is_output
        )
        inserted.extend(planted)
        start += group

    return PortChange(
        node_id=node.id,
        item_class=item_class,
        is_output=is_output,
        ports=ports,
        lines=len(edges),
        inserted=tuple(inserted),
        shares=tuple(shares[index] for index in range(len(edges))),
    )


def _plant(
    graph: FactoryGraph,
    node: Node,
    item_class: str,
    edges: Sequence[Edge],
    tree: "Fan | int",
    trunk: str,
    taken: set[str],
    occupied: list[tuple[float, float]],
    *,
    is_output: bool,
) -> list[str]:
    """Build one subtree between ``node`` and the far ends of ``edges``.

    Returns the identifiers of the nodes added. The lines that were there are taken
    out and laid again through the tree, keeping the tier each of them had: the leaf
    lines are the user's own and there is no reason to touch them.
    """
    if isinstance(tree, int):
        return []  # a single line needs no attachment: it already has a port of its own

    plan = _Planting(
        graph=graph,
        source=node,
        item_class=item_class,
        edges=edges,
        trunk=trunk,
        taken=taken,
        occupied=occupied,
        is_output=is_output,
        lanes=_lanes(graph, node, edges, is_output=is_output),
    )
    root, _ = plan.grow(tree, depth=0)
    for edge in edges:
        graph.remove_edge(edge.id)
    _join(graph, node.id, root, item_class, trunk, is_output=is_output)
    return plan.added


@dataclass
class _Planting:
    """One subtree being built, and everything its placement depends on."""

    graph: FactoryGraph
    source: Node
    item_class: str
    edges: Sequence[Edge]
    trunk: str
    taken: set[str]
    occupied: list[tuple[float, float]]
    is_output: bool
    # Where the level nearest the source goes, and how far the next one is from it.
    # Both already signed: a merger's tree grows leftwards.
    lanes: tuple[float, float]

    def __post_init__(self) -> None:
        self.added: list[str] = []

    def grow(self, tree: "Fan | int", *, depth: int) -> tuple[str, str]:
        """Build this subtree. Returns its root and the tier of the line into it.

        A leaf is one of the document's own nodes and the line into it keeps the
        tier the user gave it; an attachment is new and its trunk gets the tier
        :func:`_common_transport` settled on.
        """
        if isinstance(tree, int):
            edge = self.edges[tree]
            return (edge.target if self.is_output else edge.source), edge.transport_class

        children = [self.grow(child, depth=depth + 1) for child in tree.children]
        identifier = _fresh_id(self.source.id, self.taken, is_output=self.is_output)
        first, step = self.lanes
        # Centred on the lines it ends up serving, not on the children as placed:
        # a child pushed aside by something already on the canvas would otherwise
        # drag its whole branch after it, and the tree would lean.
        served = [self.edges[leaf] for leaf in _leaves(tree)]
        rows = [
            self.graph.node(edge.target if self.is_output else edge.source).position[1]
            for edge in served
        ]
        ideal = (first + step * depth, sum(rows) / len(rows))
        kind = SplitterNode if self.is_output else MergerNode
        placed = _clear(ideal, self.occupied)
        self.graph.add_node(kind(id=identifier, item_class=self.item_class, position=placed))
        self.occupied.append(placed)
        self.added.append(identifier)
        for child, transport in children:
            _join(
                self.graph,
                identifier,
                child,
                self.item_class,
                transport,
                is_output=self.is_output,
            )
        return identifier, self.trunk


def _join(
    graph: FactoryGraph, near: str, far: str, item_class: str, transport: str, *, is_output: bool
) -> None:
    """Lay a line between an attachment and its neighbour, the right way round."""
    source, target = (near, far) if is_output else (far, near)
    graph.edges.append(
        Edge(
            id=_fresh_edge_id(graph),
            source=source,
            target=target,
            item_class=item_class,
            transport_class=transport,
        )
    )


def _lanes(
    graph: FactoryGraph, node: Node, edges: Sequence[Edge], *, is_output: bool
) -> tuple[float, float]:
    """Where the first level of the tree goes, and how far apart the levels are.

    Between the node and the nearest of the far ends when there is room, so that a
    tidy factory stays tidy and the tree reads left to right like everything else.
    A fixed step otherwise -- when the consumers are drawn to the left of what
    feeds them there is nothing to interpolate between, and a fixed step at least
    never puts two levels in the same place.
    """
    ends = [graph.node(edge.target if is_output else edge.source).position[0] for edge in edges]
    nearest = min(ends) if is_output else max(ends)
    towards = 1.0 if is_output else -1.0
    gap = (nearest - node.position[0]) * towards
    levels = max(_depth(edges), 1) + 1
    step = gap / levels
    if step >= STEP_X:
        return node.position[0] + step * towards, step * towards
    # Not enough room between the two columns for a level each. Everything then
    # goes in **one lane** halfway across, stacked apart by :func:`_clear`, rather
    # than marching past the consumers it feeds: a column of fittings between two
    # columns of machines is a shape a reader recognises, and a tree drawn to the
    # right of what it feeds is a handful of lines crossing back.
    return node.position[0] + (gap / 2) * towards, 0.0


def _depth(edges: Sequence[Edge]) -> int:
    tree = share_tree(len(edges))
    return 0 if isinstance(tree, int) else _tree_depth(tree)


def _leaves(tree: "Fan | int") -> list[int]:
    """Every line this subtree ends at, in order."""
    if isinstance(tree, int):
        return [tree]
    return [leaf for child in tree.children for leaf in _leaves(child)]


def _tree_depth(tree: "Fan | int") -> int:
    if isinstance(tree, int):
        return 0
    return 1 + max(_tree_depth(child) for child in tree.children)


def _clear(
    ideal: tuple[float, float], occupied: Iterable[tuple[float, float]]
) -> tuple[float, float]:
    """The nearest free spot to ``ideal``, moving down until nothing is in the way.

    The one damage of this lot no test will catch is a tidy factory coming back as a
    plate of noodles, so an inserted node never lands on an existing one. Down and
    not up, and always by the same step: a layout somebody has to recognise is worth
    more than one that is marginally more compact.
    """
    x, y = ideal
    places = list(occupied)
    for _ in range(200):
        if not any(abs(x - other[0]) < CLEAR_X and abs(y - other[1]) < CLEAR_Y for other in places):
            return (x, y)
        y += NUDGE_Y
    return (x, y)


def _common_transport(edges: Sequence[Edge]) -> str:
    """The tier the trunk gets: the one most of these lines already use.

    See the module docstring -- there is no catalogue here to rank tiers by what
    they carry, and inventing one would overwrite a choice the user made. Ties go
    to the first line in identifier order, so the answer never depends on how the
    document was assembled.
    """
    counted: dict[str, int] = {}
    for edge in edges:
        counted[edge.transport_class] = counted.get(edge.transport_class, 0) + 1
    return max(sorted(counted), key=lambda transport: counted[transport])


def _fresh_id(node_id: str, taken: set[str], *, is_output: bool) -> str:
    stem = f"{node_id}-{'rep' if is_output else 'grp'}"
    index = 1
    while f"{stem}{index}" in taken:
        index += 1
    taken.add(f"{stem}{index}")
    return f"{stem}{index}"


def _fresh_edge_id(graph: FactoryGraph) -> str:
    existing = {edge.id for edge in graph.edges}
    index = len(graph.edges) + 1
    while f"e{index}" in existing:
        index += 1
    return f"e{index}"


__all__ = [
    "Fan",
    "Materialisation",
    "PortChange",
    "even_groups",
    "leaf_shares",
    "materialise",
    "share_tree",
]
