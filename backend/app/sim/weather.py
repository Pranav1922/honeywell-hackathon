"""Outdoor conditions and occupancy for a scenario.

Guarantees that a baseline run and an agent run of the same scenario observe
byte-identical conditions, which is what makes the savings figure a controlled
comparison rather than an anecdote.

`SyntheticWeather` is implemented in Milestone 1; `EpwWeather` in Milestone 4.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class WeatherSample:
    """Ambient conditions and occupancy at one timestep."""

    sim_time: datetime
    outdoor_temp_c: float
    solar_w_m2: float
    occupancy: float  # 0.0 - 1.0


@runtime_checkable
class WeatherProvider(Protocol):
    """Supplies ambient conditions for a given simulation step."""

    def sample(self, step: int) -> WeatherSample:
        """Return conditions at `step`. Deterministic for a given provider."""
        ...


class SyntheticWeather:
    """Diurnal sine-wave outdoor temperature with a weekday occupancy schedule."""

    def __init__(
        self,
        start: datetime,
        timestep_seconds: int = 900,
        mean_temp_c: float = 18.0,
        daily_swing_c: float = 8.0,
        peak_solar_w_m2: float = 700.0,
        occupied_hours: tuple[int, int] = (8, 18),
    ) -> None:
        raise NotImplementedError("Milestone 1")

    def sample(self, step: int) -> WeatherSample:
        raise NotImplementedError("Milestone 1")


class EpwWeather:
    """Reads real hourly weather from an EnergyPlus `.epw` file."""

    def __init__(self, epw_path: Path, timestep_seconds: int = 900) -> None:
        raise NotImplementedError("Milestone 4")

    def sample(self, step: int) -> WeatherSample:
        raise NotImplementedError("Milestone 4")
