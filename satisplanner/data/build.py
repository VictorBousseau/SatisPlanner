"""CLI that turns the game documentation into the shipped SQLite database.

    python -m satisplanner.data.build --game-dir "C:\\...\\Satisfactory"

Maintenance tool, not a user-facing flow: the generated database is committed and
embedded, so end users never run this.
"""

import argparse
import logging
import sys
from collections import Counter
from pathlib import Path
from typing import Final

from satisplanner.core.models import BuildingKind, Item, ItemForm
from satisplanner.data import db
from satisplanner.data.docs_parser import DocsFileError, GameDataset, load_dataset
from satisplanner.data.icons import IconIndex, embedded_icon_directory

logger = logging.getLogger("satisplanner.data.build")

DEFAULT_OUTPUT: Final = db.default_database_path()

# Beyond this many missing icons, the list is truncated: it would be noise.
MAX_LISTED_MISSING: Final = 20

FORM_LABELS_FR: Final[dict[ItemForm, str]] = {
    ItemForm.SOLID: "solide",
    ItemForm.LIQUID: "liquide",
    ItemForm.GAS: "gaz",
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m satisplanner.data.build",
        description="Genere la base de donnees de jeu depuis le dossier d'installation.",
    )
    parser.add_argument(
        "--game-dir",
        type=Path,
        required=True,
        help="dossier d'installation de Satisfactory (ou directement son dossier Docs)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"fichier SQLite a produire (defaut : {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--icons-dir",
        type=Path,
        default=embedded_icon_directory(),
        help="dossier d'icones a confronter aux donnees (defaut : icones embarquees)",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="trace detaillee")
    return parser.parse_args(argv)


def scoped_items(dataset: GameDataset) -> list[Item]:
    """Items the V1 palette shows by default.

    That is every item involved in a kept recipe, plus every raw resource, since a
    miner can be placed on any node. Event content is excluded: it is hidden behind
    a checkbox, so a missing icon there is not a gap worth reporting.
    """
    referenced = {
        slot.item_class
        for recipe in dataset.recipes
        for slot in (*recipe.ingredients, *recipe.products)
    }
    referenced.update(
        extractor.item_class for extractor in dataset.extractors if extractor.item_class
    )
    return [
        item
        for item in dataset.items
        if (item.class_name in referenced or item.is_raw_resource) and not item.is_event
    ]


def report(dataset: GameDataset, output: Path, icons: IconIndex) -> None:
    """Print the end-of-run summary required by the specification."""
    log = logger.info

    log("Base generee            : %s (%.1f Ko)", output, output.stat().st_size / 1024)
    log("Fichier de reference    : %s", dataset.source_file)
    if not dataset.reference_was_preferred:
        logger.warning(
            "  ATTENTION : aucun fichier de locale connu, choix par defaut sur le premier .json"
        )
    log("Libelles francais       : %s", dataset.french_file or "ABSENT (repli sur l'anglais)")

    forms = Counter(item.form for item in dataset.items)
    detail = ", ".join(f"{FORM_LABELS_FR[form]} {forms[form]}" for form in ItemForm)
    log("Items                   : %d  (%s)", len(dataset.items), detail)

    events = sum(1 for item in dataset.items if item.is_event)
    log("    dont evenement (FICSMAS) : %d, masques par defaut dans la palette", events)

    alternates = sum(1 for recipe in dataset.recipes if recipe.is_alternate)
    fluids = sum(1 for recipe in dataset.recipes if recipe.involves_fluid)
    byproducts = sum(1 for recipe in dataset.recipes if recipe.product_count > 1)
    event_recipes = sum(1 for recipe in dataset.recipes if recipe.is_event)
    log(
        "Recettes                : %d  (alternatives %d, fluides %d, a sous-produit %d,"
        " evenement %d)",
        len(dataset.recipes),
        alternates,
        fluids,
        byproducts,
        event_recipes,
    )

    per_machine = Counter(recipe.building_class for recipe in dataset.recipes)
    names = {building.class_name: building.display_name_fr for building in dataset.buildings}
    for machine, count in sorted(per_machine.items(), key=lambda pair: -pair[1]):
        log("    %-28s %3d recettes", names.get(machine, machine), count)

    kinds = Counter(building.kind for building in dataset.buildings)
    log(
        "Batiments               : %d  (%s)",
        len(dataset.buildings),
        ", ".join(f"{kind.value} {kinds[kind]}" for kind in BuildingKind if kinds[kind]),
    )
    for belt in sorted(dataset.belts, key=lambda b: b.tier):
        log("    convoyeur Mk.%d           %7.0f items/min", belt.tier, belt.items_per_minute)
    for pipe in sorted(dataset.pipes, key=lambda p: p.tier):
        log("    tuyauterie Mk.%d          %7.0f m3/min", pipe.tier, pipe.cubic_metres_per_minute)
    for extractor in sorted(dataset.extractors, key=lambda e: e.class_name):
        unit = "m3/min" if extractor.allowed_form.is_fluid else "items/min"
        log(
            "    %-24s %7.0f %s%s",
            names.get(extractor.class_name, extractor.class_name),
            extractor.rate_per_minute,
            unit,
            "" if extractor.has_purity else " (debit fixe, sans purete)",
        )
    for storage in sorted(dataset.storages, key=lambda s: s.class_name):
        capacity = (
            f"{storage.slots} emplacements"
            if storage.slots is not None
            else f"{storage.capacity_m3:.0f} m3"
        )
        log("    %-24s %s", names.get(storage.class_name, storage.class_name), capacity)
    for attachment in sorted(dataset.attachments, key=lambda a: a.class_name):
        roles = "/".join(role.value for role in attachment.roles)
        log(
            "    %-24s %s, %d branches",
            names.get(attachment.class_name, attachment.class_name),
            roles,
            attachment.branches,
        )

    _report_icons(dataset, icons)

    if dataset.warnings:
        logger.warning("Avertissements du parseur (%d) :", len(dataset.warnings))
        for warning in dataset.warnings:
            logger.warning("    %s", warning)
    else:
        log("Avertissements          : aucun")


def _report_icons(dataset: GameDataset, icons: IconIndex) -> None:
    log = logger.info
    log(
        "Icones indexees         : %d fichier(s) dans %s",
        len(icons),
        ", ".join(str(root) for root in icons.roots) or "aucun dossier",
    )

    in_scope = scoped_items(dataset)
    missing_items = [item for item in in_scope if icons.resolve(item.icon_file) is None]
    log(
        "Items du perimetre V1   : %d, dont %d sans icone",
        len(in_scope),
        len(missing_items),
    )
    if missing_items:
        for item in missing_items[:MAX_LISTED_MISSING]:
            logger.warning(
                "    sans icone : %-30s %s", item.class_name, item.icon_file or "(aucune declaree)"
            )
        if len(missing_items) > MAX_LISTED_MISSING:
            logger.warning("    ... et %d autre(s)", len(missing_items) - MAX_LISTED_MISSING)

    missing_buildings = sum(
        1 for building in dataset.buildings if icons.resolve(building.icon_file) is None
    )
    log(
        "Batiments               : %d, dont %d sans icone "
        "(attendu : l'export ne couvre que Resource/)",
        len(dataset.buildings),
        missing_buildings,
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(message)s",
        stream=sys.stdout,
    )

    try:
        dataset = load_dataset(args.game_dir)
    except DocsFileError as exc:
        logger.error("Echec : %s", exc)
        return 2

    db.build_database(dataset, args.output)
    report(dataset, args.output, IconIndex([args.icons_dir]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
