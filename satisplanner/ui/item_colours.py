"""The colour an item is drawn in, and the rules that keep it readable.

A palette has two layers. The **families** carry the defaults -- every fluid blue,
every ore dark grey -- and a user who never opens the preferences still gets a canvas
that can be read by nature of material. On top of that sit **per-item overrides**,
because "all fluids blue" stops helping the day a factory runs four of them side by
side. Either layer can be put back to its default, one entry at a time or all at once.

Two rules are not negotiable and are enforced here rather than left to whoever picks
a colour:

**The state stays readable.** A node's colour says what flows through it; its border
says whether it is running, starved or stopped. The border therefore has to stand out
against every background the palette can produce, which is why the defaults are dark
and desaturated: they are backgrounds, not paint.

**The text stays readable.** A label is written in light or dark according to the
luminance of what it sits on, by the same relative-luminance formula browsers use for
contrast ratios. A user free to choose any colour is a user free to choose one that
makes their own factory illegible, and answering that with "do not do that" would be
a poor answer.

Colours are plain ``#rrggbb`` strings, not ``QColor``: they are written to the
settings, exported to a file a user can send, and compared in tests, and a string does
all three without a Qt object anywhere near it.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Final

from satisplanner.core.families import ItemFamily, family_of
from satisplanner.core.models import Item
from satisplanner.ui import theme

logger = logging.getLogger(__name__)

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")

# Chosen as backgrounds under pale text, on a canvas that is already dark. Each one
# is checked by the tests for contrast against the text and against the three state
# colours of the liseré; changing one without re-reading those tests is how a palette
# ends up pretty and unreadable.
DEFAULT_FAMILY_COLOURS: Final[dict[ItemFamily, str]] = {
    # Ore is grey, and dark: it is the most common thing on a canvas and the one that
    # should draw the least attention.
    ItemFamily.RAW: "#2F3438",
    # The pipe colour, dimmed to sit behind text rather than on top of it.
    ItemFamily.FLUID: "#1B2F3E",
    # Warm, for something that came out of a furnace.
    ItemFamily.INGOT: "#382B1F",
    # The one family worth spotting from across the canvas.
    ItemFamily.SPACE_ELEVATOR: "#2F2440",
    # The default surface: no tint at all, so a factory is not a rainbow.
    ItemFamily.OTHER: theme.SURFACE_RAISED,
}

DARK_TEXT: Final = "#14171A"
LIGHT_TEXT: Final = theme.TEXT

# The worst contrast these two inks can be forced to, over every possible background:
# a mid-grey around #737373, where neither is far from it. Recorded here because it is
# the honest floor of the whole scheme, and because it is what the tests hold it to.
#
# Pure black and pure white would raise it to 4.61, and are not used: the application
# writes in #E6E6E6 everywhere else, and a node whose text jumped to pure white for
# one colour would look like a rendering fault. 3.8 clears the WCAG bar for the size
# this text is drawn at, and every colour actually shipped is far above it.
GUARANTEED_TEXT_CONTRAST: Final = 3.79


def is_colour(value: str) -> bool:
    """``#rrggbb``, and nothing else. Short forms and names are refused."""
    return bool(_HEX.match(value))


def _channels(colour: str) -> tuple[float, float, float]:
    return (
        int(colour[1:3], 16) / 255.0,
        int(colour[3:5], 16) / 255.0,
        int(colour[5:7], 16) / 255.0,
    )


def relative_luminance(colour: str) -> float:
    """Perceived brightness, 0 for black and 1 for white (WCAG 2.1 definition)."""

    def linear(channel: float) -> float:
        return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4

    red, green, blue = (linear(channel) for channel in _channels(colour))
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast_ratio(first: str, second: str) -> float:
    """WCAG contrast between two colours, from 1 (identical) to 21 (black on white)."""
    bright, dim = sorted((relative_luminance(first), relative_luminance(second)), reverse=True)
    return (bright + 0.05) / (dim + 0.05)


def text_colour_on(background: str) -> str:
    """Light or dark text, whichever is legible on this background.

    Decided rather than configured. A user picking a background has said what they
    want the node to look like, not that they want to choose the text colour too, and
    the only wrong answer here is the unreadable one.

    The choice is made by **measuring** both, not by comparing the background against
    a luminance threshold. The two give the same answer nearly everywhere and differ
    exactly where it matters -- around the mid-greys, where the threshold has to be
    guessed and the measurement does not.
    """
    if contrast_ratio(DARK_TEXT, background) >= contrast_ratio(LIGHT_TEXT, background):
        return DARK_TEXT
    return LIGHT_TEXT


@dataclass
class ItemPalette:
    """Family defaults, per-item overrides, and the rules for reading them back."""

    families: dict[ItemFamily, str] = field(default_factory=dict)
    items: dict[str, str] = field(default_factory=dict)

    # ------------------------------------------------------------------ reading

    def family_colour(self, family: ItemFamily) -> str:
        return self.families.get(family, DEFAULT_FAMILY_COLOURS[family])

    def colour_for(self, item: Item) -> str:
        """The colour of one item: its own override, or its family's."""
        override = self.items.get(item.class_name)
        if override is not None:
            return override
        return self.family_colour(family_of(item))

    def text_on(self, item: Item) -> str:
        return text_colour_on(self.colour_for(item))

    def is_default(self) -> bool:
        return not self.families and not self.items

    # ------------------------------------------------------------------ writing

    def set_family(self, family: ItemFamily, colour: str) -> None:
        self.families[family] = colour

    def set_item(self, class_name: str, colour: str) -> None:
        self.items[class_name] = colour

    def reset_family(self, family: ItemFamily) -> None:
        self.families.pop(family, None)

    def reset_item(self, class_name: str) -> None:
        self.items.pop(class_name, None)

    def reset(self) -> None:
        """Back to the shipped palette, wholesale."""
        self.families.clear()
        self.items.clear()

    # ------------------------------------------------------------ serialisation

    def to_json(self) -> str:
        """Only what differs from the defaults, so a new default reaches old files."""
        return json.dumps(
            {
                "version": PALETTE_VERSION,
                "families": {
                    family.value: colour for family, colour in sorted(self.families.items())
                },
                "items": dict(sorted(self.items.items())),
            },
            indent=1,
            ensure_ascii=True,
        )

    @classmethod
    def from_json(cls, text: str) -> "ItemPalette":
        """Read a palette back, ignoring anything that is not a colour.

        Lenient on purpose. A palette is decoration: a file with one bad entry should
        cost that entry, not the whole file, and certainly not a stack trace on top of
        somebody's factory.
        """
        try:
            payload = json.loads(text)
        except (TypeError, ValueError):
            logger.debug("palette illisible, retour aux couleurs par defaut")
            return cls()
        if not isinstance(payload, dict):
            return cls()

        families: dict[ItemFamily, str] = {}
        for name, colour in _mapping(payload.get("families")).items():
            try:
                family = ItemFamily(name)
            except ValueError:
                logger.debug("famille inconnue dans la palette : %s", name)
                continue
            if is_colour(colour):
                families[family] = colour.lower()

        items = {
            class_name: colour.lower()
            for class_name, colour in _mapping(payload.get("items")).items()
            if is_colour(colour)
        }
        return cls(families=families, items=items)


def _mapping(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(key): str(colour) for key, colour in value.items()}


# Bumped if the stored shape ever changes. Read but not enforced: an unknown version
# still has its colours taken, because a colour is a colour.
PALETTE_VERSION: Final = 1

PALETTE_FILE_FILTER: Final = "Palette SatisPlanner (*.json);;Tous les fichiers (*)"
PALETTE_FILE_SUFFIX: Final = ".json"
