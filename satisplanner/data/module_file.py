"""The module library: named pieces of factory, on disk under ``%LOCALAPPDATA%``.

**The payload is a share code.** Not a second format, and that is the whole design:
the share code already knows how to compress itself, how to refuse a version from
the future, and -- through :func:`satisplanner.data.factory_file.migrate` -- how to
read a document written by an older build. A library that invented its own encoding
would have to learn all three again, and would learn them late, on somebody's saved
work. It also means a module written today keeps working when a new kind of node is
added: the code carries whatever the graph carries.

**One file per module.** A single library file would be one corruption away from
losing everything; here a file that cannot be read is one module missing and a
sentence saying which, and :func:`load_library` goes on to the next.

What a module carries beyond the code is what a library needs to be searchable and a
reader needs to choose: a name, a description, when it was saved, a thumbnail, and
the rates it moves. Those rates are computed once, at saving time, by resolving the
module on its own -- see :mod:`satisplanner.core.interface`. They are a **label**:
inserted into a factory that starves it, the module will do less.
"""

import base64
import json
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from satisplanner import paths
from satisplanner.core.graph import FactoryGraph
from satisplanner.data.factory_file import FactoryFileError, decode_share_code

logger = logging.getLogger(__name__)

MODULE_SUFFIX: Final = ".sfm"
MODULE_FILTER: Final = "Module SatisPlanner (*.sfm)"
MODULE_SUBPATH: Final = "modules"

# Version of the **envelope** -- the fields around the code -- not of the document
# inside it, which carries its own and is migrated by ``factory_file``.
MODULE_VERSION: Final = 1

# Long enough for a sentence, short enough to stay a name.
MAX_NAME_LENGTH: Final = 80

_SLUG_STRIP: Final = re.compile(r"[^a-z0-9]+")


class ModuleError(RuntimeError):
    """A module could not be read or written. The message is French and shown as is."""


@dataclass(frozen=True)
class FactoryModule:
    """One saved module: its metadata, its label, and the code that holds it."""

    name: str
    share_code: str
    description: str = ""
    saved_at: str = ""
    # Item class -> rate per minute, the module resolved on its own.
    inputs: dict[str, float] = field(default_factory=dict)
    outputs: dict[str, float] = field(default_factory=dict)
    thumbnail: bytes | None = None
    # Where it lives, once it has been written or read. ``None`` before that.
    path: Path | None = None

    def graph(self) -> FactoryGraph:
        """The piece of factory itself, migrated if it was written long ago."""
        try:
            return decode_share_code(self.share_code).graph
        except FactoryFileError as exc:
            msg = f"« {self.name} » : {exc}"
            raise ModuleError(msg) from exc

    def produces(self, item_class: str) -> bool:
        return item_class in self.outputs

    def as_dict(self) -> dict[str, Any]:
        return {
            "module_version": MODULE_VERSION,
            "name": self.name,
            "description": self.description,
            "saved_at": self.saved_at,
            "inputs": self.inputs,
            "outputs": self.outputs,
            "thumbnail": (
                base64.b64encode(self.thumbnail).decode("ascii") if self.thumbnail else None
            ),
            "code": self.share_code,
        }


def module_directory() -> Path:
    """Where the library lives. Not created here -- see :func:`save_module`."""
    return paths.app_data_directory() / MODULE_SUBPATH


def slug(name: str) -> str:
    """A file name from a module name: readable, and safe on any file system.

    Accents are folded rather than stripped, so « Cadre lourd » and « Cadre lourd »
    written with a combining accent land on the same file instead of two.
    """
    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    cleaned = _SLUG_STRIP.sub("-", folded.lower()).strip("-")
    return cleaned or "module"


def _free_path(directory: Path, name: str, *, replacing: Path | None = None) -> Path:
    """A path for this name, adding a number rather than overwriting a neighbour."""
    base = slug(name)
    candidate = directory / f"{base}{MODULE_SUFFIX}"
    index = 2
    while candidate.exists() and candidate != replacing:
        candidate = directory / f"{base}-{index}{MODULE_SUFFIX}"
        index += 1
    return candidate


def save_module(
    module: FactoryModule, directory: Path | None = None, *, replacing: Path | None = None
) -> FactoryModule:
    """Write a module and return it with its path filled in.

    ``replacing`` is the file this one takes the place of: re-saving a module under
    its own name overwrites it, and only it.
    """
    target_dir = directory if directory is not None else module_directory()
    paths.ensure_directory(target_dir)
    stamped = FactoryModule(
        name=module.name,
        share_code=module.share_code,
        description=module.description,
        saved_at=module.saved_at or datetime.now(UTC).isoformat(timespec="seconds"),
        inputs=module.inputs,
        outputs=module.outputs,
        thumbnail=module.thumbnail,
    )
    path = replacing if replacing is not None else _free_path(target_dir, module.name)
    try:
        path.write_text(
            json.dumps(stamped.as_dict(), ensure_ascii=False, indent=1), encoding="utf-8"
        )
    except OSError as exc:
        msg = f"module « {module.name} » non enregistré : {exc.strerror}"
        raise ModuleError(msg) from exc
    logger.info("module enregistré : %s", path)
    return FactoryModule(
        name=stamped.name,
        share_code=stamped.share_code,
        description=stamped.description,
        saved_at=stamped.saved_at,
        inputs=stamped.inputs,
        outputs=stamped.outputs,
        thumbnail=stamped.thumbnail,
        path=path,
    )


def rename_module(
    module: FactoryModule, name: str, description: str | None = None
) -> FactoryModule:
    """Save the module under a new name, and drop the file it used to be in.

    The file is moved rather than left behind: a library that kept the old copy
    would answer a search twice with the same module under two names.
    """
    renamed = FactoryModule(
        name=name,
        share_code=module.share_code,
        description=module.description if description is None else description,
        saved_at=module.saved_at,
        inputs=module.inputs,
        outputs=module.outputs,
        thumbnail=module.thumbnail,
    )
    if module.path is not None and slug(name) == slug(module.name):
        return save_module(renamed, module.path.parent, replacing=module.path)
    directory = module.path.parent if module.path is not None else module_directory()
    saved = save_module(renamed, directory)
    delete_module(module)
    return saved


def delete_module(module: FactoryModule) -> None:
    if module.path is None:
        return
    try:
        module.path.unlink(missing_ok=True)
    except OSError as exc:
        msg = f"module « {module.name} » non supprimé : {exc.strerror}"
        raise ModuleError(msg) from exc
    logger.info("module supprimé : %s", module.path)


def load_module(path: Path) -> FactoryModule:
    """Read one module file. Every failure mode is a French sentence."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        msg = f"{path.name} : illisible ({exc.strerror})."
        raise ModuleError(msg) from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        msg = f"{path.name} : ce n'est pas un module SatisPlanner lisible."
        raise ModuleError(msg) from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("code"), str):
        msg = f"{path.name} : ce fichier ne contient aucun module."
        raise ModuleError(msg)
    version = raw.get("module_version", MODULE_VERSION)
    if not isinstance(version, int) or version > MODULE_VERSION:
        msg = (
            f"{path.name} : écrit par une version plus récente de SatisPlanner "
            f"(format {version}, connu jusqu'à {MODULE_VERSION})."
        )
        raise ModuleError(msg)

    return FactoryModule(
        name=str(raw.get("name") or path.stem),
        share_code=raw["code"],
        description=str(raw.get("description", "")),
        saved_at=str(raw.get("saved_at", "")),
        inputs=_rates(raw.get("inputs")),
        outputs=_rates(raw.get("outputs")),
        thumbnail=_thumbnail(raw.get("thumbnail"), path),
        path=path,
    )


def load_library(directory: Path | None = None) -> tuple[list[FactoryModule], list[str]]:
    """Every module in the directory, and a sentence for each file that failed.

    One unreadable file must not cost the reader their whole library, so the
    failures are collected and returned beside the modules rather than raised.
    """
    target = directory if directory is not None else module_directory()
    if not target.is_dir():
        return [], []
    modules: list[FactoryModule] = []
    problems: list[str] = []
    for path in sorted(target.glob(f"*{MODULE_SUFFIX}")):
        try:
            modules.append(load_module(path))
        except ModuleError as exc:
            logger.warning("module ignoré : %s", exc)
            problems.append(str(exc))
    modules.sort(key=lambda module: module.name.casefold())
    return modules, problems


def _rates(raw: Any) -> dict[str, float]:
    if not isinstance(raw, dict):
        return {}
    rates: dict[str, float] = {}
    for item_class, value in raw.items():
        try:
            rates[str(item_class)] = float(value)
        except (TypeError, ValueError):
            logger.debug("débit illisible pour %s : %r", item_class, value)
    return rates


def _thumbnail(raw: Any, path: Path) -> bytes | None:
    """A picture that will not decode costs its thumbnail, never the module."""
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return base64.b64decode(raw, validate=True)
    except (ValueError, TypeError):
        logger.debug("vignette illisible dans %s", path.name)
        return None
