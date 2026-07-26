"""Diagnostics: turning a solved factory into sentences a player can act on.

This module reads a graph and its report and produces findings; it never computes
flows. Messages are in French and displayed verbatim, so they always carry the
number that lets the user fix the problem -- a rate, a ratio, a tier.
"""

import math
from collections.abc import Iterator

from satisplanner.core import formatting
from satisplanner.core.graph import (
    Edge,
    ExternalSourceNode,
    FactoryGraph,
    GeneratorNode,
    MachineNode,
    Node,
    ResourceNode,
    StorageNode,
    WaterExtractorNode,
    generator_input_rates,
    machine_building,
    storage_item,
)
from satisplanner.core.models import GameData, Item, UnknownClassError
from satisplanner.core.results import (
    FLOW_EPSILON,
    BufferState,
    Diagnostic,
    DiagnosticCode,
    FactoryReport,
    LimitingFactor,
    NodeSolution,
    Severity,
)

# A node running at 99.999 % is running: no diagnostic for float dust.
RATIO_TOLERANCE = 1e-6


def diagnose(graph: FactoryGraph, game_data: GameData, report: FactoryReport) -> list[Diagnostic]:
    """Every finding about this factory, ordered by severity then by target."""
    findings: list[Diagnostic] = []
    findings.extend(_convergence(report))
    findings.extend(_structure(graph, game_data))
    findings.extend(_nodes(graph, game_data, report))
    findings.extend(_lines(game_data, report))
    findings.extend(_buffers(graph, game_data, report))
    findings.extend(_sustainability(game_data, report))
    findings.extend(_power(report))
    order = {Severity.ERROR: 0, Severity.WARNING: 1, Severity.INFO: 2}
    return sorted(
        findings,
        key=lambda item: (order[item.severity], item.node_id or "", item.edge_id or "", item.code),
    )


# --------------------------------------------------------------------------- #
# Convergence
# --------------------------------------------------------------------------- #


def _convergence(report: FactoryReport) -> Iterator[Diagnostic]:
    if report.converged:
        return
    yield Diagnostic(
        severity=Severity.ERROR,
        code=DiagnosticCode.NOT_CONVERGED,
        message=(
            f"La resolution n'a pas converge en {report.iterations} iterations : les debits "
            f"affiches sont ceux de la derniere iteration et sont instables. "
            f"Une boucle de recyclage est probablement mal bouclee."
        ),
    )


# --------------------------------------------------------------------------- #
# Structure, independent of the numbers
# --------------------------------------------------------------------------- #


def _structure(graph: FactoryGraph, game_data: GameData) -> Iterator[Diagnostic]:
    for edge in graph.sorted_edges():
        yield from _edge_structure(edge, game_data)
    for node in graph.sorted_nodes():
        yield from _node_structure(node, graph, game_data)


def _edge_structure(edge: Edge, game_data: GameData) -> Iterator[Diagnostic]:
    """Guards against a save built by an older version of the application."""
    try:
        item = game_data.item(edge.item_class)
        matches = game_data.transport_form_matches(edge.transport_class, item.form)
    except UnknownClassError as exc:
        yield Diagnostic(
            severity=Severity.ERROR,
            code=DiagnosticCode.INCOMPATIBLE_FORM,
            message=str(exc),
            edge_id=edge.id,
        )
        return
    if not matches:
        needed = "une tuyauterie" if item.form.is_fluid else "un convoyeur"
        yield Diagnostic(
            severity=Severity.ERROR,
            code=DiagnosticCode.INCOMPATIBLE_FORM,
            message=(
                f"{item.display_name_fr} ne peut pas circuler dans "
                f"{_building_name(edge.transport_class, game_data)} : il faut {needed}."
            ),
            edge_id=edge.id,
        )


def _node_structure(node: Node, graph: FactoryGraph, game_data: GameData) -> Iterator[Diagnostic]:
    if isinstance(node, MachineNode):
        recipe = game_data.recipe(node.recipe_class)
        declared = machine_building(node, game_data)
        if declared != recipe.building_class:
            yield Diagnostic(
                severity=Severity.ERROR,
                code=DiagnosticCode.INCOMPATIBLE_RECIPE,
                message=(
                    f"La recette « {recipe.display_name_fr} » se fabrique dans "
                    f"{_building_name(recipe.building_class, game_data)}, pas dans "
                    f"{_building_name(declared, game_data)}."
                ),
                node_id=node.id,
            )
    if isinstance(node, GeneratorNode):
        yield from _generator_structure(node, game_data)

    # Fuel, make-up water and every ingredient alike: an input nobody feeds is a
    # warning wherever it is, and the sentence is the same one.
    for item_class in sorted(required_inputs(node, game_data)):
        if not any(edge.item_class == item_class for edge in graph.incoming(node.id)):
            yield Diagnostic(
                severity=Severity.WARNING,
                code=DiagnosticCode.UNCONNECTED_NODE,
                message=(
                    f"Aucune ligne n'apporte {game_data.item(item_class).display_name_fr} : "
                    f"l'entree n'est pas raccordee."
                ),
                node_id=node.id,
            )

    if isinstance(node, StorageNode) and storage_item(node, graph) is None:
        yield Diagnostic(
            severity=Severity.WARNING,
            code=DiagnosticCode.AMBIGUOUS_BUFFER,
            message=(
                "Le contenu de ce tampon est indetermine : raccordez une seule entree, "
                "ou choisissez explicitement l'item stocke."
            ),
            node_id=node.id,
        )

    if not graph.incoming(node.id) and not graph.outgoing(node.id):
        yield Diagnostic(
            severity=Severity.INFO,
            code=DiagnosticCode.UNCONNECTED_NODE,
            message="Ce noeud n'est raccorde a rien : il ne participe pas au calcul.",
            node_id=node.id,
        )


def _generator_structure(node: GeneratorNode, game_data: GameData) -> Iterator[Diagnostic]:
    """A fuel the building does not accept, which only a hand-edited or foreign
    file can produce: the interface never offers one."""
    generator = game_data.generators.get(node.generator_class)
    if generator is None or generator.accepts(node.fuel_class):
        return
    accepted = ", ".join(
        game_data.item(fuel.item_class).display_name_fr for fuel in generator.fuels
    )
    fuel = game_data.items.get(node.fuel_class)
    yield Diagnostic(
        severity=Severity.ERROR,
        code=DiagnosticCode.INCOMPATIBLE_RECIPE,
        message=(
            f"{_building_name(node.generator_class, game_data)} ne brule pas "
            f"{fuel.display_name_fr if fuel else node.fuel_class} : "
            f"carburants acceptes, {accepted}."
        ),
        node_id=node.id,
    )


# --------------------------------------------------------------------------- #
# Nodes, from the solved numbers
# --------------------------------------------------------------------------- #


def _nodes(graph: FactoryGraph, game_data: GameData, report: FactoryReport) -> Iterator[Diagnostic]:
    nodes = graph.node_map()
    for solution in report.nodes:
        node = nodes[solution.node_id]
        yield from _blocked(node, solution, game_data)
        yield from _deficit(node, graph, solution, game_data)
        yield from _throttled(node, graph, solution, game_data)


def _blocked(node: Node, solution: NodeSolution, game_data: GameData) -> Iterator[Diagnostic]:
    """The critical rule: an unevacuated product stops the machine dead."""
    if not solution.blocked_products:
        return
    names = ", ".join(
        game_data.item(item_class).display_name_fr for item_class in solution.blocked_products
    )
    plural = "s" if len(solution.blocked_products) > 1 else ""
    yield Diagnostic(
        severity=Severity.ERROR,
        code=DiagnosticCode.BLOCKED_BYPRODUCT,
        message=(
            f"{names} n'a{plural} aucune sortie : la machine est totalement bloquee et ne "
            f"produit rien. Raccordez une ligne vers un consommateur, un tampon, ou une "
            f"sortie marquee « rejet assume »."
        ),
        node_id=node.id,
    )


def required_inputs(node: Node, game_data: GameData) -> dict[str, float]:
    """Full-load consumption of a node, per item and per minute.

    The same figures the solver takes as nominal, quoted back in the message that
    tells the user what is missing -- including the clock, which multiplies a
    machine's appetite exactly as it multiplies its output.
    """
    match node:
        case MachineNode():
            scale = node.machine_count * node.clock_speed
            rates = game_data.recipe(node.recipe_class).ingredient_rates()
            return {item: rate * scale for item, rate in sorted(rates.items())}
        case GeneratorNode():
            return generator_input_rates(node, game_data)
        case _:
            return {}


def _consumer_noun(node: Node) -> str:
    """How to name a starved consumer in a sentence, subject and verb agreeing."""
    return "Le generateur" if isinstance(node, GeneratorNode) else "La machine"


def _deficit(
    node: Node, graph: FactoryGraph, solution: NodeSolution, game_data: GameData
) -> Iterator[Diagnostic]:
    """Missing input, in per-minute terms, with the resulting operating rate.

    Machines and generators alike: a coal generator short of water is starved in
    exactly the same way as an assembler short of screws, and reads the same.
    """
    if not isinstance(node, MachineNode | GeneratorNode) or solution.blocked_products:
        return
    needed = required_inputs(node, game_data)
    subject = _consumer_noun(node)
    units = node.machine_count if isinstance(node, MachineNode) else node.count
    for item_class in solution.starved_items:
        required = needed.get(item_class, 0.0)
        if required <= FLOW_EPSILON:
            continue
        # An input that is not wired at all is already reported as unconnected.
        if not any(edge.item_class == item_class for edge in graph.incoming(node.id)):
            continue
        supplied = solution.inputs.get(item_class, 0.0)
        missing = required - supplied
        if missing <= required * RATIO_TOLERANCE:
            continue
        item = game_data.item(item_class)
        yield Diagnostic(
            severity=Severity.WARNING,
            code=DiagnosticCode.DEFICIT,
            message=(
                f"Deficit de {item.display_name_fr} : {_rate(missing, item)} manquants sur "
                f"{_rate(required, item)} requis. {subject} tourne a "
                f"{_percent(solution.ratio)} ({_number(solution.useful_machine_count or 0)} "
                f"unite(s) utile(s) sur {_number(units)})."
            ),
            node_id=node.id,
        )


def _throttled(
    node: Node, graph: FactoryGraph, solution: NodeSolution, game_data: GameData
) -> Iterator[Diagnostic]:
    """Output side: back pressure on a machine, wasted capacity on a source.

    A node held back by a line of its own is not reported here: the finding on the
    line itself carries the tier to upgrade to, and repeating it as "back pressure"
    would send the user looking in the wrong place.
    """
    if solution.limiting is not LimitingFactor.OUTPUTS:
        return
    if solution.ratio >= 1.0 - RATIO_TOLERANCE:
        return
    if not graph.outgoing(node.id):
        # Wired to nothing at all: already reported, and repeating it as a surplus
        # would only bury the one finding that matters.
        return

    if isinstance(node, MachineNode):
        yield Diagnostic(
            severity=Severity.WARNING,
            code=DiagnosticCode.BACKPRESSURE,
            message=(
                f"Contre-pression : la sortie n'est absorbee qu'a {_percent(solution.ratio)}, "
                f"donc la machine ne tourne qu'a {_percent(solution.ratio)}. "
                f"Evacuez davantage, ou reduisez le nombre de machines."
            ),
            node_id=node.id,
        )
        return

    if isinstance(node, ResourceNode | WaterExtractorNode | ExternalSourceNode):
        produced = sum(solution.outputs.values())
        item = _source_item(node, game_data)
        # Read the potential from the node itself rather than dividing by the ratio:
        # a source feeding a fully blocked consumer has a ratio of zero.
        wasted = _source_potential(node, game_data) - produced
        yield Diagnostic(
            severity=Severity.WARNING,
            code=DiagnosticCode.SURPLUS,
            message=(
                f"Surplus non consomme : {_rate(wasted, item)} de capacite inutilisee "
                f"({_percent(solution.ratio)} de la source exploitee). "
                f"Consommez davantage, ou retirez de la capacite d'extraction."
            ),
            node_id=node.id,
        )


def _source_item(node: Node, game_data: GameData) -> Item | None:
    match node:
        case ResourceNode() | ExternalSourceNode():
            return game_data.item(node.item_class)
        case WaterExtractorNode():
            extracted = game_data.extractor(node.extractor_class).item_class
            return game_data.item(extracted) if extracted else None
        case _:
            return None


def _source_potential(node: Node, game_data: GameData) -> float:
    """What a source could deliver at full tilt, in its own unit."""
    match node:
        case ResourceNode():
            return game_data.extractor(node.extractor_class).rate(node.purity) * node.count
        case WaterExtractorNode():
            return game_data.extractor(node.extractor_class).rate_per_minute * node.count
        case ExternalSourceNode():
            return node.rate_per_minute
        case _:
            return 0.0


# --------------------------------------------------------------------------- #
# Lines
# --------------------------------------------------------------------------- #


def _lines(game_data: GameData, report: FactoryReport) -> Iterator[Diagnostic]:
    """Transport capacity is a constraint: what does not fit is quantified here.

    The rate carried is capped by the tier, so the number that matters to the user is
    the one the line *would* carry -- the uncapped companion run's figure -- and how
    much of it is being turned away.
    """
    for solution in report.edges:
        if not solution.is_saturated:
            continue
        item = game_data.item(solution.item_class)
        upgrade = game_data.smallest_transport_for(item.form, solution.demanded_rate)
        current = _building_name(solution.transport_class, game_data)
        if upgrade is not None and upgrade.class_name != solution.transport_class:
            advice = f"passez en {_building_name(upgrade.class_name, game_data)}"
        else:
            lines = math.ceil(solution.demanded_rate / solution.capacity_per_minute)
            advice = f"aucun palier ne suffit : doublez la ligne sur {lines} voies"
        yield Diagnostic(
            severity=Severity.WARNING,
            code=DiagnosticCode.LINE_SATURATION,
            message=(
                f"Ligne saturee : {_rate(solution.demanded_rate, item)} demandes pour "
                f"{_rate(solution.capacity_per_minute, item)} de capacite en {current} "
                f"({_percent(solution.saturation)}). Le debit est bride a "
                f"{_rate(solution.rate_per_minute, item)} et "
                f"{_rate(solution.blocked_rate, item)} refluent en amont. "
                f"{advice[0].upper()}{advice[1:]}."
            ),
            edge_id=solution.edge_id,
        )


# --------------------------------------------------------------------------- #
# Sustainability
# --------------------------------------------------------------------------- #


def _sustainability(game_data: GameData, report: FactoryReport) -> Iterator[Diagnostic]:
    """The headline figures hold for a while, and then they do not.

    A factory whose tanks are emptying runs at the rate shown -- until they are
    empty. Saying "100 %" without saying "for thirteen minutes" is a lie of omission,
    so the report carries both the deadline and the regime that follows it.
    """
    if report.is_sustainable:
        return
    autonomy = report.shortest_autonomy_minutes
    deadline = f" pendant {_duration(autonomy)}" if autonomy is not None else ""
    drained = ", ".join(
        f"{buffer.node_id} ({_rate(buffer.net, _buffer_item(buffer.item_class, game_data))})"
        for buffer in report.draining_buffers
    )
    established = ""
    if report.sustained is not None:
        established = (
            " Le regime etabli, une fois les stocks epuises, est donne en regard : "
            f"{_outputs_summary(report.sustained, game_data)}."
        )
    yield Diagnostic(
        severity=Severity.WARNING,
        code=DiagnosticCode.NOT_SUSTAINABLE,
        message=(
            f"Ces debits ne sont pas tenables : ils vivent sur un stock{deadline}. "
            f"Tampon(s) en vidage : {drained}.{established}"
        ),
    )


def _buffer_item(item_class: str | None, game_data: GameData) -> Item | None:
    return None if item_class is None else game_data.item(item_class)


def _outputs_summary(report: FactoryReport, game_data: GameData) -> str:
    """Final outputs of a report, as a short French enumeration."""
    if not report.final_outputs:
        return "plus aucune production"
    return ", ".join(
        f"{_rate(rate, game_data.item(item_class))} de {game_data.item(item_class).display_name_fr}"
        for item_class, rate in sorted(report.final_outputs.items())
    )


# --------------------------------------------------------------------------- #
# Power
# --------------------------------------------------------------------------- #


def _power(report: FactoryReport) -> Iterator[Diagnostic]:
    """The one error that does not throttle anything, and says so.

    Everywhere else in this module an error means a rate has been reduced. Here it
    does not: electricity is counted, never allocated. In game a shortfall does not
    slow the factory down, it cuts the whole grid until a player walks over and
    switches it back on, so "everything at 60 %" would be a fiction and "everything
    at zero" would hide the very numbers needed to fix it. The message therefore
    carries the deficit and states plainly that the figures above ignore it.
    """
    if not report.has_power_deficit:
        return
    yield Diagnostic(
        severity=Severity.ERROR,
        code=DiagnosticCode.POWER_DEFICIT,
        message=(
            f"Deficit electrique : {_number(report.power_total_mw)} MW consommes pour "
            f"{_number(report.power_production_mw)} MW produits, soit "
            f"{_number(abs(report.power_balance_mw))} MW manquants. En jeu, le reseau "
            f"disjoncte entierement jusqu'a intervention manuelle : les debits affiches "
            f"ci-dessus ne sont donc pas brides, ils supposent le courant retabli."
        ),
    )


# --------------------------------------------------------------------------- #
# Buffers
# --------------------------------------------------------------------------- #


def _buffers(
    graph: FactoryGraph, game_data: GameData, report: FactoryReport
) -> Iterator[Diagnostic]:
    nodes = graph.node_map()
    for buffer in report.buffers:
        node = nodes[buffer.node_id]
        if not isinstance(node, StorageNode) or buffer.item_class is None:
            continue
        item = game_data.item(buffer.item_class)
        if buffer.state is BufferState.FILLING and buffer.minutes_to_full is not None:
            yield Diagnostic(
                severity=Severity.INFO,
                code=DiagnosticCode.BUFFER_FILLING,
                message=(
                    f"Tampon en remplissage : +{_rate(buffer.net, item)}, "
                    f"sature en {_duration(buffer.minutes_to_full)} "
                    f"({_number(buffer.capacity)} de capacite)."
                ),
                node_id=buffer.node_id,
            )
        elif buffer.state is BufferState.DRAINING and buffer.minutes_to_empty is not None:
            yield Diagnostic(
                severity=Severity.WARNING,
                code=DiagnosticCode.BUFFER_DRAINING,
                message=(
                    f"Tampon en vidage : {_rate(buffer.net, item)}, vide en "
                    f"{_duration(buffer.minutes_to_empty)}. Le regime permanent n'est pas "
                    f"tenable une fois le stock epuise."
                ),
                node_id=buffer.node_id,
            )
        else:
            yield Diagnostic(
                severity=Severity.INFO,
                code=DiagnosticCode.BUFFER_FILLING,
                message="Tampon a l'equilibre : entrees et sorties se compensent.",
                node_id=buffer.node_id,
            )


# --------------------------------------------------------------------------- #
# French formatting
# --------------------------------------------------------------------------- #

_number = formatting.number
_rate = formatting.rate
_percent = formatting.percent
_duration = formatting.duration


def _building_name(class_name: str, game_data: GameData) -> str:
    try:
        return game_data.building(class_name).display_name_fr
    except UnknownClassError:
        return class_name
