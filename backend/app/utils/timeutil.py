"""Simulation-clock arithmetic.

Narrow by design — this is not a general utility drawer. It converts between
step indices and simulated wall-clock, and answers the occupancy and cadence
questions the controllers ask every timestep.
"""

from __future__ import annotations

from datetime import datetime, timedelta

SECONDS_PER_HOUR = 3600
HOURS_PER_DAY = 24
SATURDAY = 5


def step_to_time(start: datetime, step: int, timestep_seconds: int) -> datetime:
    """Simulated wall-clock time at `step`."""
    if step < 0:
        raise ValueError(f"step must be non-negative, got {step}")
    return start + timedelta(seconds=step * timestep_seconds)


def steps_per_hour(timestep_seconds: int) -> int:
    """How many simulation steps make up one hour."""
    if timestep_seconds <= 0 or SECONDS_PER_HOUR % timestep_seconds != 0:
        raise ValueError(
            f"timestep_seconds must divide {SECONDS_PER_HOUR}, got {timestep_seconds}"
        )
    return SECONDS_PER_HOUR // timestep_seconds


def steps_for_days(days: int, timestep_seconds: int) -> int:
    """Horizon length for a run of `days` days."""
    if days <= 0:
        raise ValueError(f"days must be positive, got {days}")
    return days * HOURS_PER_DAY * steps_per_hour(timestep_seconds)


def hour_of_day(sim_time: datetime) -> float:
    """Fractional hour of day, e.g. 14.5 for 14:30."""
    return sim_time.hour + sim_time.minute / 60 + sim_time.second / 3600


def is_weekend(sim_time: datetime) -> bool:
    """Saturday or Sunday."""
    return sim_time.weekday() >= SATURDAY


def is_occupied(sim_time: datetime, occupied_hours: tuple[int, int]) -> bool:
    """Whether the building is scheduled to be occupied. Weekends are unoccupied."""
    if is_weekend(sim_time):
        return False
    start_hour, end_hour = occupied_hours
    return start_hour <= hour_of_day(sim_time) < end_hour


def is_cadence_due(step: int, cadence_steps: int) -> bool:
    """Whether the supervisor should re-plan at this step."""
    if cadence_steps <= 0:
        raise ValueError(f"cadence_steps must be positive, got {cadence_steps}")
    return step % cadence_steps == 0
