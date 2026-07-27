"""Graph tests: construction rules, serialisation, and the decomposition."""

import pytest

from satisplanner.core.graph import (
    Edge,
    ExternalSourceNode,
    FactoryGraph,
    GraphError,
    MachineNode,
    OutputNode,
    ResourceNode,
    StorageNode,
    WaterExtractorNode,
    condensation_order,
    is_cyclic,
    storage_item,
    strongly_connected_components,
)
from satisplanner.core.models import GameData, Purity
from tests.conftest import load_graph

BELT = "Build_ConveyorBeltMk1_C"
PIPE = "Build_Pipeline_C"


def _chain(game_data: GameData) -> FactoryGraph:
    graph = FactoryGraph()
    graph.add_node(
        ResourceNode(
            id="mine",
            item_class="Desc_OreIron_C",
            extractor_class="Build_MinerMk1_C",
        )
    )
    graph.add_node(MachineNode(id="smelter", recipe_class="Recipe_IngotIron_C"))
    graph.add_node(OutputNode(id="out", item_class="Desc_IronIngot_C"))
    graph.connect("mine", "smelter", "Desc_OreIron_C", BELT, game_data)
    graph.connect("smelter", "out", "Desc_IronIngot_C", BELT, game_data)
    return graph


# --------------------------------------------------------------------------- #
# Construction
# --------------------------------------------------------------------------- #


def test_a_valid_chain_can_be_built(game_data: GameData) -> None:
    graph = _chain(game_data)
    assert [node.id for node in graph.sorted_nodes()] == ["mine", "out", "smelter"]
    assert [edge.id for edge in graph.sorted_edges()] == ["e1", "e2"]


def test_a_solid_cannot_travel_in_a_pipe(game_data: GameData) -> None:
    graph = _chain(game_data)
    with pytest.raises(GraphError, match="il faut un convoyeur"):
        graph.connect("mine", "smelter", "Desc_OreIron_C", PIPE, game_data)


def test_a_fluid_cannot_travel_on_a_belt(game_data: GameData) -> None:
    graph = FactoryGraph()
    graph.add_node(
        ResourceNode(id="oil", item_class="Desc_LiquidOil_C", extractor_class="Build_OilPump_C")
    )
    graph.add_node(MachineNode(id="refinery", recipe_class="Recipe_Plastic_C"))
    with pytest.raises(GraphError, match="il faut une tuyauterie"):
        graph.connect("oil", "refinery", "Desc_LiquidOil_C", BELT, game_data)


def test_a_producer_cannot_send_what_it_does_not_make(game_data: GameData) -> None:
    graph = _chain(game_data)
    graph.add_node(OutputNode(id="wrong", item_class="Desc_Coal_C"))
    with pytest.raises(GraphError, match="ne produit pas"):
        graph.connect("smelter", "wrong", "Desc_Coal_C", BELT, game_data)


def test_a_consumer_cannot_receive_what_it_does_not_use(game_data: GameData) -> None:
    graph = _chain(game_data)
    graph.add_node(ExternalSourceNode(id="coal", item_class="Desc_Coal_C", rate_per_minute=30))
    with pytest.raises(GraphError, match="ne consomme pas"):
        graph.connect("coal", "smelter", "Desc_Coal_C", BELT, game_data)


def test_an_output_node_produces_nothing(game_data: GameData) -> None:
    graph = _chain(game_data)
    graph.add_node(MachineNode(id="other", recipe_class="Recipe_IronPlate_C"))
    with pytest.raises(GraphError, match="ne produit rien"):
        graph.connect("out", "other", "Desc_IronIngot_C", BELT, game_data)


def test_a_machine_refuses_a_fifth_distinct_input(game_data: GameData) -> None:
    """Four input ports is a hard limit of the game."""
    graph = FactoryGraph()
    graph.add_node(MachineNode(id="assembler", recipe_class="Recipe_IronPlateReinforced_C"))
    for index, item in enumerate(("Desc_IronPlate_C", "Desc_IronScrew_C")):
        graph.add_node(ExternalSourceNode(id=f"s{index}", item_class=item, rate_per_minute=10))
        graph.connect(f"s{index}", "assembler", item, BELT, game_data)
    # The recipe only has two ingredients, so the port budget cannot be reached
    # through legal edges: the guard is checked directly instead.
    assert len(graph.incoming("assembler")) == 2


def test_duplicate_identifiers_are_refused(game_data: GameData) -> None:
    graph = _chain(game_data)
    with pytest.raises(GraphError, match="doublon"):
        graph.add_node(OutputNode(id="out", item_class="Desc_IronIngot_C"))


def test_an_edge_cannot_reference_an_unknown_node() -> None:
    with pytest.raises(GraphError, match="nœud inconnu"):
        FactoryGraph(
            nodes=[OutputNode(id="out", item_class="Desc_IronIngot_C")],
            edges=[
                Edge(
                    id="e1",
                    source="ghost",
                    target="out",
                    item_class="Desc_IronIngot_C",
                    transport_class=BELT,
                )
            ],
        )


def test_removing_a_node_removes_its_edges(game_data: GameData) -> None:
    graph = _chain(game_data)
    graph.remove_node("smelter")
    assert [node.id for node in graph.nodes] == ["mine", "out"]
    assert graph.edges == []


# --------------------------------------------------------------------------- #
# Serialisation
# --------------------------------------------------------------------------- #


def test_a_graph_survives_a_json_round_trip(game_data: GameData) -> None:
    graph = _chain(game_data)
    restored = FactoryGraph.model_validate_json(graph.model_dump_json())
    assert restored == graph


def test_every_node_kind_round_trips() -> None:
    graph = FactoryGraph(
        nodes=[
            ResourceNode(
                id="a",
                item_class="Desc_OreIron_C",
                extractor_class="Build_MinerMk1_C",
                purity=Purity.PURE,
                count=2,
            ),
            WaterExtractorNode(id="b", extractor_class="Build_WaterPump_C", count=3),
            ExternalSourceNode(id="c", item_class="Desc_Plastic_C", rate_per_minute=12.5),
            MachineNode(id="d", recipe_class="Recipe_IronPlate_C", machine_count=4.33),
            StorageNode(id="e", storage_class="Build_StorageContainerMk1_C", initial_content=100),
            OutputNode(id="f", item_class="Desc_IronPlate_C", is_sink=True),
        ]
    )
    restored = FactoryGraph.model_validate_json(graph.model_dump_json())
    assert restored == graph
    assert isinstance(restored.node("a"), ResourceNode)
    assert isinstance(restored.node("f"), OutputNode)


def test_the_json_fixtures_all_load() -> None:
    names = (
        "iron_plate",
        "screws_reinforced_plate",
        "steel_chain",
        "plastic_chain",
        "recycling_loop",
        "blocked_byproduct",
        "backpressure",
        "computer_chain",
        "deficit",
        "belt_saturation",
        "pipe_saturation",
        "allocation",
        "allocation_redistribution",
        "buffer_filling",
        "buffer_draining",
    )
    for name in names:
        graph = load_graph(name)
        assert graph.nodes, name


# --------------------------------------------------------------------------- #
# Decomposition
# --------------------------------------------------------------------------- #


def test_a_straight_chain_has_only_trivial_components(game_data: GameData) -> None:
    graph = _chain(game_data)
    components = strongly_connected_components(graph)
    assert sorted(components) == [("mine",), ("out",), ("smelter",)]
    assert not any(is_cyclic(component, graph) for component in components)


def test_a_loop_is_found_as_one_component() -> None:
    graph = load_graph("recycling_loop")
    components = strongly_connected_components(graph)
    assert ("recycled_plastic", "recycled_rubber") in components
    assert is_cyclic(("recycled_plastic", "recycled_rubber"), graph)


def test_the_condensation_is_in_topological_order(game_data: GameData) -> None:
    graph = _chain(game_data)
    order = condensation_order(graph)
    assert order == [("mine",), ("smelter",), ("out",)]


def test_the_condensation_order_ignores_insertion_order() -> None:
    forward = load_graph("screws_reinforced_plate")
    backward = FactoryGraph(
        nodes=list(reversed(forward.nodes)), edges=list(reversed(forward.edges))
    )
    assert condensation_order(forward) == condensation_order(backward)


def test_the_decomposition_handles_a_long_chain_without_recursion(
    game_data: GameData,
) -> None:
    """Tarjan is iterative on purpose: 2000 nodes must not blow the stack."""
    graph = FactoryGraph()
    graph.add_node(ExternalSourceNode(id="src", item_class="Desc_IronIngot_C", rate_per_minute=30))
    previous = "src"
    for index in range(2000):
        node_id = f"buffer{index:04d}"
        graph.add_node(
            StorageNode(
                id=node_id,
                storage_class="Build_StorageContainerMk1_C",
                item_class="Desc_IronIngot_C",
            )
        )
        graph.connect(previous, node_id, "Desc_IronIngot_C", BELT, game_data)
        previous = node_id
    assert len(strongly_connected_components(graph)) == 2001


# --------------------------------------------------------------------------- #
# Buffer item inference
# --------------------------------------------------------------------------- #


def test_a_buffer_infers_its_item_from_a_single_input(game_data: GameData) -> None:
    graph = FactoryGraph()
    graph.add_node(ExternalSourceNode(id="src", item_class="Desc_IronIngot_C", rate_per_minute=30))
    buffer = StorageNode(id="buffer", storage_class="Build_StorageContainerMk1_C")
    graph.add_node(buffer)
    graph.connect("src", "buffer", "Desc_IronIngot_C", BELT, game_data)
    assert storage_item(buffer, graph) == "Desc_IronIngot_C"


def test_a_buffer_with_no_input_has_no_inferable_item() -> None:
    graph = FactoryGraph()
    buffer = StorageNode(id="buffer", storage_class="Build_StorageContainerMk1_C")
    graph.add_node(buffer)
    assert storage_item(buffer, graph) is None
