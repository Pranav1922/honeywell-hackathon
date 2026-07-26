"""Thermal comfort: Fanger PMV and PPD.

Pure functions, no I/O, so the comfort model is unit-testable in isolation. PMV
is named explicitly in the problem statement and carries 20% of the evaluation,
so it is computed properly rather than approximated by a temperature band.

Implemented in Milestone 1.
"""

from __future__ import annotations

PMV_ACCEPTABLE_LOW = -0.5   # ASHRAE-55 acceptable band
PMV_ACCEPTABLE_HIGH = 0.5

MET_OFFICE = 1.1            # metabolic rate, seated light office work
CLO_WINTER = 1.0            # clothing insulation
CLO_SUMMER = 0.5


def pmv(
    air_temp_c: float,
    mean_radiant_temp_c: float,
    air_speed_ms: float,
    relative_humidity: float,
    metabolic_rate: float = MET_OFFICE,
    clothing_insulation: float = CLO_WINTER,
    external_work: float = 0.0,
) -> float:
    """Fanger Predicted Mean Vote, roughly -3 (cold) to +3 (hot)."""
    raise NotImplementedError("Milestone 1")


def ppd(pmv_value: float) -> float:
    """Predicted Percentage Dissatisfied, 5-100%, derived from PMV."""
    raise NotImplementedError("Milestone 1")


def is_comfortable(
    pmv_value: float,
    occupancy: float,
    low: float = PMV_ACCEPTABLE_LOW,
    high: float = PMV_ACCEPTABLE_HIGH,
) -> bool:
    """Whether comfort is satisfied. Unoccupied steps are never violations."""
    raise NotImplementedError("Milestone 1")


def clothing_for_season(outdoor_temp_c: float) -> float:
    """Pick a clothing insulation value from prevailing outdoor conditions."""
    raise NotImplementedError("Milestone 1")
