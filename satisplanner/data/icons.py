"""Icon file index.

Resolution is by **file name**, never by directory layout: the user exports icons
with FModel and keeps whatever tree it produces, so every root is indexed
recursively once and looked up from a flat mapping afterwards.

This module stays free of Qt on purpose -- it answers "where is this file", not
"give me a pixmap". The pixmap side, including the generative fallback, belongs to
the UI layer.
"""

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final

from satisplanner import paths
from satisplanner.core.i18n import _

logger = logging.getLogger(__name__)

ICON_SUFFIXES: Final[frozenset[str]] = frozenset({".png", ".webp", ".jpg", ".jpeg"})


def embedded_icon_directory() -> Path:
    """Icons shipped with the application, if any were built into it at all."""
    return paths.resource_directory() / "icons"


def default_icon_roots(user_directory: Path | None = None) -> list[Path]:
    """Icon directories in resolution order: embedded first, then the user's own.

    ``user_directory`` is a folder the user pointed the preferences at, or the default
    one under ``%LOCALAPPDATA%`` when they never chose. Directories that do not exist
    are dropped, so an installation with neither costs nothing and falls back to the
    generated icons.
    """
    chosen = user_directory if user_directory is not None else paths.default_user_icon_directory()
    return [root for root in (embedded_icon_directory(), chosen) if root.is_dir()]


class IconIndex:
    """Flat, case-insensitive index of icon files found under a set of roots."""

    def __init__(self, roots: Sequence[Path] = ()) -> None:
        self._by_name: dict[str, Path] = {}
        self.roots: tuple[Path, ...] = tuple(roots)
        for root in self.roots:
            self._index(root)

    def _index(self, root: Path) -> None:
        if not root.is_dir():
            logger.debug("dossier d'icônes absent, ignore : %s", root)
            return
        found = 0
        for path in root.rglob("*"):
            if path.suffix.lower() not in ICON_SUFFIXES or not path.is_file():
                continue
            # First root wins, so embedded icons take precedence over user ones.
            self._by_name.setdefault(path.name.lower(), path)
            found += 1
        logger.debug("%s : %d fichier(s) d'icône indexé(s)", root, found)

    def resolve(self, filename: str | None) -> Path | None:
        """Path of the icon with this file name, or ``None`` if it was not exported."""
        if not filename:
            return None
        return self._by_name.get(filename.lower())

    def missing(self, filenames: Iterable[str | None]) -> list[str]:
        """File names that could not be resolved, deduplicated and sorted."""
        return sorted({name for name in filenames if name and self.resolve(name) is None})

    def __len__(self) -> int:
        return len(self._by_name)


class IconSupply(StrEnum):
    """Why the index holds what it holds. Three answers, and two of them look alike."""

    # Files were found. Nothing to say beyond how many.
    PRESENT = "present"
    # None found, and the application is packaged: this is the ``-NoAssets`` build,
    # which ships without the game's icons on purpose. Nominal.
    PUBLISHABLE_BUILD = "publishable_build"
    # None found, running from sources: the directory is not versioned, so a clone
    # simply does not carry it. Nominal too -- but for a completely different
    # reason, and one the user can act on.
    NOT_EXTRACTED = "not_extracted"


@dataclass(frozen=True)
class IconStatus:
    """What the icon situation is, in a form both a log line and a dialog can use.

    It exists because "no icons" was one message for two situations, and the two
    call for opposite reactions. A packaged ``-NoAssets`` build has nothing to fix.
    A source checkout has an extraction to run, and nothing on screen said so --
    which is exactly how somebody installs the project on a second machine and
    concludes the icons are broken.
    """

    indexed: int
    supply: IconSupply
    roots: tuple[Path, ...]

    def sentence(self) -> str:
        """One line of French, suitable for a log, a dialog or the self-check."""
        match self.supply:
            case IconSupply.PRESENT:
                return _("{count} fichier(s) d'icône indexé(s)").format(
                    count=self.indexed
                )
            case IconSupply.PUBLISHABLE_BUILD:
                return _(
                    "aucune icône du jeu embarquée (variante publiable) : "
                    "tout est dessiné, ce qui est le fonctionnement nominal"
                )
            case IconSupply.NOT_EXTRACTED:
                return _(
                    "aucune icône : le dossier n'est pas versionné, donc un clone ne "
                    "l'emporte pas. Tout est dessiné, ce qui fonctionne ; pour avoir "
                    "les icônes du jeu, voir la procédure FModel du README"
                )


def status(index: IconIndex) -> IconStatus:
    """Read the situation off the index and off how the application was started."""
    if len(index):
        supply = IconSupply.PRESENT
    elif paths.is_frozen():
        supply = IconSupply.PUBLISHABLE_BUILD
    else:
        supply = IconSupply.NOT_EXTRACTED
    return IconStatus(indexed=len(index), supply=supply, roots=index.roots)
