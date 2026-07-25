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
