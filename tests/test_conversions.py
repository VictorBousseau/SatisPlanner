"""The control values of the specification, asserted against the real game data.

If a conversion formula ever drifts, these tests fail here rather than letting a
wrong number spread through the database and into every calculation.

All values are at 100 % clock speed and normal purity.
"""

import pytest

from satisplanner.core.models import ItemForm
from satisplanner.data import conversions

# --------------------------------------------------------------------------- #
# Units
# --------------------------------------------------------------------------- #


def test_solids_are_never_rescaled() -> None:
    assert conversions.normalise_amount(30, ItemForm.SOLID) == 30


@pytest.mark.parametrize("form", [ItemForm.LIQUID, ItemForm.GAS])
def test_fluids_and_gases_go_from_litres_to_cubic_metres(form: ItemForm) -> None:
    # The single most common mistake of this parser: 3000 in the file is 3 m3.
    assert conversions.normalise_amount(3000, form) == 3
    assert form.is_fluid


def test_stack_sizes() -> None:
    # Plastic, SS_BIG -> 200 items. Heavy Oil Residue, SS_FLUID -> 50000 L = 50 m3.
    assert conversions.stack_size(200, ItemForm.SOLID) == 200
    assert conversions.stack_size(50000, ItemForm.LIQUID) == 50


def test_a_zero_cycle_time_is_refused_rather_than_dividing_by_zero() -> None:
    with pytest.raises(ValueError, match="duree de cycle"):
        conversions.rate_per_minute(1, 0, ItemForm.SOLID)


# --------------------------------------------------------------------------- #
# Transport
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("m_speed", "expected"),
    [(120, 60), (240, 120), (540, 270), (960, 480), (1560, 780), (2400, 1200)],
    ids=[f"Mk{tier}" for tier in range(1, 7)],
)
def test_belt_throughput(m_speed: float, expected: float) -> None:
    assert conversions.belt_items_per_minute(m_speed) == expected


@pytest.mark.parametrize(("m_flow_limit", "expected"), [(5, 300), (10, 600)], ids=["Mk1", "Mk2"])
def test_pipe_throughput(m_flow_limit: float, expected: float) -> None:
    assert conversions.pipe_cubic_metres_per_minute(m_flow_limit) == expected


# --------------------------------------------------------------------------- #
# Extraction
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("cycle_seconds", "expected"),
    [(1.0, 60), (0.5, 120), (0.25, 240)],
    ids=["MinerMk1", "MinerMk2", "MinerMk3"],
)
def test_miner_rates(cycle_seconds: float, expected: float) -> None:
    assert conversions.extractor_rate_per_minute(1, cycle_seconds, ItemForm.SOLID) == expected


def test_fluid_extractor_rates() -> None:
    # Oil and Water Extractor: 2000 L per second -> 120 m3/min.
    assert conversions.extractor_rate_per_minute(2000, 1.0, ItemForm.LIQUID) == 120
