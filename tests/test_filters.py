"""The three splitters: what may be written on a branch, and what it changes.

The claim this file exists to hold is that **the standard splitter is the case of
the programmable one where nothing has been written**. Not a resemblance and not a
second implementation kept in step by hand: the same code, reached the same way,
giving the same figures to the last bit. Everything else here is what the writing
adds -- a branch that only takes refusals, a branch filtered on something that
never comes past, and the routing of a byproduct that the old model could not say
at all.
"""

import pytest

from satisplanner.core import engine
from satisplanner.core.graph import (
    ANY_BRANCH,
    OVERFLOW_BRANCH,
    ExternalSourceNode,
    FactoryGraph,
    MachineNode,
    OutputNode,
    SplitterNode,
)
from satisplanner.core.models import AttachmentRole, GameData, SplitterMode
from satisplanner.core.results import DiagnosticCode, Severity
from tests.conftest import load_graph

BELT = "Build_ConveyorBeltMk3_C"
PIPE = "Build_PipelineMK2_C"
INGOT = "Desc_IronIngot_C"
PLATE = "Desc_IronPlate_C"
RESIN = "Desc_PolymerResin_C"


def fan_out(
    game_data: GameData,
    mode: SplitterMode,
    filters: dict[str, str],
    *,
    supply: float = 180.0,
    machines: float = 2.0,
) -> FactoryGraph:
    """One source, one splitter, three constructors of equal appetite.

    Two machines each is 60 ingots a minute each, so at 180 in the branches are
    exactly served and at 90 they are not: the same factory says one thing about
    sharing and another about priority depending on the figure it is given.
    """
    graph = FactoryGraph()
    graph.add_node(ExternalSourceNode(id="src", item_class=INGOT, rate_per_minute=supply))
    graph.add_node(SplitterNode(id="tri", mode=mode, filters=dict(filters)))
    graph.connect("src", "tri", INGOT, BELT, game_data)
    for name in ("un", "deux", "trois"):
        graph.add_node(
            MachineNode(id=name, recipe_class="Recipe_IronPlate_C", machine_count=machines)
        )
        graph.add_node(OutputNode(id=f"sortie_{name}", item_class=PLATE))
        graph.connect("tri", name, INGOT, BELT, game_data)
        graph.connect(name, f"sortie_{name}", PLATE, BELT, game_data)
    return graph


def _figures(report: object) -> list[tuple[object, ...]]:
    """Every solved number of a report, and none of the names.

    The two factories below really are different in one respect -- one is priced as
    a programmable splitter and reads as one on the canvas -- so comparing whole
    solutions would compare the label too. What must not differ is the arithmetic.
    """
    return [
        (node.node_id, node.ratio, node.limiting, node.inputs, node.outputs, node.power_mw)
        for node in report.nodes  # type: ignore[attr-defined]
    ]


def intake(graph: FactoryGraph, game_data: GameData) -> dict[str, float]:
    report = engine.solve(graph, game_data)
    return {name: report.node(name).inputs.get(INGOT, 0.0) for name in ("un", "deux", "trois")}


# --------------------------------------------------------------------------- #
# The standard splitter is the programmable one with nothing written on it
# --------------------------------------------------------------------------- #


def test_a_programmable_splitter_left_blank_is_a_standard_one(game_data: GameData) -> None:
    """Bit for bit, and on the whole report rather than on the rates alone.

    If this ever fails it means the modes have grown a code path of their own, and
    the first thing that would tell is a factory that uses none of them changing
    its figures on an upgrade.
    """
    for supply in (90.0, 180.0, 400.0):
        blank = fan_out(game_data, SplitterMode.STANDARD, {}, supply=supply)
        plain = engine.solve(blank, game_data)
        written = engine.solve(
            fan_out(
                game_data,
                SplitterMode.PROGRAMMABLE,
                {"un": ANY_BRANCH, "deux": ANY_BRANCH, "trois": ANY_BRANCH},
                supply=supply,
            ),
            game_data,
        )
        assert _figures(written) == _figures(plain), f"à {supply}/min"
        assert written.edges == plain.edges
        assert written.final_outputs == plain.final_outputs
        assert written.diagnostics == plain.diagnostics
    # The one thing that does differ, and it is a price rather than a rate.
    assert written.shopping_list.attachments == {
        "Build_ConveyorAttachmentSplitterProgrammable_C": 1
    }
    assert plain.shopping_list.attachments == {"Build_ConveyorAttachmentSplitter_C": 1}


def test_a_filter_on_what_the_line_carries_changes_nothing(game_data: GameData) -> None:
    """Writing the item down is saying out loud what was already true.

    A line carries one item, so a branch filtered on that item admits everything
    that ever reaches it. It reads as an intention on the canvas and it is one --
    but it is not a second behaviour.
    """
    written = intake(
        fan_out(game_data, SplitterMode.PROGRAMMABLE, {"un": INGOT, "deux": INGOT}), game_data
    )
    assert written == intake(fan_out(game_data, SplitterMode.STANDARD, {}), game_data)


# --------------------------------------------------------------------------- #
# Overflow
# --------------------------------------------------------------------------- #


def test_an_overflow_branch_takes_only_what_the_others_refused(game_data: GameData) -> None:
    """Ninety ingots for three machines that want sixty each.

    Shared, everyone gets thirty and nobody runs. With two branches on overflow the
    first is served whole and the other two share the sixty left over -- which is
    the difference between a factory that half-works everywhere and one that works
    where it was meant to.
    """
    shared = intake(fan_out(game_data, SplitterMode.STANDARD, {}, supply=90.0), game_data)
    assert shared == {"un": 30.0, "deux": 30.0, "trois": 30.0}

    ordered = intake(
        fan_out(
            game_data,
            SplitterMode.PROGRAMMABLE,
            {"deux": OVERFLOW_BRANCH, "trois": OVERFLOW_BRANCH},
            supply=90.0,
        ),
        game_data,
    )
    assert ordered == {"un": 60.0, "deux": 15.0, "trois": 15.0}


def test_overflow_changes_nothing_when_there_is_enough_for_everyone(
    game_data: GameData,
) -> None:
    """A priority only decides who goes without, so with enough it decides nothing."""
    plenty = {
        mode: intake(fan_out(game_data, mode, filters, supply=180.0), game_data)
        for mode, filters in (
            (SplitterMode.STANDARD, {}),
            (SplitterMode.PROGRAMMABLE, {"deux": OVERFLOW_BRANCH, "trois": OVERFLOW_BRANCH}),
        )
    }
    assert plenty[SplitterMode.STANDARD] == plenty[SplitterMode.PROGRAMMABLE]
    assert plenty[SplitterMode.STANDARD] == {"un": 60.0, "deux": 60.0, "trois": 60.0}


def test_a_smart_splitter_serves_its_one_written_branch_first(game_data: GameData) -> None:
    """One branch set, the rest tout-venant: the smart splitter's whole vocabulary."""
    graph = fan_out(game_data, SplitterMode.SMART, {"trois": OVERFLOW_BRANCH}, supply=90.0)
    assert intake(graph, game_data) == {"un": 45.0, "deux": 45.0, "trois": 0.0}
    assert (
        engine.solve(graph, game_data).shopping_list.attachments
        == {"Build_ConveyorAttachmentSplitterSmart_C": 1}
    )


# --------------------------------------------------------------------------- #
# A branch nothing goes down
# --------------------------------------------------------------------------- #


def test_a_branch_filtered_on_something_else_receives_nothing_and_says_so(
    game_data: GameData,
) -> None:
    """The one filter that really does close a branch, in this model.

    A line carries one item, so filtering a branch on anything else names something
    that never comes past. It is not refused -- somebody may be building towards it
    -- but it is not left silent either: the figures would simply read zero and
    look like a shortage.
    """
    graph = fan_out(game_data, SplitterMode.PROGRAMMABLE, {"trois": PLATE}, supply=180.0)
    report = engine.solve(graph, game_data)

    assert report.node("trois").inputs == {}
    # And the two open branches share the whole of it, not two thirds of it.
    assert report.node("un").inputs[INGOT] == 60.0
    assert report.node("deux").inputs[INGOT] == 60.0

    finding = next(item for item in report.diagnostics if item.code is DiagnosticCode.BRANCH_FILTER)
    assert finding.node_id == "tri"
    assert "Plaque de fer" in finding.message
    assert "Lingot de fer" in finding.message


def test_a_smart_splitter_refuses_to_be_a_programmable_one(game_data: GameData) -> None:
    """Two branches written on a building that writes on one."""
    graph = fan_out(
        game_data,
        SplitterMode.SMART,
        {"deux": OVERFLOW_BRANCH, "trois": OVERFLOW_BRANCH},
    )
    report = engine.solve(graph, game_data)
    finding = next(item for item in report.diagnostics if item.code is DiagnosticCode.BRANCH_FILTER)
    assert finding.severity is Severity.ERROR
    assert "programmable" in finding.message


def test_a_fluid_cannot_be_filtered_because_the_game_has_no_such_junction(
    game_data: GameData,
) -> None:
    """A choice of the game's, reported rather than modelled around."""
    graph = FactoryGraph()
    graph.add_node(ExternalSourceNode(id="puits", item_class="Desc_Water_C", rate_per_minute=120))
    graph.add_node(SplitterNode(id="tri", mode=SplitterMode.SMART))
    graph.connect("puits", "tri", "Desc_Water_C", PIPE, game_data)
    for index in (1, 2):
        graph.add_node(OutputNode(id=f"out{index}", item_class="Desc_Water_C"))
        graph.connect("tri", f"out{index}", "Desc_Water_C", PIPE, game_data)

    report = engine.solve(graph, game_data)
    finding = next(item for item in report.diagnostics if item.code is DiagnosticCode.BRANCH_FILTER)
    assert finding.severity is Severity.ERROR
    assert "jonction de pipeline filtrante" in finding.message
    # And it is not priced, because there is no such building to buy.
    assert report.shopping_list.attachments == {}


def test_a_setting_kept_for_a_branch_that_no_longer_exists_is_reported(
    game_data: GameData,
) -> None:
    graph = fan_out(game_data, SplitterMode.PROGRAMMABLE, {"disparu": OVERFLOW_BRANCH})
    report = engine.solve(graph, game_data)
    finding = next(item for item in report.diagnostics if item.code is DiagnosticCode.BRANCH_FILTER)
    assert "disparu" in finding.message


# --------------------------------------------------------------------------- #
# The case the lot exists for
# --------------------------------------------------------------------------- #


def test_a_byproduct_is_recycled_first_and_flared_with_what_is_left(
    game_data: GameData,
) -> None:
    """Three refineries make 90 resin a minute; one recycler can take 60 of it.

    Written down: the recycler first, then the standby, then the flare. The
    standby gets fifteen and the flare fifteen, and the recycler runs at a hundred
    per cent -- which is the whole point, and what the old model could not say.
    """
    report = engine.solve(load_graph("byproduct_routing"), game_data)
    assert report.converged

    assert report.node("carburant").outputs == {"Desc_LiquidFuel_C": 120.0, RESIN: 90.0}
    assert report.node("recyclage").inputs[RESIN] == 60.0
    assert report.node("recyclage").ratio == 1.0
    assert report.node("secours").inputs[RESIN] == 15.0
    assert report.node("torchere").inputs[RESIN] == 15.0
    assert report.discarded_outputs == {RESIN: 15.0}


def test_without_the_modes_the_same_factory_shares_instead_of_ordering(
    game_data: GameData,
) -> None:
    """The comparison that says what the writing bought, on the same factory.

    Shared, both recyclers run at three quarters and the flare gets nothing. There
    is nothing wrong with that answer -- it is more plastic, in fact -- but it is
    not the one that was asked for, and until this lot it was the only one the
    model could express.
    """
    graph = load_graph("byproduct_routing")
    splitter = graph.node("tri")
    assert isinstance(splitter, SplitterNode)
    splitter.mode = SplitterMode.STANDARD
    splitter.filters = {}

    report = engine.solve(graph, game_data)
    assert report.node("recyclage").inputs[RESIN] == 45.0
    assert report.node("secours").inputs[RESIN] == 45.0
    assert report.node("torchere").inputs.get(RESIN, 0.0) == 0.0


def test_the_shopping_list_follows_the_mode(game_data: GameData) -> None:
    """Three buildings, three prices: a programmable splitter is not cheap.

    Read from the game's own build recipes through §2, so the figures below are
    the game's and not a table maintained here.
    """
    costs = {}
    for mode in SplitterMode:
        attachment = game_data.attachment_for(
            game_data.item(INGOT).form, AttachmentRole.SPLIT, mode
        )
        assert attachment is not None
        costs[mode] = game_data.building_costs[attachment.class_name].amounts
    assert costs[SplitterMode.STANDARD] == {"Desc_Cable_C": 2.0, "Desc_IronPlate_C": 2.0}
    assert "Desc_Computer_C" in costs[SplitterMode.PROGRAMMABLE]
    assert costs[SplitterMode.SMART] != costs[SplitterMode.PROGRAMMABLE]


def test_the_reference_factories_do_not_move(game_data: GameData) -> None:
    """A lot that adds modes owes nothing to the factories that use none of them.

    Every reference factory is solved and every splitter in them is standard, so
    the branch machinery must be reached and must decide nothing. Checked here
    rather than argued: the diagnostics gain no finding and no rate moves.
    """
    for name in ("recycling_loop", "buffer_to_sink", "fuel_power", "computer_chain"):
        report = engine.solve(load_graph(name), game_data)
        assert not [
            item for item in report.diagnostics if item.code is DiagnosticCode.BRANCH_FILTER
        ], name


@pytest.mark.parametrize("mode", list(SplitterMode))
def test_every_mode_has_a_building_on_a_belt(game_data: GameData, mode: SplitterMode) -> None:
    graph = fan_out(game_data, mode, {})
    report = engine.solve(graph, game_data)
    assert len(report.shopping_list.attachments) == 1
