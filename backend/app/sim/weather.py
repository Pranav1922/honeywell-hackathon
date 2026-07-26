"""Outdoor conditions and occupancy for a scenario.

Guarantees that a baseline run and an agent run of the same scenario observe
byte-identical conditions, which is what makes the savings figure a controlled
comparison rather than an anecdote. Both providers are pure functions of the
step index — there is no internal state and no randomness, so replaying a step
always yields the same sample.

`SyntheticWeather` is implemented here; `EpwWeather` arrives in Milestone 4.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

from app.utils.timeutil import hour_of_day, is_weekend, step_to_time

SOLAR_SUNRISE_HOUR = 6.0
SOLAR_SUNSET_HOUR = 18.0
PEAK_TEMPERATURE_HOUR = 15.0
RAMP_OCCUPANCY = 0.5


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
        """Configure the synthetic climate.

        Args:
            start: Simulated wall-clock time at step 0.
            timestep_seconds: Simulation timestep.
            mean_temp_c: Daily mean outdoor dry-bulb temperature.
            daily_swing_c: Peak-to-trough diurnal temperature range.
            peak_solar_w_m2: Incident solar irradiance at solar noon.
            occupied_hours: Half-open [start_hour, end_hour) occupied window.
        """
        if daily_swing_c < 0:
            raise ValueError(f"daily_swing_c must be non-negative, got {daily_swing_c}")
        if peak_solar_w_m2 < 0:
            raise ValueError(
                f"peak_solar_w_m2 must be non-negative, got {peak_solar_w_m2}"
            )
        start_hour, end_hour = occupied_hours
        if not 0 <= start_hour < end_hour <= 24:
            raise ValueError(f"invalid occupied_hours: {occupied_hours}")

        self._start = start
        self._timestep_seconds = timestep_seconds
        self._mean_temp_c = mean_temp_c
        self._daily_swing_c = daily_swing_c
        self._peak_solar_w_m2 = peak_solar_w_m2
        self._occupied_hours = occupied_hours

    def sample(self, step: int) -> WeatherSample:
        """Conditions at `step`, computed rather than looked up."""
        sim_time = step_to_time(self._start, step, self._timestep_seconds)
        hour = hour_of_day(sim_time)
        return WeatherSample(
            sim_time=sim_time,
            outdoor_temp_c=self._outdoor_temp_c(hour),
            solar_w_m2=self._solar_w_m2(hour),
            occupancy=self._occupancy(sim_time, hour),
        )

    def _outdoor_temp_c(self, hour: float) -> float:
        """Cosine diurnal profile peaking mid-afternoon."""
        phase = 2.0 * math.pi * (hour - PEAK_TEMPERATURE_HOUR) / 24.0
        return self._mean_temp_c + (self._daily_swing_c / 2.0) * math.cos(phase)

    def _solar_w_m2(self, hour: float) -> float:
        """Half-sine daylight profile, zero outside daylight hours."""
        if not SOLAR_SUNRISE_HOUR <= hour <= SOLAR_SUNSET_HOUR:
            return 0.0
        daylight_fraction = (hour - SOLAR_SUNRISE_HOUR) / (
            SOLAR_SUNSET_HOUR - SOLAR_SUNRISE_HOUR
        )
        return self._peak_solar_w_m2 * math.sin(math.pi * daylight_fraction)

    def _occupancy(self, sim_time: datetime, hour: float) -> float:
        """Trapezoidal weekday profile: half-occupied in the first and last hour.

        The ramp matters — it is the difference between a clock-driven schedule
        and a controller that responds to who is actually in the building.
        """
        if is_weekend(sim_time):
            return 0.0
        start_hour, end_hour = self._occupied_hours
        if not start_hour <= hour < end_hour:
            return 0.0
        if hour < start_hour + 1.0 or hour >= end_hour - 1.0:
            return RAMP_OCCUPANCY
        return 1.0


class EpwWeather:
    """Reads real hourly weather from an EnergyPlus `.epw` file."""

    def __init__(self, epw_path: Path, timestep_seconds: int = 900) -> None:
        raise NotImplementedError("Milestone 4")

    def sample(self, step: int) -> WeatherSample:
        raise NotImplementedError("Milestone 4")
