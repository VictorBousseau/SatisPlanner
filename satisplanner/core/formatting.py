"""Formatting of the numbers the planner shows, in the language it is set to.

One set of rules, used by the diagnostics and by the canvas alike: a rate written
"66,667 %" in a warning must read the same on a node. Keeping them here rather than
in each layer is what stops the two from drifting apart.

Qt-free, like everything in ``core`` -- and that is why ``QLocale`` is not what
decides the decimal separator. What decides it is :func:`satisplanner.core.i18n
.language`, the same switch the sentences follow, which has a second virtue beyond
the layering: a French user on an English Windows gets a French interface **and**
French numbers, where ``QLocale.system()`` would have given them one of each.

Only two rules actually differ, and they are the two a reader notices:

* the **decimal separator** -- ``24,549`` against ``24.549``. A comma read as a
  thousands separator turns twenty-four into twenty-four thousand, which is the
  whole reason this exists;
* the **space before the percent sign** -- ``81,8 %`` against ``81.8%``. French
  typography wants it, English typography does not.

Thousands are grouped in neither: the figures here are rates per minute and rarely
reach four digits, and a separator that is a space in one language and a comma in
the other is a way of being misread in both.
"""

import unicodedata
from typing import Final

from satisplanner.core.i18n import Language, language
from satisplanner.core.models import Item

# Rates are shown to the thousandth: 66,667 a minute is the honest way to write two
# thirds of a hundred, and anything finer is float dust the game cannot express.
DECIMALS: Final = 3

# Percentages get one. A clock, a saturation or an operating rate is read at a
# glance and compared to a round number -- "81,8 %" answers "is it near enough?"
# exactly as well as "81,831 %" and asks for a third of the attention. The rate it
# is derived from keeps all three decimals, because that one is added up.
PERCENT_DECIMALS: Final = 1

_MINUTES_PER_HOUR: Final = 60
_SECONDS_PER_MINUTE: Final = 60


def decimal_separator() -> str:
    """What separates the units from the tenths, in the language in force."""
    return "," if language() is Language.FRENCH else "."


def number(value: float, decimals: int = DECIMALS) -> str:
    """Compact number, with no trailing zeros and the separator of the language."""
    rounded = round(value, decimals)
    if rounded == int(rounded):
        return str(int(rounded))
    return f"{rounded}".replace(".", decimal_separator())


def unit(item: Item | None) -> str:
    """The unit a quantity of this item is counted in."""
    return "m³/min" if item is not None and item.form.is_fluid else "/min"


def rate(value: float, item: Item | None) -> str:
    """Rates read in the game's own units: m³/min for fluids, plain /min for solids."""
    return f"{number(abs(value))} {unit(item)}".replace(" /min", "/min")


def percent(ratio: float) -> str:
    """``81,8 %`` in French, ``81.8%`` in English: the space is French typography."""
    space = " " if language() is Language.FRENCH else ""
    return f"{number(ratio * 100, PERCENT_DECIMALS)}{space}%"


def differ(actual: float, nominal: float | None) -> bool:
    """Whether the two figures are worth writing separately.

    Compared at the precision they are *displayed* at, not at the precision they are
    held at: two rates that both print as "30" must not be shown as "30 / 30" because
    they differ in the twelfth decimal.
    """
    if nominal is None:
        return False
    return abs(nominal - actual) > 10.0**-DECIMALS / 2


def pair(actual: float, nominal: float | None, item: Item | None) -> str:
    """``24,549 / 30/min`` when the two differ, ``30/min`` when they agree.

    What a node achieves and what it was built to achieve, side by side, so the
    shortfall is read rather than worked out. The second figure is dropped when it
    would only repeat the first: a well-built factory is nominal nearly everywhere,
    and "30 / 30" on every port is noise that hides the one port that is not.

    ``None`` means the node has no nameplate for this port at all -- a buffer or an
    exit absorbs whatever arrives -- which is different from the two being equal and
    is written the same way, with one figure, because there is only one to write.
    """
    if not differ(actual, nominal):
        return rate(actual, item)
    assert nominal is not None
    return f"{number(abs(actual))} / {rate(nominal, item)}"


def pair_number(actual: float, nominal: float | None) -> str:
    """The same two figures with no unit, for a column whose header already says it."""
    if not differ(actual, nominal):
        return number(abs(actual))
    assert nominal is not None
    return f"{number(abs(actual))} / {number(abs(nominal))}"


def duration(minutes: float) -> str:
    """Seconds under a minute, hours over an hour, minutes in between.

    The three symbols are the same in both languages -- ``s``, ``min`` and ``h`` are
    SI, not French -- so only the number changes.
    """
    if minutes < 1:
        return f"{number(minutes * _SECONDS_PER_MINUTE)} s"
    if minutes < _MINUTES_PER_HOUR:
        return f"{number(minutes)} min"
    return f"{number(minutes / _MINUTES_PER_HOUR)} h"


def of(name: str) -> str:
    """``de X``, ``d'X`` or ``of X``, whichever the language in force wants.

    Six lines rather than a sentence that reads "de Ordinateur". The French rule is
    the ordinary one -- elision before a vowel and before a mute h -- and the list of
    aspirated h words the game uses is empty, so there is nothing to except. English
    elides nothing and the function is then a single word, which is why the sentence
    that uses it can stay one sentence in both languages instead of two.
    """
    if language() is Language.ENGLISH:
        return f"of {name}"
    stripped = "".join(
        char
        for char in unicodedata.normalize("NFKD", name[:1].casefold())
        if not unicodedata.combining(char)
    )
    return f"d'{name}" if stripped in "aeiouyh" else f"de {name}"
