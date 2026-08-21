"""Generation and reading of the SQLite game database.

The database is a **delivered artefact**: it is generated once from the game files,
committed, and embedded in the executable. The application therefore never needs
the game to be installed. Nothing here writes a timestamp, so regenerating from
the same game version produces the same file and an empty diff.
"""

import logging
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Final

from satisplanner import __version__, paths
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
    RecipeAvailability,
    RecipeSlot,
    SplitterMode,
    Storage,
)
from satisplanner.data.docs_parser import GameDataset

logger = logging.getLogger(__name__)

# 2 added the `attachments` table: splitters, mergers and pipe junctions.
# 3 added `buildings.power_exponent` and the `power_shards` table, both needed
# to price overclocking: the draw follows a power law whose exponent is per
# building, and the shard says how much clock one of them buys.
# 4 added `items.description_fr`: the game's own blurb, for the item card.
# 5 added `items.energy_mj` and the `generators` / `generator_fuels` tables, so
# that power can be produced and not only consumed. The burn rate of every fuel
# is derived at generation time, exactly as a recipe's rates are.
# 6 added the `building_costs` table: what each building costs to put down, read
# from the recipe the game crafts it with. Nothing is inferred -- a building with
# no such recipe simply has no rows, and the shopping list names it.
# 7 added `attachments.splitter_mode`, which is how the three conveyor splitters
# are told apart. They differ in what may be written on a branch and in what they
# cost, and nothing else in the row says which is which.
# 8 keeps the recipes no node can place. `recipes.availability` says what stops
# each one and `recipes.building_name_fr` names the machine the catalogue does not
# carry; `items.byproduct_of_fr` names the unplaceable building an item drops out
# of. A recipe dropped at parse time made "outside the scope" and "missing from
# the data" look the same on screen, which is the one thing a catalogue must not do.
# 9 described the resource well: `extractors.activator_class` names the pressuriser
# a satellite cannot work without -- the only extractor in the game that is half a
# building -- and `extractor_resources` lists what an extractor may be put on when
# the game restricts it, which is how a well is offered for oil, nitrogen and water
# and for nothing else.
# 10 added `recipes.power_constant_mw` and `recipes.power_factor_mw`. Three machines
# declare `mPowerConsumption` at zero and put their draw on the recipe instead, as a
# swing between the two: the fixed nameplate on a building turns out to be the
# particular case, and the pair machine-and-recipe the general one.
# 11 described the last two generators. `generator_fuels.byproduct_class` and its
# rate carry what a nuclear plant returns on a belt -- the one generator in the
# game that puts matter back into the factory -- and `generators.power_min_mw`,
# `power_max_mw` and `has_purity` carry the geothermal one, whose output swings and
# depends on the geyser it stands on rather than on any fuel.
SCHEMA_VERSION: Final = 11

# The documentation files carry no version field: this is the game version we
# target and validate against, declared here rather than read from the data.
GAME_VERSION: Final = "1.2"

DEFAULT_DATABASE_NAME: Final = f"game_{GAME_VERSION}.sqlite"


def default_database_path() -> Path:
    """The database shipped inside the package, which is what the application uses.

    Resolved through :mod:`satisplanner.paths` rather than from ``__file__``, so that
    a packaged run reads the copy PyInstaller unpacked instead of a path that only
    exists inside the archive.
    """
    return paths.resource_directory() / DEFAULT_DATABASE_NAME


SCHEMA: Final = """
CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE items (
    class_name      TEXT PRIMARY KEY,
    display_name    TEXT NOT NULL,
    display_name_fr TEXT NOT NULL,
    -- mDescription, French when the locale has one. Empty rather than null: an
    -- item with no blurb in the data shows none, and nothing is written for it.
    description_fr  TEXT NOT NULL DEFAULT '',
    form            TEXT NOT NULL CHECK (form IN ('solid', 'liquid', 'gas')),
    stack_size      REAL NOT NULL,
    icon_file       TEXT,
    sink_points     INTEGER NOT NULL,
    is_raw_resource INTEGER NOT NULL CHECK (is_raw_resource IN (0, 1)),
    -- Seasonal event content: kept in the database, hidden by default in the UI.
    is_event        INTEGER NOT NULL CHECK (is_event IN (0, 1)),
    -- mEnergyValue, normalised: MJ per item for a solid, MJ per m3 for a fluid.
    -- Zero for anything that cannot be burnt.
    energy_mj       REAL NOT NULL DEFAULT 0,
    -- French name of the out-of-scope building this item falls out of, when no
    -- recipe at all produces it. Empty for everything else, gathered items
    -- included: "no recipe" and "picked up off the ground" are not the same fact.
    byproduct_of_fr TEXT NOT NULL DEFAULT ''
);

CREATE TABLE buildings (
    class_name      TEXT PRIMARY KEY,
    display_name    TEXT NOT NULL,
    display_name_fr TEXT NOT NULL,
    kind            TEXT NOT NULL,
    power_mw        REAL NOT NULL,
    -- mPowerConsumptionExponent: overclocking raises the draw to this power.
    power_exponent  REAL NOT NULL,
    icon_file       TEXT
);

-- What one of a building costs to put down, read from its build-gun recipe.
-- ``recipe_class`` is carried so a figure on screen can be traced back to the
-- line of the game files it came from. A building absent from this table has no
-- build recipe in the data, and the shopping list says so rather than guessing.
CREATE TABLE building_costs (
    building_class TEXT NOT NULL REFERENCES buildings (class_name),
    item_class     TEXT NOT NULL REFERENCES items (class_name),
    amount         REAL NOT NULL,
    recipe_class   TEXT NOT NULL,
    PRIMARY KEY (building_class, item_class)
);

-- Every recipe that makes a part, placeable or not. `building_class` carries no
-- foreign key any more: an unplaceable recipe names a machine -- or a workbench --
-- that this catalogue deliberately does not describe, so the key could not
-- resolve. `building_name_fr` is what replaces the lookup, and only for those rows.
CREATE TABLE recipes (
    class_name      TEXT PRIMARY KEY,
    display_name    TEXT NOT NULL,
    display_name_fr TEXT NOT NULL,
    building_class  TEXT NOT NULL,
    cycle_seconds   REAL NOT NULL,
    is_alternate    INTEGER NOT NULL CHECK (is_alternate IN (0, 1)),
    involves_fluid  INTEGER NOT NULL CHECK (involves_fluid IN (0, 1)),
    product_count   INTEGER NOT NULL,
    is_event        INTEGER NOT NULL CHECK (is_event IN (0, 1)),
    availability     TEXT NOT NULL DEFAULT 'placeable'
                     CHECK (availability IN ('placeable', 'machine', 'hand')),
    building_name_fr TEXT NOT NULL DEFAULT '',
    -- What one machine running this recipe draws, when the building declares
    -- nothing. Both zero for every recipe whose building has a nameplate, which is
    -- all but the Converter, the Particle Accelerator and the Quantum Encoder. The
    -- draw swings between the constant and the constant plus the factor.
    power_constant_mw REAL NOT NULL DEFAULT 0,
    power_factor_mw   REAL NOT NULL DEFAULT 0
);

CREATE TABLE recipe_ingredients (
    recipe_class     TEXT NOT NULL REFERENCES recipes (class_name),
    slot_index       INTEGER NOT NULL,
    item_class       TEXT NOT NULL REFERENCES items (class_name),
    amount_per_cycle REAL NOT NULL,
    rate_per_minute  REAL NOT NULL,
    PRIMARY KEY (recipe_class, slot_index)
);

CREATE TABLE recipe_products (
    recipe_class     TEXT NOT NULL REFERENCES recipes (class_name),
    slot_index       INTEGER NOT NULL,
    item_class       TEXT NOT NULL REFERENCES items (class_name),
    amount_per_cycle REAL NOT NULL,
    rate_per_minute  REAL NOT NULL,
    PRIMARY KEY (recipe_class, slot_index)
);

CREATE TABLE extractors (
    class_name      TEXT PRIMARY KEY REFERENCES buildings (class_name),
    item_class      TEXT REFERENCES items (class_name),
    allowed_form    TEXT NOT NULL CHECK (allowed_form IN ('solid', 'liquid', 'gas')),
    rate_per_minute REAL NOT NULL,
    has_purity      INTEGER NOT NULL CHECK (has_purity IN (0, 1)),
    -- The building that has to stand beside this one for it to work: the
    -- resource-well pressuriser, and nothing else in the game. Null for every
    -- extractor that is a building on its own.
    activator_class TEXT REFERENCES buildings (class_name)
);

-- What an extractor may be put on, when the game names a list. A solid miner names
-- none -- it takes any ore -- and gets no rows; the resource-well satellite names
-- crude oil, nitrogen and water, and gets three.
CREATE TABLE extractor_resources (
    extractor_class TEXT NOT NULL REFERENCES extractors (class_name),
    slot_index      INTEGER NOT NULL,
    item_class      TEXT NOT NULL REFERENCES items (class_name),
    PRIMARY KEY (extractor_class, slot_index)
);

-- Buildings that put power on the grid instead of taking it off.
CREATE TABLE generators (
    class_name TEXT PRIMARY KEY REFERENCES buildings (class_name),
    -- mPowerProduction, in MW at 100 %. Generators are not clockable in this
    -- version: their production exponent is not the consumption one. For the
    -- geothermal generator, whose output swings, this is the **mean** over a cycle.
    power_mw   REAL NOT NULL,
    -- The two ends of the swing. Equal to power_mw twice over for a generator that
    -- holds still, which is every one of them but the geothermal.
    power_min_mw REAL NOT NULL DEFAULT 0,
    power_max_mw REAL NOT NULL DEFAULT 0,
    -- Whether output depends on the purity of the spot: true of the geothermal
    -- generator alone, which is built on a geyser and not on a foundation.
    has_purity   INTEGER NOT NULL DEFAULT 0 CHECK (has_purity IN (0, 1))
);

CREATE TABLE generator_fuels (
    generator_class    TEXT NOT NULL REFERENCES generators (class_name),
    slot_index         INTEGER NOT NULL,
    item_class         TEXT NOT NULL REFERENCES items (class_name),
    -- Burnt at nominal power: items/min for a solid, m3/min for a fluid.
    rate_per_minute    REAL NOT NULL,
    -- Make-up water. A real input on a pipe, hence a class name and a rate.
    supplemental_class TEXT REFERENCES items (class_name),
    supplemental_per_minute REAL NOT NULL DEFAULT 0,
    -- What burning this fuel leaves on the output belt, and how much per minute.
    -- Null for every generator but the nuclear plant, and null for one of its
    -- three rods: a ficsonium rod leaves nothing behind.
    byproduct_class      TEXT REFERENCES items (class_name),
    byproduct_per_minute REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (generator_class, slot_index)
);

CREATE TABLE belts (
    class_name       TEXT PRIMARY KEY REFERENCES buildings (class_name),
    tier             INTEGER NOT NULL,
    items_per_minute REAL NOT NULL
);

CREATE TABLE pipes (
    class_name              TEXT PRIMARY KEY REFERENCES buildings (class_name),
    tier                    INTEGER NOT NULL,
    cubic_metres_per_minute REAL NOT NULL
);

CREATE TABLE storages (
    class_name  TEXT PRIMARY KEY REFERENCES buildings (class_name),
    form        TEXT NOT NULL CHECK (form IN ('solid', 'liquid', 'gas')),
    slots       INTEGER,
    capacity_m3 REAL
);

-- Line attachments: splitters, mergers and pipe junctions, placed as nodes.
CREATE TABLE attachments (
    class_name    TEXT PRIMARY KEY REFERENCES buildings (class_name),
    form          TEXT NOT NULL CHECK (form IN ('solid', 'liquid', 'gas')),
    can_split     INTEGER NOT NULL CHECK (can_split IN (0, 1)),
    can_merge     INTEGER NOT NULL CHECK (can_merge IN (0, 1)),
    branches      INTEGER NOT NULL,
    -- NULL for anything that is not one of the three conveyor splitters.
    splitter_mode TEXT CHECK (splitter_mode IN ('standard', 'smart', 'programmable'))
);

-- Consumables that raise a building's clock ceiling. Only the overclocking kind
-- is stored; the Somersloop amplifies production instead and is out of scope.
CREATE TABLE power_shards (
    class_name      TEXT PRIMARY KEY REFERENCES items (class_name),
    extra_potential REAL NOT NULL
);

CREATE INDEX idx_recipes_building ON recipes (building_class);
CREATE INDEX idx_recipes_available ON recipes (availability);
CREATE INDEX idx_ingredients_item ON recipe_ingredients (item_class);
CREATE INDEX idx_products_item    ON recipe_products (item_class);
"""


@contextmanager
def connect(path: Path, *, read_only: bool = True) -> Iterator[sqlite3.Connection]:
    """Open the database with foreign keys enforced and rows accessible by name."""
    uri = f"file:{path.as_posix()}?mode=ro" if read_only else path.as_posix()
    connection = sqlite3.connect(uri, uri=read_only)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        yield connection
    finally:
        connection.close()


def build_database(dataset: GameDataset, path: Path) -> None:
    """Write ``dataset`` to ``path``, replacing any existing file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        logger.info("remplacement de la base existante %s", path)
        path.unlink()

    with closing(sqlite3.connect(path.as_posix())) as connection:
        connection.executescript(SCHEMA)
        connection.execute("PRAGMA foreign_keys = ON")
        _insert_meta(connection, dataset)
        _insert_items(connection, dataset.items)
        _insert_buildings(connection, dataset.buildings)
        _insert_building_costs(connection, dataset.building_costs)
        _insert_recipes(connection, (*dataset.recipes, *dataset.unavailable_recipes))
        _insert_extractors(connection, dataset.extractors)
        _insert_generators(connection, dataset.generators)
        _insert_belts(connection, dataset.belts)
        _insert_pipes(connection, dataset.pipes)
        _insert_storages(connection, dataset.storages)
        _insert_attachments(connection, dataset.attachments)
        _insert_power_shards(connection, dataset.power_shards)
        connection.commit()
        # Fails loudly if a reference does not resolve, rather than shipping a
        # database whose recipes point at nothing.
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            msg = f"intégrité référentielle violée : {violations[:5]}"
            raise RuntimeError(msg)


def _insert_meta(connection: sqlite3.Connection, dataset: GameDataset) -> None:
    connection.executemany(
        "INSERT INTO meta (key, value) VALUES (?, ?)",
        sorted(
            {
                "schema_version": str(SCHEMA_VERSION),
                "game_version": GAME_VERSION,
                "generator_version": __version__,
                "source_file": dataset.source_file,
                "french_file": dataset.french_file or "",
            }.items()
        ),
    )


def _insert_items(connection: sqlite3.Connection, items: tuple[Item, ...]) -> None:
    connection.executemany(
        "INSERT INTO items (class_name, display_name, display_name_fr, description_fr, form,"
        " stack_size, icon_file, sink_points, is_raw_resource, is_event, energy_mj,"
        " byproduct_of_fr) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                item.class_name,
                item.display_name,
                item.display_name_fr,
                item.description_fr,
                item.form.value,
                item.stack_size,
                item.icon_file,
                item.sink_points,
                int(item.is_raw_resource),
                int(item.is_event),
                item.energy_mj,
                item.byproduct_of_fr,
            )
            for item in items
        ],
    )


def _insert_buildings(connection: sqlite3.Connection, buildings: tuple[Building, ...]) -> None:
    connection.executemany(
        "INSERT INTO buildings (class_name, display_name, display_name_fr, kind, power_mw,"
        " power_exponent, icon_file) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (
                building.class_name,
                building.display_name,
                building.display_name_fr,
                building.kind.value,
                building.power_mw,
                building.power_exponent,
                building.icon_file,
            )
            for building in buildings
        ],
    )


def _insert_building_costs(connection: sqlite3.Connection, costs: tuple[BuildingCost, ...]) -> None:
    connection.executemany(
        "INSERT INTO building_costs (building_class, item_class, amount, recipe_class)"
        " VALUES (?, ?, ?, ?)",
        [
            (cost.class_name, item_class, amount, cost.recipe_class)
            for cost in costs
            for item_class, amount in sorted(cost.amounts.items())
        ],
    )


def _slot_rows(recipe: Recipe, slots: tuple[RecipeSlot, ...]) -> list[tuple[object, ...]]:
    return [
        (recipe.class_name, index, slot.item_class, slot.amount_per_cycle, slot.rate_per_minute)
        for index, slot in enumerate(slots)
    ]


def _insert_recipes(connection: sqlite3.Connection, recipes: tuple[Recipe, ...]) -> None:
    connection.executemany(
        "INSERT INTO recipes (class_name, display_name, display_name_fr, building_class,"
        " cycle_seconds, is_alternate, involves_fluid, product_count, is_event,"
        " availability, building_name_fr, power_constant_mw, power_factor_mw)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                recipe.class_name,
                recipe.display_name,
                recipe.display_name_fr,
                recipe.building_class,
                recipe.cycle_seconds,
                int(recipe.is_alternate),
                int(recipe.involves_fluid),
                recipe.product_count,
                int(recipe.is_event),
                recipe.availability.value,
                recipe.building_name_fr,
                recipe.power_constant_mw,
                recipe.power_factor_mw,
            )
            for recipe in recipes
        ],
    )
    for table, attribute in (
        ("recipe_ingredients", "ingredients"),
        ("recipe_products", "products"),
    ):
        rows = [row for recipe in recipes for row in _slot_rows(recipe, getattr(recipe, attribute))]
        connection.executemany(
            f"INSERT INTO {table} (recipe_class, slot_index, item_class, amount_per_cycle,"
            " rate_per_minute) VALUES (?, ?, ?, ?, ?)",
            rows,
        )


def _insert_extractors(connection: sqlite3.Connection, extractors: tuple[Extractor, ...]) -> None:
    connection.executemany(
        "INSERT INTO extractors (class_name, item_class, allowed_form, rate_per_minute,"
        " has_purity, activator_class) VALUES (?, ?, ?, ?, ?, ?)",
        [
            (
                extractor.class_name,
                extractor.item_class,
                extractor.allowed_form.value,
                extractor.rate_per_minute,
                int(extractor.has_purity),
                extractor.activator_class,
            )
            for extractor in extractors
        ],
    )
    connection.executemany(
        "INSERT INTO extractor_resources (extractor_class, slot_index, item_class)"
        " VALUES (?, ?, ?)",
        [
            (extractor.class_name, index, item_class)
            for extractor in extractors
            for index, item_class in enumerate(extractor.allowed_items)
        ],
    )


def _insert_generators(connection: sqlite3.Connection, generators: tuple[Generator, ...]) -> None:
    connection.executemany(
        "INSERT INTO generators (class_name, power_mw, power_min_mw, power_max_mw,"
        " has_purity) VALUES (?, ?, ?, ?, ?)",
        [
            (
                generator.class_name,
                generator.power_mw,
                generator.power_min_mw,
                generator.power_max_mw,
                int(generator.has_purity),
            )
            for generator in generators
        ],
    )
    connection.executemany(
        "INSERT INTO generator_fuels (generator_class, slot_index, item_class, rate_per_minute,"
        " supplemental_class, supplemental_per_minute, byproduct_class,"
        " byproduct_per_minute) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                generator.class_name,
                index,
                fuel.item_class,
                fuel.rate_per_minute,
                fuel.supplemental_class,
                fuel.supplemental_per_minute,
                fuel.byproduct_class,
                fuel.byproduct_per_minute,
            )
            for generator in generators
            for index, fuel in enumerate(generator.fuels)
        ],
    )


def _insert_belts(connection: sqlite3.Connection, belts: tuple[Belt, ...]) -> None:
    connection.executemany(
        "INSERT INTO belts (class_name, tier, items_per_minute) VALUES (?, ?, ?)",
        [(belt.class_name, belt.tier, belt.items_per_minute) for belt in belts],
    )


def _insert_pipes(connection: sqlite3.Connection, pipes: tuple[Pipe, ...]) -> None:
    connection.executemany(
        "INSERT INTO pipes (class_name, tier, cubic_metres_per_minute) VALUES (?, ?, ?)",
        [(pipe.class_name, pipe.tier, pipe.cubic_metres_per_minute) for pipe in pipes],
    )


def _insert_storages(connection: sqlite3.Connection, storages: tuple[Storage, ...]) -> None:
    connection.executemany(
        "INSERT INTO storages (class_name, form, slots, capacity_m3) VALUES (?, ?, ?, ?)",
        [
            (storage.class_name, storage.form.value, storage.slots, storage.capacity_m3)
            for storage in storages
        ],
    )


def _insert_attachments(
    connection: sqlite3.Connection, attachments: tuple[Attachment, ...]
) -> None:
    connection.executemany(
        "INSERT INTO attachments"
        " (class_name, form, can_split, can_merge, branches, splitter_mode)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        [
            (
                attachment.class_name,
                attachment.form.value,
                int(AttachmentRole.SPLIT in attachment.roles),
                int(AttachmentRole.MERGE in attachment.roles),
                attachment.branches,
                attachment.splitter_mode.value if attachment.splitter_mode else None,
            )
            for attachment in attachments
        ],
    )


def _insert_power_shards(connection: sqlite3.Connection, shards: tuple[PowerShard, ...]) -> None:
    connection.executemany(
        "INSERT INTO power_shards (class_name, extra_potential) VALUES (?, ?)",
        [(shard.class_name, shard.extra_potential) for shard in shards],
    )


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #


def read_meta(connection: sqlite3.Connection) -> dict[str, str]:
    return {row["key"]: row["value"] for row in connection.execute("SELECT key, value FROM meta")}


def read_items(connection: sqlite3.Connection) -> list[Item]:
    return [
        Item(
            class_name=row["class_name"],
            display_name=row["display_name"],
            display_name_fr=row["display_name_fr"],
            description_fr=row["description_fr"],
            form=ItemForm(row["form"]),
            stack_size=row["stack_size"],
            icon_file=row["icon_file"],
            sink_points=row["sink_points"],
            is_raw_resource=bool(row["is_raw_resource"]),
            is_event=bool(row["is_event"]),
            energy_mj=row["energy_mj"],
            byproduct_of_fr=row["byproduct_of_fr"],
        )
        for row in connection.execute("SELECT * FROM items ORDER BY class_name")
    ]


def read_buildings(connection: sqlite3.Connection) -> list[Building]:
    return [
        Building(
            class_name=row["class_name"],
            display_name=row["display_name"],
            display_name_fr=row["display_name_fr"],
            kind=BuildingKind(row["kind"]),
            power_mw=row["power_mw"],
            power_exponent=row["power_exponent"],
            icon_file=row["icon_file"],
        )
        for row in connection.execute("SELECT * FROM buildings ORDER BY class_name")
    ]


def read_building_costs(connection: sqlite3.Connection) -> list[BuildingCost]:
    """One :class:`BuildingCost` per building, rebuilt from its rows."""
    amounts: dict[str, dict[str, float]] = {}
    recipes: dict[str, str] = {}
    for row in connection.execute(
        "SELECT * FROM building_costs ORDER BY building_class, item_class"
    ):
        building = row["building_class"]
        amounts.setdefault(building, {})[row["item_class"]] = row["amount"]
        recipes[building] = row["recipe_class"]
    return [
        BuildingCost(class_name=building, recipe_class=recipes[building], amounts=items)
        for building, items in sorted(amounts.items())
    ]


def read_recipes(connection: sqlite3.Connection) -> list[Recipe]:
    """The recipes a node can be placed for -- what the engine is allowed to see."""
    return _read_recipes(connection, placeable=True)


def read_unavailable_recipes(connection: sqlite3.Connection) -> list[Recipe]:
    """The recipes the game has and no node can place, with what stops each one."""
    return _read_recipes(connection, placeable=False)


def _read_recipes(connection: sqlite3.Connection, *, placeable: bool) -> list[Recipe]:
    """Both readers go through here so the filter is written once and cannot be
    forgotten: an unfiltered read would put out-of-scope recipes in front of the
    engine, and nothing downstream would notice until a factory computed wrong."""
    slots: dict[str, dict[str, list[RecipeSlot]]] = {}
    for table, key in (("recipe_ingredients", "ingredients"), ("recipe_products", "products")):
        query = f"SELECT * FROM {table} ORDER BY recipe_class, slot_index"
        for row in connection.execute(query):
            slots.setdefault(row["recipe_class"], {}).setdefault(key, []).append(
                RecipeSlot(
                    item_class=row["item_class"],
                    amount_per_cycle=row["amount_per_cycle"],
                    rate_per_minute=row["rate_per_minute"],
                )
            )
    return [
        Recipe(
            class_name=row["class_name"],
            display_name=row["display_name"],
            display_name_fr=row["display_name_fr"],
            building_class=row["building_class"],
            cycle_seconds=row["cycle_seconds"],
            is_alternate=bool(row["is_alternate"]),
            involves_fluid=bool(row["involves_fluid"]),
            ingredients=tuple(slots.get(row["class_name"], {}).get("ingredients", [])),
            products=tuple(slots.get(row["class_name"], {}).get("products", [])),
            is_event=bool(row["is_event"]),
            availability=RecipeAvailability(row["availability"]),
            building_name_fr=row["building_name_fr"],
            power_constant_mw=row["power_constant_mw"],
            power_factor_mw=row["power_factor_mw"],
        )
        for row in connection.execute(
            "SELECT * FROM recipes WHERE (availability = 'placeable') = ? ORDER BY class_name",
            (int(placeable),),
        )
    ]


def read_extractors(connection: sqlite3.Connection) -> list[Extractor]:
    allowed: dict[str, list[str]] = {}
    query = "SELECT * FROM extractor_resources ORDER BY extractor_class, slot_index"
    for row in connection.execute(query):
        allowed.setdefault(row["extractor_class"], []).append(row["item_class"])
    return [
        Extractor(
            class_name=row["class_name"],
            item_class=row["item_class"],
            allowed_form=ItemForm(row["allowed_form"]),
            rate_per_minute=row["rate_per_minute"],
            has_purity=bool(row["has_purity"]),
            activator_class=row["activator_class"],
            allowed_items=tuple(allowed.get(row["class_name"], ())),
        )
        for row in connection.execute("SELECT * FROM extractors ORDER BY class_name")
    ]


def read_generators(connection: sqlite3.Connection) -> list[Generator]:
    fuels: dict[str, list[GeneratorFuel]] = {}
    query = "SELECT * FROM generator_fuels ORDER BY generator_class, slot_index"
    for row in connection.execute(query):
        fuels.setdefault(row["generator_class"], []).append(
            GeneratorFuel(
                item_class=row["item_class"],
                rate_per_minute=row["rate_per_minute"],
                supplemental_class=row["supplemental_class"],
                supplemental_per_minute=row["supplemental_per_minute"],
                byproduct_class=row["byproduct_class"],
                byproduct_per_minute=row["byproduct_per_minute"],
            )
        )
    return [
        Generator(
            class_name=row["class_name"],
            power_mw=row["power_mw"],
            fuels=tuple(fuels.get(row["class_name"], [])),
            power_min_mw=row["power_min_mw"],
            power_max_mw=row["power_max_mw"],
            has_purity=bool(row["has_purity"]),
        )
        for row in connection.execute("SELECT * FROM generators ORDER BY class_name")
    ]


def read_belts(connection: sqlite3.Connection) -> list[Belt]:
    return [
        Belt(
            class_name=row["class_name"],
            tier=row["tier"],
            items_per_minute=row["items_per_minute"],
        )
        for row in connection.execute("SELECT * FROM belts ORDER BY tier")
    ]


def read_pipes(connection: sqlite3.Connection) -> list[Pipe]:
    return [
        Pipe(
            class_name=row["class_name"],
            tier=row["tier"],
            cubic_metres_per_minute=row["cubic_metres_per_minute"],
        )
        for row in connection.execute("SELECT * FROM pipes ORDER BY tier")
    ]


def read_storages(connection: sqlite3.Connection) -> list[Storage]:
    return [
        Storage(
            class_name=row["class_name"],
            form=ItemForm(row["form"]),
            slots=row["slots"],
            capacity_m3=row["capacity_m3"],
        )
        for row in connection.execute("SELECT * FROM storages ORDER BY class_name")
    ]


def read_attachments(connection: sqlite3.Connection) -> list[Attachment]:
    rows = connection.execute("SELECT * FROM attachments ORDER BY class_name")
    return [
        Attachment(
            class_name=row["class_name"],
            form=ItemForm(row["form"]),
            roles=tuple(
                role
                for role, flag in (
                    (AttachmentRole.SPLIT, row["can_split"]),
                    (AttachmentRole.MERGE, row["can_merge"]),
                )
                if flag
            ),
            branches=row["branches"],
            splitter_mode=(
                SplitterMode(row["splitter_mode"]) if row["splitter_mode"] else None
            ),
        )
        for row in rows
    ]


def read_power_shards(connection: sqlite3.Connection) -> list[PowerShard]:
    return [
        PowerShard(class_name=row["class_name"], extra_potential=row["extra_potential"])
        for row in connection.execute("SELECT * FROM power_shards ORDER BY class_name")
    ]


def load_game_data(connection: sqlite3.Connection) -> GameData:
    """Read the whole catalogue in the form the engine expects.

    This is the injection point: the engine receives a ``GameData`` and never
    touches SQLite itself.
    """
    return GameData.from_rows(
        items=read_items(connection),
        recipes=read_recipes(connection),
        buildings=read_buildings(connection),
        extractors=read_extractors(connection),
        generators=read_generators(connection),
        belts=read_belts(connection),
        pipes=read_pipes(connection),
        storages=read_storages(connection),
        attachments=read_attachments(connection),
        power_shards=read_power_shards(connection),
        building_costs=read_building_costs(connection),
        unavailable_recipes=read_unavailable_recipes(connection),
    )


def load_game_data_from_file(path: Path) -> GameData:
    """Convenience wrapper that opens the database read-only and closes it."""
    with connect(path) as connection:
        return load_game_data(connection)
