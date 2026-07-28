"""The report as HTML: the totals panel and the PDF export render the same thing.

Written as plain functions over the report rather than as widget code, for two
reasons: the PDF export needs the identical document, and a table of numbers is far
easier to assert on as a string than as a tree of layouts.

The one piece of judgement here is the **unsustainable block**. A factory living off
a tank shows perfectly healthy totals, so those totals are put side by side with the
regime that survives the stock, under a banner that cannot be scrolled past by
accident. Everywhere else the ordering follows the specification: solids, fluids and
byproducts, power, then the shopping list, then what that list costs to build.
"""

from collections.abc import Iterable, Mapping
from html import escape
from typing import Final

from satisplanner.core import construction, formatting
from satisplanner.core.models import GameData
from satisplanner.core.results import FactoryReport, Severity
from satisplanner.ui import theme

SEVERITY_LABELS: Final[dict[Severity, str]] = {
    Severity.ERROR: "Erreur",
    Severity.WARNING: "Attention",
    Severity.INFO: "Information",
}

# An empty table still needs a row, or it renders as a gap with no explanation.
_NOTHING: Final = "<tr><td class='muted'>Aucun bâtiment concerné.</td></tr>"

SEVERITY_COLOURS: Final[dict[Severity, str]] = {
    Severity.ERROR: theme.STATE_BLOCKED,
    Severity.WARNING: theme.STATE_STARVED,
    Severity.INFO: theme.TEXT_MUTED,
}


def stylesheet() -> str:
    """Inline styles: a QTextBrowser has no external sheet and neither has the PDF."""
    return f"""
    body {{ color: {theme.TEXT}; font-size: 10pt; }}
    h2 {{ color: {theme.ACCENT}; font-size: 11pt; margin: 14px 0 4px 0; }}
    h3 {{ color: {theme.TEXT}; font-size: 10pt; margin: 10px 0 2px 0; }}
    table {{ width: 100%; border-collapse: collapse; }}
    td {{ padding: 1px 4px; }}
    td.value {{ text-align: right; white-space: nowrap; }}
    .muted {{ color: {theme.TEXT_MUTED}; }}
    .banner {{ background-color: {theme.STATE_BLOCKED}; color: {theme.BACKGROUND};
               padding: 6px; font-weight: bold; }}
    .warn {{ color: {theme.STATE_STARVED}; }}
    """


def document(report: FactoryReport | None, game_data: GameData) -> str:
    """The whole totals document, ready to hand to a QTextBrowser or a QTextDocument."""
    if report is None or not report.nodes:
        return _page("<p class='muted'>Aucune usine à résumer pour l'instant.</p>")
    blocks = [
        _sustainability(report, game_data),
        _solids(report, game_data),
        _fluids(report, game_data),
        _power(report, game_data),
        _outputs(report, game_data),
        _shopping_list(report, game_data),
        _construction_materials(report, game_data),
    ]
    return _page("\n".join(block for block in blocks if block))


def _page(body: str) -> str:
    return f"<html><head><style>{stylesheet()}</style></head><body>{body}</body></html>"


# --------------------------------------------------------------------------- #
# The block that must not be missed
# --------------------------------------------------------------------------- #


def _sustainability(report: FactoryReport, game_data: GameData) -> str:
    """Two columns of figures when the factory is living on a stock.

    "The refinery runs at 100 %" and "the tank is empty in 13 minutes" are both true.
    Putting them in different parts of the report is how a planner lies by omission,
    so here they sit in the same table under a red banner.
    """
    if report.is_sustainable:
        return ""
    autonomy = report.shortest_autonomy_minutes
    deadline = formatting.duration(autonomy) if autonomy is not None else "un temps indéterminé"
    rows = [
        "<tr><td></td><td class='value'><b>Avec les stocks</b></td>"
        "<td class='value'><b>Régime établi</b></td></tr>"
    ]
    established = report.sustained
    for label, values in (
        ("Production finale", (report.final_outputs, _outputs_of(established))),
        ("Solides bruts", (report.raw_solids, _raw_solids_of(established))),
        ("Fluides bruts", (report.raw_fluids, _raw_fluids_of(established))),
    ):
        with_stock, without = values
        rows.append(
            f"<tr><td>{escape(label)}</td>"
            f"<td class='value'>{_inline_rates(with_stock, game_data)}</td>"
            f"<td class='value'>{_inline_rates(without, game_data)}</td></tr>"
        )
    power = "-" if established is None else f"{formatting.number(established.power_total_mw)} MW"
    rows.append(
        f"<tr><td>Électricité</td>"
        f"<td class='value'>{formatting.number(report.power_total_mw)} MW</td>"
        f"<td class='value'>{power}</td></tr>"
    )

    drained = "".join(
        f"<tr><td>{escape(buffer.node_id)}</td>"
        f"<td class='value warn'>{_rate_of(buffer.item_class, buffer.net, game_data)}</td>"
        f"<td class='value warn'>vide en "
        f"{formatting.duration(buffer.minutes_to_empty or 0.0)}</td></tr>"
        for buffer in report.draining_buffers
    )
    return (
        f"<p class='banner'>CES DÉBITS NE SONT PAS TENABLES — ils vivent sur un stock "
        f"pendant {escape(deadline)}.</p>"
        f"<table>{''.join(rows)}</table>"
        f"<h3>Tampons en cours de vidage</h3><table>{drained}</table>"
    )


def _outputs_of(report: FactoryReport | None) -> Mapping[str, float]:
    return {} if report is None else report.final_outputs


def _raw_solids_of(report: FactoryReport | None) -> Mapping[str, float]:
    return {} if report is None else report.raw_solids


def _raw_fluids_of(report: FactoryReport | None) -> Mapping[str, float]:
    return {} if report is None else report.raw_fluids


# --------------------------------------------------------------------------- #
# The three categories
# --------------------------------------------------------------------------- #


def _solids(report: FactoryReport, game_data: GameData) -> str:
    if not report.raw_solids:
        return "<h2>1. Solides bruts</h2><p class='muted'>Aucun minerai consomme.</p>"
    return f"<h2>1. Solides bruts</h2>{_rate_table(report.raw_solids, game_data)}"


def _fluids(report: FactoryReport, game_data: GameData) -> str:
    parts = ["<h2>2. Fluides et sous-produits</h2>"]
    if report.raw_fluids:
        parts.append(_rate_table(report.raw_fluids, game_data))
    else:
        parts.append("<p class='muted'>Aucun fluide brut consomme.</p>")
    if report.byproducts:
        parts.append("<h3>Bilan des sous-produits</h3><table>")
        for balance in report.byproducts:
            item = game_data.items.get(balance.item_class)
            name = item.display_name_fr if item else balance.item_class
            detail = (
                f"produit {formatting.number(balance.produced)}, "
                f"recycle {formatting.number(balance.recycled)}, "
                f"stocké {formatting.number(balance.stored)}, "
                f"exporté {formatting.number(balance.exported)}, "
                f"rejete {formatting.number(balance.discarded)}"
            )
            parts.append(
                f"<tr><td>{escape(name)}</td><td class='value muted'>{escape(detail)}</td></tr>"
            )
        parts.append("</table>")
    return "".join(parts)


def _power(report: FactoryReport, game_data: GameData) -> str:
    """Two numbers side by side, and no third.

    Consumption counts machines that are stopped, because they are built and
    plugged in. Production counts only what is actually burnt, because a generator
    with no coal produces nothing. Neither figure bridles anything: electricity is
    a counter here, never a term of the ``min()`` -- see the help page.
    """
    consumed = "".join(
        f"<tr><td>{escape(_building_name(class_name, game_data))}</td>"
        f"<td class='value'>{formatting.number(power)} MW</td></tr>"
        for class_name, power in sorted(report.power_by_building.items())
    )
    produced = "".join(
        f"<tr><td>{escape(_building_name(class_name, game_data))}</td>"
        f"<td class='value'>{formatting.number(power)} MW</td></tr>"
        for class_name, power in sorted(report.power_production_by_building.items())
    )
    balance = ""
    if report.has_power_deficit:
        balance = (
            f"<p class='warn'><b>{formatting.number(abs(report.power_balance_mw))} MW manquants."
            f"</b> Ce déficit ne bride aucun débit ci-dessus : en jeu, manquer de courant ne "
            f"ralentit pas l'usine, cela coupe tout le réseau jusqu'a intervention manuelle.</p>"
        )
    elif report.has_generators:
        balance = (
            f"<p class='muted'><b>{formatting.number(report.power_balance_mw)} MW</b> de marge.</p>"
        )
    return (
        f"<h2>3. Électricité</h2>"
        f"<table><tr>"
        f"<td class='value'><b>{formatting.number(report.power_total_mw)} MW consommés</b></td>"
        f"<td class='value'><b>{formatting.number(report.power_production_mw)} MW produits"
        f"</b></td></tr></table>"
        f"{balance}"
        f"<h3>Consommation, machines à l'arrêt comprises : elles sont construites</h3>"
        f"<table>{consumed or _NOTHING}</table>"
        f"<h3>Production</h3>"
        f"<table>{produced or _NOTHING}</table>"
    )


def _outputs(report: FactoryReport, game_data: GameData) -> str:
    parts = [f"<h2>Production</h2>{_rate_table(report.final_outputs, game_data)}"]
    if report.discarded_outputs:
        parts.append("<h3>Rejets assumés</h3>")
        parts.append(_rate_table(report.discarded_outputs, game_data))
    return "".join(parts)


def _shopping_list(report: FactoryReport, game_data: GameData) -> str:
    shopping = report.shopping_list
    rows = [
        f"<tr><td>{escape(_building_name(class_name, game_data))}</td>"
        f"<td class='value'>{count}</td></tr>"
        for class_name, count in sorted(shopping.buildings.items())
    ]
    rows.extend(
        f"<tr><td>Convoyeur Mk.{tier}</td><td class='value'>{count} ligne(s)</td></tr>"
        for tier, count in sorted(shopping.belts_by_tier.items())
    )
    rows.extend(
        f"<tr><td>Tuyauterie Mk.{tier}</td><td class='value'>{count} ligne(s)</td></tr>"
        for tier, count in sorted(shopping.pipes_by_tier.items())
    )
    rows.extend(
        f"<tr><td>{escape(_building_name(class_name, game_data))}</td>"
        f"<td class='value'>{count}</td></tr>"
        for class_name, count in sorted(shopping.attachments.items())
    )
    # Consumables rather than buildings, so they are listed apart from the count.
    shard_rows = [
        f"<tr><td>{escape(_item_name(class_name, game_data))}</td>"
        f"<td class='value'>{count}</td></tr>"
        for class_name, count in sorted(shopping.power_shards.items())
    ]
    note = (
        f"<p class='muted'>{shopping.total_buildings} bâtiment(s) au total. Les répartiteurs et "
        f"groupeurs sont déduits des lignes qui partagent un nœud."
    )
    if shard_rows:
        note += " Les éclats ne se construisent pas : ils se fabriquent et se glissent dans"
        note += " les machines surcadencées."
    return (
        f"<h2>Liste de courses</h2><table>{''.join(rows) + ''.join(shard_rows)}</table>{note}</p>"
    )


def _construction_materials(report: FactoryReport, game_data: GameData) -> str:
    """What to make before any of the above can be put down.

    Ordered by quantity rather than by name: this is a list to work through, and
    the thing there is most of is the thing to start on.
    """
    materials = construction.materials_for(report.shopping_list, game_data)
    rows = [
        f"<tr><td>{escape(_item_name(class_name, game_data))}</td>"
        f"<td class='value'>{formatting.number(amount)}</td></tr>"
        for class_name, amount in sorted(
            materials.amounts.items(), key=lambda pair: (-pair[1], pair[0])
        )
    ]
    notes = []
    if materials.line_count:
        notes.append(
            f"{materials.line_count} ligne(s) — {materials.belt_lines} de convoyeur et "
            f"{materials.pipe_lines} de tuyauterie — <b>ne sont pas chiffrées</b> : leur coût "
            "se paie à la longueur, et l'outil ne connaît aucune distance. Une estimation "
            "à partir d'une longueur moyenne serait un chiffre inventé au milieu de chiffres "
            "exacts, ce qui est pire qu'un blanc."
        )
    if materials.unpriced:
        missing = ", ".join(_building_name(name, game_data) for name in materials.unpriced)
        notes.append(
            f"<span class='warn'>Sans recette de construction dans ce catalogue, donc absent(s) "
            f"du total : {escape(missing)}.</span>"
        )
    body = "".join(rows) or _NOTHING
    note = "".join(f"<p class='muted'>{text}</p>" for text in notes)
    return f"<h2>Matériaux de construction</h2><table>{body}</table>{note}"


# --------------------------------------------------------------------------- #
# Diagnostics, for the PDF and for anything that wants them as text
# --------------------------------------------------------------------------- #


def diagnostics_section(report: FactoryReport) -> str:
    if not report.diagnostics:
        return "<h2>Diagnostics</h2><p class='muted'>Aucun : l'usine est nominale.</p>"
    rows = "".join(
        f"<tr><td class='value' style='color:{SEVERITY_COLOURS[item.severity]}'>"
        f"{SEVERITY_LABELS[item.severity]}</td>"
        f"<td class='muted'>{escape(item.node_id or item.edge_id or '')}</td>"
        f"<td>{escape(item.message)}</td></tr>"
        for item in report.diagnostics
    )
    return f"<h2>Diagnostics</h2><table>{rows}</table>"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _rate_table(values: Mapping[str, float], game_data: GameData) -> str:
    if not values:
        return "<p class='muted'>Rien.</p>"
    rows = "".join(
        f"<tr><td>{escape(_item_name(item_class, game_data))}</td>"
        f"<td class='value'>{_rate_of(item_class, rate, game_data)}</td></tr>"
        for item_class, rate in sorted(values.items())
    )
    return f"<table>{rows}</table>"


def _inline_rates(values: Mapping[str, float], game_data: GameData) -> str:
    if not values:
        return "<span class='muted'>rien</span>"
    return escape(
        ", ".join(
            f"{_item_name(item_class, game_data)} "
            f"{_rate_of(item_class, rate, game_data, markup=False)}"
            for item_class, rate in sorted(values.items())
        )
    )


def _rate_of(
    item_class: str | None, rate: float, game_data: GameData, *, markup: bool = True
) -> str:
    item = game_data.items.get(item_class) if item_class else None
    text = formatting.rate(rate, item)
    return escape(text) if markup else text


def _item_name(class_name: str, game_data: GameData) -> str:
    item = game_data.items.get(class_name)
    return item.display_name_fr if item else class_name


def _building_name(class_name: str, game_data: GameData) -> str:
    building = game_data.buildings.get(class_name)
    return building.display_name_fr if building else class_name


def plain_lines(report: FactoryReport, game_data: GameData) -> Iterable[str]:
    """A flat text rendering, used by the tests and by anything that logs a report."""
    yield f"Production : {_inline_rates(report.final_outputs, game_data)}"
    yield f"Solides : {_inline_rates(report.raw_solids, game_data)}"
    yield f"Fluides : {_inline_rates(report.raw_fluids, game_data)}"
    yield (
        f"Électricité : {formatting.number(report.power_total_mw)} MW consommés, "
        f"{formatting.number(report.power_production_mw)} MW produits"
    )
