"""Diagnostics: turning a solved factory into sentences a player can act on.

This module reads a graph and its report and produces findings; it never computes
flows. Messages are displayed verbatim, so they always carry the number that lets the
user fix the problem -- a rate, a ratio, a tier.

Written in French and translated through :func:`satisplanner.core.i18n._`, which is
why every sentence here is a literal with named ``{fields}`` rather than an f-string:
a key has to be findable in the source to be counted and to be translated. These are
the sentences a user reads most, so they were the first to go through the catalogue.
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
from satisplanner.core.i18n import _
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
        message=_(
            "La résolution n'a pas convergé en {iterations} itérations : les débits "
            "affichés sont ceux de la dernière itération et sont instables. "
            "Une boucle de recyclage est probablement mal bouclée."
        ).format(iterations=report.iterations),
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
        # Two whole sentences rather than one with a noun spliced into it: an
        # article agrees with its noun in French and the two languages do not put
        # them in the same place, so a fragment is a sentence nobody can translate.
        pattern = (
            _("{item} ne peut pas circuler dans {transport} : il faut une tuyauterie.")
            if item.form.is_fluid
            else _("{item} ne peut pas circuler dans {transport} : il faut un convoyeur.")
        )
        yield Diagnostic(
            severity=Severity.ERROR,
            code=DiagnosticCode.INCOMPATIBLE_FORM,
            message=pattern.format(
                item=item.name,
                transport=_building_name(edge.transport_class, game_data),
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
                message=_(
                    "La recette « {recipe} » se fabrique dans {expected}, "
                    "pas dans {declared}."
                ).format(
                    recipe=recipe.name,
                    expected=_building_name(recipe.building_class, game_data),
                    declared=_building_name(declared, game_data),
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
                message=_(
                    "Aucune ligne n'apporte {item} : l'entrée n'est pas raccordée."
                ).format(item=game_data.item(item_class).name),
                node_id=node.id,
            )

    if isinstance(node, StorageNode) and storage_item(node, graph) is None:
        yield Diagnostic(
            severity=Severity.WARNING,
            code=DiagnosticCode.AMBIGUOUS_BUFFER,
            message=_(
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
        message = (
            _(
                "Ce répartiteur ne transporte rien de déterminé : une ligne ne porte "
                "qu'un item, donc toutes celles qui y arrivent et en partent doivent "
                "porter le même."
            )
            if isinstance(node, SplitterNode)
            else _(
                "Ce groupeur ne transporte rien de déterminé : une ligne ne porte "
                "qu'un item, donc toutes celles qui y arrivent et en partent doivent "
                "porter le même."
            )
        )
        yield Diagnostic(
            severity=Severity.WARNING,
            code=DiagnosticCode.AMBIGUOUS_BUFFER,
            message=message,
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
            message=_("Ce nœud n'est raccordé à rien : il ne participe pas au calcul."),
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
            message=_(
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
            message=_(
                "Un répartiteur intelligent ne règle qu'une seule de ses branches ; "
                "{count} le sont ici. Passez-le en programmable, ou remettez les "
                "autres en « n'importe lequel »."
            ).format(count=len(named) + _overflows(written)),
            node_id=node.id,
        )

    for target, setting in named.items():
        if carried is not None and setting != carried:
            wanted = game_data.items.get(setting)
            yield Diagnostic(
                severity=Severity.WARNING,
                code=DiagnosticCode.BRANCH_FILTER,
                message=_(
                    "La branche vers {branch} est filtrée sur {wanted}, que cette "
                    "ligne ne transporte jamais : elle porte {carried}. Rien ne "
                    "passera par là."
                ).format(
                    branch=target,
                    wanted=wanted.name if wanted else setting,
                    carried=game_data.item(carried).name,
                ),
                node_id=node.id,
            )

    stale = sorted(set(node.filters) - set(branches))
    if stale:
        yield Diagnostic(
            severity=Severity.WARNING,
            code=DiagnosticCode.BRANCH_FILTER,
            message=_(
                "Réglage(s) conservé(s) pour une branche qui n'existe plus : "
                "{branches}. Sans effet tant que la ligne n'est pas redessinée."
            ).format(branches=", ".join(stale)),
            node_id=node.id,
        )

    if branches and _overflows(written) == len(branches):
        yield Diagnostic(
            severity=Severity.WARNING,
            code=DiagnosticCode.BRANCH_FILTER,
            message=_(
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
        game_data.item(fuel.item_class).name for fuel in generator.fuels
    )
    fuel = game_data.items.get(node.fuel_class)
    yield Diagnostic(
        severity=Severity.ERROR,
        code=DiagnosticCode.INCOMPATIBLE_RECIPE,
        message=_("{building} ne brûle pas {fuel} : carburants acceptés, {accepted}.").format(
            building=_building_name(node.generator_class, game_data),
            fuel=fuel.name if fuel else node.fuel_class,
            accepted=accepted,
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
            game_data.item(item).name for item in extractor.allowed_items
        )
        yield Diagnostic(
            severity=Severity.ERROR,
            code=DiagnosticCode.INCOMPATIBLE_RECIPE,
            message=_(
                "Un puits de ressource ne s'ouvre pas sur {item} : seuls {allowed} en ont."
            ).format(item=game_data.item(node.item_class).name, allowed=allowed),
            node_id=node.id,
        )
    if node.satellite_count == 0:
        # The trap is that this is not free: the pressuriser draws its 150 MW
        # whether or not a single satellite has been opened on it.
        yield Diagnostic(
            severity=Severity.WARNING,
            code=DiagnosticCode.UNCONNECTED_NODE,
            message=_(
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
        game_data.item(item_class).name for item_class in solution.blocked_products
    )
    # Written out in full rather than assembled from a subject and a plural mark.
    # Both languages make the verb agree, and neither puts the agreement where the
    # other does, so the whole sentence is the smallest piece that can be translated.
    message = _blocked_sentence(node, plural=len(solution.blocked_products) > 1).format(
        names=names
    )
    yield Diagnostic(
        severity=Severity.ERROR,
        code=DiagnosticCode.BLOCKED_BYPRODUCT,
        message=message,
        node_id=node.id,
    )


def _blocked_sentence(node: Node, *, plural: bool) -> str:
    """The blockage, worded for what is blocked and for how many items it is.

    A power plant is not "a machine" to a reader, and its output is not what is
    blocked -- the current keeps flowing until the waste stops it. A splitter with
    nothing behind it blocks just as surely and reads as the dead end it is.
    """
    if isinstance(node, SplitterNode):
        if plural:
            return _(
                "{names} n'ont aucune sortie au-delà de ce raccord : le répartiteur ne "
                "laisse rien passer et bloque tout ce qui l'alimente. Raccordez une de "
                "ses branches."
            )
        return _(
            "{names} n'a aucune sortie au-delà de ce raccord : le répartiteur ne laisse "
            "rien passer et bloque tout ce qui l'alimente. Raccordez une de ses branches."
        )
    if isinstance(node, MergerNode):
        if plural:
            return _(
                "{names} n'ont aucune sortie au-delà de ce raccord : le groupeur ne "
                "laisse rien passer et bloque tout ce qui l'alimente. Raccordez une de "
                "ses branches."
            )
        return _(
            "{names} n'a aucune sortie au-delà de ce raccord : le groupeur ne laisse "
            "rien passer et bloque tout ce qui l'alimente. Raccordez une de ses branches."
        )
    if isinstance(node, GeneratorNode):
        if plural:
            return _(
                "{names} n'ont aucune sortie : la centrale est totalement bloquée et ne "
                "produit rien. Raccordez une ligne vers un consommateur, un tampon, ou "
                "une sortie marquée « rejet assumé »."
            )
        return _(
            "{names} n'a aucune sortie : la centrale est totalement bloquée et ne "
            "produit rien. Raccordez une ligne vers un consommateur, un tampon, ou une "
            "sortie marquée « rejet assumé »."
        )
    if plural:
        return _(
            "{names} n'ont aucune sortie : la machine est totalement bloquée et ne "
            "produit rien. Raccordez une ligne vers un consommateur, un tampon, ou une "
            "sortie marquée « rejet assumé »."
        )
    return _(
        "{names} n'a aucune sortie : la machine est totalement bloquée et ne produit "
        "rien. Raccordez une ligne vers un consommateur, un tampon, ou une sortie "
        "marquée « rejet assumé »."
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


def _deficit_sentence(node: Node) -> str:
    """The shortfall, naming the starved consumer for what it is."""
    if isinstance(node, GeneratorNode):
        return _(
            "Déficit de {item} : {missing} manquants sur {required} requis. Le "
            "générateur tourne à {ratio} ({useful} unité(s) utile(s) sur {units})."
        )
    return _(
        "Déficit de {item} : {missing} manquants sur {required} requis. La machine "
        "tourne à {ratio} ({useful} unité(s) utile(s) sur {units})."
    )


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
    pattern = _deficit_sentence(node)
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
            message=pattern.format(
                item=item.name,
                missing=_rate(missing, item),
                required=_rate(required, item),
                ratio=_percent(solution.ratio),
                useful=_number(solution.useful_machine_count or 0),
                units=_number(units),
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
            message=_(
                "Contre-pression : la sortie n'est absorbée qu'à {ratio}, donc la "
                "machine ne tourne qu'à {ratio}. Évacuez davantage, ou réduisez le "
                "nombre de machines."
            ).format(ratio=_percent(solution.ratio)),
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
            message=_(
                "Surplus non consommé : {wasted} de capacité inutilisée ({ratio} de la "
                "source exploitée). Consommez davantage, ou retirez de la capacité "
                "d'extraction."
            ).format(wasted=_rate(wasted, item), ratio=_percent(solution.ratio)),
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
        # The advice is part of the sentence and not a fragment glued to its end:
        # its first letter used to be capitalised by hand, which is a way of saying
        # it was never really a sentence of its own.
        lines = math.ceil(solution.demanded_rate / solution.capacity_per_minute)
        if upgrade is not None and upgrade.class_name != solution.transport_class:
            pattern = _(
                "Ligne saturée : {demanded} demandes pour {capacity} de capacité en "
                "{current} ({saturation}). Le débit est bridé à {carried} et {blocked} "
                "refluent en amont. Passez en {upgrade}."
            )
        else:
            pattern = _(
                "Ligne saturée : {demanded} demandes pour {capacity} de capacité en "
                "{current} ({saturation}). Le débit est bridé à {carried} et {blocked} "
                "refluent en amont. Aucun palier ne suffit : doublez la ligne sur "
                "{lines} voies."
            )
        yield Diagnostic(
            severity=Severity.WARNING,
            code=DiagnosticCode.LINE_SATURATION,
            message=pattern.format(
                demanded=_rate(solution.demanded_rate, item),
                capacity=_rate(solution.capacity_per_minute, item),
                current=_building_name(solution.transport_class, game_data),
                saturation=_percent(solution.saturation),
                carried=_rate(solution.rate_per_minute, item),
                blocked=_rate(solution.blocked_rate, item),
                upgrade=(
                    _building_name(upgrade.class_name, game_data) if upgrade is not None else ""
                ),
                lines=lines,
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
    drained = ", ".join(
        f"{buffer.node_id} ({_rate(buffer.net, _buffer_item(buffer.item_class, game_data))})"
        for buffer in report.draining_buffers
    )
    if autonomy is not None:
        message = _(
            "Ces débits ne sont pas tenables : ils vivent sur un stock pendant "
            "{autonomy}. Tampon(s) en vidage : {drained}."
        ).format(autonomy=_duration(autonomy), drained=drained)
    else:
        message = _(
            "Ces débits ne sont pas tenables : ils vivent sur un stock. "
            "Tampon(s) en vidage : {drained}."
        ).format(drained=drained)
    if report.sustained is not None:
        message += " " + _(
            "Le régime établi, une fois les stocks épuisés, est donné en regard : {outputs}."
        ).format(outputs=_outputs_summary(report.sustained, game_data))
    yield Diagnostic(
        severity=Severity.WARNING,
        code=DiagnosticCode.NOT_SUSTAINABLE,
        message=message,
    )


def _buffer_item(item_class: str | None, game_data: GameData) -> Item | None:
    return None if item_class is None else game_data.item(item_class)


def _outputs_summary(report: FactoryReport, game_data: GameData) -> str:
    """Final outputs of a report, as a short French enumeration."""
    if not report.final_outputs:
        return _("plus aucune production")
    # "de X" here and not ``formatting.of``, which would elide into "d'Eau": this
    # sentence has never elided and a translation lot is not the place to start.
    # The inconsistency with the plan's summary is real and left alone on purpose.
    return ", ".join(
        _("{rate} de {item}").format(
            rate=_rate(rate, game_data.item(item_class)),
            item=game_data.item(item_class).name,
        )
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
        message=_(
            "Déficit électrique : {consumed} MW consommés pour {produced} MW produits, "
            "soit {missing} MW manquants. En jeu, le réseau disjoncte entièrement "
            "jusqu'à intervention manuelle : les débits affichés ci-dessus ne sont donc "
            "pas bridés, ils supposent le courant rétabli."
        ).format(
            consumed=_number(report.power_total_mw),
            produced=_number(report.power_production_mw),
            missing=_number(abs(report.power_balance_mw)),
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
                message=_(
                    "Tampon en remplissage : +{net}, saturé en {deadline} "
                    "({capacity} de capacité)."
                ).format(
                    net=_rate(buffer.net, item),
                    deadline=_duration(buffer.minutes_to_full),
                    capacity=_number(buffer.capacity),
                ),
                node_id=buffer.node_id,
            )
        elif buffer.state is BufferState.DRAINING and buffer.minutes_to_empty is not None:
            yield Diagnostic(
                severity=Severity.WARNING,
                code=DiagnosticCode.BUFFER_DRAINING,
                message=_(
                    "Tampon en vidage : {net}, vide en {deadline}. Le régime permanent "
                    "n'est pas tenable une fois le stock épuisé."
                ).format(
                    net=_rate(buffer.net, item),
                    deadline=_duration(buffer.minutes_to_empty),
                ),
                node_id=buffer.node_id,
            )
        else:
            yield Diagnostic(
                severity=Severity.INFO,
                code=DiagnosticCode.BUFFER_FILLING,
                message=_("Tampon à l'équilibre : entrées et sorties se compensent."),
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
        return game_data.building(class_name).name
    except UnknownClassError:
        return class_name
