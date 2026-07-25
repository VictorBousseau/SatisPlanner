"""Icon file index.

Resolution is by **file name**, never by directory layout: the user exports icons
with FModel and keeps whatever tree it produces, so every root is indexed
recursively once and looked up from a flat mapping afterwards.

This module stays free of Qt on purpose -- it answers "where is this file", not
"give me a pixmap". The pixmap side, including the generative fallback, belongs to
the UI layer and lands in phase 3.
"""

import logging
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Final

logger = logging.getLogger(__name__)

ICON_SUFFIXES: Final[frozenset[str]] = frozenset({".png", ".webp", ".jpg", ".jpeg"})

# Icons shipped with the application.
EMBEDDED_ICON_DIRECTORY: Final = Path(__file__).resolve().parent.parent / "resources" / "icons"

# Subdirectory of the application's own data directory where a user may drop their
# own export. Made configurable from the preferences in phase 5.
USER_ICON_SUBPATH: Final = Path("icons")


def default_icon_roots(app_data_directory: Path | None = None) -> list[Path]:
    """Icon directories in resolution order: embedded first, then user-provided.

    ``app_data_directory`` is the application's own data directory, which only the UI
    layer can locate; this module stays free of Qt. Directories that do not exist are
    dropped, so a user who never exported anything costs nothing.
    """
    roots = [EMBEDDED_ICON_DIRECTORY]
    if app_data_directory is not None:
        roots.append(app_data_directory / USER_ICON_SUBPATH)
    return [root for root in roots if root.is_dir()]


class IconIndex:
    """Flat, case-insensitive index of icon files found under a set of roots."""

    def __init__(self, roots: Sequence[Path] = ()) -> None:
        self._by_name: dict[str, Path] = {}
        self.roots: tuple[Path, ...] = tuple(roots)
        for root in self.roots:
            self._index(root)

    def _index(self, root: Path) -> None:
        if not root.is_dir():
            logger.debug("dossier d'icones absent, ignore : %s", root)
            return
        found = 0
        for path in root.rglob("*"):
            if path.suffix.lower() not in ICON_SUFFIXES or not path.is_file():
                continue
            # First root wins, so embedded icons take precedence over user ones.
            self._by_name.setdefault(path.name.lower(), path)
            found += 1
        logger.debug("%s : %d fichier(s) d'icone indexe(s)", root, found)

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
