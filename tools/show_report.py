"""Solve a factory from a JSON file and print its report in the console.

    py -3.12 tools/show_report.py tests/fixtures/graphs/plastic_chain.json

The phase 2 deliverable: the engine is fully usable without a single line of Qt.
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from satisplanner.core import engine
from satisplanner.core.graph import FactoryGraph
from satisplanner.core.models import GameData
from satisplanner.core.results import FactoryReport, Severity
from satisplanner.data import db

logger = logging.getLogger("show_report")
log = logger.info

RULE = "-" * 78

SEVERITY_LABELS = {
    Severity.ERROR: "ERREUR",
    Severity.WARNING: "ATTENTION",
    Severity.INFO: "info",
}


def item_name(class_name: str, game_data: GameData) -> str:
    try:
        return game_data.item(class_name).display_name_fr
    except LookupError:
        return class_name


def building_name(class_name: str, game_data: GameData) -> str:
    try:
        return game_data.building(class_name).display_name_fr
    except LookupError:
        return class_name


def unit(class_name: str, game_data: GameData) -> str:
    try:
        return "m³/min" if game_data.item(class_name).form.is_fluid else "/min"
    except LookupError:
        return "/min"


def rates(values: dict[str, float], game_data: GameData) -> str:
    if not values:
        return "-"
    return ", ".join(
        f"{item_name(item, game_data)} {rate:g}{unit(item, game_data)}"
        for item, rate in sorted(values.items())
    )


def show(report: FactoryReport, game_data: GameData) -> None:
    _show_nodes(report, game_data)
    _show_edges(report, game_data)
    _show_totals(report, game_data)
    _show_shopping_list(report, game_data)
    _show_diagnostics(report)
    _show_sustained(report, game_data)


def _show_sustained(report: FactoryReport, game_data: GameData) -> None:
    """The second set of figures, once the stocks are gone."""
    if report.sustained is None:
        return
    log("%s\nREGIME ETABLI (une fois les stocks epuises)\n%s", RULE, RULE)
    log("Production finale : %s", rates(report.sustained.final_outputs, game_data))
    log("Solides bruts     : %s", rates(report.sustained.raw_solids, game_data))
    log("Fluides bruts     : %s", rates(report.sustained.raw_fluids, game_data))
    for node in report.sustained.nodes:
        if node.machine_count is not None:
            log("   %-22s %6.1f%%", node.node_id, node.ratio * 100)


def _show_nodes(report: FactoryReport, game_data: GameData) -> None:
    log("%s\nNOEUDS\n%s", RULE, RULE)
    log("%-22s %-26s %7s  %-16s %s", "noeud", "role", "taux", "machines", "sorties")
    for node in report.nodes:
        machines = "-"
        if node.machine_count is not None:
            machines = f"{node.useful_machine_count:g} / {node.machine_count:g}"
            if node.integer_machine_count != node.machine_count:
                machines += f" (a batir {node.integer_machine_count})"
        log(
            "%-22s %-26s %6.1f%%  %-16s %s",
            node.node_id,
            node.label[:26],
            node.ratio * 100,
            machines,
            rates(node.outputs, game_data),
        )


def _show_edges(report: FactoryReport, game_data: GameData) -> None:
    log("%s\nLIGNES\n%s", RULE, RULE)
    for edge in report.edges:
        flag = ""
        if edge.is_saturated:
            flag = f"  SATUREE, {edge.blocked_rate:g} refoules"
        elif edge.is_at_capacity:
            flag = "  pleine"
        log(
            "%-10s %-18s -> %-18s %10.3f%s de %g%s (%.0f%%)%s",
            edge.edge_id,
            edge.source,
            edge.target,
            edge.rate_per_minute,
            unit(edge.item_class, game_data),
            edge.capacity_per_minute,
            unit(edge.item_class, game_data),
            edge.saturation * 100,
            flag,
        )


def _show_totals(report: FactoryReport, game_data: GameData) -> None:
    log("%s\nBILAN\n%s", RULE, RULE)
    log("1. Solides bruts consommes : %s", rates(report.raw_solids, game_data))
    log("2. Fluides bruts consommes : %s", rates(report.raw_fluids, game_data))
    for balance in report.byproducts:
        log(
            "   sous-produit %s : produit %g, recycle %g, stocke %g, exporte %g, rejete %g",
            item_name(balance.item_class, game_data),
            balance.produced,
            balance.recycled,
            balance.stored,
            balance.exported,
            balance.discarded,
        )
    log("3. Electricite : %.1f MW au total", report.power_total_mw)
    for building, power in sorted(report.power_by_building.items()):
        log("   %-28s %8.1f MW", building_name(building, game_data), power)

    log("Production finale : %s", rates(report.final_outputs, game_data))
    if report.discarded_outputs:
        log("Rejets assumes    : %s", rates(report.discarded_outputs, game_data))
    for buffer in report.buffers:
        detail = f"net {buffer.net:+g}"
        if buffer.minutes_to_full is not None:
            detail += f", sature en {buffer.minutes_to_full:.1f} min"
        if buffer.minutes_to_empty is not None:
            detail += f", vide en {buffer.minutes_to_empty:.1f} min"
        log("Tampon %-16s %-10s %s", buffer.node_id, buffer.state.value, detail)


def _show_shopping_list(report: FactoryReport, game_data: GameData) -> None:
    log("%s\nLISTE DE COURSES\n%s", RULE, RULE)
    for building, count in sorted(report.shopping_list.buildings.items()):
        log("   %-28s %4d", building_name(building, game_data), count)
    for tier, count in sorted(report.shopping_list.belts_by_tier.items()):
        log("   %-28s %4d ligne(s)", f"convoyeur Mk.{tier}", count)
    for tier, count in sorted(report.shopping_list.pipes_by_tier.items()):
        log("   %-28s %4d ligne(s)", f"tuyauterie Mk.{tier}", count)
    for attachment, count in sorted(report.shopping_list.attachments.items()):
        log("   %-28s %4d", building_name(attachment, game_data), count)


def _show_diagnostics(report: FactoryReport) -> None:
    log("%s\nDIAGNOSTICS\n%s", RULE, RULE)
    if not report.diagnostics:
        log("   aucun : l'usine est nominale.")
        return
    for finding in report.diagnostics:
        log(
            "   [%-9s] %-18s %s",
            SEVERITY_LABELS[finding.severity],
            finding.node_id or finding.edge_id or "-",
            finding.message,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("graph", type=Path, help="fichier .json decrivant l'usine")
    parser.add_argument(
        "--database",
        type=Path,
        default=db.default_database_path(),
        help="base de donnees de jeu (defaut : celle embarquee dans le paquet)",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)

    if not args.database.is_file():
        logger.error(
            "Base introuvable : %s. Generez-la avec "
            "python -m satisplanner.data.build --game-dir ...",
            args.database,
        )
        return 2

    game_data = db.load_game_data_from_file(args.database)
    graph = FactoryGraph.model_validate_json(args.graph.read_text(encoding="utf-8"))
    report = engine.solve(graph, game_data)

    log("Usine      : %s", args.graph)
    log("Base       : %s", args.database)
    log(
        "Resolution : %s en %d iteration(s)",
        "convergee" if report.converged else "NON CONVERGEE",
        report.iterations,
    )
    show(report, game_data)
    return 0 if report.converged and not report.has_errors() else 1


if __name__ == "__main__":
    raise SystemExit(main())
