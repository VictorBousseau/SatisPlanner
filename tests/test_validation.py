"""Diagnostics: the right level, the right target, and a message that says what to do.

These are user-facing sentences, so they are asserted on their content: a diagnostic
without a number in it is not actionable.
"""

from satisplanner.core import engine
from satisplanner.core.graph import (
    ExternalSourceNode,
    FactoryGraph,
    MachineNode,
    OutputNode,
    ResourceNode,
    StorageNode,
)
from satisplanner.core.models import GameData
from satisplanner.core.results import Diagnostic, DiagnosticCode, Severity
from tests.conftest import load_graph

BELT = "Build_ConveyorBeltMk1_C"


def diagnostics_of(name: str, game_data: GameData) -> list[Diagnostic]:
    return list(engine.solve(load_graph(name), game_data).diagnostics)


def find(items: list[Diagnostic], code: DiagnosticCode) -> Diagnostic:
    for item in items:
        if item.code is code:
            return item
    msg = f"aucun diagnostic {code} ; obtenus : {[item.code for item in items]}"
    raise AssertionError(msg)


# --------------------------------------------------------------------------- #
# Levels and ordering
# --------------------------------------------------------------------------- #


def test_a_clean_factory_reports_nothing(game_data: GameData) -> None:
    assert diagnostics_of("iron_plate", game_data) == []


def test_a_blocked_byproduct_is_an_error_on_its_machine(game_data: GameData) -> None:
    finding = find(diagnostics_of("blocked_byproduct", game_data), DiagnosticCode.BLOCKED_BYPRODUCT)
    assert finding.severity is Severity.ERROR
    assert finding.node_id == "refinery"
    assert finding.edge_id is None


def test_a_deficit_is_a_warning_carrying_the_missing_rate(game_data: GameData) -> None:
    finding = find(diagnostics_of("deficit", game_data), DiagnosticCode.DEFICIT)
    assert finding.severity is Severity.WARNING
    assert finding.node_id == "smelter"
    assert "Minerai de fer" in finding.message
    assert "30/min" in finding.message


def test_saturation_targets_the_edge_not_the_node(game_data: GameData) -> None:
    finding = find(diagnostics_of("belt_saturation", game_data), DiagnosticCode.LINE_SATURATION)
    assert finding.severity is Severity.WARNING
    assert finding.edge_id == "e1"
    assert finding.node_id is None


def test_errors_come_before_warnings(game_data: GameData) -> None:
    findings = diagnostics_of("blocked_byproduct", game_data)
    severities = [item.severity for item in findings]
    assert severities == sorted(
        severities, key=lambda level: ["error", "warning", "info"].index(level.value)
    )


def test_diagnostics_are_readable_as_text(game_data: GameData) -> None:
    finding = find(diagnostics_of("deficit", game_data), DiagnosticCode.DEFICIT)
    rendered = str(finding)
    assert "warning" in rendered
    assert "smelter" in rendered


# --------------------------------------------------------------------------- #
# Structural findings
# --------------------------------------------------------------------------- #


def test_an_unconnected_ingredient_is_reported_once(game_data: GameData) -> None:
    """A machine missing a whole input line: one clear finding, not a deficit as well."""
    graph = FactoryGraph()
    graph.add_node(
        ExternalSourceNode(id="plates", item_class="Desc_IronPlate_C", rate_per_minute=30)
    )
    graph.add_node(MachineNode(id="assembler", recipe_class="Recipe_IronPlateReinforced_C"))
    graph.add_node(OutputNode(id="out", item_class="Desc_IronPlateReinforced_C"))
    graph.connect("plates", "assembler", "Desc_IronPlate_C", BELT, game_data)
    graph.connect("assembler", "out", "Desc_IronPlateReinforced_C", BELT, game_data)

    findings = list(engine.solve(graph, game_data).diagnostics)
    unconnected = [item for item in findings if item.code is DiagnosticCode.UNCONNECTED_NODE]
    assert len(unconnected) == 1
    assert "Vis" in unconnected[0].message
    assert not [item for item in findings if item.code is DiagnosticCode.DEFICIT]


def test_a_node_wired_to_nothing_is_only_an_information(game_data: GameData) -> None:
    graph = FactoryGraph()
    graph.add_node(
        ResourceNode(id="lonely", item_class="Desc_OreIron_C", extractor_class="Build_MinerMk1_C")
    )
    (finding,) = engine.solve(graph, game_data).diagnostics
    assert finding.severity is Severity.INFO
    assert finding.code is DiagnosticCode.UNCONNECTED_NODE
    assert finding.node_id == "lonely"


def test_a_recipe_in_the_wrong_machine_is_an_error(game_data: GameData) -> None:
    """The UI can put a recipe on the wrong building; the engine says so in French."""
    graph = FactoryGraph()
    graph.add_node(
        MachineNode(
            id="wrong",
            recipe_class="Recipe_IronPlate_C",
            building_class="Build_SmelterMk1_C",
        )
    )
    finding = find(
        list(engine.solve(graph, game_data).diagnostics), DiagnosticCode.INCOMPATIBLE_RECIPE
    )
    assert finding.severity is Severity.ERROR
    assert "Constructeur" in finding.message
    assert "Fonderie" in finding.message


def test_an_ambiguous_buffer_is_reported(game_data: GameData) -> None:
    """Two different items into one buffer: its capacity cannot be worked out."""
    graph = FactoryGraph()
    graph.add_node(ExternalSourceNode(id="ore", item_class="Desc_OreIron_C", rate_per_minute=30))
    graph.add_node(ExternalSourceNode(id="coal", item_class="Desc_Coal_C", rate_per_minute=30))
    graph.add_node(StorageNode(id="buffer", storage_class="Build_StorageContainerMk1_C"))
    graph.connect("ore", "buffer", "Desc_OreIron_C", BELT, game_data)
    graph.connect("coal", "buffer", "Desc_Coal_C", BELT, game_data)

    finding = find(
        list(engine.solve(graph, game_data).diagnostics), DiagnosticCode.AMBIGUOUS_BUFFER
    )
    assert finding.severity is Severity.WARNING
    assert finding.node_id == "buffer"


def test_an_incompatible_form_loaded_from_a_save_is_diagnosed(game_data: GameData) -> None:
    """``connect`` refuses it, but a file written by an older version might contain it."""
    graph = load_graph("plastic_chain")
    oil_edge = next(edge for edge in graph.edges if edge.item_class == "Desc_LiquidOil_C")
    oil_edge.transport_class = BELT  # a pipe replaced by a belt

    finding = find(
        list(engine.solve(graph, game_data).diagnostics), DiagnosticCode.INCOMPATIBLE_FORM
    )
    assert finding.severity is Severity.ERROR
    assert finding.edge_id == oil_edge.id
    assert "tuyauterie" in finding.message


# --------------------------------------------------------------------------- #
# French formatting
# --------------------------------------------------------------------------- #


def test_fluid_rates_are_shown_in_cubic_metres(game_data: GameData) -> None:
    finding = find(diagnostics_of("backpressure", game_data), DiagnosticCode.SURPLUS)
    assert "m³/min" in finding.message


def test_solid_rates_have_no_volume_unit(game_data: GameData) -> None:
    finding = find(diagnostics_of("deficit", game_data), DiagnosticCode.DEFICIT)
    assert "m³" not in finding.message


def test_decimals_use_a_comma(game_data: GameData) -> None:
    finding = find(diagnostics_of("deficit", game_data), DiagnosticCode.DEFICIT)
    assert "66,7 %" in finding.message
    assert "66.7" not in finding.message


def test_a_percentage_gets_one_decimal_and_a_rate_keeps_three(game_data: GameData) -> None:
    """The two are read differently, so they are written differently.

    A rate is added up and compared to a belt's capacity, and its thousandths carry
    real information. A percentage is glanced at and compared to a hundred; three
    decimals there are three characters of noise on every node of the canvas.
    """
    finding = find(diagnostics_of("deficit", game_data), DiagnosticCode.DEFICIT)
    assert "66,7 %" in finding.message, finding.message
    assert "66,667 %" not in finding.message, finding.message
    assert "30/min" in finding.message, finding.message


def test_durations_are_scaled_to_something_readable(game_data: GameData) -> None:
    finding = find(diagnostics_of("buffer_filling", game_data), DiagnosticCode.BUFFER_FILLING)
    assert "1,333 h" in finding.message, finding.message
