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
    ANY_BRANCH,
    OVERFLOW_BRANCH,
    ExternalSourceNode,
    GeneratorNode,
    MachineNode,
    MergerNode,
    Node,
    OutputNode,
    ResourceNode,
    ResourceWellNode,
    SplitterNode,
    StorageNode,
    WaterExtractorNode,
)
from satisplanner.core.models import (
    AttachmentRole,
    Building,
    BuildingKind,
    Extractor,
    GameData,
    Item,
    ItemForm,
    Purity,
    SplitterMode,
)

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

# The same one place for the three splitters, in the order the game unlocks them.
SPLITTER_MODE_LABELS: Final[dict[SplitterMode, str]] = {
    SplitterMode.STANDARD: "Standard",
    SplitterMode.SMART: "Intelligent",
    SplitterMode.PROGRAMMABLE: "Programmable",
}

# And for what may be written on a branch, besides the name of an item.
BRANCH_LABELS: Final[dict[str, str]] = {
    ANY_BRANCH: "n'importe lequel",
    OVERFLOW_BRANCH: "surplus",
}


def branch_label(setting: str, game_data: GameData) -> str:
    """How a branch's setting reads on a node, in a menu and in a message."""
    if setting in BRANCH_LABELS:
        return BRANCH_LABELS[setting]
    item = game_data.items.get(setting)
    return item.display_name_fr if item else setting


class EntryKind(StrEnum):
    """What kind of node an entry drops on the canvas."""

    RECIPE = "recipe"
    EXTRACTOR = "extractor"
    RESOURCE_WELL = "resource_well"
    WATER_EXTRACTOR = "water_extractor"
    GENERATOR = "generator"
    STORAGE = "storage"
    SPLITTER = "splitter"
    MERGER = "merger"
    EXTERNAL = "external"
    OUTPUT = "output"
    SINK = "sink"


# Section headings, in the order the palette shows them.
SECTION_LABELS: Final[dict[EntryKind, str]] = {
    EntryKind.RECIPE: "Recettes",
    EntryKind.EXTRACTOR: "Extraction",
    EntryKind.RESOURCE_WELL: "Extraction",
    EntryKind.WATER_EXTRACTOR: "Extraction",
    EntryKind.GENERATOR: "Électricité",
    EntryKind.STORAGE: "Stockage",
    EntryKind.SPLITTER: "Raccords",
    EntryKind.MERGER: "Raccords",
    EntryKind.EXTERNAL: "Entrées et sorties",
    EntryKind.OUTPUT: "Entrées et sorties",
    EntryKind.SINK: "Entrées et sorties",
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
            case EntryKind.RESOURCE_WELL:
                # Named by what it pulls up, exactly as a deposit entry is.
                return self.class_name if self.class_name in game_data.items else None
            case EntryKind.GENERATOR:
                # A generator is about what it burns, which is the item the card
                # can actually say something useful about.
                generator = game_data.generators.get(self.class_name)
                return generator.default_fuel if generator else None
            case EntryKind.STORAGE | EntryKind.SPLITTER | EntryKind.MERGER:
                # About a building, or about nothing until a line reaches it.
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
            case EntryKind.RESOURCE_WELL:
                assert self.extractor_class is not None
                # One normal satellite to begin with, for the same reason a deposit
                # starts on one extractor and normal purity: it is a size, not a
                # reading of the map, and the map is the one thing this application
                # cannot know. What is actually out there gets typed in afterwards.
                return ResourceWellNode(
                    id=node_id,
                    item_class=self.class_name,
                    extractor_class=self.extractor_class,
                    satellites={Purity.NORMAL: 1},
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
            case EntryKind.SPLITTER:
                # No item and no building class: both follow from the line it is
                # dropped on, and asking twice for something already on screen is
                # exactly the ceremony a splitter is meant to save.
                return SplitterNode(id=node_id, position=position)
            case EntryKind.MERGER:
                return MergerNode(id=node_id, position=position)
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
    entries.extend(_attachments(game_data))
    entries.extend(_endpoints(game_data))
    return entries


def mode_for(role: AttachmentRole) -> SplitterMode | None:
    """The mode a freshly dropped attachment starts in: plain, or none at all."""
    return SplitterMode.STANDARD if role is AttachmentRole.SPLIT else None


def _attachments(game_data: GameData) -> list[PaletteEntry]:
    """One splitter and one merger, whatever the form of what will run through them.

    Not one per building: a solid splits in a conveyor splitter and a fluid in a
    pipe junction, and which of the two it is follows from the line it is dropped
    on. Offering both would be asking the user to answer a question the canvas is
    about to answer for them -- and to get it wrong.

    The label names both buildings all the same, because that is what a player is
    searching for: "jonction" has to find this row.
    """
    entries = []
    for kind, role, label in (
        (EntryKind.SPLITTER, AttachmentRole.SPLIT, "Répartiteur"),
        (EntryKind.MERGER, AttachmentRole.MERGE, "Groupeur"),
    ):
        names = [
            game_data.buildings[attachment.class_name].display_name_fr
            for attachment in sorted(game_data.attachments.values(), key=lambda a: a.class_name)
            if role in attachment.roles
            and attachment.class_name in game_data.buildings
            and attachment.splitter_mode in (None, SplitterMode.STANDARD)
        ]
        # Dropped as the plain one, on a belt. The mode is a property of the node
        # and is changed there, exactly as a generator's fuel is: offering three
        # rows would be asking for a decision before the line even exists.
        plain = game_data.attachment_for(ItemForm.SOLID, role, mode_for(role))
        icon = plain.class_name if plain else ""
        building = game_data.buildings.get(icon)
        verb = "sortir" if kind is EntryKind.SPLITTER else "entrer"
        entries.append(
            PaletteEntry(
                kind=kind,
                label=label,
                detail=(
                    f"{', '.join(names)} — pour faire {verb} plus d'une ligne d'un port"
                    if names
                    else f"pour faire {verb} plus d'une ligne d'un port"
                ),
                class_name=icon,
                icon_class=icon,
                icon_file=building.icon_file if building else None,
            )
        )
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
        if extractor.needs_activator:
            # A well satellite is not a building you place: it is opened by a
            # pressuriser, and what the palette offers is the well as a whole.
            entries.extend(_well_entries(game_data, extractor, name, building))
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


def _well_entries(
    game_data: GameData, extractor: Extractor, name: str, building: Building | None
) -> list[PaletteEntry]:
    """One well entry per resource the game says a well can be sunk into.

    Crude oil, nitrogen and water, and the list is read rather than written: a
    solid miner names no resource because it takes any ore, and a satellite names
    three because those are the three that come out of the ground under pressure.
    """
    del building
    entries = []
    for item_class in extractor.allowed_items:
        item = game_data.items.get(item_class)
        if item is None:
            continue
        entries.append(
            PaletteEntry(
                kind=EntryKind.RESOURCE_WELL,
                label=item.display_name_fr,
                detail=name,
                class_name=item.class_name,
                icon_class=item.class_name,
                icon_file=item.icon_file,
                extractor_class=extractor.class_name,
                is_event=item.is_event,
            )
        )
    return entries


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
            (EntryKind.EXTERNAL, "Entrée"),
            (EntryKind.OUTPUT, "Sortie"),
            (EntryKind.SINK, "Rejet assumé"),
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
            return "apport venu d'ailleurs, débit à saisir"
        case EntryKind.SINK:
            return "torchère ou puits AWESOME, absorbe sans limite"
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
