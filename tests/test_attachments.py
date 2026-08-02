"""Splitters and mergers: the port budget, the trees, and the migration into them.

The lot's whole claim is that a port carries one line and that what a document was
missing is a tree of splitters. Two things are therefore checked at every turn: the
budget is refused with a reason, and the tree gives the shares a real tree gives --
exactly the old ones where the arithmetic allows and different ones, named, where it
does not.
"""

from fractions import Fraction

import pytest

from satisplanner.core import attachments, constants, engine
from satisplanner.core.graph import (
    ExternalSourceNode,
    FactoryGraph,
    GraphError,
    MachineNode,
    MergerNode,
    OutputNode,
    SplitterNode,
    StorageNode,
    port_line_budget,
)
from satisplanner.core.models import GameData
from satisplanner.data import factory_file
from tests.conftest import load_graph

BELT = "Build_ConveyorBeltMk3_C"
SMALL_BELT = "Build_ConveyorBeltMk1_C"
PIPE = "Build_PipelineMK2_C"
INGOT = "Desc_IronIngot_C"
ORE = "Desc_OreIron_C"


def fed_bank(game_data: GameData, machines: float) -> FactoryGraph:
    """A bank of ``machines`` smelters, fed, with nothing on its output yet."""
    graph = FactoryGraph()
    graph.add_node(ExternalSourceNode(id="src", item_class=ORE, rate_per_minute=600))
    graph.add_node(
        MachineNode(id="bank", recipe_class="Recipe_IngotIron_C", machine_count=machines)
    )
    graph.connect("src", "bank", ORE, BELT, game_data)
    return graph


def exits(graph: FactoryGraph, game_data: GameData, source: str, count: int) -> int:
    """Wire ``count`` more exits to ``source``; returns how many were accepted."""
    accepted = 0
    for index in range(count):
        name = f"{source}_out{len(graph.nodes)}_{index}"
        graph.add_node(OutputNode(id=name, item_class=INGOT))
        try:
            graph.connect(source, name, INGOT, BELT, game_data)
        except GraphError:
            graph.remove_node(name)
            break
        accepted += 1
    return accepted


# --------------------------------------------------------------------------- #
# The budget
# --------------------------------------------------------------------------- #


def test_a_port_carries_one_line_per_building(game_data: GameData) -> None:
    """One machine, one line. The refusal names the building that fixes it."""
    graph = fed_bank(game_data, 1)
    assert exits(graph, game_data, "bank", 3) == 1
    with pytest.raises(GraphError, match=r"1 port.*Lingot de fer.*répartiteur"):
        graph.add_node(OutputNode(id="extra", item_class=INGOT))
        graph.connect("bank", "extra", INGOT, BELT, game_data)


def test_the_budget_follows_the_machine_count(game_data: GameData) -> None:
    """Take a node from one machine to three and a second and a third line fit.

    The point of counting ports rather than lines: a bank of eight smelters has
    eight outputs, and eight consumers hung off it need no splitter at all.
    """
    graph = fed_bank(game_data, 1)
    assert exits(graph, game_data, "bank", 3) == 1

    graph.node("bank").machine_count = 3
    assert exits(graph, game_data, "bank", 3) == 2, "les deux places libérées, pas une de plus"
    assert len(graph.outgoing("bank")) == 3


def test_a_fractional_count_is_rounded_up(game_data: GameData) -> None:
    """Two and a half smelters are three buildings, so three ports."""
    graph = fed_bank(game_data, 2.5)
    assert port_line_budget(graph.node("bank"), is_output=True) == 3
    assert exits(graph, game_data, "bank", 5) == 3


def test_the_budget_is_per_item_and_per_direction(game_data: GameData) -> None:
    """A manufacturer has a port per slot, so two products do not share a budget."""
    graph = FactoryGraph()
    graph.add_node(ExternalSourceNode(id="oil", item_class="Desc_LiquidOil_C", rate_per_minute=300))
    graph.add_node(MachineNode(id="refinery", recipe_class="Recipe_Plastic_C", machine_count=1))
    graph.connect("oil", "refinery", "Desc_LiquidOil_C", PIPE, game_data)
    graph.add_node(OutputNode(id="plastic", item_class="Desc_Plastic_C"))
    graph.add_node(OutputNode(id="residue", item_class="Desc_HeavyOilResidue_C"))
    graph.connect("refinery", "plastic", "Desc_Plastic_C", BELT, game_data)
    # A second product on its own port, refused only when *that* port is full.
    graph.connect("refinery", "residue", "Desc_HeavyOilResidue_C", PIPE, game_data)
    graph.add_node(OutputNode(id="plastic2", item_class="Desc_Plastic_C"))
    with pytest.raises(GraphError, match="port"):
        graph.connect("refinery", "plastic2", "Desc_Plastic_C", BELT, game_data)


def test_a_boundary_has_no_budget_and_a_buffer_has_one(game_data: GameData) -> None:
    """The three cases the game data does not settle, decided and written down.

    An exit and an import are the edge of what is being modelled, not buildings, so
    demanding a merger in front of one would be ceremony with nothing behind it in
    game. A buffer is a building with one door each way, so it gets one.
    """
    assert port_line_budget(OutputNode(id="o", item_class=INGOT), is_output=False) is None
    assert port_line_budget(ExternalSourceNode(id="i", item_class=INGOT), is_output=True) is None
    buffer = StorageNode(id="b", storage_class="Build_StorageContainerMk1_C")
    assert port_line_budget(buffer, is_output=True) == 1
    assert port_line_budget(buffer, is_output=False) == 1


def test_a_splitter_has_one_input_and_three_outputs(game_data: GameData) -> None:
    graph = fed_bank(game_data, 1)
    graph.add_node(SplitterNode(id="fork"))
    graph.connect("bank", "fork", INGOT, BELT, game_data)
    assert exits(graph, game_data, "fork", 5) == constants.ATTACHMENT_BRANCHES

    graph.add_node(MachineNode(id="other", recipe_class="Recipe_IngotIron_C", machine_count=1))
    graph.add_node(ExternalSourceNode(id="more", item_class=ORE, rate_per_minute=60))
    graph.connect("more", "other", ORE, BELT, game_data)
    with pytest.raises(GraphError, match="groupeur"):
        graph.connect("other", "fork", INGOT, BELT, game_data)


def test_a_merger_is_the_mirror_image(game_data: GameData) -> None:
    graph = FactoryGraph()
    graph.add_node(MergerNode(id="join"))
    graph.add_node(OutputNode(id="out", item_class=INGOT))
    for index in range(4):
        graph.add_node(
            ExternalSourceNode(id=f"src{index}", item_class=INGOT, rate_per_minute=60)
        )
    for index in range(constants.ATTACHMENT_BRANCHES):
        graph.connect(f"src{index}", "join", INGOT, BELT, game_data)
    with pytest.raises(GraphError, match="groupeur"):
        graph.connect("src3", "join", INGOT, BELT, game_data)
    graph.connect("join", "out", INGOT, BELT, game_data)


def test_a_fitting_carries_one_item(game_data: GameData) -> None:
    """Undeclared, it takes the item of its first line and refuses any other."""
    graph = fed_bank(game_data, 1)
    graph.add_node(SplitterNode(id="fork"))
    graph.connect("bank", "fork", INGOT, BELT, game_data)
    graph.add_node(
        ExternalSourceNode(id="copper", item_class="Desc_CopperIngot_C", rate_per_minute=60)
    )
    with pytest.raises(GraphError, match="qu'un item"):
        graph.connect("copper", "fork", "Desc_CopperIngot_C", BELT, game_data)


def test_a_line_keeps_its_place_when_only_its_tier_changes(game_data: GameData) -> None:
    """Changing a belt's tier is not asking for a second belt on the same port."""
    graph = fed_bank(game_data, 1)
    graph.add_node(OutputNode(id="out", item_class=INGOT))
    edge = graph.connect("bank", "out", INGOT, BELT, game_data)
    replacement = edge.model_copy(update={"transport_class": SMALL_BELT})
    from satisplanner.core.graph import check_edge

    check_edge(graph, replacement, game_data)  # does not raise


# --------------------------------------------------------------------------- #
# The shape of the tree
# --------------------------------------------------------------------------- #


def shares_of(count: int) -> list[Fraction]:
    tree = attachments.share_tree(count)
    values = attachments.leaf_shares(tree)
    return sorted(
        (Fraction(value).limit_denominator(10**6) for value in values.values()), reverse=True
    )


@pytest.mark.parametrize("count", [2, 3, 4, 6, 8, 9, 12])
def test_a_share_that_halves_and_thirds_comes_out_even(count: int) -> None:
    """Two, three, four, six, nine and their products: the same figures as before."""
    assert shares_of(count) == [Fraction(1, count)] * count


@pytest.mark.parametrize("count", [5, 7, 10, 11])
def test_a_share_that_does_not_is_uneven_and_still_adds_up(count: int) -> None:
    """Five cannot be halved or thirded, so a real tree does not give fifths."""
    shares = shares_of(count)
    assert sum(shares) == 1, "rien ne se perd dans l'arbre"
    assert shares != [Fraction(1, count)] * count


def test_five_lines_give_a_third_and_four_sixths() -> None:
    """The exact shape, so that a change to the rule is a change to this line.

    One splitter three ways, two of its branches split again in two. It is what a
    player builds and it is what the game then does with it.
    """
    assert shares_of(5) == [
        Fraction(1, 3),
        Fraction(1, 6),
        Fraction(1, 6),
        Fraction(1, 6),
        Fraction(1, 6),
    ]


def test_the_engine_gives_the_shares_the_tree_promises(game_data: GameData) -> None:
    """The arithmetic above, checked against the solver rather than assumed.

    Five consumers behind one machine: a third to one, a sixth to each of the rest.
    """
    graph = FactoryGraph()
    graph.add_node(ExternalSourceNode(id="src", item_class=INGOT, rate_per_minute=180))
    graph.add_node(SplitterNode(id="top"))
    graph.connect("src", "top", INGOT, BELT, game_data)
    for branch in ("a", "b"):
        graph.add_node(SplitterNode(id=branch))
        graph.connect("top", branch, INGOT, BELT, game_data)
    for index, parent in enumerate(("a", "a", "b", "b", "top")):
        graph.add_node(
            MachineNode(id=f"m{index}", recipe_class="Recipe_IronPlate_C", machine_count=10)
        )
        graph.add_node(OutputNode(id=f"o{index}", item_class="Desc_IronPlate_C"))
        graph.connect(parent, f"m{index}", INGOT, BELT, game_data)
        graph.connect(f"m{index}", f"o{index}", "Desc_IronPlate_C", BELT, game_data)

    report = engine.solve(graph, game_data)
    received = sorted(report.node(f"m{index}").inputs[INGOT] for index in range(5))
    assert received == [30.0, 30.0, 30.0, 30.0, 60.0]
    assert sum(received) == 180.0, "un raccord ne perd rien"


def test_a_merger_shares_its_output_line_when_it_is_saturated(game_data: GameData) -> None:
    """The only case where the shape of a merge decides anything.

    Three sources of 180 into one Mk.1 belt: 60 leaves, so 20 comes in on each --
    which is the round robin the building does. Everywhere else a merge simply adds
    up, which is why the migration of merges moves almost no figures.
    """
    graph = FactoryGraph()
    graph.add_node(MergerNode(id="join"))
    graph.add_node(OutputNode(id="out", item_class=INGOT))
    for index in range(3):
        graph.add_node(ExternalSourceNode(id=f"s{index}", item_class=INGOT, rate_per_minute=180))
        graph.connect(f"s{index}", "join", INGOT, BELT, game_data)
    graph.connect("join", "out", INGOT, SMALL_BELT, game_data)

    report = engine.solve(graph, game_data)
    assert [round(report.edge(f"e{index}").rate_per_minute, 9) for index in (1, 2, 3)] == [
        20.0,
        20.0,
        20.0,
    ]
    assert report.node("join").inputs == report.node("join").outputs == {INGOT: 60.0}


def test_a_fitting_never_keeps_anything(game_data: GameData) -> None:
    """What goes in comes out, and what cannot come out does not go in.

    Conservation is not a rule bolted onto the solver: a splitter offers what it
    took and may take what it pushed, so the fixed point has no other solution.
    """
    graph = FactoryGraph()
    graph.add_node(ExternalSourceNode(id="src", item_class=INGOT, rate_per_minute=600))
    graph.add_node(SplitterNode(id="fork"))
    graph.connect("src", "fork", INGOT, BELT, game_data)
    graph.add_node(MachineNode(id="plates", recipe_class="Recipe_IronPlate_C", machine_count=2))
    graph.add_node(OutputNode(id="out", item_class="Desc_IronPlate_C"))
    graph.connect("fork", "plates", INGOT, BELT, game_data)
    graph.connect("plates", "out", "Desc_IronPlate_C", BELT, game_data)

    solution = engine.solve(graph, game_data).node("fork")
    assert solution.inputs == solution.outputs == {INGOT: 60.0}
    assert solution.ratio == 1.0, "un raccord ne tourne pas à un régime"


def test_a_fitting_with_nothing_behind_it_blocks_what_feeds_it(game_data: GameData) -> None:
    """A dead end is a dead end however many fittings stand in front of it."""
    graph = FactoryGraph()
    graph.add_node(ExternalSourceNode(id="src", item_class=ORE, rate_per_minute=60))
    graph.add_node(MachineNode(id="bank", recipe_class="Recipe_IngotIron_C", machine_count=1))
    graph.connect("src", "bank", ORE, BELT, game_data)
    graph.add_node(SplitterNode(id="fork"))
    graph.connect("bank", "fork", INGOT, BELT, game_data)

    report = engine.solve(graph, game_data)
    assert report.node("bank").blocked_products == (INGOT,)
    assert report.node("bank").ratio == 0.0
    assert any("aucune sortie au-delà de ce raccord" in item.message for item in report.diagnostics)


# --------------------------------------------------------------------------- #
# Migration
# --------------------------------------------------------------------------- #


# The schema before splitters became nodes. Named rather than derived from the
# current one: what this exercises is the step that inserts the trees, and that
# step will not move just because a later one is added.
BEFORE_ATTACHMENTS = 4


def migrated(graph: FactoryGraph) -> tuple[FactoryGraph, list[str]]:
    payload, notes = factory_file.migrate(graph.model_dump(mode="json"), BEFORE_ATTACHMENTS)
    return FactoryGraph.model_validate(payload), notes


def test_an_older_document_gains_the_tree_it_always_implied(game_data: GameData) -> None:
    """Four lines out of one machine become a balanced tree, figures untouched."""
    graph = FactoryGraph()
    graph.add_node(ExternalSourceNode(id="src", item_class=INGOT, rate_per_minute=240))
    graph.add_node(StorageNode(id="buffer", storage_class="Build_StorageContainerMk1_C"))
    graph.connect("src", "buffer", INGOT, BELT, game_data)
    for index in range(4):
        graph.add_node(
            OutputNode(id=f"out{index}", item_class=INGOT, position=(600.0, index * 200))
        )
        graph.edges.append(
            graph.edges[0].model_copy(
                update={"id": f"x{index}", "source": "buffer", "target": f"out{index}"}
            )
        )
    before = engine.solve(graph, game_data)

    lifted, notes = migrated(graph)
    after = engine.solve(lifted, game_data)

    assert sum(1 for node in lifted.nodes if isinstance(node, SplitterNode)) == 3
    assert after.final_outputs == before.final_outputs
    assert [round(after.node(f"out{index}").inputs[INGOT], 9) for index in range(4)] == [60.0] * 4
    assert any("arbre équilibré" in note for note in notes)


def test_an_uneven_share_is_named_with_its_old_and_new_figures(game_data: GameData) -> None:
    """Five lines cannot be shared evenly, so the note says so and says where."""
    graph = FactoryGraph()
    graph.add_node(ExternalSourceNode(id="src", item_class=INGOT, rate_per_minute=300))
    graph.add_node(StorageNode(id="buffer", storage_class="Build_StorageContainerMk1_C"))
    graph.connect("src", "buffer", INGOT, BELT, game_data)
    for index in range(5):
        graph.add_node(
            OutputNode(id=f"out{index}", item_class=INGOT, position=(600.0, index * 200))
        )
        graph.edges.append(
            graph.edges[0].model_copy(
                update={"id": f"x{index}", "source": "buffer", "target": f"out{index}"}
            )
        )

    _, notes = migrated(graph)
    written = "\n".join(notes)
    assert "buffer" in written
    assert "20 %" in written, "l'ancienne part de chaque ligne"
    assert "33,3 %" in written and "16,7 %" in written, "les nouvelles"
    assert "groupage" not in written, "rien n'a été groupé ici"


def test_migration_leaves_a_document_that_respects_the_budget(game_data: GameData) -> None:
    """The whole point, checked on every reference factory rather than argued."""
    for name in ("recycling_loop", "buffer_to_sink", "fuel_power", "computer_chain"):
        graph = load_graph(name)
        for node in graph.sorted_nodes():
            for is_output in (True, False):
                budget = port_line_budget(node, is_output=is_output)
                if budget is None:
                    continue
                lines = graph.outgoing(node.id) if is_output else graph.incoming(node.id)
                counted: dict[str, int] = {}
                for edge in lines:
                    counted[edge.item_class] = counted.get(edge.item_class, 0) + 1
                assert max(counted.values(), default=0) <= budget, f"{name} / {node.id}"


def test_migration_is_idempotent(game_data: GameData) -> None:
    """Opening an already-converted document adds nothing. It will happen."""
    once = load_graph("recycling_loop")
    twice, notes = migrated(once)
    assert len(twice.nodes) == len(once.nodes)
    assert not any("matérialisé" in note for note in notes)


def test_an_inserted_tree_lands_on_nobody(game_data: GameData) -> None:
    """The one damage of this lot no other test would catch.

    A migrated factory that comes back as a plate of noodles is not something anyone
    repairs by hand, so nothing inserted is allowed to sit on top of anything.
    """
    graph = FactoryGraph()
    graph.add_node(
        ExternalSourceNode(
            id="src", item_class=INGOT, rate_per_minute=600, position=(-700.0, 800.0)
        )
    )
    graph.add_node(
        StorageNode(
            id="buffer", storage_class="Build_StorageContainerMk1_C", position=(0.0, 800.0)
        )
    )
    graph.connect("src", "buffer", INGOT, BELT, game_data)
    for index in range(9):
        graph.add_node(
            OutputNode(id=f"out{index}", item_class=INGOT, position=(1600.0, index * 180))
        )
        graph.edges.append(
            graph.edges[0].model_copy(
                update={"id": f"x{index}", "source": "buffer", "target": f"out{index}"}
            )
        )
    before = {node.id for node in graph.nodes}
    lifted, _ = migrated(graph)

    inserted = [node for node in lifted.sorted_nodes() if node.id not in before]
    assert inserted, "il devait y avoir un arbre à poser"
    for fitting in inserted:
        for other in lifted.sorted_nodes():
            if other.id == fitting.id:
                continue
            apart_x = abs(fitting.position[0] - other.position[0])
            apart_y = abs(fitting.position[1] - other.position[1])
            assert apart_x >= attachments.CLEAR_X or apart_y >= attachments.CLEAR_Y, (
                f"{fitting.id} en {fitting.position} tombe sur {other.id} en {other.position}"
            )


def test_the_trunk_keeps_the_tier_the_lines_already_used(game_data: GameData) -> None:
    """No catalogue is in reach during a migration, so no tier is invented."""
    graph = FactoryGraph()
    graph.add_node(ExternalSourceNode(id="src", item_class=INGOT, rate_per_minute=240))
    graph.add_node(StorageNode(id="buffer", storage_class="Build_StorageContainerMk1_C"))
    graph.connect("src", "buffer", INGOT, SMALL_BELT, game_data)
    for index in range(2):
        graph.add_node(
            OutputNode(id=f"out{index}", item_class=INGOT, position=(600.0, index * 200))
        )
        graph.edges.append(
            graph.edges[0].model_copy(
                update={
                    "id": f"x{index}",
                    "source": "buffer",
                    "target": f"out{index}",
                    "transport_class": BELT,
                }
            )
        )
    lifted, _ = migrated(graph)
    (splitter,) = [node for node in lifted.nodes if isinstance(node, SplitterNode)]
    (trunk,) = lifted.incoming(splitter.id)
    assert trunk.transport_class == BELT, "le palier des lignes qu'il remplace, pas un autre"
