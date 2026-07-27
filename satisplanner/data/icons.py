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
from pathlib import Path
from typing import Final

from satisplanner import paths

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
