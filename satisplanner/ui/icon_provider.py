"""Icons for items and buildings, with three backends tried in order.

1. the directory shipped inside the package, ``satisplanner/resources/icons``;
2. an optional user directory under the local application data, for someone who
   exported their own set with FModel;
3. a **generative fallback**: a rounded square whose colour is derived from a stable
   hash of the class name, with the initials of the French label in the middle.

The third backend is the normal way this application runs, not a degraded mode. The
game's own icons belong to Coffee Stain Studios and are never redistributed, so the
executable ships without any of the building icons and without the handful of item
icons that the documented export does not cover. Nothing here ever asks the user to
rename a file: the index is by file name, recursively, whatever tree they kept.
"""

import hashlib
import logging
from pathlib import Path
from typing import Final

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication

from satisplanner.core.models import Building, Item
from satisplanner.data import icons
from satisplanner.data.icons import IconIndex, default_icon_roots

logger = logging.getLogger(__name__)

# Rendering size of a cached icon, in device-independent pixels. Nodes draw them
# smaller; asking for one size and scaling down keeps the cache to one entry each.
ICON_SIZE: Final = 64

# Generated icons: saturation and lightness are fixed so the whole palette sits at
# the same visual weight, and only the hue tells two classes apart.
_FALLBACK_SATURATION: Final = 120
_FALLBACK_LIGHTNESS: Final = 105
_CORNER_RADIUS: Final = 0.22  # fraction of the side
_LIGHT_TEXT_THRESHOLD: Final = 127

# Words too short to carry meaning in a monogram ("de", "en", "a").
_MINOR_WORD_LENGTH: Final = 2
_MAX_INITIALS: Final = 2


def stable_hue(seed: str) -> int:
    """A hue in [0, 359] that never changes between runs.

    Python's own ``hash`` is salted per process, so an icon would change colour every
    time the application started -- exactly the kind of instability that makes a
    canvas impossible to learn.
    """
    digest = hashlib.blake2b(seed.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") % 360


def initials(label: str) -> str:
    """Up to two initials from a French label: "Minerai de fer" gives "MF"."""
    words = [word for word in label.split() if len(word) > _MINOR_WORD_LENGTH]
    if not words:
        return (label[:1] or "?").upper()
    return "".join(word[0] for word in words[:_MAX_INITIALS]).upper()


class IconProvider:
    """Resolves a class to a ``QIcon``, generating one when no file was exported."""

    def __init__(self, index: IconIndex | None = None, size: int = ICON_SIZE) -> None:
        self.index = index if index is not None else IconIndex()
        self.size = size
        self._cache: dict[str, QIcon] = {}
        self._generated: set[str] = set()
        # Read off the index, and settled once: what is on disk does not change
        # while the application runs, and a provider built for a test carries the
        # honest answer for the index it was given.
        self.status = icons.status(self.index)

    @classmethod
    def from_default_roots(
        cls, size: int = ICON_SIZE, user_directory: Path | None = None
    ) -> "IconProvider":
        """The provider the application runs with: embedded icons, then the user's.

        The situation is written to the log at ``INFO`` rather than ``DEBUG``, and
        as a sentence rather than a count. "0 fichier indexé" in a debug line is
        the same thing the application said when everything was fine, and telling
        the two apart is the whole point of :func:`icons.status`.
        """
        provider = cls(IconIndex(default_icon_roots(user_directory)), size)
        provider.status = icons.status(provider.index)
        logger.info("icônes : %s", provider.status.sentence())
        if provider.status.supply is icons.IconSupply.NOT_EXTRACTED:
            logger.warning(
                "aucun dossier d'icônes : cherché dans %s",
                ", ".join(str(root) for root in default_icon_roots(user_directory))
                or "aucun dossier existant",
            )
        return provider

    # ------------------------------------------------------------------ public

    def for_item(self, item: Item) -> QIcon:
        return self.icon_for(item.class_name, item.icon_file, item.name)

    def for_building(self, building: Building) -> QIcon:
        return self.icon_for(building.class_name, building.icon_file, building.name)

    def icon_for(self, class_name: str, icon_file: str | None, label: str) -> QIcon:
        """The icon for one class, resolved once and cached by class name."""
        cached = self._cache.get(class_name)
        if cached is not None:
            return cached
        icon = self._resolve(class_name, icon_file, label)
        self._cache[class_name] = icon
        return icon

    def was_generated(self, class_name: str) -> bool:
        """True when this class is drawn by the fallback rather than from a file."""
        return class_name in self._generated

    @property
    def generated_count(self) -> int:
        """How many classes the fallback has had to draw so far."""
        return len(self._generated)

    # ----------------------------------------------------------------- private

    def _resolve(self, class_name: str, icon_file: str | None, label: str) -> QIcon:
        path = self.index.resolve(icon_file)
        if path is not None:
            pixmap = QPixmap(str(path))
            if not pixmap.isNull():
                return QIcon(pixmap)
            logger.debug("icône illisible, repli généré : %s", path)
        self._generated.add(class_name)
        return QIcon(self.generate(class_name, label))

    def generate(self, seed: str, label: str) -> QPixmap:
        """Draw the fallback: a coloured rounded square with the label's initials."""
        ratio = _device_pixel_ratio()
        pixmap = QPixmap(QSize(round(self.size * ratio), round(self.size * ratio)))
        pixmap.setDevicePixelRatio(ratio)
        pixmap.fill(Qt.GlobalColor.transparent)

        side = float(self.size)
        colour = QColor.fromHsl(stable_hue(seed), _FALLBACK_SATURATION, _FALLBACK_LIGHTNESS)
        text = initials(label)
        painter = QPainter(pixmap)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(colour))
            radius = side * _CORNER_RADIUS
            painter.drawRoundedRect(QRectF(0, 0, side, side), radius, radius)

            font = QFont(painter.font())
            font.setBold(True)
            font.setPixelSize(round(side * (0.5 if len(text) < _MAX_INITIALS else 0.38)))
            painter.setFont(font)
            dark = colour.lightness() > _LIGHT_TEXT_THRESHOLD
            painter.setPen(QColor(Qt.GlobalColor.black if dark else Qt.GlobalColor.white))
            painter.drawText(QRectF(0, 0, side, side), Qt.AlignmentFlag.AlignCenter, text)
        finally:
            painter.end()
        return pixmap


def _device_pixel_ratio() -> float:
    """Screen scaling, so a generated icon is not blurry on a high-DPI display."""
    app = QApplication.instance()
    if isinstance(app, QApplication):
        screen = app.primaryScreen()
        if screen is not None:
            return float(screen.devicePixelRatio())
    return 1.0
