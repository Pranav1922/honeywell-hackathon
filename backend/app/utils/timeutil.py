"""Simulation-clock arithmetic.

Narrow by design — this is not a general utility drawer. It converts between
step indices and simulated wall-clock, and answers the occupancy and cadence
questions the controllers ask every timestep.

Implemented in Milestone 1.
"""

from __future__ import annotations

from datetime import datetime


def step_to_time(start: datetime, step: int, timestep_seconds: int) -> datetime:
    """Simulated wall-clock time at `step`."""
    raise NotImplementedError("Milestone 1")


def steps_per_hour(timestep_seconds: int) -> int:
    raise NotImplementedError("Milestone 1")


def steps_for_days(days: int, timestep_seconds: int) -> int:
    """Horizon length for a run of `days` days."""
    raise NotImplementedError("Milestone 1")


def is_occupied(sim_time: datetime, occupied_hours: tuple[int, int]) -> bool:
    """Whether the building is scheduled to be occupied. Weekends are unoccupied."""
    raise NotImplementedError("Milestone 1")


def is_cadence_due(step: int, cadence_steps: int) -> bool:
    """Whether the supervisor should re-plan at this step."""
    raise NotImplementedError("Milestone 1")
