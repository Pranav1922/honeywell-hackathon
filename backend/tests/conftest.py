"""Shared fixtures. Only what more than one test module needs.

`make_state` exists because a `BuildingState` has fourteen fields and almost
every agent test cares about two of them. Spelling out the other twelve in each
test buries the thing under test.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Callable

import pytest

from app.sim.base import BuildingState

BASE_TIME = datetime(2024, 7, 15, 12, 0)  # a Monday, midday, scheduled occupied


def build_state(**overrides: Any) -> BuildingState:
    """A plausible mid-summer occupied snapshot, with any field overridden."""
    fields: dict[str, Any] = {
        "step": 96,
        "sim_time": BASE_TIME,
        "zone_temp_c": 24.5,
        "outdoor_temp_c": 30.0,
        "occupancy": 0.6,
        "hvac_mode": "cooling",
        "heating_sp_c": 21.0,
        "cooling_sp_c": 24.0,
        "lighting_level": 1.0,
        "ventilation_ach": 2.0,
        "power_kw": 6.4,
        "co2_ppm": 760.0,
        "relative_humidity": 0.5,
        "air_speed_ms": 0.1,
    }
    fields.update(overrides)
    return BuildingState(**fields)


def build_history(count: int, timestep_seconds: int = 900, **overrides: Any):
    """A chronological run of states, warming through the window."""
    return tuple(
        build_state(
            step=index,
            sim_time=BASE_TIME + timedelta(seconds=index * timestep_seconds),
            zone_temp_c=22.0 + 0.05 * index,
            outdoor_temp_c=24.0 + 0.06 * index,
            occupancy=0.0 if index % 8 == 0 else 0.5,
            power_kw=2.0 + 0.02 * index,
            co2_ppm=450.0 + 4.0 * index,
            **overrides,
        )
        for index in range(count)
    )


@pytest.fixture
def make_state() -> Callable[..., BuildingState]:
    """Factory for a `BuildingState` with sensible defaults."""
    return build_state


@pytest.fixture
def state() -> BuildingState:
    """The default occupied mid-summer snapshot."""
    return build_state()
