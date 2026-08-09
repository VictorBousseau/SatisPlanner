"""The two modes, and the one claim that matters: nothing was lost going back.

Designing a factory and building one want different things. The simple mode is the
application as it was before fittings became nodes -- a port carries as many lines
as you draw, the max-min share happens there, and the splitters are worked out for
the shopping list. The faithful mode is the game's own rule.

The heaviest test here is :func:`test_the_reference_factories_are_untouched_in_simple_mode`
and it is worth its weight: it holds today's answers against a **snapshot taken from
the build that preceded the fittings**, field by field. If one figure has moved, the
return is incomplete, and no amount of reasoning about "the same code with fewer
nodes" would tell us so.
"""

import json
from pathlib import Path

import pytest

from satisplanner.core import attachments, engine
from satisplanner.core.graph import (
    AttachmentMode,
    Edge,
    ExternalSourceNode,
    FactoryGraph,
    MergerNode,
    OutputNode,
    SplitterNode,
    StorageNode,
)
from satisplanner.core.models import GameData, SplitterMode
from satisplanner.data import factory_file
from tests.conftest import load_graph

BELT = "Build_ConveyorBeltMk3_C"
INGOT = "Desc_IronIngot_C"

SNAPSHOT = Path(__file__).resolve().parent / "fixtures" / "reports_avant_lot4.json"


def fan(count: int, *, rate: float = 240.0) -> FactoryGraph:
    """A buffer with ``count`` lines off its single output port, in the simple mode."""
    graph = FactoryGraph()
    graph.add_node(ExternalSourceNode(id="src", item_class=INGOT, rate_per_minute=rate))
    graph.add_node(StorageNode(id="buffer", storage_class="Build_StorageContainerMk1_C"))
    graph.edges.append(
        Edge(id="e0", source="src", target="buffer", item_class=INGOT, transport_class=BELT)
    )
    for index in range(count):
        graph.add_node(
            OutputNode(id=f"out{index}", item_class=INGOT, position=(900.0, index * 220))
        )
        graph.edges.append(
            Edge(
                id=f"x{index}",
                source="buffer",
                target=f"out{index}",
                item_class=INGOT,
                transport_class=BELT,
            )
        )
    return graph


def received(graph: FactoryGraph, game_data: GameData, count: int) -> list[float]:
    report = engine.solve(graph, game_data)
    return [round(report.node(f"out{index}").inputs.get(INGOT, 0.0), 6) for index in range(count)]


# --------------------------------------------------------------------------- #
# The return is complete
# --------------------------------------------------------------------------- #


def test_a_new_factory_is_simple(game_data: GameData) -> None:
    """The default, because designing comes before building."""
    assert FactoryGraph().attachment_mode is AttachmentMode.SIMPLE
    assert not FactoryGraph().is_faithful


def test_the_reference_factories_are_untouched_in_simple_mode(game_data: GameData) -> None:
    """Every figure, against a snapshot the pre-fittings build produced itself.

    The fixtures are pre-fittings documents -- they load at their own schema and are
    not converted -- so this is the same question the specification asks: does the
    simple mode give back exactly what the application gave before the rule existed.
    """
    expected = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    assert expected, "l'instantané doit exister"
    compared = 0

    for name, before in sorted(expected.items()):
        graph = load_graph(name)
        if graph.is_faithful:
            # The four fixtures §4 and §5 rewrote are faithful documents now; the
            # snapshot holds their *old* flat shape, which is no longer what the
            # file contains. They are covered by the round-trip tests instead.
            continue
        report = engine.solve(graph, game_data)
        assert report.raw_solids == before["raw_solids"], name
        assert report.raw_fluids == before["raw_fluids"], name
        assert report.final_outputs == before["final_outputs"], name
        assert report.discarded_outputs == before["discarded_outputs"], name
        assert report.power_total_mw == before["power_total_mw"], name
        assert report.power_production_mw == before["power_production_mw"], name
        assert {edge.edge_id: edge.rate_per_minute for edge in report.edges} == before["edges"], (
            name
        )
        for node in report.nodes:
            was = before["nodes"][node.node_id]
            assert round(node.ratio, 9) == was["ratio"], f"{name} / {node.node_id}"
            assert node.inputs == was["inputs"], f"{name} / {node.node_id}"
            assert node.outputs == was["outputs"], f"{name} / {node.node_id}"
            assert node.power_mw == was["power_mw"], f"{name} / {node.node_id}"
        assert report.shopping_list.buildings == before["shopping_list"]["buildings"], name
        assert report.shopping_list.attachments == before["shopping_list"]["attachments"], name
        compared += 1

    # Without this the test would pass just as happily on nothing at all. Fifteen
    # of the eighteen: the three §4 rewrote into faithful documents are covered by
    # the round-trip tests instead, since the file no longer holds the flat shape
    # the snapshot was taken from.
    assert compared == 15, f"15 usines à comparer, {compared} comparées"


def test_the_shopping_list_still_counts_the_fittings_nobody_drew(game_data: GameData) -> None:
    """The deduction is back, and it is the only thing the two modes disagree on."""
    graph = fan(4)
    report = engine.solve(graph, game_data)
    # Four lines off one port: a splitter serves three, so two units chained.
    assert report.shopping_list.attachments == {"Build_ConveyorAttachmentSplitter_C": 2}
    assert not any(
        node.kind in ("splitter", "merger") for node in graph.nodes
    ), "rien n'a été dessiné"


# --------------------------------------------------------------------------- #
# The bascule, both ways
# --------------------------------------------------------------------------- #


def test_a_balanced_share_survives_the_round_trip(game_data: GameData) -> None:
    """Four lines: even before, even as a tree, even again. No figure moves."""
    graph = fan(4)
    start = received(graph, game_data, 4)
    assert start == [60.0] * 4

    up = attachments.switch_mode(graph, AttachmentMode.FAITHFUL)
    assert graph.is_faithful
    assert any("arbre équilibré" in note for note in up)
    assert received(graph, game_data, 4) == start

    down = attachments.switch_mode(graph, AttachmentMode.SIMPLE)
    assert graph.attachment_mode is AttachmentMode.SIMPLE
    assert not [node for node in graph.nodes if isinstance(node, SplitterNode | MergerNode)]
    assert any("identiques" in note for note in down)
    assert received(graph, game_data, 4) == start


def test_an_uneven_share_is_reported_in_both_directions(game_data: GameData) -> None:
    """Five lines cannot be halved or thirded, so the figures move -- and it is said."""
    graph = fan(5, rate=300.0)
    flat = received(graph, game_data, 5)
    # Whatever gets through the trunk, a flat port shares it five equal ways.
    assert flat == [flat[0]] * 5 and flat[0] > 0

    up = "\n".join(attachments.switch_mode(graph, AttachmentMode.FAITHFUL))
    assert "20 %" in up, "l'ancienne part"
    assert "33,3 %" in up and "16,7 %" in up, "les nouvelles"
    tree = received(graph, game_data, 5)
    assert sorted(tree) != flat, "un arbre à cinq feuilles n'est pas égal"
    assert round(sum(tree), 6) == round(sum(flat), 6), "rien ne se perd en route"

    down = "\n".join(attachments.switch_mode(graph, AttachmentMode.SIMPLE))
    assert "33,3 %" in down and "20 %" in down, "l'écart est signalé dans l'autre sens aussi"
    assert received(graph, game_data, 5) == flat, "égal de nouveau"


def test_a_filtered_splitter_refuses_the_bascule_by_name(game_data: GameData) -> None:
    """A refusal, not a warning: a routing quietly deleted is worse than a menu that says no."""
    graph = fan(3)
    attachments.switch_mode(graph, AttachmentMode.FAITHFUL)
    splitter = next(node for node in graph.nodes if isinstance(node, SplitterNode))
    splitter.mode = SplitterMode.SMART
    splitter.filters = {graph.outgoing(splitter.id)[0].target: INGOT}

    assert attachments.non_standard_splitters(graph) == [splitter.id]
    with pytest.raises(attachments.ModeRefusedError, match=splitter.id):
        attachments.switch_mode(graph, AttachmentMode.SIMPLE)
    assert graph.is_faithful, "rien n'a bougé"

    # Put it back to standard and the bascule goes through.
    splitter.mode = SplitterMode.STANDARD
    splitter.filters = {}
    attachments.switch_mode(graph, AttachmentMode.SIMPLE)
    assert graph.attachment_mode is AttachmentMode.SIMPLE


def test_dissolving_keeps_the_narrowest_tier_of_the_path(game_data: GameData) -> None:
    """A chain carries what its narrowest hop carries, so the line that replaces it does."""
    graph = fan(2)
    attachments.switch_mode(graph, AttachmentMode.FAITHFUL)
    splitter = next(node for node in graph.nodes if isinstance(node, SplitterNode))
    trunk = graph.incoming(splitter.id)[0]
    trunk.transport_class = "Build_ConveyorBeltMk1_C"

    attachments.switch_mode(graph, AttachmentMode.SIMPLE)
    assert {edge.transport_class for edge in graph.outgoing("buffer")} == {
        "Build_ConveyorBeltMk1_C"
    }


# --------------------------------------------------------------------------- #
# The mode travels
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("mode", list(AttachmentMode))
def test_the_mode_survives_a_share_code(mode: AttachmentMode) -> None:
    graph = fan(2)
    graph.attachment_mode = mode
    back = factory_file.decode_share_code(factory_file.encode_share_code(graph)).graph
    assert back.attachment_mode is mode


@pytest.mark.parametrize("mode", list(AttachmentMode))
def test_the_mode_survives_a_file(tmp_path: Path, mode: AttachmentMode) -> None:
    graph = fan(2)
    graph.attachment_mode = mode
    path = tmp_path / f"usine{factory_file.FILE_SUFFIX}"
    factory_file.save(path, graph)
    assert factory_file.load(path).graph.attachment_mode is mode


def test_a_document_from_before_the_fittings_opens_simple() -> None:
    """No forced conversion: it opens in the mode it was written under."""
    graph = fan(4)
    payload = graph.model_dump(mode="json")
    payload.pop("attachment_mode")
    lifted, notes = factory_file.migrate(payload, 4)
    back = FactoryGraph.model_validate(lifted)
    assert back.attachment_mode is AttachmentMode.SIMPLE
    assert not [node for node in back.nodes if isinstance(node, SplitterNode)]
    assert not any("fidèle" in note for note in notes)


def test_a_document_of_the_v2_schema_opens_faithful() -> None:
    """It carries drawn fittings, so it was written under the rule and keeps it."""
    graph = fan(4)
    attachments.switch_mode(graph, AttachmentMode.FAITHFUL)
    payload = graph.model_dump(mode="json")
    payload.pop("attachment_mode")
    lifted, notes = factory_file.migrate(payload, 6)
    assert FactoryGraph.model_validate(lifted).attachment_mode is AttachmentMode.FAITHFUL
    assert any("fidèle" in note for note in notes)
