"""Deterministic controllers: the experimental control arm and the fast tier.

`BaselineScheduler` reproduces conventional fixed-schedule BMS behaviour. It is
what the agent is measured against, and it is also the fallback that keeps a run
alive when the LLM is unavailable.

`ReactiveGuard` is the fast tier of the two-tier design. It applies whatever
policy is currently active and clamps the result to hard comfort and equipment
limits, so a hallucinated set-point can never reach the building. Enforcement
lives in code, not in a prompt.

Implemented in Milestone 1.
"""

from __future__ import annotations

from typing import Sequence

from app.agents.base import ControlPolicy, Decision
from app.sim.base import BuildingState


class BaselineScheduler:
    """Fixed occupancy-schedule controller — the savings baseline."""

    name = "baseline"

    def __init__(
        self,
        occupied_heating_sp_c: float = 21.0,
        occupied_cooling_sp_c: float = 24.0,
        setback_heating_sp_c: float = 16.0,
        setback_cooling_sp_c: float = 28.0,
        occupied_hours: tuple[int, int] = (8, 18),
        lighting_level: float = 1.0,
        ventilation_ach: float = 2.0,
    ) -> None:
        raise NotImplementedError("Milestone 1")

    def decide(self, state: BuildingState, history: Sequence[BuildingState]) -> Decision:
        raise NotImplementedError("Milestone 1")

    def reset(self) -> None:
        raise NotImplementedError("Milestone 1")


class ReactiveGuard:
    """Applies the active policy every timestep and clamps it to hard limits."""

    name = "rule"

    def __init__(
        self,
        min_zone_temp_c: float = 19.0,
        max_zone_temp_c: float = 26.0,
        min_setpoint_gap_c: float = 1.5,
        max_co2_ppm: float = 1000.0,
        min_ventilation_ach: float = 0.5,
        max_ventilation_ach: float = 6.0,
    ) -> None:
        raise NotImplementedError("Milestone 1")

    def set_policy(self, policy: ControlPolicy) -> None:
        """Adopt a new supervisory policy, replacing the active one."""
        raise NotImplementedError("Milestone 1")

    def decide(self, state: BuildingState, history: Sequence[BuildingState]) -> Decision:
        raise NotImplementedError("Milestone 1")

    def reset(self) -> None:
        raise NotImplementedError("Milestone 1")
