"""Every unit conversion between the game files and the application, in one place.

The game does not store the rates the player sees. It stores cycle times, per-cycle
amounts and internal speed fields, from which the rates must be derived. Each
function below documents three things: the **source field** it reads, the
**formula** it applies, and the **control value** it must reproduce. The control
values are asserted in ``tests/test_conversions.py``; if a formula ever drifts,
that test fails instead of the error spreading silently through the database.

The single most dangerous conversion is the litre one: the game stores fluid and
gas amounts in litres, a thousand times the number shown in game. Every amount
that belongs to a liquid or a gas goes through :func:`normalise_amount`.
"""

from typing import Final

from satisplanner.core.models import ItemForm

SECONDS_PER_MINUTE: Final = 60.0

# Fluid and gas amounts are stored in litres; the game displays m3.
LITRES_PER_CUBIC_METRE: Final = 1000.0

# `mSpeed` counts the belt's throughput in internal units per minute. One item
# occupies two of those units, so the player-visible rate is half the field.
# Verified against all six tiers (see CONTROL values below).
BELT_SPEED_DIVISOR: Final = 2.0

# `mFlowLimit` is a volume per second, in m3.
PIPE_FLOW_SECONDS: Final = SECONDS_PER_MINUTE

# Raw values of `mForm` as they appear in the game files. `RF_INVALID` marks
# classes that are not items at all (buildings, mostly).
FORM_BY_RAW: Final[dict[str, ItemForm]] = {
    "RF_SOLID": ItemForm.SOLID,
    "RF_LIQUID": ItemForm.LIQUID,
    "RF_GAS": ItemForm.GAS,
}


def normalise_amount(raw_amount: float, form: ItemForm) -> float:
    """Convert a raw per-cycle amount into the unit the application reasons in.

    Source: ``Amount`` inside ``mIngredients`` / ``mProduct``, or ``mItemsPerCycle``.
    Formula: solids unchanged, fluids and gases divided by 1000.
    Control: the Plastic recipe stores ``Amount=3000`` of Crude Oil, i.e. 3 m3
    per cycle.
    """
    if form.is_fluid:
        return raw_amount / LITRES_PER_CUBIC_METRE
    return raw_amount


def rate_per_minute(raw_amount: float, cycle_seconds: float, form: ItemForm) -> float:
    """Steady-state rate of one recipe slot, in items/min or m3/min.

    Source: ``Amount`` and ``mManufactoringDuration``.
    Formula: ``normalised_amount x 60 / cycle_seconds``.
    Control: Plastic, 3000 L of oil over 6 s -> 30 m3/min; 2 plastic over 6 s
    -> 20 items/min; 1000 L of heavy oil residue over 6 s -> 10 m3/min.
    """
    if cycle_seconds <= 0:
        msg = f"duree de cycle invalide : {cycle_seconds}"
        raise ValueError(msg)
    return normalise_amount(raw_amount, form) * SECONDS_PER_MINUTE / cycle_seconds


def belt_items_per_minute(m_speed: float) -> float:
    """Throughput of a conveyor belt, in items/min.

    Source: ``mSpeed`` on ``FGBuildableConveyorBelt``.
    Formula: ``mSpeed / 2``.
    Control: 120 / 240 / 540 / 960 / 1560 / 2400 -> 60 / 120 / 270 / 480 / 780
    / 1200 items/min for Mk.1 to Mk.6.
    """
    return m_speed / BELT_SPEED_DIVISOR


def pipe_cubic_metres_per_minute(m_flow_limit: float) -> float:
    """Throughput of a pipeline, in m3/min.

    Source: ``mFlowLimit`` on ``FGBuildablePipeline``, a volume per second in m3.
    Formula: ``mFlowLimit x 60``.
    Control: 5 -> 300 m3/min (Mk.1), 10 -> 600 m3/min (Mk.2).
    """
    return m_flow_limit * PIPE_FLOW_SECONDS


def extractor_rate_per_minute(
    items_per_cycle: float, cycle_seconds: float, form: ItemForm
) -> float:
    """Base extraction rate at 100 % clock speed on a normal purity node.

    Source: ``mItemsPerCycle`` and ``mExtractCycleTime``.
    Formula: same as :func:`rate_per_minute`.
    Control: Miner Mk.1/2/3 (1 item per 1 / 0.5 / 0.25 s) -> 60 / 120 / 240
    items/min; Oil and Water Extractor (2000 L per 1 s) -> 120 m3/min.

    Purity multipliers are a domain concern and live in ``core.constants``.
    """
    return rate_per_minute(items_per_cycle, cycle_seconds, form)


def stack_size(m_cached_stack_size: float, form: ItemForm) -> float:
    """Stack size, in items for solids and in m3 for fluids.

    Source: ``mCachedStackSize`` (the game also exposes the ``SS_*`` enum in
    ``mStackSize``, but only the cached field carries the number).
    Formula: solids unchanged, fluids divided by 1000.
    Control: Plastic ``SS_BIG`` -> 200 items; Heavy Oil Residue ``SS_FLUID``
    -> 50000 L, i.e. 50 m3.
    """
    return normalise_amount(m_cached_stack_size, form)
