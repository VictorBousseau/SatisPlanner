"""The ``.sfp`` factory file, and the share code that carries the same thing as text.

A ``.sfp`` is a ZIP holding three members:

* ``factory.json`` -- the graph, exactly as :class:`FactoryGraph` serialises it;
* ``manifest.json`` -- who wrote it, when, against which game data, and under which
  schema version;
* ``thumbnail.png`` -- a picture of the canvas, so a file manager and a future
  "recent factories" screen have something to show. Optional on read.

Two things this module takes seriously.

**Migration exists from the first version.** :func:`migrate` is the single door a
document goes through on the way in, and it walks one step at a time from the file's
schema version to the current one. There is nothing to migrate today; the point is
that the day there is, there is one place to put it and one place tests can aim at.

**A file the application did not write must not crash it.** Truncated archives,
corrupted payloads, versions from the future and class names that no longer exist all
come back as a :class:`FactoryFileError` carrying a French sentence, or as a warning
on an otherwise usable document. A stack trace is never an error message.
"""

import base64
import binascii
import json
import logging
import zipfile
import zlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from satisplanner import __version__
from satisplanner.core.graph import (
    SCHEMA_VERSION,
    ExternalSourceNode,
    FactoryGraph,
    GeneratorNode,
    GraphError,
    MachineNode,
    Node,
    OutputNode,
    ResourceNode,
    StorageNode,
    WaterExtractorNode,
)
from satisplanner.core.models import GameData
from satisplanner.data.db import GAME_VERSION

logger = logging.getLogger(__name__)

FILE_SUFFIX: Final = ".sfp"
FILE_FILTER: Final = "Usine SatisPlanner (*.sfp)"

GRAPH_MEMBER: Final = "factory.json"
MANIFEST_MEMBER: Final = "manifest.json"
THUMBNAIL_MEMBER: Final = "thumbnail.png"

# Share codes. The prefix is the version of the *envelope*, not of the document: it
# says how to unwrap, and the schema version inside says how to read the result.
SHARE_PREFIX: Final = "SFP1:"

# A code longer than this is refused before being decompressed: a zlib bomb should
# not be able to make the application allocate gigabytes from a pasted string.
MAX_SHARE_CODE_LENGTH: Final = 2_000_000
MAX_DECOMPRESSED_BYTES: Final = 32 * 1024 * 1024


class FactoryFileError(RuntimeError):
    """A factory could not be read. The message is French and shown as is."""


@dataclass(frozen=True)
class Manifest:
    """What a file says about itself."""

    application_version: str
    game_version: str
    schema_version: int
    saved_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "application_version": self.application_version,
            "game_version": self.game_version,
            "schema_version": self.schema_version,
            "saved_at": self.saved_at,
        }

    @classmethod
    def current(cls) -> "Manifest":
        return cls(
            application_version=__version__,
            game_version=GAME_VERSION,
            schema_version=SCHEMA_VERSION,
            saved_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Manifest":
        try:
            version = int(raw.get("schema_version", SCHEMA_VERSION))
        except (TypeError, ValueError) as exc:
            msg = f"version de schema illisible dans le manifeste : {raw.get('schema_version')!r}"
            raise FactoryFileError(msg) from exc
        return cls(
            application_version=str(raw.get("application_version", "?")),
            game_version=str(raw.get("game_version", "?")),
            schema_version=version,
            saved_at=str(raw.get("saved_at", "")),
        )


@dataclass
class LoadedFactory:
    """A document that came from outside, with everything odd about it recorded."""

    graph: FactoryGraph
    manifest: Manifest
    thumbnail: bytes | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        return not self.warnings


# --------------------------------------------------------------------------- #
# Schema migration
# --------------------------------------------------------------------------- #


def _one_to_two(payload: dict[str, Any]) -> dict[str, Any]:
    """Schema 1 to 2: clock speed added to extractors and machines.

    Nothing to write. The field defaults to 100 %, which is exactly what a document
    from before it existed meant. The step is here because the walk must not have a
    hole in it -- and because a version that migrates by doing nothing is a fact
    worth stating once rather than rediscovering.
    """
    return payload


def _two_to_three(payload: dict[str, Any]) -> dict[str, Any]:
    """Schema 2 to 3: the generator node appeared.

    Nothing to write either. A document from before it existed simply has none,
    and every other node is untouched. The step exists so the walk stays whole and
    so a schema-3 file is refused by a build that cannot draw a generator.
    """
    return payload


def _three_to_four(payload: dict[str, Any]) -> dict[str, Any]:
    """Schema 3 to 4: the per-node deployed-rendering override appeared.

    Nothing to write once more. ``None`` means "follow the global preference",
    which is what a document written before the field existed meant.
    """
    return payload


# One entry per version that needs lifting, keyed by the version it lifts *from*.
MIGRATIONS: Final[dict[int, Callable[[dict[str, Any]], dict[str, Any]]]] = {
    1: _one_to_two,
    2: _two_to_three,
    3: _three_to_four,
}


def migrate(payload: dict[str, Any], schema_version: int) -> tuple[dict[str, Any], list[str]]:
    """Lift a document to the current schema, one version at a time.

    Returns the payload and any note worth showing the user. A version from the
    future is refused outright: guessing at a format that did not exist yet would
    silently drop whatever it added.
    """
    if schema_version > SCHEMA_VERSION:
        msg = (
            f"ce fichier a ete ecrit par une version plus recente de SatisPlanner "
            f"(schema {schema_version}, cette version lit jusqu'au {SCHEMA_VERSION}). "
            f"Mettez l'application a jour pour l'ouvrir."
        )
        raise FactoryFileError(msg)

    notes: list[str] = []
    current = schema_version
    while current < SCHEMA_VERSION:
        step = MIGRATIONS.get(current)
        if step is None:  # pragma: no cover - unreachable while MIGRATIONS is empty
            msg = (
                f"aucune migration connue depuis le schema {current} : "
                f"le fichier ne peut pas etre converti."
            )
            raise FactoryFileError(msg)
        payload = step(payload)
        notes.append(f"document converti du schema {current} au schema {current + 1}")
        current += 1
    return payload, notes


# --------------------------------------------------------------------------- #
# Writing
# --------------------------------------------------------------------------- #


def graph_json(graph: FactoryGraph) -> str:
    """The graph as it is stored: indented, so a diff on a save is readable."""
    return json.dumps(json.loads(graph.model_dump_json()), indent=2, ensure_ascii=False)


def save(path: Path, graph: FactoryGraph, thumbnail: bytes | None = None) -> Manifest:
    """Write ``graph`` to ``path`` as a ``.sfp`` archive. Returns what was recorded."""
    manifest = Manifest.current()
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(GRAPH_MEMBER, graph_json(graph))
        archive.writestr(
            MANIFEST_MEMBER, json.dumps(manifest.as_dict(), indent=2, ensure_ascii=False)
        )
        if thumbnail:
            archive.writestr(THUMBNAIL_MEMBER, thumbnail)
    logger.info("usine enregistree : %s", path)
    return manifest


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #


def load(path: Path) -> LoadedFactory:
    """Read a ``.sfp``. Raises :class:`FactoryFileError` with a French reason."""
    if not path.is_file():
        msg = f"fichier introuvable : {path}"
        raise FactoryFileError(msg)
    try:
        with zipfile.ZipFile(path) as archive:
            members = set(archive.namelist())
            if GRAPH_MEMBER not in members:
                msg = f"{path.name} n'est pas une usine SatisPlanner : {GRAPH_MEMBER} manquant."
                raise FactoryFileError(msg)
            raw_graph = archive.read(GRAPH_MEMBER).decode("utf-8")
            raw_manifest = (
                archive.read(MANIFEST_MEMBER).decode("utf-8")
                if MANIFEST_MEMBER in members
                else "{}"
            )
            thumbnail = archive.read(THUMBNAIL_MEMBER) if THUMBNAIL_MEMBER in members else None
    except (zipfile.BadZipFile, OSError) as exc:
        msg = f"{path.name} est illisible ou endommage : {exc}"
        raise FactoryFileError(msg) from exc
    except UnicodeDecodeError as exc:
        msg = f"{path.name} contient du texte qui n'est pas de l'UTF-8."
        raise FactoryFileError(msg) from exc

    loaded = _document_from(raw_graph, raw_manifest)
    loaded.thumbnail = thumbnail
    return loaded


def _document_from(raw_graph: str, raw_manifest: str) -> LoadedFactory:
    """Parse, migrate and validate, turning every failure into a sentence."""
    try:
        payload = json.loads(raw_graph)
        manifest = Manifest.from_dict(json.loads(raw_manifest))
    except json.JSONDecodeError as exc:
        msg = f"le contenu n'est pas du JSON valide : {exc.msg} (ligne {exc.lineno})"
        raise FactoryFileError(msg) from exc
    if not isinstance(payload, dict):
        msg = "le contenu ne decrit pas une usine : un objet JSON etait attendu."
        raise FactoryFileError(msg)

    payload, warnings = migrate(payload, manifest.schema_version)
    try:
        graph = FactoryGraph.model_validate(payload)
    except GraphError as exc:
        msg = f"l'usine decrite est incoherente : {exc}"
        raise FactoryFileError(msg) from exc
    except ValueError as exc:
        msg = f"l'usine decrite ne respecte pas le format attendu : {_first_line(exc)}"
        raise FactoryFileError(msg) from exc

    if manifest.game_version not in ("?", GAME_VERSION):
        warnings.append(
            f"ce fichier a ete enregistre avec les donnees du jeu {manifest.game_version}, "
            f"alors que cette version embarque celles de la {GAME_VERSION} : "
            f"les recettes ont pu changer, verifiez les debits."
        )
    return LoadedFactory(graph=graph, manifest=manifest, warnings=warnings)


def _first_line(error: Exception) -> str:
    """Pydantic errors are several lines long; the user only needs the first."""
    return str(error).splitlines()[0]


# --------------------------------------------------------------------------- #
# Classes the catalogue no longer knows
# --------------------------------------------------------------------------- #


def unknown_classes(graph: FactoryGraph, game_data: GameData) -> list[str]:
    """Class names the loaded factory refers to and the catalogue does not have.

    A recipe removed by a game update leaves a node pointing at nothing. The file is
    still opened -- losing someone's layout because one recipe moved would be worse
    -- but every dangling name is reported so the user knows what to fix.
    """
    transports = set(game_data.belts) | set(game_data.pipes)
    missing: set[str] = set()
    for node in graph.sorted_nodes():
        missing.update(
            name for name, catalogue in _referenced_by(node, game_data) if name not in catalogue
        )
    for edge in graph.sorted_edges():
        if edge.item_class not in game_data.items:
            missing.add(edge.item_class)
        if edge.transport_class not in transports:
            missing.add(edge.transport_class)
    return sorted(missing)


def _referenced_by(node: Node, game_data: GameData) -> list[tuple[str, Any]]:
    """Every class name a node points at, paired with the catalogue that should hold it."""
    match node:
        case MachineNode() as machine:
            found: list[tuple[str, Any]] = [(machine.recipe_class, game_data.recipes)]
            if machine.building_class:
                found.append((machine.building_class, game_data.buildings))
            return found
        case ResourceNode() as deposit:
            return [
                (deposit.item_class, game_data.items),
                (deposit.extractor_class, game_data.extractors),
            ]
        case WaterExtractorNode() as pump:
            return [(pump.extractor_class, game_data.extractors)]
        case GeneratorNode() as generator:
            return [
                (generator.generator_class, game_data.generators),
                (generator.fuel_class, game_data.items),
            ]
        case ExternalSourceNode() | OutputNode() as endpoint:
            return [(endpoint.item_class, game_data.items)]
        case StorageNode() as storage:
            stored: list[tuple[str, Any]] = [(storage.storage_class, game_data.storages)]
            if storage.item_class:
                stored.append((storage.item_class, game_data.items))
            return stored


def prune_unknown(graph: FactoryGraph, game_data: GameData) -> tuple[list[str], list[str]]:
    """Remove what the catalogue cannot describe, and say what went.

    Returns the class names that were missing and the identifiers of the nodes that
    referred to them. Keeping such a node is not an option: every layer below --
    the solver, the canvas, the table -- looks its recipe up and is entitled to find
    it, and weakening that invariant everywhere to accommodate a file from another
    game version would be a poor trade. Dropping the node and naming it keeps the
    rest of the layout, which is what the user actually stands to lose.
    """
    missing = unknown_classes(graph, game_data)
    if not missing:
        return [], []
    absent = set(missing)
    doomed = [
        node.id
        for node in graph.sorted_nodes()
        if any(name in absent for name, _ in _referenced_by(node, game_data))
    ]
    for node_id in doomed:
        graph.remove_node(node_id)
    graph.edges = [
        edge
        for edge in graph.edges
        if edge.item_class not in absent and edge.transport_class not in absent
    ]
    return missing, doomed


def describe_unknown(missing: Sequence[str], removed: Sequence[str] = ()) -> str:
    """The French sentence shown when a file refers to classes we do not have."""
    listed = ", ".join(missing[:8])
    more = f" et {len(missing) - 8} autre(s)" if len(missing) > 8 else ""
    sentence = (
        f"{len(missing)} classe(s) de cette usine sont absentes du catalogue embarque : "
        f"{listed}{more}."
    )
    if removed:
        sentence += (
            f" Les {len(removed)} noeud(s) concerne(s) ont ete retire(s) "
            f"({', '.join(removed[:8])}) ; le reste de l'usine est intact."
        )
    return sentence


# --------------------------------------------------------------------------- #
# Share code
# --------------------------------------------------------------------------- #


def encode_share_code(graph: FactoryGraph) -> str:
    """The whole factory as one pasteable line.

    Compact rather than readable: a share code goes through chat clients that wrap,
    truncate and re-encode, so the payload is base64url with no padding surprises and
    the version lives in a prefix that survives all of that.
    """
    payload = json.dumps(
        {"manifest": Manifest.current().as_dict(), "graph": json.loads(graph.model_dump_json())},
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    packed = base64.urlsafe_b64encode(zlib.compress(payload, level=9)).decode("ascii")
    return f"{SHARE_PREFIX}{packed}"


def decode_share_code(code: str) -> LoadedFactory:
    """Read a share code back. Every failure mode is a French sentence."""
    cleaned = "".join(code.split())
    if not cleaned:
        msg = "aucun code n'a ete fourni."
        raise FactoryFileError(msg)
    if not cleaned.startswith(SHARE_PREFIX):
        msg = (
            f"ce texte n'est pas un code SatisPlanner : il devrait commencer par "
            f"« {SHARE_PREFIX} »."
        )
        raise FactoryFileError(msg)
    if len(cleaned) > MAX_SHARE_CODE_LENGTH:
        msg = "ce code est trop long pour etre une usine : il a probablement ete mal colle."
        raise FactoryFileError(msg)

    body = cleaned[len(SHARE_PREFIX) :]
    try:
        compressed = base64.urlsafe_b64decode(_padded(body))
    except (binascii.Error, ValueError) as exc:
        msg = "ce code est incomplet ou abime : la partie codee n'a pas pu etre relue."
        raise FactoryFileError(msg) from exc

    try:
        raw = _decompress(compressed)
    except zlib.error as exc:
        msg = "ce code est tronque ou corrompu : les donnees compressees sont incompletes."
        raise FactoryFileError(msg) from exc

    try:
        envelope = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        msg = "ce code se decompresse mais ne contient pas une usine lisible."
        raise FactoryFileError(msg) from exc
    if not isinstance(envelope, dict) or "graph" not in envelope:
        msg = "ce code ne contient pas d'usine."
        raise FactoryFileError(msg)

    return _document_from(json.dumps(envelope["graph"]), json.dumps(envelope.get("manifest", {})))


def _padded(body: str) -> str:
    """Restore the ``=`` padding a chat client may have eaten."""
    return body + "=" * (-len(body) % 4)


def _decompress(compressed: bytes) -> bytes:
    """Inflate with a ceiling, so a hostile code cannot exhaust memory."""
    machine = zlib.decompressobj()
    raw = machine.decompress(compressed, MAX_DECOMPRESSED_BYTES)
    if machine.unconsumed_tail:
        msg = "donnees decompressees au-dela de la limite acceptee"
        raise zlib.error(msg)
    if not machine.eof:
        msg = "flux compresse incomplet"
        raise zlib.error(msg)
    return raw
