"""Game values that are absent from ``Docs.json`` and must live in code.

Policy: every value here is either confirmed against the reference table agreed
with the user, or flagged ``# TODO: a verifier``. Nothing is guessed silently.
Values that *can* be derived from the game files belong in the data layer's
conversion module, not here.
"""

from typing import Final

# Purity multipliers applied to an extractor's base rate. Confirmed values.
IMPURE_MULTIPLIER: Final = 0.5
NORMAL_MULTIPLIER: Final = 1.0
PURE_MULTIPLIER: Final = 2.0

# Solid storage capacity = slot count x stack size. Stack sizes come from
# Docs.json; the slot counts do not.
# TODO: a verifier -- slot counts and the exact building class names, once the
# `buildings` table exists (phase 1).
STORAGE_CONTAINER_SLOTS: Final = 24
INDUSTRIAL_STORAGE_CONTAINER_SLOTS: Final = 48

# Fixed-point solver for cyclic components (see specification 8.2).
CONVERGENCE_TOLERANCE: Final = 1e-9
MAX_ITERATIONS: Final = 1000

# --------------------------------------------------------------------- clock

# Lowest clock speed a building accepts, 1 %. This one *is* in the game files --
# `mMinPotential`, and it reads 0.01 on all sixty-two buildables -- but the graph
# validates its field without a catalogue in hand, so the bound is repeated here.
# `tests/test_conversions.py` checks the two against each other.
MIN_CLOCK_SPEED: Final = 0.01

# How many power shards a building takes. **Not in Docs.json**: every buildable
# declares `mPotentialShardSlots = 0` with `mOverridePotentialShardSlots = False`,
# which means "use the engine's default", and the default itself is not exported.
# Three is the value the game uses.
POWER_SHARD_SLOTS: Final = 3

# Highest clock speed, 250 %. Derived: 100 % plus three shards at the
# `mExtraPotential` the game declares for the power shard. Stated as a number
# because the graph's field bound cannot reach the catalogue, and checked against
# the data by `tests/test_conversions.py` so it cannot drift from it.
MAX_CLOCK_SPEED: Final = 2.5

# ------------------------------------------------------------------- families
#
# Which items belong together, for the reader who wants to colour a factory by the
# nature of what flows through it.
#
# Two families cost nothing: **Docs.json** declares `is_raw_resource` and the form
# of every item, so "raw" and "fluid" are read from the data and never listed here.
#
# The two below are **not in Docs.json in any form**. There is no field saying that
# an iron ingot is a smelted metal, and no field marking the space elevator parts as
# the things the game asks you to ship. Both are hand-written, both are checked
# against the shipped catalogue by `tests/test_families.py` so that a class renamed
# by a game update fails a test instead of silently losing its colour, and neither
# changes a single rate: a family is a way of looking, not a property of the item.

# The five smelted and cast metals. Concrete is deliberately absent -- it comes out
# of a constructor, not a smelter, and a reader looking for "the ingots" is looking
# for the metals.
INGOT_CLASSES: Final[tuple[str, ...]] = (
    "Desc_AluminumIngot_C",
    "Desc_CopperIngot_C",
    "Desc_GoldIngot_C",
    "Desc_IronIngot_C",
    "Desc_SteelIngot_C",
)

# The twelve space elevator parts, which is what "finished product" means in this
# game: everything else is an intermediate on the way to one of them.
SPACE_ELEVATOR_CLASSES: Final[tuple[str, ...]] = tuple(
    f"Desc_SpaceElevatorPart_{index}_C" for index in range(1, 13)
)
