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
from datetime import datetime, timedelta
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
    """Reads real hourly weather from an EnergyPlus `.epw` file.

    An `.epw` is 8,760 hourly records after an eight-line header. A simulation
    timestep is finer than that, so sub-hourly steps are linearly interpolated
    between the bracketing hours — a step change on the hour would put a
    discontinuity into the zone heat balance that the real climate does not have.

    Occupancy is not in the weather file, so the same weekday schedule
    `SyntheticWeather` uses is applied. That keeps the two providers
    interchangeable and keeps a baseline and agent run on identical occupancy.
    """

    HEADER_LINES = 8
    DRY_BULB_FIELD = 6
    GLOBAL_HORIZONTAL_FIELD = 13
    HOURS_PER_YEAR = 8760

    def __init__(
        self,
        epw_path: Path,
        timestep_seconds: int = 900,
        start: datetime | None = None,
        occupied_hours: tuple[int, int] = (8, 18),
    ) -> None:
        """Parse the weather file once, up front.

        Args:
            epw_path: Path to the `.epw` file.
            timestep_seconds: Simulation timestep.
            start: Simulated wall-clock at step 0. Defaults to the first record
                in the file.
            occupied_hours: Half-open [start_hour, end_hour) occupied window.
        """
        path = Path(epw_path)
        if not path.is_file():
            raise FileNotFoundError(f"epw file not found: {path}")

        records = self._parse(path)
        if not records:
            raise ValueError(f"{path.name} contains no hourly weather records")

        self._temps_c = [record[0] for record in records]
        self._solar_w_m2 = [record[1] for record in records]
        self._timestep_seconds = timestep_seconds
        self._occupied_hours = occupied_hours
        self._start = start if start is not None else records[0][2]

    def sample(self, step: int) -> WeatherSample:
        """Conditions at `step`, interpolated between hourly records."""
        if step < 0:
            raise ValueError(f"step must be non-negative, got {step}")

        sim_time = step_to_time(self._start, step, self._timestep_seconds)
        elapsed_hours = step * self._timestep_seconds / 3600.0
        index = int(elapsed_hours) % len(self._temps_c)
        next_index = (index + 1) % len(self._temps_c)
        blend = elapsed_hours - int(elapsed_hours)

        return WeatherSample(
            sim_time=sim_time,
            outdoor_temp_c=_lerp(self._temps_c[index], self._temps_c[next_index], blend),
            solar_w_m2=_lerp(
                self._solar_w_m2[index], self._solar_w_m2[next_index], blend
            ),
            occupancy=self._occupancy(sim_time, hour_of_day(sim_time)),
        )

    def _occupancy(self, sim_time: datetime, hour: float) -> float:
        """The same trapezoidal weekday profile the synthetic provider uses."""
        if is_weekend(sim_time):
            return 0.0
        start_hour, end_hour = self._occupied_hours
        if not start_hour <= hour < end_hour:
            return 0.0
        if hour < start_hour + 1.0 or hour >= end_hour - 1.0:
            return RAMP_OCCUPANCY
        return 1.0

    @classmethod
    def _parse(cls, path: Path) -> list[tuple[float, float, datetime]]:
        """Extract (dry-bulb, global horizontal solar, timestamp) per hour.

        Malformed lines are skipped rather than fatal. Real weather files carry
        occasional bad rows, and losing one hour out of 8,760 is not a reason to
        abandon a run.
        """
        records: list[tuple[float, float, datetime]] = []
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line_number, line in enumerate(handle):
                if line_number < cls.HEADER_LINES:
                    continue
                fields = line.strip().split(",")
                if len(fields) <= cls.GLOBAL_HORIZONTAL_FIELD:
                    continue
                try:
                    # EPW hours run 1-24; hour 24 is midnight ending that day.
                    hour = int(fields[3])
                    timestamp = datetime(
                        int(fields[0]), int(fields[1]), int(fields[2])
                    ) + timedelta(hours=hour - 1)
                    records.append(
                        (
                            float(fields[cls.DRY_BULB_FIELD]),
                            max(0.0, float(fields[cls.GLOBAL_HORIZONTAL_FIELD])),
                            timestamp,
                        )
                    )
                except (ValueError, IndexError):
                    continue
        return records


def _lerp(low: float, high: float, blend: float) -> float:
    """Linear interpolation between two hourly records."""
    return low + (high - low) * blend
