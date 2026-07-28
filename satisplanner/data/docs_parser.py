"""Parser for the game's own documentation dump.

The game ships one JSON file per locale in ``CommunityResources/Docs``. All of them
share the same structure and the same class identifiers; only the labels differ.
The reference file is therefore read for structure, and ``fr.json`` for labels.

Three traps are handled explicitly here:

* the files are UTF-16 with a BOM -- reading them as UTF-8 fails outright;
* numbers are stored as strings, and composite properties such as ``mIngredients``
  are pseudo-structured strings that need their own parsing;
* fluid and gas amounts are in litres (see :mod:`satisplanner.data.conversions`).

Identifiers are always class paths (``Desc_IronOre_C``), never display names.
"""

import json
import logging
import re
from collections.abc import Iterable, Iterator, Sequence
from pathlib import Path
from typing import Any, Final

from pydantic import BaseModel, ConfigDict

from satisplanner.core import constants
from satisplanner.core.models import (
    Attachment,
    AttachmentRole,
    Belt,
    Building,
    BuildingCost,
    BuildingKind,
    Extractor,
    GameData,
    Generator,
    GeneratorFuel,
    Item,
    ItemForm,
    Pipe,
    PowerShard,
    Recipe,
    RecipeSlot,
    Storage,
)
from satisplanner.data import conversions

logger = logging.getLogger(__name__)

# One class of the dump. Almost every value is a string -- numbers included, which
# is why :func:`parse_float` exists -- but a handful of fields are real JSON: a
# generator's ``mFuel`` is a list of objects. The value type is therefore ``Any``
# rather than ``str``, so that the annotation says what the file actually holds.
ClassEntry = dict[str, Any]
# A whole locale file, grouped by short native class name.
Locale = dict[str, list[ClassEntry]]

# --------------------------------------------------------------------------- #
# Locating and reading the source files
# --------------------------------------------------------------------------- #

DOCS_SUBPATH: Final = Path("CommunityResources") / "Docs"
FRENCH_FILENAME: Final = "fr.json"

# Preference order for the structural reference. No filename is ever assumed to
# exist: the directory is inspected and the first available candidate wins.
REFERENCE_CANDIDATES: Final[tuple[str, ...]] = (
    "en-US.json",
    "en-GB.json",
    "en-AU.json",
    "en-CA.json",
    "en-AE.json",
    "Docs.json",  # historical name, used by versions before the locale split
)

# Tried in order. The game writes UTF-16 LE with a BOM; the fallbacks exist so a
# hand-edited or re-encoded file still loads instead of crashing the CLI.
TEXT_ENCODINGS: Final[tuple[str, ...]] = ("utf-16", "utf-16-le", "utf-8-sig")


class DocsFileError(RuntimeError):
    """The documentation file could not be located or decoded."""


def locate_docs_directory(game_dir: Path) -> Path:
    """Return the ``Docs`` directory, accepting either the game root or Docs itself."""
    candidates = (game_dir / DOCS_SUBPATH, game_dir)
    for candidate in candidates:
        if candidate.is_dir() and any(candidate.glob("*.json")):
            return candidate
    msg = (
        f"aucun fichier .json trouvé dans {game_dir / DOCS_SUBPATH} "
        f"ni dans {game_dir} : vérifiez --game-dir"
    )
    raise DocsFileError(msg)


def select_reference_file(docs_dir: Path) -> tuple[Path, bool]:
    """Pick the structural reference file.

    Returns the chosen path and whether it came from the preferred candidates. A
    false flag means the caller must warn the user: the choice was a blind
    fallback on whatever JSON happened to be there.
    """
    available = {path.name: path for path in sorted(docs_dir.glob("*.json"))}
    for name in REFERENCE_CANDIDATES:
        if name in available:
            return available[name], True
    if not available:
        msg = f"aucun fichier .json dans {docs_dir}"
        raise DocsFileError(msg)
    return next(iter(available.values())), False


def read_text_file(path: Path) -> str:
    """Read a text file, trying the known encodings in order and logging the winner."""
    errors: list[str] = []
    for encoding in TEXT_ENCODINGS:
        try:
            text = path.read_text(encoding=encoding)
        except (UnicodeDecodeError, UnicodeError) as exc:
            errors.append(f"{encoding}: {exc}")
            continue
        logger.debug("%s lu en %s", path.name, encoding)
        # A stray BOM survives 'utf-16-le' and would break json.loads.
        return text.lstrip("﻿")
    detail = " | ".join(errors)
    msg = f"impossible de décoder {path} ({detail})"
    raise DocsFileError(msg)


def read_locale(path: Path) -> Locale:
    """Load one locale file and group its classes by short native class name."""
    raw = json.loads(read_text_file(path))
    grouped: Locale = {}
    for entry in raw:
        native = entry["NativeClass"].rsplit(".", 1)[-1].strip("'\"")
        grouped.setdefault(native, []).extend(entry["Classes"])
    return grouped


def french_labels(grouped: Locale) -> dict[str, str]:
    """Flatten a locale into ``class name -> display name``.

    Class names are unique across native classes, so a single flat mapping is
    enough and spares the caller from knowing where a class lives.
    """
    return {
        cls["ClassName"]: cls["mDisplayName"]
        for classes in grouped.values()
        for cls in classes
        if cls.get("ClassName") and cls.get("mDisplayName")
    }


def french_descriptions(grouped: Locale) -> dict[str, str]:
    """Flatten a locale into ``class name -> description``.

    Separate from :func:`french_labels` because most classes have a label and only
    some have a blurb, and an item card must be able to tell "no description in the
    data" from "description not loaded".
    """
    return {
        cls["ClassName"]: cls["mDescription"]
        for classes in grouped.values()
        for cls in classes
        if cls.get("ClassName") and cls.get("mDescription")
    }


# --------------------------------------------------------------------------- #
# Composite property parsing
# --------------------------------------------------------------------------- #

# ItemClass="/Script/Engine.BlueprintGeneratedClass'/Game/.../Desc_OreIron.Desc_OreIron_C'",Amount=1
_ITEM_AMOUNT_RE: Final = re.compile(
    r"""ItemClass\s*=\s*"[^"]*\.(?P<cls>\w+)'?"\s*,\s*Amount\s*=\s*(?P<amount>-?\d+)"""
)
# Quoted entries of a list property such as mProducedIn.
_QUOTED_RE: Final = re.compile(r'"([^"]+)"')
# Class paths inside mAllowedResources.
_RESOURCE_CLASS_RE: Final = re.compile(r"\.(\w+_C)'")
# Texture2D /Game/.../UI/IconDesc_Plastic_256.IconDesc_Plastic_256
_ICON_RE: Final = re.compile(r"/(?P<file>[^/.\s]+)\.[^/.\s]+\s*$")


def parse_item_amounts(raw: str) -> list[tuple[str, int]]:
    """Parse ``mIngredients`` / ``mProduct`` into ``(item class, raw amount)`` pairs.

    Amounts stay raw here: normalising them requires knowing the item's form,
    which only the caller has.
    """
    return [
        (match.group("cls"), int(match.group("amount")))
        for match in _ITEM_AMOUNT_RE.finditer(raw or "")
    ]


def parse_class_list(raw: str) -> list[str]:
    """Parse a list of class paths into short class names, order preserved."""
    return [entry.rsplit(".", 1)[-1].strip("'\" ") for entry in _QUOTED_RE.findall(raw or "")]


def parse_allowed_resources(raw: str) -> list[str]:
    """Parse ``mAllowedResources`` into item class names."""
    return _RESOURCE_CLASS_RE.findall(raw or "")


def parse_icon_filename(raw: str | None) -> str | None:
    """Extract the icon's file name from ``mSmallIcon`` / ``mPersistentBigIcon``.

    The asset path repeats the file name after a dot. Only the 256 px variant is
    exported by the documented FModel procedure, so a ``_512`` asset is rewritten
    to ``_256``. Returns ``None`` when the class declares no icon.
    """
    match = _ICON_RE.search(raw or "")
    if match is None:
        return None
    name = match.group("file")
    if name.endswith("_512"):
        name = f"{name.removesuffix('_512')}_256"
    return f"{name}.png"


def parse_float(raw: str | None, default: float = 0.0) -> float:
    """Read a numeric field that the game stores as a string."""
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


# --------------------------------------------------------------------------- #
# V1 scope
# --------------------------------------------------------------------------- #

PRODUCTION_MACHINES: Final[frozenset[str]] = frozenset(
    {
        "Build_SmelterMk1_C",
        "Build_FoundryMk1_C",
        "Build_ConstructorMk1_C",
        "Build_AssemblerMk1_C",
        "Build_ManufacturerMk1_C",
        "Build_OilRefinery_C",
        "Build_Packager_C",
    }
)

# The build gun. Anything crafted "in" it is a building being put down rather than
# a part being manufactured, which is exactly how the game states a build cost.
BUILD_GUN: Final = "BP_BuildGun_C"

# Out of V1 scope on purpose (specification 5.3). Listed rather than inferred so
# that a machine added by a future game version shows up as unknown instead of
# being silently swallowed.
EXCLUDED_MACHINES: Final[frozenset[str]] = frozenset(
    {
        "Build_Blender_C",
        "Build_Converter_C",
        "Build_HadronCollider_C",
        "Build_QuantumEncoder_C",
    }
)

# Generators in scope. The biomass burner, the coal generator and the fuel
# generator: everything that burns an item a V1 factory can produce or extract.
GENERATORS: Final[frozenset[str]] = frozenset(
    {
        "Build_GeneratorBiomass_Automated_C",
        "Build_GeneratorCoal_C",
        "Build_GeneratorFuel_C",
    }
)

# Out of scope, listed rather than inferred so a generator added by a future game
# version shows up as unknown instead of being silently swallowed. The geothermal
# generator has no input at all -- its output depends on a spot on the map, which
# is geometry -- and the nuclear plant belongs to a tier this version does not model.
EXCLUDED_GENERATORS: Final[frozenset[str]] = frozenset(
    {"Build_GeneratorGeoThermal_C", "Build_GeneratorNuclear_C"}
)

# Native classes generators live under. Fuel-burning ones are the only kind that
# takes an input; the other two are named so that finding a class there is a
# deliberate exclusion rather than an oversight.
GENERATOR_NATIVE_CLASS: Final = "FGBuildableGeneratorFuel"
OTHER_GENERATOR_NATIVE_CLASSES: Final[tuple[str, ...]] = (
    "FGBuildableGeneratorGeoThermal",
    "FGBuildableGeneratorNuclear",
)

# True when the extracted node has a purity (impure / normal / pure). The Water
# Extractor has a fixed output and no node purity.
EXTRACTOR_HAS_PURITY: Final[dict[str, bool]] = {
    "Build_MinerMk1_C": True,
    "Build_MinerMk2_C": True,
    "Build_MinerMk3_C": True,
    "Build_OilPump_C": True,
    "Build_WaterPump_C": False,
}

# Slot counts are absent from the game files; see core.constants.
STORAGE_SLOTS: Final[dict[str, int]] = {
    "Build_StorageContainerMk1_C": constants.STORAGE_CONTAINER_SLOTS,
    "Build_StorageContainerMk2_C": constants.INDUSTRIAL_STORAGE_CONTAINER_SLOTS,
}
FLUID_TANKS: Final[frozenset[str]] = frozenset(
    {"Build_PipeStorageTank_C", "Build_IndustrialTank_C"}
)

# Line attachments. They are never nodes on the canvas -- a node with three outgoing
# lines is drawn as three lines -- but they have to be built, so the shopping list
# counts them. Port counts come from the buildings themselves and are not in the
# documentation dump: a conveyor splitter is one input and three outputs, a conveyor
# merger three inputs and one output, and a pipe junction has four ports and so does
# either job. The smart and programmable splitters and the priority merger are V2.
ATTACHMENTS: Final[dict[str, tuple[ItemForm, tuple[AttachmentRole, ...], int]]] = {
    "Build_ConveyorAttachmentSplitter_C": (ItemForm.SOLID, (AttachmentRole.SPLIT,), 3),
    "Build_ConveyorAttachmentMerger_C": (ItemForm.SOLID, (AttachmentRole.MERGE,), 3),
    "Build_PipelineJunction_Cross_C": (
        ItemForm.LIQUID,
        (AttachmentRole.SPLIT, AttachmentRole.MERGE),
        3,
    ),
}

# Native classes the attachments above live under, so a class that moves between
# them in a future game version shows up as missing instead of being ignored.
ATTACHMENT_NATIVE_CLASSES: Final[tuple[str, ...]] = (
    "FGBuildableAttachmentSplitter",
    "FGBuildableAttachmentMerger",
    "FGBuildablePipelineJunction",
)

# `mPowerShardType` of the shard that raises a clock ceiling. The other value the
# game uses on that field, PST_ProductionBoost, is the Somersloop: a different
# formula and V2 work, so it is filtered out here rather than in the engine.
OVERCLOCK_SHARD_TYPE: Final = "PST_Overclock"

_BELT_TIER_RE: Final = re.compile(r"ConveyorBeltMk(\d)_C$")
_PIPE_TIER_RE: Final = re.compile(r"Build_Pipeline(?:MK(\d))?_C$", re.IGNORECASE)

RECIPE_ALTERNATE_PREFIX: Final = "Recipe_Alternate_"
# Only ever present in the English labels, which is why the reference locale must
# stay English: the French one reads "... (alternative)".
RECIPE_ALTERNATE_LABEL: Final = "Alternate:"


# Event content (FICSMAS) lives under its own asset directory. That path is a far
# more reliable marker than a class-name heuristic: it catches every ornament and
# firework without catching legitimate items such as ammunition.
EVENT_ASSET_MARKER: Final = "/Events/"


class GameDataset(BaseModel):
    """Everything the database needs, already normalised."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_file: str
    french_file: str | None
    reference_was_preferred: bool
    items: tuple[Item, ...]
    recipes: tuple[Recipe, ...]
    buildings: tuple[Building, ...]
    building_costs: tuple[BuildingCost, ...] = ()
    extractors: tuple[Extractor, ...]
    generators: tuple[Generator, ...] = ()
    belts: tuple[Belt, ...]
    pipes: tuple[Pipe, ...]
    storages: tuple[Storage, ...]
    attachments: tuple[Attachment, ...]
    power_shards: tuple[PowerShard, ...] = ()
    warnings: tuple[str, ...]

    def to_game_data(self) -> GameData:
        """The catalogue the engine consumes."""
        return GameData.from_rows(
            items=self.items,
            recipes=self.recipes,
            buildings=self.buildings,
            extractors=self.extractors,
            generators=self.generators,
            belts=self.belts,
            pipes=self.pipes,
            storages=self.storages,
            attachments=self.attachments,
            power_shards=self.power_shards,
            building_costs=self.building_costs,
        )


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #


def _iter_all_classes(grouped: Locale) -> Iterator[ClassEntry]:
    for classes in grouped.values():
        yield from classes


def _label(class_name: str, fallback: str, labels: dict[str, str]) -> str:
    """French label if the locale provides a non-empty one, else the reference label."""
    return labels.get(class_name) or fallback


def is_event_class(cls: ClassEntry) -> bool:
    """True for seasonal event content (FICSMAS).

    Decided on the asset path rather than the class name: items expose it through
    their icon, recipes through ``FullName``. Such content is kept in the database
    but hidden by default in the palette -- it belongs to no production chain.
    """
    return any(
        EVENT_ASSET_MARKER in (cls.get(field) or "")
        for field in ("FullName", "mSmallIcon", "mPersistentBigIcon")
    )


def parse_items(
    grouped: Locale,
    labels: dict[str, str],
    descriptions: dict[str, str] | None = None,
) -> list[Item]:
    """Every descriptor that has a real resource form is an item.

    Buildings are descriptors too, but carry ``RF_INVALID``, which excludes them
    without having to enumerate native classes.
    """
    raw_resources = {
        cls["ClassName"] for cls in grouped.get("FGResourceDescriptor", []) if "ClassName" in cls
    }
    items: list[Item] = []
    seen: set[str] = set()

    for cls in _iter_all_classes(grouped):
        class_name = cls.get("ClassName", "")
        form = conversions.FORM_BY_RAW.get(cls.get("mForm", ""))
        if form is None or not class_name or class_name in seen:
            continue
        seen.add(class_name)
        display_name = cls.get("mDisplayName") or class_name
        items.append(
            Item(
                class_name=class_name,
                display_name=display_name,
                display_name_fr=_label(class_name, display_name, labels),
                description_fr=_label(class_name, cls.get("mDescription", ""), descriptions or {}),
                form=form,
                stack_size=conversions.stack_size(parse_float(cls.get("mCachedStackSize")), form),
                icon_file=parse_icon_filename(
                    cls.get("mSmallIcon") or cls.get("mPersistentBigIcon")
                ),
                sink_points=int(parse_float(cls.get("mResourceSinkPoints"))),
                is_raw_resource=class_name in raw_resources,
                is_event=is_event_class(cls),
                energy_mj=conversions.energy_mj(parse_float(cls.get("mEnergyValue")), form),
            )
        )
    return sorted(items, key=lambda item: item.class_name)


def _machine_of(recipe: ClassEntry) -> str | None:
    """The V1 production machine a recipe runs in, if any.

    ``mProducedIn`` also lists manual crafting stations (the build gun, the
    workbench, the equipment workshop); those are not machines and are ignored.
    """
    for candidate in parse_class_list(recipe.get("mProducedIn", "")):
        if candidate in PRODUCTION_MACHINES:
            return candidate
    return None


def parse_recipes(
    grouped: Locale,
    labels: dict[str, str],
    forms: dict[str, ItemForm],
    warnings: list[str],
) -> list[Recipe]:
    """Recipes of the V1 machines, with every amount normalised to per-minute rates."""
    recipes: list[Recipe] = []
    missing_items: set[str] = set()

    for cls in grouped.get("FGRecipe", []):
        machine = _machine_of(cls)
        if machine is None:
            continue
        class_name = cls["ClassName"]
        cycle_seconds = parse_float(cls.get("mManufactoringDuration"))
        if cycle_seconds <= 0:
            warnings.append(f"{class_name} : durée de cycle nulle, recette ignorée")
            continue

        slots: dict[str, tuple[RecipeSlot, ...]] = {}
        unresolved = False
        for field, key in (("mIngredients", "ingredients"), ("mProduct", "products")):
            parsed: list[RecipeSlot] = []
            for item_class, raw_amount in parse_item_amounts(cls.get(field, "")):
                form = forms.get(item_class)
                if form is None:
                    missing_items.add(item_class)
                    unresolved = True
                    continue
                parsed.append(
                    RecipeSlot(
                        item_class=item_class,
                        amount_per_cycle=conversions.normalise_amount(raw_amount, form),
                        rate_per_minute=conversions.rate_per_minute(
                            raw_amount, cycle_seconds, form
                        ),
                    )
                )
            slots[key] = tuple(parsed)

        if unresolved or not slots["products"]:
            warnings.append(f"{class_name} : item inconnu ou aucun produit, recette ignorée")
            continue

        display_name = cls.get("mDisplayName") or class_name
        # The game encodes "alternate" twice and inconsistently: usually in the
        # class name, sometimes only in the English label. Both are authoritative,
        # so either one marks the recipe (Turbofuel has the prefix but not the
        # label; Pure Aluminum Ingot has the label but not the prefix).
        is_alternate = class_name.startswith(RECIPE_ALTERNATE_PREFIX) or display_name.startswith(
            RECIPE_ALTERNATE_LABEL
        )
        involved = tuple(slots["ingredients"]) + tuple(slots["products"])
        recipes.append(
            Recipe(
                class_name=class_name,
                display_name=display_name,
                display_name_fr=_label(class_name, display_name, labels),
                building_class=machine,
                cycle_seconds=cycle_seconds,
                is_alternate=is_alternate,
                involves_fluid=any(forms[slot.item_class].is_fluid for slot in involved),
                ingredients=slots["ingredients"],
                products=slots["products"],
                is_event=is_event_class(cls),
            )
        )

    if missing_items:
        warnings.append(
            f"{len(missing_items)} item(s) référence(s) par une recette mais absent(s) des "
            f"descripteurs : {', '.join(sorted(missing_items)[:5])}"
        )
    return sorted(recipes, key=lambda recipe: recipe.class_name)


def parse_building_costs(
    grouped: Locale,
    buildings: Sequence[Building],
    forms: dict[str, ItemForm],
    warnings: list[str],
) -> list[BuildingCost]:
    """What each building in scope costs to put down, from its build-gun recipe.

    Buildings are crafted like everything else, so the cost is read rather than
    written here. Nothing is inferred and nothing is estimated: a building whose
    recipe is missing, or whose recipe calls for an item this catalogue does not
    describe, gets **no entry at all** and a warning naming it. A cost that is
    partly right is worse than a cost that is absent, because absent is visible.
    """
    wanted = {building.class_name for building in buildings}
    costs: list[BuildingCost] = []
    priced: set[str] = set()

    for cls in grouped.get("FGRecipe", []):
        if BUILD_GUN not in parse_class_list(cls.get("mProducedIn", "")):
            continue
        products = parse_item_amounts(cls.get("mProduct", ""))
        if len(products) != 1:
            continue
        descriptor, built = products[0]
        building_class = descriptor.replace("Desc_", "Build_", 1)
        if building_class not in wanted or built <= 0:
            continue

        recipe_class = cls["ClassName"]
        amounts: dict[str, float] = {}
        unknown: list[str] = []
        for item_class, raw_amount in parse_item_amounts(cls.get("mIngredients", "")):
            form = forms.get(item_class)
            if form is None:
                unknown.append(item_class)
                continue
            # Divided by what one recipe yields, so the figure is per building even
            # if the game ever declares a recipe that puts down two at once.
            amounts[item_class] = (
                amounts.get(item_class, 0.0)
                + conversions.normalise_amount(raw_amount, form) / built
            )
        if unknown:
            warnings.append(
                f"{recipe_class} : coût de construction ignoré, item(s) inconnu(s) "
                f"{', '.join(sorted(unknown))}"
            )
            continue
        if building_class in priced:
            warnings.append(
                f"{building_class} : plusieurs recettes de construction, {recipe_class} ignorée"
            )
            continue
        priced.add(building_class)
        costs.append(
            BuildingCost(
                class_name=building_class,
                recipe_class=recipe_class,
                amounts=dict(sorted(amounts.items())),
            )
        )

    unpriced = sorted(wanted - priced)
    if unpriced:
        warnings.append(
            f"{len(unpriced)} bâtiment(s) sans recette de construction : {', '.join(unpriced)}"
        )
    return sorted(costs, key=lambda cost: cost.class_name)


def _building_icon(class_name: str, descriptors: dict[str, ClassEntry]) -> str | None:
    """Icons live on the ``Desc_*`` twin of a ``Build_*`` class."""
    descriptor = descriptors.get(class_name.replace("Build_", "Desc_", 1))
    if descriptor is None:
        return None
    return parse_icon_filename(descriptor.get("mSmallIcon") or descriptor.get("mPersistentBigIcon"))


def _building(
    cls: ClassEntry,
    kind: BuildingKind,
    labels: dict[str, str],
    descriptors: dict[str, ClassEntry],
) -> Building:
    class_name = cls["ClassName"]
    display_name = cls.get("mDisplayName") or class_name
    return Building(
        class_name=class_name,
        display_name=display_name,
        display_name_fr=_label(class_name, display_name, labels),
        kind=kind,
        power_mw=parse_float(cls.get("mPowerConsumption")),
        # Defaults to 1 -- strictly proportional -- when the field is absent, which
        # is the only assumption that cannot overstate a power bill.
        power_exponent=parse_float(cls.get("mPowerConsumptionExponent"), 1.0),
        icon_file=_building_icon(class_name, descriptors),
    )


def parse_power_shards(
    grouped: Locale,
) -> list[PowerShard]:
    """The consumable that raises a building's clock ceiling.

    Only the overclocking kind is kept. The game declares a second type on the same
    native class -- ``PST_ProductionBoost``, the Somersloop -- which multiplies output
    instead of speed, follows its own formula and is V2 work.
    """
    shards: list[PowerShard] = []
    for cls in grouped.get("FGPowerShardDescriptor", []):
        if cls.get("mPowerShardType") != OVERCLOCK_SHARD_TYPE:
            continue
        extra = parse_float(cls.get("mExtraPotential"))
        if extra <= 0:
            continue
        shards.append(PowerShard(class_name=cls["ClassName"], extra_potential=extra))
    return shards


def parse_fuel_entries(raw: object) -> list[tuple[str, str | None]]:
    """``mFuel`` into ``(fuel class, supplemental class or None)`` pairs.

    One of the very few fields the dump stores as real JSON rather than as a
    pseudo-structured string, so there is nothing to parse with a regular
    expression -- only a shape to check, because a malformed entry must be dropped
    rather than crash the build.
    """
    if not isinstance(raw, list):
        return []
    pairs: list[tuple[str, str | None]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        fuel = str(entry.get("mFuelClass") or "")
        if not fuel:
            continue
        supplemental = str(entry.get("mSupplementalResourceClass") or "")
        pairs.append((fuel, supplemental or None))
    return pairs


def parse_generators(
    grouped: Locale,
    labels: dict[str, str],
    forms: dict[str, ItemForm],
    energies: dict[str, float],
    warnings: list[str],
) -> tuple[list[Building], list[Generator]]:
    """Generators in scope, with the rate of every fuel they accept.

    Nothing here is hard-coded: the power comes from ``mPowerProduction``, the burn
    rate from that power and the fuel's own energy value, and the make-up water from
    ``mSupplementalToPowerRatio``. A fuel whose item is not in the catalogue -- rocket
    fuel, whose recipes belong to machines this version excludes -- is dropped from
    the list rather than offered as a choice that could never be supplied.
    """
    descriptors = {
        cls["ClassName"]: cls
        for cls in grouped.get("FGBuildingDescriptor", [])
        if "ClassName" in cls
    }
    buildings: list[Building] = []
    generators: list[Generator] = []

    for cls in grouped.get(GENERATOR_NATIVE_CLASS, []):
        class_name = cls["ClassName"]
        if class_name not in GENERATORS:
            if class_name not in EXCLUDED_GENERATORS:
                warnings.append(f"{class_name} : générateur inconnu, hors périmètre par défaut")
            continue
        power = parse_float(cls.get("mPowerProduction"))
        if power <= 0:
            warnings.append(f"{class_name} : puissance produite nulle, générateur ignore")
            continue
        ratio = parse_float(cls.get("mSupplementalToPowerRatio"))
        supplemental_rate = conversions.supplemental_rate_per_minute(ratio, power)

        fuels: list[GeneratorFuel] = []
        unknown: list[str] = []
        for fuel_class, supplemental in parse_fuel_entries(cls.get("mFuel")):
            form = forms.get(fuel_class)
            energy = energies.get(fuel_class, 0.0)
            if form is None or energy <= 0:
                unknown.append(fuel_class)
                continue
            fuels.append(
                GeneratorFuel(
                    item_class=fuel_class,
                    rate_per_minute=conversions.fuel_rate_per_minute(power, energy),
                    supplemental_class=supplemental,
                    supplemental_per_minute=supplemental_rate if supplemental else 0.0,
                )
            )
        if unknown:
            logger.debug("%s : carburant(s) hors catalogue ignore(s) : %s", class_name, unknown)
        if not fuels:
            warnings.append(f"{class_name} : aucun carburant exploitable, générateur ignore")
            continue

        buildings.append(_building(cls, BuildingKind.GENERATOR, labels, descriptors))
        generators.append(Generator(class_name=class_name, power_mw=power, fuels=tuple(fuels)))

    for native in OTHER_GENERATOR_NATIVE_CLASSES:
        for cls in grouped.get(native, []):
            if cls.get("ClassName") not in EXCLUDED_GENERATORS:
                warnings.append(
                    f"{cls.get('ClassName')} : générateur inconnu sous {native}, hors périmètre"
                )

    buildings.sort(key=lambda building: building.class_name)
    generators.sort(key=lambda generator: generator.class_name)
    return buildings, generators


def parse_buildings(
    grouped: Locale,
    labels: dict[str, str],
    warnings: list[str],
) -> tuple[
    list[Building],
    list[Extractor],
    list[Belt],
    list[Pipe],
    list[Storage],
    list[Attachment],
]:
    """Parse every buildable within V1 scope, plus its transport or storage rates."""
    descriptors = {
        cls["ClassName"]: cls
        for cls in grouped.get("FGBuildingDescriptor", [])
        if "ClassName" in cls
    }
    buildings: list[Building] = []
    extractors: list[Extractor] = []
    belts: list[Belt] = []
    pipes: list[Pipe] = []
    storages: list[Storage] = []
    attachments: list[Attachment] = []

    # Production machines
    for cls in grouped.get("FGBuildableManufacturer", []):
        class_name = cls["ClassName"]
        if class_name in PRODUCTION_MACHINES:
            buildings.append(_building(cls, BuildingKind.MANUFACTURER, labels, descriptors))
        elif class_name not in EXCLUDED_MACHINES:
            warnings.append(
                f"{class_name} : machine de production inconnue, hors périmètre par défaut"
            )

    # Extractors: solid miners and the oil pump, plus the water extractor which
    # the game models with its own native class.
    extractor_classes: Iterable[ClassEntry] = [
        *grouped.get("FGBuildableResourceExtractor", []),
        *grouped.get("FGBuildableWaterPump", []),
    ]
    for cls in extractor_classes:
        class_name = cls["ClassName"]
        if class_name not in EXTRACTOR_HAS_PURITY:
            continue
        forms_declared = conversions.FORM_BY_RAW.get(
            (cls.get("mAllowedResourceForms") or "").strip("()"), ItemForm.SOLID
        )
        allowed = parse_allowed_resources(cls.get("mAllowedResources", ""))
        buildings.append(_building(cls, BuildingKind.EXTRACTOR, labels, descriptors))
        extractors.append(
            Extractor(
                class_name=class_name,
                item_class=allowed[0] if len(allowed) == 1 else None,
                allowed_form=forms_declared,
                rate_per_minute=conversions.extractor_rate_per_minute(
                    parse_float(cls.get("mItemsPerCycle")),
                    parse_float(cls.get("mExtractCycleTime"), 1.0),
                    forms_declared,
                ),
                has_purity=EXTRACTOR_HAS_PURITY[class_name],
            )
        )

    # Conveyor belts
    for cls in grouped.get("FGBuildableConveyorBelt", []):
        match = _BELT_TIER_RE.search(cls["ClassName"])
        if match is None:
            warnings.append(f"{cls['ClassName']} : palier de convoyeur illisible, ignore")
            continue
        buildings.append(_building(cls, BuildingKind.BELT, labels, descriptors))
        belts.append(
            Belt(
                class_name=cls["ClassName"],
                tier=int(match.group(1)),
                items_per_minute=conversions.belt_items_per_minute(parse_float(cls.get("mSpeed"))),
            )
        )

    # Pipelines. The game also ships indicator-less cosmetic twins with identical
    # flow; keeping them would duplicate every tier in the palette.
    for cls in grouped.get("FGBuildablePipeline", []):
        class_name = cls["ClassName"]
        match = _PIPE_TIER_RE.search(class_name)
        if match is None:
            logger.debug("tuyauterie ignorée (variante cosmétique) : %s", class_name)
            continue
        buildings.append(_building(cls, BuildingKind.PIPE, labels, descriptors))
        pipes.append(
            Pipe(
                class_name=class_name,
                tier=int(match.group(1) or 1),
                cubic_metres_per_minute=conversions.pipe_cubic_metres_per_minute(
                    parse_float(cls.get("mFlowLimit"))
                ),
            )
        )

    # Storage
    for cls in grouped.get("FGBuildableStorage", []):
        class_name = cls["ClassName"]
        if class_name not in STORAGE_SLOTS:
            continue
        buildings.append(_building(cls, BuildingKind.STORAGE, labels, descriptors))
        storages.append(
            Storage(
                class_name=class_name,
                form=ItemForm.SOLID,
                slots=STORAGE_SLOTS[class_name],
                capacity_m3=None,
            )
        )
    for cls in grouped.get("FGBuildablePipeReservoir", []):
        class_name = cls["ClassName"]
        if class_name not in FLUID_TANKS:
            continue
        buildings.append(_building(cls, BuildingKind.STORAGE, labels, descriptors))
        storages.append(
            Storage(
                class_name=class_name,
                form=ItemForm.LIQUID,
                slots=None,
                # mStorageCapacity is already in m3, unlike item amounts.
                capacity_m3=parse_float(cls.get("mStorageCapacity")),
            )
        )

    # Splitters, mergers and pipe junctions
    for native in ATTACHMENT_NATIVE_CLASSES:
        for cls in grouped.get(native, []):
            class_name = cls["ClassName"]
            declared = ATTACHMENTS.get(class_name)
            if declared is None:
                continue
            form, roles, branches = declared
            buildings.append(_building(cls, BuildingKind.ATTACHMENT, labels, descriptors))
            attachments.append(
                Attachment(class_name=class_name, form=form, roles=roles, branches=branches)
            )
    absent = sorted(set(ATTACHMENTS) - {attachment.class_name for attachment in attachments})
    if absent:
        warnings.append(f"attachement(s) de ligne introuvable(s) : {', '.join(absent)}")

    missing_icons = [b.class_name for b in buildings if b.icon_file is None]
    if missing_icons:
        warnings.append(
            f"{len(missing_icons)} bâtiment(s) sans nom d'icône dans les données : "
            f"{', '.join(sorted(missing_icons)[:5])}"
        )

    buildings.sort(key=lambda building: building.class_name)
    attachments.sort(key=lambda attachment: attachment.class_name)
    return buildings, extractors, belts, pipes, storages, attachments


def parse_dataset(
    reference: Locale,
    labels: dict[str, str],
    descriptions: dict[str, str] | None = None,
    *,
    source_file: str,
    french_file: str | None,
    reference_was_preferred: bool = True,
) -> GameDataset:
    """Turn two loaded locales into the full, normalised dataset."""
    warnings: list[str] = []
    items = parse_items(reference, labels, descriptions)
    forms = {item.class_name: item.form for item in items}
    recipes = parse_recipes(reference, labels, forms, warnings)
    buildings, extractors, belts, pipes, storages, attachments = parse_buildings(
        reference, labels, warnings
    )
    energies = {item.class_name: item.energy_mj for item in items}
    generator_buildings, generators = parse_generators(reference, labels, forms, energies, warnings)
    buildings = sorted([*buildings, *generator_buildings], key=lambda b: b.class_name)

    known_buildings = {building.class_name for building in buildings}
    orphans = sorted({r.building_class for r in recipes} - known_buildings)
    if orphans:
        warnings.append(f"recettes rattachées à un bâtiment absent : {', '.join(orphans)}")

    return GameDataset(
        source_file=source_file,
        french_file=french_file,
        reference_was_preferred=reference_was_preferred,
        items=tuple(items),
        recipes=tuple(recipes),
        buildings=tuple(buildings),
        building_costs=tuple(parse_building_costs(reference, buildings, forms, warnings)),
        extractors=tuple(extractors),
        generators=tuple(generators),
        belts=tuple(belts),
        pipes=tuple(pipes),
        storages=tuple(storages),
        attachments=tuple(attachments),
        power_shards=tuple(parse_power_shards(reference)),
        warnings=tuple(warnings),
    )


def load_dataset(game_dir: Path) -> GameDataset:
    """Locate, read and parse the game documentation under ``game_dir``."""
    docs_dir = locate_docs_directory(game_dir)
    reference_path, preferred = select_reference_file(docs_dir)
    reference = read_locale(reference_path)

    french_path = docs_dir / FRENCH_FILENAME
    labels: dict[str, str] = {}
    descriptions: dict[str, str] = {}
    if french_path.is_file():
        french = read_locale(french_path)
        labels = french_labels(french)
        descriptions = french_descriptions(french)
    else:
        logger.warning("%s absent : les libellés resteront en anglais", FRENCH_FILENAME)

    return parse_dataset(
        reference,
        labels,
        descriptions,
        source_file=reference_path.name,
        french_file=french_path.name if labels else None,
        reference_was_preferred=preferred,
    )


def item_forms(items: Sequence[Item]) -> dict[str, ItemForm]:
    """Convenience mapping used by callers that need to know an item's unit."""
    return {item.class_name: item.form for item in items}
