"""French formatting of the numbers the planner shows.

One set of rules, used by the diagnostics and by the canvas alike: a rate written
"66,667 %" in a warning must read the same on a node. Keeping them here rather than
in each layer is what stops the two from drifting apart.

Qt-free, like everything in ``core``.
"""

from typing import Final

from satisplanner.core.models import Item

# Rates are shown to the thousandth: 66,667 % is the honest way to write two thirds,
# and anything finer is float dust the game cannot express anyway.
DECIMALS: Final = 3

_MINUTES_PER_HOUR: Final = 60
_SECONDS_PER_MINUTE: Final = 60


def number(value: float) -> str:
    """Compact French number: comma as decimal separator, no trailing zeros."""
    rounded = round(value, DECIMALS)
    if rounded == int(rounded):
        return str(int(rounded))
    return f"{rounded}".replace(".", ",")


def unit(item: Item | None) -> str:
    """The unit a quantity of this item is counted in."""
    return "m³/min" if item is not None and item.form.is_fluid else "/min"


def rate(value: float, item: Item | None) -> str:
    """Rates read in the game's own units: m³/min for fluids, plain /min for solids."""
    return f"{number(abs(value))} {unit(item)}".replace(" /min", "/min")


def percent(ratio: float) -> str:
    return f"{number(ratio * 100)} %"


def duration(minutes: float) -> str:
    """Seconds under a minute, hours over an hour, minutes in between."""
    if minutes < 1:
        return f"{number(minutes * _SECONDS_PER_MINUTE)} s"
    if minutes < _MINUTES_PER_HOUR:
        return f"{number(minutes)} min"
    return f"{number(minutes / _MINUTES_PER_HOUR)} h"
