"""Sanity checks on the package skeleton itself."""

from importlib import metadata

import satisplanner
from satisplanner.core import constants


def test_declared_version_matches_the_installed_distribution() -> None:
    assert satisplanner.__version__ == metadata.version("satisplanner")


def test_purity_multipliers() -> None:
    # Reference values agreed with the user: impure 0.5, normal 1, pure 2.
    assert constants.IMPURE_MULTIPLIER == 0.5
    assert constants.NORMAL_MULTIPLIER == 1.0
    assert constants.PURE_MULTIPLIER == 2.0


def test_solver_limits_are_sane() -> None:
    assert 0 < constants.CONVERGENCE_TOLERANCE < 1e-6
    assert constants.MAX_ITERATIONS >= 100
