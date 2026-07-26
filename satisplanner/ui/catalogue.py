"""What the palette offers, and how each entry becomes a node.

The palette is a flat searchable list rather than a tree: a player looking for
"plaque" wants the recipe, wherever it lives. Every entry therefore carries its own
French label, the machine it belongs to for filtering, and enough class names to
build the node it stands for.

Deliberately free of Qt: this is the bridge between the catalogue and the widgets,
and keeping it a plain data structure is what lets it be tested without a window.
"""

import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from satisplanner.core.graph import (
    ExternalSourceNode,
    GeneratorNode,
    MachineNode,
    Node,
    OutputNode,
    ResourceNode,
    StorageNode,
    WaterExtractorNode,
)
from satisplanner.core.models import BuildingKind, GameData, Item, ItemForm, Purity

# Default rate offered when the user drops an "import from outside" node. It is only
# a starting point, edited on the node itself, and no game value is implied.
DEFAULT_EXTERNAL_RATE: Final = 60.0

# The one place the three purities are spelled in French, so a node, a table cell and
# a menu cannot end up calling the same thing three different names.
PURITY_LABELS: Final[dict[Purity, str]] = {
    Purity.IMPURE: "Impur",
    Purity.NORMAL: "Normal",
    Purity.PURE: "Pur",
}


class EntryKind(StrEnum):
    """What kind of node an entry drops on the canvas."""

    RECIPE = "recipe"
    EXTRACTOR = "extractor"
    WATER_EXTRACTOR = "water_extractor"
    GENERATOR = "generator"
    STORAGE = "storage"
    EXTERNAL = "external"
    OUTPUT = "output"
    SINK = "sink"


# Section headings, in the order the palette shows them.
SECTION_LABELS: Final[dict[EntryKind, str]] = {
    EntryKind.RECIPE: "Recettes",
    EntryKind.EXTRACTOR: "Extraction",
    EntryKind.WATER_EXTRACTOR: "Extraction",
    EntryKind.GENERATOR: "Electricite",
    EntryKind.STORAGE: "Stockage",
    EntryKind.EXTERNAL: "Entrees et sorties",
    EntryKind.OUTPUT: "Entrees et sorties",
    EntryKind.SINK: "Entrees et sorties",
}


def fold(text: str) -> str:
    """Lower-case and strip accents, so "resine" finds "Résine"."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


@dataclass(frozen=True)
class PaletteEntry:
    """One draggable line of the palette."""

    kind: EntryKind
    label: str  # French, shown as is
    detail: str  # French, shown under the label
    class_name: str  # recipe, storage or item class, depending on the kind
    icon_class: str
    icon_file: str | None
    # Machine this entry belongs to, for the "filter by machine" combo. None for
    # anything that is not made in a machine.
    machine_class: str | None = None
    extractor_class: str | None = None
    # The fuel a generator entry drops its node on: the game's own first choice,
    # changed afterwards on the node itself.
    fuel_class: str | None = None
    is_alternate: bool = False
    is_event: bool = False

    def subject_item(self, game_data: GameData) -> str | None:
        """The item this entry is *about*, for the item card.

        A recipe is about what it makes, an extractor about what it pulls out, and
        an endpoint about what it carries. A storage entry is about a building, not
        an item, and has no card.
        """
        match self.kind:
            case EntryKind.RECIPE:
                recipe = game_data.recipes.get(self.class_name)
                if recipe is None or not recipe.products:
                    return None
                return recipe.products[0].item_class
            case EntryKind.WATER_EXTRACTOR:
                extractor = game_data.extractors.get(self.class_name)
                return extractor.item_class if extractor else None
            case EntryKind.GENERATOR:
                # A generator is about what it burns, which is the item the card
                # can actually say something useful about.
                generator = game_data.generators.get(self.class_name)
                return generator.default_fuel if generator else None
            case EntryKind.STORAGE:
                return None
            case _:
                return self.class_name if self.class_name in game_data.items else None

    @property
    def search_key(self) -> str:
        """Everything worth matching a query against, folded once."""
        return fold(f"{self.label} {self.detail} {self.class_name}")

    def matches(self, query: str) -> bool:
        return all(word in self.search_key for word in fold(query).split())

    def make_node(self, node_id: str, position: tuple[float, float]) -> Node:
        """Build the graph node this entry stands for."""
        match self.kind:
            case EntryKind.RECIPE:
                return MachineNode(id=node_id, recipe_class=self.class_name, position=position)
            case EntryKind.EXTRACTOR:
                assert self.extractor_class is not None
                return ResourceNode(
                    id=node_id,
                    item_class=self.class_name,
                    extractor_class=self.extractor_class,
                    position=position,
                )
            case EntryKind.WATER_EXTRACTOR:
                return WaterExtractorNode(
                    id=node_id, extractor_class=self.class_name, position=position
                )
            case EntryKind.GENERATOR:
                # The fuel is carried by the entry rather than looked up here: an
                # entry has to be able to build its node without a catalogue, and
                # the palette already knows which fuel it advertised.
                assert self.fuel_class is not None
                return GeneratorNode(
                    id=node_id,
                    generator_class=self.class_name,
                    fuel_class=self.fuel_class,
                    position=position,
                )
            case EntryKind.STORAGE:
                return StorageNode(id=node_id, storage_class=self.class_name, position=position)
            case EntryKind.EXTERNAL:
                return ExternalSourceNode(
                    id=node_id,
                    item_class=self.class_name,
                    rate_per_minute=DEFAULT_EXTERNAL_RATE,
                    position=position,
                )
            case EntryKind.OUTPUT | EntryKind.SINK:
                return OutputNode(
                    id=node_id,
                    item_class=self.class_name,
                    is_sink=self.kind is EntryKind.SINK,
                    position=position,
                )


def build_entries(game_data: GameData) -> list[PaletteEntry]:
    """Every entry the palette can show, event content included but flagged.

    Nothing is filtered out here: the toggles in the palette decide what is visible,
    exactly as they do for alternate recipes. Filtering at build time would make the
    checkboxes lie.
    """
    entries: list[PaletteEntry] = []
    entries.extend(_recipes(game_data))
    entries.extend(_extraction(game_data))
    entries.extend(_generators(game_data))
    entries.extend(_storage(game_data))
    entries.extend(_endpoints(game_data))
    return entries


def _recipes(game_data: GameData) -> list[PaletteEntry]:
    entries = []
    for recipe in sorted(game_data.recipes.values(), key=lambda r: fold(r.display_name_fr)):
        machine = game_data.buildings.get(recipe.building_class)
        headline = recipe.products[0].item_class if recipe.products else recipe.class_name
        item = game_data.items.get(headline)
        entries.append(
            PaletteEntry(
                kind=EntryKind.RECIPE,
                label=recipe.display_name_fr,
                detail=machine.display_name_fr if machine else recipe.building_class,
                class_name=recipe.class_name,
                icon_class=headline,
                icon_file=item.icon_file if item else None,
                machine_class=recipe.building_class,
                is_alternate=recipe.is_alternate,
                is_event=recipe.is_event,
            )
        )
    return entries


def _extraction(game_data: GameData) -> list[PaletteEntry]:
    """One entry per (extractor, resource) pair the extractor actually accepts.

    A miner takes any solid node, so it appears once per ore; the oil pump and the
    water extractor name their own resource in the game files and appear once.
    """
    entries = []
    for extractor in sorted(game_data.extractors.values(), key=lambda e: e.class_name):
        building = game_data.buildings.get(extractor.class_name)
        name = building.display_name_fr if building else extractor.class_name
        if extractor.item_class is not None and not extractor.has_purity:
            # Fixed output and a resource of its own: the water extractor.
            entries.append(
                PaletteEntry(
                    kind=EntryKind.WATER_EXTRACTOR,
                    label=name,
                    detail=f"{extractor.rate_per_minute:g} m³/min",
                    class_name=extractor.class_name,
                    icon_class=extractor.class_name,
                    icon_file=building.icon_file if building else None,
                )
            )
            continue
        for item in _resources_for(game_data, extractor.allowed_form, extractor.item_class):
            entries.append(
                PaletteEntry(
                    kind=EntryKind.EXTRACTOR,
                    label=item.display_name_fr,
                    detail=name,
                    class_name=item.class_name,
                    icon_class=item.class_name,
                    icon_file=item.icon_file,
                    extractor_class=extractor.class_name,
                    is_event=item.is_event,
                )
            )
    return sorted(entries, key=lambda entry: (fold(entry.detail), fold(entry.label)))


def _resources_for(game_data: GameData, form: ItemForm, only: str | None) -> list[Item]:
    if only is not None:
        item = game_data.items.get(only)
        return [item] if item is not None else []
    return sorted(
        (item for item in game_data.items.values() if item.is_raw_resource and item.form is form),
        key=lambda item: fold(item.display_name_fr),
    )


def _generators(game_data: GameData) -> list[PaletteEntry]:
    """One entry per generator, not one per fuel.

    A fuel generator would otherwise appear five times for the same building. The
    fuel is a property of the node, editable there like the purity of a deposit, and
    the entry simply starts it on the game's first choice.
    """
    entries = []
    for generator in sorted(game_data.generators.values(), key=lambda g: g.class_name):
        fuel_class = generator.default_fuel
        if fuel_class is None:
            continue
        building = game_data.buildings.get(generator.class_name)
        fuel = game_data.items.get(fuel_class)
        detail = f"{generator.power_mw:g} MW produits"
        if fuel is not None:
            detail += f" — {fuel.display_name_fr}"
        entries.append(
            PaletteEntry(
                kind=EntryKind.GENERATOR,
                label=building.display_name_fr if building else generator.class_name,
                detail=detail,
                class_name=generator.class_name,
                icon_class=generator.class_name,
                icon_file=building.icon_file if building else None,
                fuel_class=fuel_class,
            )
        )
    return sorted(entries, key=lambda entry: fold(entry.label))


def _storage(game_data: GameData) -> list[PaletteEntry]:
    entries = []
    for storage in sorted(game_data.storages.values(), key=lambda s: s.class_name):
        building = game_data.buildings.get(storage.class_name)
        detail = (
            f"{storage.slots} emplacements"
            if storage.slots is not None
            else f"{storage.capacity_m3:g} m³"
        )
        entries.append(
            PaletteEntry(
                kind=EntryKind.STORAGE,
                label=building.display_name_fr if building else storage.class_name,
                detail=detail,
                class_name=storage.class_name,
                icon_class=storage.class_name,
                icon_file=building.icon_file if building else None,
            )
        )
    return entries


def _endpoints(game_data: GameData) -> list[PaletteEntry]:
    """Imports, exports and deliberate discards, one per item that can be handled."""
    entries = []
    handled = _handled_items(game_data)
    for item in sorted(game_data.items.values(), key=lambda i: fold(i.display_name_fr)):
        if item.class_name not in handled:
            continue
        for kind, prefix in (
            (EntryKind.EXTERNAL, "Entree"),
            (EntryKind.OUTPUT, "Sortie"),
            (EntryKind.SINK, "Rejet assume"),
        ):
            entries.append(
                PaletteEntry(
                    kind=kind,
                    label=f"{prefix} : {item.display_name_fr}",
                    detail=_endpoint_detail(kind),
                    class_name=item.class_name,
                    icon_class=item.class_name,
                    icon_file=item.icon_file,
                    is_event=item.is_event,
                )
            )
    return entries


def _endpoint_detail(kind: EntryKind) -> str:
    match kind:
        case EntryKind.EXTERNAL:
            return "apport venu d'ailleurs, debit a saisir"
        case EntryKind.SINK:
            return "torchere ou puits AWESOME, absorbe sans limite"
        case _:
            return "ce qui sort de l'usine"


def _handled_items(game_data: GameData) -> set[str]:
    """Items some kept recipe or extractor deals with.

    Without this the endpoint sections would list every descriptor in the game,
    including the ones no V1 machine can make or consume.
    """
    handled = {item.class_name for item in game_data.items.values() if item.is_raw_resource}
    for recipe in game_data.recipes.values():
        handled.update(slot.item_class for slot in (*recipe.ingredients, *recipe.products))
    return handled


def machine_choices(game_data: GameData) -> list[tuple[str, str]]:
    """``(class name, French label)`` of every production machine, for the filter."""
    return sorted(
        (
            (building.class_name, building.display_name_fr)
            for building in game_data.buildings.values()
            if building.kind is BuildingKind.MANUFACTURER
        ),
        key=lambda pair: fold(pair[1]),
    )


def extractor_choices(game_data: GameData, item_class: str) -> list[tuple[str, str]]:
    """Extractors that could work this deposit, as ``(class name, French label)``.

    Filtered on what the game allows: the form has to match, and an extractor that
    names one resource -- the oil pump, the water pump -- only appears for it. That
    is what makes "change the miner on this node" a safe operation rather than one
    that has to be checked afterwards.
    """
    item = game_data.items.get(item_class)
    if item is None:
        return []
    candidates = [
        extractor
        for extractor in game_data.extractors.values()
        if extractor.allowed_form.is_fluid is item.form.is_fluid
        and extractor.item_class in (None, item_class)
    ]
    return sorted(
        (
            (
                extractor.class_name,
                game_data.buildings[extractor.class_name].display_name_fr
                if extractor.class_name in game_data.buildings
                else extractor.class_name,
            )
            for extractor in candidates
        ),
        key=lambda choice: choice[1],
    )


def fuel_choices(game_data: GameData, generator_class: str) -> list[tuple[str, str]]:
    """Fuels this generator burns, in the game's own order, as ``(class, label)``.

    The game's order and not the alphabet: it puts the ordinary fuel first, and a
    list starting with turbofuel would suggest a choice the building does not make
    by default. Exactly like the extractor list, only what the data allows appears.
    """
    generator = game_data.generators.get(generator_class)
    if generator is None:
        return []
    return [
        (
            fuel.item_class,
            game_data.items[fuel.item_class].display_name_fr
            if fuel.item_class in game_data.items
            else fuel.item_class,
        )
        for fuel in generator.fuels
    ]


def transport_choices(game_data: GameData, form: ItemForm) -> list[tuple[str, str]]:
    """Belts or pipes, cheapest first, as ``(class name, French label)``."""
    return [
        (
            transport.class_name,
            game_data.buildings[transport.class_name].display_name_fr
            if transport.class_name in game_data.buildings
            else transport.class_name,
        )
        for transport in game_data.transports_for(form)
    ]
