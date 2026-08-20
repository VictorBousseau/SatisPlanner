"""The resource well: one pressuriser, several satellites, a purity each.

The well is the first node in this application that is **two buildings**, and the
first whose purity is not a value but a tally. Both facts come from the same place:
a pressuriser opens several satellite nodes at once and the game does not promise
they match, so the rule a deposit lives by -- "the purity applies to every extractor
of this node" -- is simply false here.

That is why this is a node kind and not a wider :class:`ResourceNode`. Widening it
would have made the rule conditional on a field, and a rule with an exception
written into the model is not a rule any more. The tests below hold the two halves
apart: the tally decides the flow, the pressuriser decides the bill.
"""

import math

import pytest

from satisplanner.core import engine, planner
from satisplanner.core.graph import (
    SCHEMA_VERSION,
    AttachmentMode,
    FactoryGraph,
    GraphError,
    MachineNode,
    OutputNode,
    ResourceWellNode,
    unit_count,
)
from satisplanner.core.models import GameData, Purity
from satisplanner.core.results import Severity
from satisplanner.data import db, factory_file
from satisplanner.ui import edits
from satisplanner.ui.catalogue import EntryKind, build_entries
from satisplanner.ui.document import FactoryDocument

NITROGEN = "Desc_NitrogenGas_C"
SATELLITE = "Build_FrackingExtractor_C"
PRESSURISER = "Build_FrackingSmasher_C"
PIPE = "Build_PipelineMK2_C"


@pytest.fixture(scope="module")
def catalogue() -> GameData:
    """The shipped database: the well is read from the game files, not written here."""
    return db.load_game_data_from_file(db.default_database_path())


def well(**tally: int) -> ResourceWellNode:
    satellites = {Purity(name): count for name, count in tally.items()}
    return ResourceWellNode(
        id="puits", item_class=NITROGEN, extractor_class=SATELLITE, satellites=satellites
    )


def drained(
    node: ResourceWellNode, catalogue: GameData, mode: AttachmentMode = AttachmentMode.SIMPLE
) -> FactoryGraph:
    """The well, wired to an exit that absorbs everything it can put out."""
    graph = FactoryGraph(attachment_mode=mode)
    graph.add_node(node)
    graph.add_node(OutputNode(id="sortie", item_class=NITROGEN))
    graph.connect("puits", "sortie", NITROGEN, PIPE, game_data=catalogue)
    return graph


def well_of(graph: FactoryGraph) -> ResourceWellNode:
    node = graph.node("puits")
    assert isinstance(node, ResourceWellNode)
    return node


# --------------------------------------------------------------------------- #
# What the game files say
# --------------------------------------------------------------------------- #


def test_the_two_halves_are_in_the_catalogue(catalogue: GameData) -> None:
    satellite = catalogue.extractors[SATELLITE]
    assert satellite.rate_per_minute == 60, "1000 unités par seconde de fluide"
    assert satellite.has_purity is True
    assert satellite.activator_class == PRESSURISER
    assert catalogue.buildings[PRESSURISER].power_mw == 150
    assert catalogue.buildings[SATELLITE].power_mw == 0, "un satellite ne consomme rien"


def test_a_satellite_gives_thirty_sixty_or_a_hundred_and_twenty(catalogue: GameData) -> None:
    """The published figures, and none of them is written in this repository.

    They fall out of 60 m3/min and the purity multipliers the whole application
    already uses, which is the check that matters: had they needed a table of their
    own, the well would not have been the same kind of thing as a deposit.
    """
    satellite = catalogue.extractors[SATELLITE]
    rates = {purity: satellite.rate(purity) for purity in Purity}
    assert rates == {Purity.IMPURE: 30, Purity.NORMAL: 60, Purity.PURE: 120}


def test_a_well_is_offered_for_the_three_resources_that_have_one(catalogue: GameData) -> None:
    """Crude oil, nitrogen and water. Read from the data, never listed here."""
    offered = {
        entry.class_name
        for entry in build_entries(catalogue)
        if entry.kind is EntryKind.RESOURCE_WELL
    }
    assert offered == {"Desc_LiquidOil_C", NITROGEN, "Desc_Water_C"}


def test_the_satellite_is_never_offered_as_an_extractor_of_its_own(
    catalogue: GameData,
) -> None:
    """It cannot be placed alone, so the palette must not pretend otherwise."""
    lone = [
        entry
        for entry in build_entries(catalogue)
        if entry.kind is EntryKind.EXTRACTOR and entry.extractor_class == SATELLITE
    ]
    assert lone == []


# --------------------------------------------------------------------------- #
# The tally decides the flow
# --------------------------------------------------------------------------- #


def test_the_output_is_the_sum_over_the_tally(catalogue: GameData) -> None:
    """One impure, two normal, three pure: 30 + 120 + 360, not six times any one rate."""
    report = engine.solve(drained(well(impure=1, normal=2, pure=3), catalogue), catalogue)
    solution = next(node for node in report.nodes if node.node_id == "puits")
    assert solution.outputs[NITROGEN] == pytest.approx(510.0)


def test_a_well_of_one_purity_is_a_multiple_of_that_purity(catalogue: GameData) -> None:
    report = engine.solve(drained(well(pure=4), catalogue), catalogue)
    solution = next(node for node in report.nodes if node.node_id == "puits")
    assert solution.outputs[NITROGEN] == pytest.approx(480.0)


def test_the_satellites_are_what_the_node_counts() -> None:
    """Not the pressuriser: the satellites are what scales, and what has ports."""
    assert unit_count(well(impure=1, normal=2, pure=3)) == 6


# --------------------------------------------------------------------------- #
# The pressuriser decides the bill
# --------------------------------------------------------------------------- #


def test_all_of_the_power_is_the_pressuriser(catalogue: GameData) -> None:
    """Six satellites or one, the draw is the same: they are declared at zero."""
    small = engine.solve(drained(well(normal=1), catalogue), catalogue)
    large = engine.solve(drained(well(impure=1, normal=2, pure=3), catalogue), catalogue)
    assert small.power_total_mw == 150.0
    assert large.power_total_mw == 150.0


def test_overclocking_a_well_costs_what_the_pressuriser_costs(catalogue: GameData) -> None:
    """The only reading under which overclocking a well costs anything at all.

    The satellites draw nothing, so putting the clock on them would make a well
    free to overclock -- a figure the game plainly does not intend. The draw
    follows the pressuriser's own exponent, exactly as a machine's does.
    """
    graph = drained(well(normal=2), catalogue)
    well_of(graph).clock_speed = 2.5
    report = engine.solve(graph, catalogue)
    exponent = catalogue.buildings[PRESSURISER].power_exponent
    assert report.power_total_mw == pytest.approx(150.0 * 2.5**exponent)


def test_the_shopping_list_holds_both_buildings(catalogue: GameData) -> None:
    report = engine.solve(drained(well(impure=1, normal=2, pure=3), catalogue), catalogue)
    assert report.shopping_list.buildings[SATELLITE] == 6
    assert report.shopping_list.buildings[PRESSURISER] == 1


def test_one_pressuriser_however_many_satellites(catalogue: GameData) -> None:
    report = engine.solve(drained(well(normal=1), catalogue), catalogue)
    assert report.shopping_list.buildings == {SATELLITE: 1, PRESSURISER: 1}


# --------------------------------------------------------------------------- #
# The port rule, in the mode that enforces it
# --------------------------------------------------------------------------- #


def three_satellites_and_three_exits(catalogue: GameData) -> FactoryGraph:
    graph = drained(well(normal=3), catalogue, AttachmentMode.FAITHFUL)
    for index in (2, 3):
        graph.add_node(OutputNode(id=f"sortie{index}", item_class=NITROGEN))
        graph.connect("puits", f"sortie{index}", NITROGEN, PIPE, game_data=catalogue)
    return graph


def test_each_satellite_carries_one_line(catalogue: GameData) -> None:
    """A satellite has a pipe of its own, so three of them carry three lines."""
    assert len(three_satellites_and_three_exits(catalogue).edges) == 3


def test_a_fourth_line_on_three_satellites_is_refused(catalogue: GameData) -> None:
    graph = three_satellites_and_three_exits(catalogue)
    graph.add_node(OutputNode(id="sortie4", item_class=NITROGEN))
    with pytest.raises(GraphError):
        graph.connect("puits", "sortie4", NITROGEN, PIPE, game_data=catalogue)


# --------------------------------------------------------------------------- #
# What is diagnosed
# --------------------------------------------------------------------------- #


def test_a_well_with_no_satellite_is_named(catalogue: GameData) -> None:
    """It is not free: the pressuriser draws its 150 MW on an empty well."""
    report = engine.solve(drained(well(), catalogue), catalogue)
    assert [d for d in report.diagnostics if "satellite" in d.message]
    assert report.power_total_mw == 150.0


def test_a_well_sunk_into_something_that_has_none_is_an_error(catalogue: GameData) -> None:
    graph = FactoryGraph()
    graph.add_node(
        ResourceWellNode(
            id="puits",
            item_class="Desc_OreIron_C",
            extractor_class=SATELLITE,
            satellites={Purity.NORMAL: 1},
        )
    )
    report = engine.solve(graph, catalogue)
    errors = [d for d in report.diagnostics if d.severity is Severity.ERROR]
    assert any("puits de ressource" in d.message for d in errors)


# --------------------------------------------------------------------------- #
# The document
# --------------------------------------------------------------------------- #


def test_a_well_survives_the_share_code(catalogue: GameData) -> None:
    graph = drained(well(impure=1, normal=2, pure=3), catalogue)
    loaded = factory_file.decode_share_code(factory_file.encode_share_code(graph))
    assert loaded.is_clean
    assert well_of(loaded.graph).satellites == {
        Purity.IMPURE: 1,
        Purity.NORMAL: 2,
        Purity.PURE: 3,
    }


def test_the_document_schema_moved_for_it() -> None:
    """A build that cannot draw a well must refuse the file, not read past it."""
    assert SCHEMA_VERSION == 8


def test_lifting_a_document_that_has_no_well_changes_nothing() -> None:
    """The 7-to-8 step writes nothing: no older document could hold a well.

    The note saying the document was converted is the walk's own, not this step's,
    and it is what tells the reader the file was written by an earlier build.
    """
    payload: dict[str, object] = {"schema_version": 7, "nodes": [], "edges": []}
    lifted, notes = factory_file.migrate(dict(payload), 7)
    assert notes == ["document converti du schéma 7 au schéma 8"]
    assert lifted["nodes"] == []


# --------------------------------------------------------------------------- #
# Editing, by the door the interface uses
# --------------------------------------------------------------------------- #


def test_the_tally_is_set_one_purity_at_a_time(catalogue: GameData) -> None:
    document = FactoryDocument(catalogue)
    document.graph.add_node(well(normal=1))
    assert edits.set_satellites(document, "puits", Purity.PURE, 3) is None
    assert well_of(document.graph).satellites == {Purity.NORMAL: 1, Purity.PURE: 3}
    document.undo_stack.undo()
    assert well_of(document.graph).satellites == {Purity.NORMAL: 1}


def test_half_a_satellite_is_refused_rather_than_rounded(catalogue: GameData) -> None:
    document = FactoryDocument(catalogue)
    document.graph.add_node(well(normal=1))
    assert edits.set_satellites(document, "puits", Purity.NORMAL, 2.5) is not None
    assert edits.set_satellites(document, "puits", Purity.NORMAL, -1) is not None
    assert edits.set_satellites(document, "puits", Purity.NORMAL, math.nan) is not None


# --------------------------------------------------------------------------- #
# The generator
# --------------------------------------------------------------------------- #


def test_the_generator_sinks_a_well_for_nitrogen(catalogue: GameData) -> None:
    """Nitric acid was unreachable before this lot: nothing could pull nitrogen up."""
    graph = planner.build(catalogue, planner.plan(catalogue, "Desc_NitricAcid_C", 30.0))
    wells = [node for node in graph.nodes if isinstance(node, ResourceWellNode)]
    assert [node.item_class for node in wells] == [NITROGEN]
    assert wells[0].id == "puits-azote", "un puits n'est pas un gisement, jusque dans son nom"


def test_the_generator_uses_a_pump_for_water_and_a_derrick_for_oil(
    catalogue: GameData,
) -> None:
    """A well is two buildings and 150 MW: it is the answer only where nothing else is."""
    for target in ("Desc_Plastic_C", "Desc_Rubber_C"):
        graph = planner.build(catalogue, planner.plan(catalogue, target, 30.0))
        assert not [node for node in graph.nodes if isinstance(node, ResourceWellNode)]


def test_a_generated_well_is_a_whole_number_of_satellites(catalogue: GameData) -> None:
    """Even in the exact-ratio variant, where machines are allowed decimals."""
    graph = planner.build(catalogue, planner.plan(catalogue, "Desc_NitricAcid_C", 7.0))
    node = next(n for n in graph.nodes if isinstance(n, ResourceWellNode))
    assert all(count == int(count) for count in node.satellites.values())
    assert node.satellite_count >= 1


def test_a_factory_without_a_well_gained_nothing(catalogue: GameData) -> None:
    """The invariant of this series: adding a node kind moves no existing figure."""
    graph = FactoryGraph()
    graph.add_node(MachineNode(id="four", recipe_class="Recipe_IngotIron_C", machine_count=2))
    report = engine.solve(graph, catalogue)
    solution = next(node for node in report.nodes if node.node_id == "four")
    assert solution.extra_buildings == {}
