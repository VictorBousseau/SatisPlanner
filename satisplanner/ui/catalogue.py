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
    MachineNode,
    Node,
    OutputNode,
    ResourceNode,
    StorageNode,
    WaterExtractorNode,
)
from satisplanner.core.models import BuildingKind, GameData, Item, ItemForm

# Default rate offered when the user drops an "import from outside" node. It is only
# a starting point, edited on the node itself, and no game value is implied.
DEFAULT_EXTERNAL_RATE: Final = 60.0


class EntryKind(StrEnum):
    """What kind of node an entry drops on the canvas."""

    RECIPE = "recipe"
    EXTRACTOR = "extractor"
    WATER_EXTRACTOR = "water_extractor"
    STORAGE = "storage"
    EXTERNAL = "external"
    OUTPUT = "output"
    SINK = "sink"


# Section headings, in the order the palette shows them.
SECTION_LABELS: Final[dict[EntryKind, str]] = {
    EntryKind.RECIPE: "Recettes",
    EntryKind.EXTRACTOR: "Extraction",
    EntryKind.WATER_EXTRACTOR: "Extraction",
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
    is_alternate: bool = False
    is_event: bool = False

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
