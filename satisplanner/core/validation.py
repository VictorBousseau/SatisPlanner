"""Diagnostics: turning a solved factory into sentences a player can act on.

This module reads a graph and its report and produces findings; it never computes
flows. Messages are in French and displayed verbatim, so they always carry the
number that lets the user fix the problem -- a rate, a ratio, a tier.
"""

import math
from collections.abc import Iterator

from satisplanner.core import formatting
from satisplanner.core.graph import (
    ANY_BRANCH,
    OVERFLOW_BRANCH,
    Edge,
    ExternalSourceNode,
    FactoryGraph,
    GeneratorNode,
    GeothermalNode,
    MachineNode,
    MergerNode,
    Node,
    ResourceNode,
    ResourceWellNode,
    SplitterNode,
    StorageNode,
    WaterExtractorNode,
    generator_input_rates,
    machine_building,
    pass_through_item,
    storage_item,
)
from satisplanner.core.models import GameData, Item, SplitterMode, UnknownClassError
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
            f"La résolution n'a pas convergé en {report.iterations} itérations : les débits "
            f"affichés sont ceux de la dernière itération et sont instables. "
            f"Une boucle de recyclage est probablement mal bouclée."
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
    if isinstance(node, ResourceWellNode):
        yield from _well_structure(node, game_data)

    # Fuel, make-up water and every ingredient alike: an input nobody feeds is a
    # warning wherever it is, and the sentence is the same one.
    for item_class in sorted(required_inputs(node, game_data)):
        if not any(edge.item_class == item_class for edge in graph.incoming(node.id)):
            yield Diagnostic(
                severity=Severity.WARNING,
                code=DiagnosticCode.UNCONNECTED_NODE,
                message=(
                    f"Aucune ligne n'apporte {game_data.item(item_class).display_name_fr} : "
                    f"l'entrée n'est pas raccordée."
                ),
                node_id=node.id,
            )

    if isinstance(node, StorageNode) and storage_item(node, graph) is None:
        yield Diagnostic(
            severity=Severity.WARNING,
            code=DiagnosticCode.AMBIGUOUS_BUFFER,
            message=(
                "Le contenu de ce tampon est indéterminé : raccordez une seule entrée, "
                "ou choisissez explicitement l'item stocké."
            ),
            node_id=node.id,
        )

    if isinstance(node, SplitterNode):
        yield from _branch_filters(node, graph, game_data)

    if isinstance(node, SplitterNode | MergerNode) and pass_through_item(node, graph) is None:
        # Two different items on its lines, or none at all. Either way there is no
        # building to count for it and nothing flows, which is worth saying out loud
        # rather than leaving as an empty row on the canvas.
        role = "répartiteur" if isinstance(node, SplitterNode) else "groupeur"
        yield Diagnostic(
            severity=Severity.WARNING,
            code=DiagnosticCode.AMBIGUOUS_BUFFER,
            message=(
                f"Ce {role} ne transporte rien de déterminé : une ligne ne porte qu'un item, "
                f"donc toutes celles qui y arrivent et en partent doivent porter le même."
            ),
            node_id=node.id,
        )

    # A geyser is never wired to anything and never will be: it takes nothing and
    # returns nothing but current. Saying it does not take part in the calculation
    # would be false -- it is the two hundred megawatts holding the factory up.
    if (
        not isinstance(node, GeothermalNode)
        and not graph.incoming(node.id)
        and not graph.outgoing(node.id)
    ):
        yield Diagnostic(
            severity=Severity.INFO,
            code=DiagnosticCode.UNCONNECTED_NODE,
            message="Ce nœud n'est raccordé à rien : il ne participe pas au calcul.",
            node_id=node.id,
        )


def _branch_filters(
    node: SplitterNode, graph: FactoryGraph, game_data: GameData
) -> Iterator[Diagnostic]:
    """What is written on a splitter's branches, checked against what it can do.

    Everything here is silent on a standard splitter, which has nothing written on
    it, and that is the point: the modes are an addition and they cost the
    factories that use none of them nothing at all -- not a figure, not a warning.
    """
    if node.mode is SplitterMode.STANDARD and not node.filters:
        return

    carried = pass_through_item(node, graph)
    branches = [edge.target for edge in graph.outgoing(node.id)]
    written = {target: node.filters.get(target, ANY_BRANCH) for target in branches}
    named = {
        target: setting
        for target, setting in sorted(written.items())
        if setting not in (ANY_BRANCH, OVERFLOW_BRANCH)
    }

    if carried is not None and game_data.item(carried).form.is_fluid:
        # There is one pipe junction and it filters nothing.
        yield Diagnostic(
            severity=Severity.ERROR,
            code=DiagnosticCode.BRANCH_FILTER,
            message=(
                "Le jeu n'a pas de jonction de pipeline filtrante : un raccord sur un "
                "fluide ne peut être que standard. Repassez-le en standard, ou triez "
                "en amont."
            ),
            node_id=node.id,
        )

    if node.mode is SplitterMode.SMART and len(named) + _overflows(written) > 1:
        yield Diagnostic(
            severity=Severity.ERROR,
            code=DiagnosticCode.BRANCH_FILTER,
            message=(
                f"Un répartiteur intelligent ne règle qu'une seule de ses branches ; "
                f"{len(named) + _overflows(written)} le sont ici. Passez-le en "
                f"programmable, ou remettez les autres en « n'importe lequel »."
            ),
            node_id=node.id,
        )

    for target, setting in named.items():
        if carried is not None and setting != carried:
            wanted = game_data.items.get(setting)
            yield Diagnostic(
                severity=Severity.WARNING,
                code=DiagnosticCode.BRANCH_FILTER,
                message=(
                    f"La branche vers {target} est filtrée sur "
                    f"{wanted.display_name_fr if wanted else setting}, que cette ligne "
                    f"ne transporte jamais : elle porte "
                    f"{game_data.item(carried).display_name_fr}. Rien ne passera par là."
                ),
                node_id=node.id,
            )

    stale = sorted(set(node.filters) - set(branches))
    if stale:
        yield Diagnostic(
            severity=Severity.WARNING,
            code=DiagnosticCode.BRANCH_FILTER,
            message=(
                f"Réglage(s) conservé(s) pour une branche qui n'existe plus : "
                f"{', '.join(stale)}. Sans effet tant que la ligne n'est pas redessinée."
            ),
            node_id=node.id,
        )

    if branches and _overflows(written) == len(branches):
        yield Diagnostic(
            severity=Severity.WARNING,
            code=DiagnosticCode.BRANCH_FILTER,
            message=(
                "Toutes les branches sont en « surplus » : il n'y a donc rien à "
                "refuser en premier, et le raccord se comporte comme un standard."
            ),
            node_id=node.id,
        )


def _overflows(written: dict[str, str]) -> int:
    return sum(1 for setting in written.values() if setting == OVERFLOW_BRANCH)


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
            f"{_building_name(node.generator_class, game_data)} ne brûle pas "
            f"{fuel.display_name_fr if fuel else node.fuel_class} : "
            f"carburants acceptés, {accepted}."
        ),
        node_id=node.id,
    )


# --------------------------------------------------------------------------- #
# Nodes, from the solved numbers
# --------------------------------------------------------------------------- #


def _well_structure(node: ResourceWellNode, game_data: GameData) -> Iterator[Diagnostic]:
    """A resource well is a pressuriser plus a tally, and both can be wrong."""
    extractor = game_data.extractors.get(node.extractor_class)
    if extractor is not None and node.item_class not in (extractor.allowed_items or ()):
        allowed = ", ".join(
            game_data.item(item).display_name_fr for item in extractor.allowed_items
        )
        yield Diagnostic(
            severity=Severity.ERROR,
            code=DiagnosticCode.INCOMPATIBLE_RECIPE,
            message=(
                f"Un puits de ressource ne s'ouvre pas sur "
                f"{game_data.item(node.item_class).display_name_fr} : "
                f"seuls {allowed} en ont."
            ),
            node_id=node.id,
        )
    if node.satellite_count == 0:
        # The trap is that this is not free: the pressuriser draws its 150 MW
        # whether or not a single satellite has been opened on it.
        yield Diagnostic(
            severity=Severity.WARNING,
            code=DiagnosticCode.UNCONNECTED_NODE,
            message=(
                "Ce puits n'a aucun satellite : rien n'est extrait, et le pressuriseur "
                "consomme quand même sa puissance nominale."
            ),
            node_id=node.id,
        )


def _nodes(graph: FactoryGraph, game_data: GameData, report: FactoryReport) -> Iterator[Diagnostic]:
    nodes = graph.node_map()
    for solution in report.nodes:
        node = nodes[solution.node_id]
        yield from _blocked(node, solution, game_data)
        yield from _deficit(node, graph, solution, game_data)
        yield from _throttled(node, graph, solution, game_data)


def _blocked(node: Node, solution: NodeSolution, game_data: GameData) -> Iterator[Diagnostic]:
    """The critical rule: an unevacuated product stops the machine dead.

    A splitter with nothing behind it is blocked in the same sense and stops the
    machine feeding it just as surely, so it gets the same finding worded for what
    it is -- and a line into a dead-end splitter is named as the dead end it is
    rather than left to look like back pressure.
    """
    if not solution.blocked_products:
        return
    names = ", ".join(
        game_data.item(item_class).display_name_fr for item_class in solution.blocked_products
    )
    plural = "s" if len(solution.blocked_products) > 1 else ""
    if isinstance(node, SplitterNode | MergerNode):
        subject = "le répartiteur" if isinstance(node, SplitterNode) else "le groupeur"
        message = (
            f"{names} n'a{plural} aucune sortie au-delà de ce raccord : {subject} ne laisse "
            f"rien passer et bloque tout ce qui l'alimente. Raccordez une de ses branches."
        )
    else:
        # A power plant is not "a machine" to a reader, and its output is not what
        # is blocked -- the current keeps flowing until the waste stops it.
        subject = "la centrale" if isinstance(node, GeneratorNode) else "la machine"
        message = (
            f"{names} n'a{plural} aucune sortie : {subject} est totalement bloquée et ne "
            f"produit rien. Raccordez une ligne vers un consommateur, un tampon, ou une "
            f"sortie marquée « rejet assumé »."
        )
    yield Diagnostic(
        severity=Severity.ERROR,
        code=DiagnosticCode.BLOCKED_BYPRODUCT,
        message=message,
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
    return "Le générateur" if isinstance(node, GeneratorNode) else "La machine"


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
                f"Déficit de {item.display_name_fr} : {_rate(missing, item)} manquants sur "
                f"{_rate(required, item)} requis. {subject} tourne à "
                f"{_percent(solution.ratio)} ({_number(solution.useful_machine_count or 0)} "
                f"unité(s) utile(s) sur {_number(units)})."
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
                f"Contre-pression : la sortie n'est absorbée qu'à {_percent(solution.ratio)}, "
                f"donc la machine ne tourne qu'à {_percent(solution.ratio)}. "
                f"Évacuez davantage, ou réduisez le nombre de machines."
            ),
            node_id=node.id,
        )
        return

    if isinstance(node, ResourceNode | ResourceWellNode | WaterExtractorNode | ExternalSourceNode):
        produced = sum(solution.outputs.values())
        item = _source_item(node, game_data)
        # Read the potential from the node itself rather than dividing by the ratio:
        # a source feeding a fully blocked consumer has a ratio of zero.
        wasted = _source_potential(node, game_data) - produced
        yield Diagnostic(
            severity=Severity.WARNING,
            code=DiagnosticCode.SURPLUS,
            message=(
                f"Surplus non consomme : {_rate(wasted, item)} de capacité inutilisée "
                f"({_percent(solution.ratio)} de la source exploitée). "
                f"Consommez davantage, ou retirez de la capacité d'extraction."
            ),
            node_id=node.id,
        )


def _source_item(node: Node, game_data: GameData) -> Item | None:
    match node:
        case ResourceNode() | ResourceWellNode() | ExternalSourceNode():
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
            rate = game_data.extractor(node.extractor_class).rate(node.purity) * node.count
            return rate * node.clock_speed
        case ResourceWellNode():
            extractor = game_data.extractor(node.extractor_class)
            rate = sum(
                extractor.rate(purity) * count for purity, count in sorted(node.satellites.items())
            )
            return rate * node.clock_speed
        case WaterExtractorNode():
            rate = game_data.extractor(node.extractor_class).rate_per_minute * node.count
            return rate * node.clock_speed
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
                f"Ligne saturée : {_rate(solution.demanded_rate, item)} demandes pour "
                f"{_rate(solution.capacity_per_minute, item)} de capacité en {current} "
                f"({_percent(solution.saturation)}). Le débit est bridé à "
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
            " Le régime établi, une fois les stocks épuisés, est donne en regard : "
            f"{_outputs_summary(report.sustained, game_data)}."
        )
    yield Diagnostic(
        severity=Severity.WARNING,
        code=DiagnosticCode.NOT_SUSTAINABLE,
        message=(
            f"Ces débits ne sont pas tenables : ils vivent sur un stock{deadline}. "
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
            f"Déficit électrique : {_number(report.power_total_mw)} MW consommés pour "
            f"{_number(report.power_production_mw)} MW produits, soit "
            f"{_number(abs(report.power_balance_mw))} MW manquants. En jeu, le réseau "
            f"disjoncte entièrement jusqu'à intervention manuelle : les débits affichés "
            f"ci-dessus ne sont donc pas bridés, ils supposent le courant rétabli."
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
                    f"saturé en {_duration(buffer.minutes_to_full)} "
                    f"({_number(buffer.capacity)} de capacité)."
                ),
                node_id=buffer.node_id,
            )
        elif buffer.state is BufferState.DRAINING and buffer.minutes_to_empty is not None:
            yield Diagnostic(
                severity=Severity.WARNING,
                code=DiagnosticCode.BUFFER_DRAINING,
                message=(
                    f"Tampon en vidage : {_rate(buffer.net, item)}, vide en "
                    f"{_duration(buffer.minutes_to_empty)}. Le régime permanent n'est pas "
                    f"tenable une fois le stock épuisé."
                ),
                node_id=buffer.node_id,
            )
        else:
            yield Diagnostic(
                severity=Severity.INFO,
                code=DiagnosticCode.BUFFER_FILLING,
                message="Tampon à l'équilibre : entrées et sorties se compensent.",
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
