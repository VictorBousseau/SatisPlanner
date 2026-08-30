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
from satisplanner.core.i18n import _
from satisplanner.core.models import GameData
from satisplanner.core.results import FactoryReport, Severity
from satisplanner.ui import theme


def severity_label(severity: Severity) -> str:
    """How a finding's severity reads, in the report and in the panel alike."""
    match severity:
        case Severity.ERROR:
            return _("Erreur")
        case Severity.WARNING:
            return _("Attention")
        case Severity.INFO:
            return _("Information")


def _nothing() -> str:
    """An empty table still needs a row, or it renders as a gap with no explanation."""
    return f"<tr><td class='muted'>{_('Aucun bâtiment concerné.')}</td></tr>"

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
        return _page(f"<p class='muted'>{_('Aucune usine à résumer pour l\'instant.')}</p>")
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
    deadline = (
        formatting.duration(autonomy) if autonomy is not None else _("un temps indéterminé")
    )
    rows = [
        f"<tr><td></td><td class='value'><b>{_('Avec les stocks')}</b></td>"
        f"<td class='value'><b>{_('Régime établi')}</b></td></tr>"
    ]
    established = report.sustained
    for label, values in (
        (_("Production finale"), (report.final_outputs, _outputs_of(established))),
        (_("Solides bruts"), (report.raw_solids, _raw_solids_of(established))),
        (_("Fluides bruts"), (report.raw_fluids, _raw_fluids_of(established))),
    ):
        with_stock, without = values
        rows.append(
            f"<tr><td>{escape(label)}</td>"
            f"<td class='value'>{_inline_rates(with_stock, game_data)}</td>"
            f"<td class='value'>{_inline_rates(without, game_data)}</td></tr>"
        )
    power = "-" if established is None else f"{formatting.number(established.power_total_mw)} MW"
    rows.append(
        f"<tr><td>{_('Électricité')}</td>"
        f"<td class='value'>{formatting.number(report.power_total_mw)} MW</td>"
        f"<td class='value'>{power}</td></tr>"
    )

    empty_in = _("vide en {deadline}")
    drained = "".join(
        f"<tr><td>{escape(buffer.node_id)}</td>"
        f"<td class='value warn'>{_rate_of(buffer.item_class, buffer.net, game_data)}</td>"
        f"<td class='value warn'>"
        f"{empty_in.format(deadline=formatting.duration(buffer.minutes_to_empty or 0.0))}"
        f"</td></tr>"
        for buffer in report.draining_buffers
    )
    banner = _("CES DÉBITS NE SONT PAS TENABLES — ils vivent sur un stock pendant {deadline}.")
    return (
        f"<p class='banner'>{banner.format(deadline=escape(deadline))}</p>"
        f"<table>{''.join(rows)}</table>"
        f"<h3>{_('Tampons en cours de vidage')}</h3><table>{drained}</table>"
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
    heading = f"<h2>1. {_('Solides bruts')}</h2>"
    if not report.raw_solids:
        return f"{heading}<p class='muted'>{_('Aucun minerai consommé.')}</p>"
    return f"{heading}{_rate_table(report.raw_solids, game_data)}"


def _fluids(report: FactoryReport, game_data: GameData) -> str:
    parts = [f"<h2>2. {_('Fluides et sous-produits')}</h2>"]
    if report.raw_fluids:
        parts.append(_rate_table(report.raw_fluids, game_data))
    else:
        parts.append(f"<p class='muted'>{_('Aucun fluide brut consommé.')}</p>")
    if report.byproducts:
        parts.append(f"<h3>{_('Bilan des sous-produits')}</h3><table>")
        for balance in report.byproducts:
            item = game_data.items.get(balance.item_class)
            name = item.name if item else balance.item_class
            detail = _(
                "produit {made}, recyclé {recycled}, stocké {stored}, "
                "exporté {exported}, rejeté {discarded}"
            ).format(
                made=formatting.number(balance.produced),
                recycled=formatting.number(balance.recycled),
                stored=formatting.number(balance.stored),
                exported=formatting.number(balance.exported),
                discarded=formatting.number(balance.discarded),
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
        missing = _("<b>{missing} MW manquants.</b> Ce déficit ne bride aucun débit ci-dessus : "
                    "en jeu, manquer de courant ne ralentit pas l'usine, cela coupe tout le "
                    "réseau jusqu'a intervention manuelle.")
        balance = (
            f"<p class='warn'>"
            f"{missing.format(missing=formatting.number(abs(report.power_balance_mw)))}</p>"
        )
    elif report.has_generators:
        margin = _("<b>{margin} MW</b> de marge.")
        balance = (
            f"<p class='muted'>"
            f"{margin.format(margin=formatting.number(report.power_balance_mw))}</p>"
        )
    consumed_mw = _("{power} MW consommés").format(
        power=formatting.number(report.power_total_mw)
    )
    produced_mw = _("{power} MW produits").format(
        power=formatting.number(report.power_production_mw)
    )
    consumption_heading = _("Consommation, machines à l'arrêt comprises : elles sont construites")
    return (
        f"<h2>3. {_('Électricité')}</h2>"
        f"<table><tr>"
        f"<td class='value'><b>{consumed_mw}</b></td>"
        f"<td class='value'><b>{produced_mw}</b></td></tr></table>"
        f"{balance}"
        f"<h3>{consumption_heading}</h3>"
        f"<table>{consumed or _nothing()}</table>"
        f"<h3>{_('Production')}</h3>"
        f"<table>{produced or _nothing()}</table>"
    )


def _outputs(report: FactoryReport, game_data: GameData) -> str:
    parts = [f"<h2>{_('Production')}</h2>{_rate_table(report.final_outputs, game_data)}"]
    if report.discarded_outputs:
        parts.append(f"<h3>{_('Rejets assumés')}</h3>")
        parts.append(_rate_table(report.discarded_outputs, game_data))
    return "".join(parts)


def _shopping_list(report: FactoryReport, game_data: GameData) -> str:
    shopping = report.shopping_list
    rows = [
        f"<tr><td>{escape(_building_name(class_name, game_data))}</td>"
        f"<td class='value'>{count}</td></tr>"
        for class_name, count in sorted(shopping.buildings.items())
    ]
    # The tier is written into the name rather than looked up, because the list adds
    # up lines and not buildings -- and the two words are the game's own, in both
    # languages, so the catalogue carries "Conveyor Belt Mk." and "Pipeline Mk.".
    rows.extend(
        f"<tr><td>{_('Convoyeur Mk.{tier}').format(tier=tier)}</td>"
        f"<td class='value'>{_('{count} ligne(s)').format(count=count)}</td></tr>"
        for tier, count in sorted(shopping.belts_by_tier.items())
    )
    rows.extend(
        f"<tr><td>{_('Tuyauterie Mk.{tier}').format(tier=tier)}</td>"
        f"<td class='value'>{_('{count} ligne(s)').format(count=count)}</td></tr>"
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
    total = _(
        "{count} bâtiment(s) au total. Les répartiteurs et groupeurs sont ceux qui "
        "figurent sur le plan : un port porte une ligne, et il en faut un dès qu'il "
        "en part davantage."
    ).format(count=shopping.total_buildings)
    note = f"<p class='muted'>{total}"
    if shard_rows:
        note += " " + _(
            "Les éclats ne se construisent pas : ils se fabriquent et se glissent "
            "dans les machines surcadencées."
        )
    return (
        f"<h2>{_('Liste de courses')}</h2>"
        f"<table>{''.join(rows) + ''.join(shard_rows)}</table>{note}</p>"
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
            _(
                "{lines} ligne(s) — {belts} de convoyeur et {pipes} de tuyauterie — "
                "<b>ne sont pas chiffrées</b> : leur coût se paie à la longueur, et "
                "l'outil ne connaît aucune distance. Une estimation à partir d'une "
                "longueur moyenne serait un chiffre inventé au milieu de chiffres "
                "exacts, ce qui est pire qu'un blanc."
            ).format(
                lines=materials.line_count,
                belts=materials.belt_lines,
                pipes=materials.pipe_lines,
            )
        )
    if materials.unpriced:
        missing = ", ".join(_building_name(name, game_data) for name in materials.unpriced)
        absent = _(
            "Sans recette de construction dans ce catalogue, donc absent(s) du total : "
            "{buildings}."
        ).format(buildings=escape(missing))
        notes.append(f"<span class='warn'>{absent}</span>")
    body = "".join(rows) or _nothing()
    note = "".join(f"<p class='muted'>{text}</p>" for text in notes)
    return f"<h2>{_('Matériaux de construction')}</h2><table>{body}</table>{note}"


# --------------------------------------------------------------------------- #
# Diagnostics, for the PDF and for anything that wants them as text
# --------------------------------------------------------------------------- #


def diagnostics_section(report: FactoryReport) -> str:
    if not report.diagnostics:
        nominal = _("Aucun : l'usine est nominale.")
        return f"<h2>Diagnostics</h2><p class='muted'>{nominal}</p>"
    rows = "".join(
        f"<tr><td class='value' style='color:{SEVERITY_COLOURS[item.severity]}'>"
        f"{severity_label(item.severity)}</td>"
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
        return f"<p class='muted'>{_('Rien.')}</p>"
    rows = "".join(
        f"<tr><td>{escape(_item_name(item_class, game_data))}</td>"
        f"<td class='value'>{_rate_of(item_class, rate, game_data)}</td></tr>"
        for item_class, rate in sorted(values.items())
    )
    return f"<table>{rows}</table>"


def _inline_rates(values: Mapping[str, float], game_data: GameData) -> str:
    if not values:
        return f"<span class='muted'>{_('rien')}</span>"
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
    return item.name if item else class_name


def _building_name(class_name: str, game_data: GameData) -> str:
    building = game_data.buildings.get(class_name)
    return building.name if building else class_name


def plain_lines(report: FactoryReport, game_data: GameData) -> Iterable[str]:
    """A flat text rendering, used by the tests and by anything that logs a report."""
    # Whole lines rather than a label glued to " : ", because the space before a
    # colon is French typography and English does not want it.
    yield _("Production : {rates}").format(
        rates=_inline_rates(report.final_outputs, game_data)
    )
    yield _("Solides : {rates}").format(rates=_inline_rates(report.raw_solids, game_data))
    yield _("Fluides : {rates}").format(rates=_inline_rates(report.raw_fluids, game_data))
    yield _("Électricité : {consumed} MW consommés, {produced} MW produits").format(
        consumed=formatting.number(report.power_total_mw),
        produced=formatting.number(report.power_production_mw),
    )
