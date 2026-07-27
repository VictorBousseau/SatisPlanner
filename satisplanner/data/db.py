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
SCHEMA_VERSION: Final = 5

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
    energy_mj       REAL NOT NULL DEFAULT 0
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

CREATE TABLE recipes (
    class_name      TEXT PRIMARY KEY,
    display_name    TEXT NOT NULL,
    display_name_fr TEXT NOT NULL,
    building_class  TEXT NOT NULL REFERENCES buildings (class_name),
    cycle_seconds   REAL NOT NULL,
    is_alternate    INTEGER NOT NULL CHECK (is_alternate IN (0, 1)),
    involves_fluid  INTEGER NOT NULL CHECK (involves_fluid IN (0, 1)),
    product_count   INTEGER NOT NULL,
    is_event        INTEGER NOT NULL CHECK (is_event IN (0, 1))
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
    has_purity      INTEGER NOT NULL CHECK (has_purity IN (0, 1))
);

-- Buildings that put power on the grid instead of taking it off.
CREATE TABLE generators (
    class_name TEXT PRIMARY KEY REFERENCES buildings (class_name),
    -- mPowerProduction, in MW at 100 %. Generators are not clockable in this
    -- version: their production exponent is not the consumption one.
    power_mw   REAL NOT NULL
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

-- Line attachments: never nodes on the canvas, only entries in the shopping list.
CREATE TABLE attachments (
    class_name TEXT PRIMARY KEY REFERENCES buildings (class_name),
    form       TEXT NOT NULL CHECK (form IN ('solid', 'liquid', 'gas')),
    can_split  INTEGER NOT NULL CHECK (can_split IN (0, 1)),
    can_merge  INTEGER NOT NULL CHECK (can_merge IN (0, 1)),
    branches   INTEGER NOT NULL
);

-- Consumables that raise a building's clock ceiling. Only the overclocking kind
-- is stored; the Somersloop amplifies production instead and is out of V1 scope.
CREATE TABLE power_shards (
    class_name      TEXT PRIMARY KEY REFERENCES items (class_name),
    extra_potential REAL NOT NULL
);

CREATE INDEX idx_recipes_building ON recipes (building_class);
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
        _insert_recipes(connection, dataset.recipes)
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
        " stack_size, icon_file, sink_points, is_raw_resource, is_event, energy_mj)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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


def _slot_rows(recipe: Recipe, slots: tuple[RecipeSlot, ...]) -> list[tuple[object, ...]]:
    return [
        (recipe.class_name, index, slot.item_class, slot.amount_per_cycle, slot.rate_per_minute)
        for index, slot in enumerate(slots)
    ]


def _insert_recipes(connection: sqlite3.Connection, recipes: tuple[Recipe, ...]) -> None:
    connection.executemany(
        "INSERT INTO recipes (class_name, display_name, display_name_fr, building_class,"
        " cycle_seconds, is_alternate, involves_fluid, product_count, is_event)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
        " has_purity) VALUES (?, ?, ?, ?, ?)",
        [
            (
                extractor.class_name,
                extractor.item_class,
                extractor.allowed_form.value,
                extractor.rate_per_minute,
                int(extractor.has_purity),
            )
            for extractor in extractors
        ],
    )


def _insert_generators(connection: sqlite3.Connection, generators: tuple[Generator, ...]) -> None:
    connection.executemany(
        "INSERT INTO generators (class_name, power_mw) VALUES (?, ?)",
        [(generator.class_name, generator.power_mw) for generator in generators],
    )
    connection.executemany(
        "INSERT INTO generator_fuels (generator_class, slot_index, item_class, rate_per_minute,"
        " supplemental_class, supplemental_per_minute) VALUES (?, ?, ?, ?, ?, ?)",
        [
            (
                generator.class_name,
                index,
                fuel.item_class,
                fuel.rate_per_minute,
                fuel.supplemental_class,
                fuel.supplemental_per_minute,
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
        "INSERT INTO attachments (class_name, form, can_split, can_merge, branches)"
        " VALUES (?, ?, ?, ?, ?)",
        [
            (
                attachment.class_name,
                attachment.form.value,
                int(AttachmentRole.SPLIT in attachment.roles),
                int(AttachmentRole.MERGE in attachment.roles),
                attachment.branches,
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


def read_recipes(connection: sqlite3.Connection) -> list[Recipe]:
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
        )
        for row in connection.execute("SELECT * FROM recipes ORDER BY class_name")
    ]


def read_extractors(connection: sqlite3.Connection) -> list[Extractor]:
    return [
        Extractor(
            class_name=row["class_name"],
            item_class=row["item_class"],
            allowed_form=ItemForm(row["allowed_form"]),
            rate_per_minute=row["rate_per_minute"],
            has_purity=bool(row["has_purity"]),
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
            )
        )
    return [
        Generator(
            class_name=row["class_name"],
            power_mw=row["power_mw"],
            fuels=tuple(fuels.get(row["class_name"], [])),
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
    )


def load_game_data_from_file(path: Path) -> GameData:
    """Convenience wrapper that opens the database read-only and closes it."""
    with connect(path) as connection:
        return load_game_data(connection)
