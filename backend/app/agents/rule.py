"""Deterministic controllers: the experimental control arm and the fast tier.

`BaselineScheduler` reproduces conventional fixed-schedule BMS behaviour. It is
what the agent is measured against, and it is also the fallback that keeps a run
alive when the LLM is unavailable.

`ReactiveGuard` is the fast tier of the two-tier design. It applies whatever
policy is currently active and clamps the result to hard comfort and equipment
limits, so a hallucinated set-point can never reach the building. Enforcement
lives in code, not in a prompt.

The difference between them is the whole savings argument. The baseline knows
only the clock. The guard measures the building: it derives the widest set-point
band that still satisfies PMV, dims lighting to actual occupancy, and ventilates
to the air quality it measures rather than to a fixed design rate.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Sequence

from app.agents.base import (
    STRATEGY_HOLD,
    STRATEGY_PRECOOL,
    STRATEGY_PREHEAT,
    STRATEGY_SETBACK,
    ControlPolicy,
    Decision,
)
from app.comfort import (
    PMV_ACCEPTABLE_HIGH,
    PMV_ACCEPTABLE_LOW,
    comfort_band,
)
from app.sim.base import BuildingState, ControlAction
from app.utils.timeutil import is_occupied

OUTDOOR_CO2_PPM = 420.0


class BaselineScheduler:
    """Fixed occupancy-schedule controller — the savings baseline.

    Deliberately blind to everything except the clock. It does not look at
    occupancy, zone temperature, or air quality; it applies the same set-points
    at the same times every weekday. This is how the large majority of installed
    building management systems actually run, and it is what the autonomous
    controller has to beat.
    """

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
        setback_ventilation_ach: float = 0.0,
    ) -> None:
        """Configure the fixed schedule.

        Args:
            occupied_heating_sp_c: Heating set-point during scheduled hours.
            occupied_cooling_sp_c: Cooling set-point during scheduled hours.
            setback_heating_sp_c: Heating set-point outside scheduled hours.
            setback_cooling_sp_c: Cooling set-point outside scheduled hours.
            occupied_hours: The schedule itself, as [start_hour, end_hour).
            lighting_level: Lighting output during scheduled hours.
            ventilation_ach: Design ventilation rate during scheduled hours.
            setback_ventilation_ach: Ventilation outside scheduled hours.
        """
        if occupied_heating_sp_c > occupied_cooling_sp_c:
            raise ValueError("occupied heating set-point exceeds cooling set-point")
        if setback_heating_sp_c > setback_cooling_sp_c:
            raise ValueError("setback heating set-point exceeds cooling set-point")

        self._occupied_heating_sp_c = occupied_heating_sp_c
        self._occupied_cooling_sp_c = occupied_cooling_sp_c
        self._setback_heating_sp_c = setback_heating_sp_c
        self._setback_cooling_sp_c = setback_cooling_sp_c
        self._occupied_hours = occupied_hours
        self._lighting_level = lighting_level
        self._ventilation_ach = ventilation_ach
        self._setback_ventilation_ach = setback_ventilation_ach

    def decide(
        self, state: BuildingState, history: Sequence[BuildingState]
    ) -> Decision:
        """Look up the schedule for the current hour. Nothing else is consulted."""
        scheduled = is_occupied(state.sim_time, self._occupied_hours)
        if scheduled:
            policy = ControlPolicy(
                strategy=STRATEGY_HOLD,
                heating_sp_c=self._occupied_heating_sp_c,
                cooling_sp_c=self._occupied_cooling_sp_c,
                lighting_level=self._lighting_level,
                ventilation_ach=self._ventilation_ach,
                expires_at_step=state.step + 1,
            )
            rationale = (
                f"Scheduled occupied period; holding fixed set-points "
                f"{self._occupied_heating_sp_c:.1f}/{self._occupied_cooling_sp_c:.1f} C."
            )
        else:
            policy = ControlPolicy(
                strategy=STRATEGY_SETBACK,
                heating_sp_c=self._setback_heating_sp_c,
                cooling_sp_c=self._setback_cooling_sp_c,
                lighting_level=0.0,
                ventilation_ach=self._setback_ventilation_ach,
                expires_at_step=state.step + 1,
            )
            rationale = (
                f"Outside scheduled hours; setting back to "
                f"{self._setback_heating_sp_c:.1f}/{self._setback_cooling_sp_c:.1f} C."
            )

        return Decision(
            step=state.step,
            sim_time=state.sim_time,
            action=_action_from(policy),
            policy=policy,
            rationale=rationale,
        )

    def reset(self) -> None:
        """Nothing to clear — the schedule is stateless."""


class ReactiveGuard:
    """Applies the active policy every timestep and clamps it to hard limits.

    Two jobs. As the fast tier under an LLM supervisor it enforces a policy it
    was handed and reports when it had to override. Standalone it *is* the
    controller, synthesising the minimum-energy policy that still satisfies
    comfort: heat no higher than the coolest acceptable temperature, cool no
    lower than the warmest, dim to actual occupancy, and ventilate to measured
    air quality.
    """

    name = "rule"

    def __init__(
        self,
        min_zone_temp_c: float = 19.0,
        max_zone_temp_c: float = 26.0,
        min_setpoint_gap_c: float = 1.5,
        max_co2_ppm: float = 1000.0,
        min_ventilation_ach: float = 0.5,
        max_ventilation_ach: float = 6.0,
        comfort_pmv_low: float = PMV_ACCEPTABLE_LOW,
        comfort_pmv_high: float = PMV_ACCEPTABLE_HIGH,
        unoccupied_min_temp_c: float = 12.0,
        unoccupied_max_temp_c: float = 32.0,
        co2_target_margin_ppm: float = 100.0,
        comfort_margin_c: float = 0.2,
        occupied_hours: tuple[int, int] = (8, 18),
        min_precondition_minutes: int = 30,
        max_precondition_minutes: int = 180,
        recovery_rate_k_per_h: float = 4.0,
    ) -> None:
        """Configure the hard limits the guard enforces.

        Args:
            min_zone_temp_c: Coldest permitted zone temperature when occupied.
            max_zone_temp_c: Warmest permitted zone temperature when occupied.
            min_setpoint_gap_c: Minimum dead-band, preventing simultaneous
                heating and cooling.
            max_co2_ppm: Air quality ceiling; ventilation is forced up above it.
            min_ventilation_ach: Floor on ventilation, maintained even when empty.
            max_ventilation_ach: Fan capacity ceiling.
            comfort_pmv_low: Lower edge of the acceptable PMV band.
            comfort_pmv_high: Upper edge of the acceptable PMV band.
            unoccupied_min_temp_c: Frost-protection floor when the zone is empty.
            unoccupied_max_temp_c: Fabric-protection ceiling when the zone is empty.
            co2_target_margin_ppm: Control target set below the hard CO2 ceiling.
            comfort_margin_c: Inset from the comfort band edges, so the plant is
                not permanently working at the exact boundary.
            occupied_hours: When the building is expected to fill, used only to
                schedule optimum start.
            min_precondition_minutes: Shortest optimum-start lead, used when the
                zone is already close to the comfort band.
            max_precondition_minutes: Longest lead, bounding how much of a deep
                setback the controller will try to recover from.
            recovery_rate_k_per_h: How fast the plant can move the zone, in
                kelvin per hour. This is a commissioning constant, not a
                universal truth — it depends on plant capacity against building
                mass, and it is the knob to turn if morning recovery is late.
        """
        if min_zone_temp_c + min_setpoint_gap_c > max_zone_temp_c:
            raise ValueError(
                "min_setpoint_gap_c does not fit between the zone temperature limits"
            )
        if min_ventilation_ach > max_ventilation_ach:
            raise ValueError("min_ventilation_ach exceeds max_ventilation_ach")

        self._min_zone_temp_c = min_zone_temp_c
        self._max_zone_temp_c = max_zone_temp_c
        self._min_setpoint_gap_c = min_setpoint_gap_c
        self._max_co2_ppm = max_co2_ppm
        self._min_ventilation_ach = min_ventilation_ach
        self._max_ventilation_ach = max_ventilation_ach
        self._comfort_pmv_low = comfort_pmv_low
        self._comfort_pmv_high = comfort_pmv_high
        self._unoccupied_min_temp_c = unoccupied_min_temp_c
        self._unoccupied_max_temp_c = unoccupied_max_temp_c
        self._co2_target_ppm = max_co2_ppm - co2_target_margin_ppm
        self._comfort_margin_c = comfort_margin_c
        if recovery_rate_k_per_h <= 0:
            raise ValueError(
                f"recovery_rate_k_per_h must be positive, got {recovery_rate_k_per_h}"
            )
        self._occupied_hours = occupied_hours
        self._min_precondition_minutes = min_precondition_minutes
        self._max_precondition_minutes = max_precondition_minutes
        self._recovery_rate_k_per_h = recovery_rate_k_per_h

        self._policy: ControlPolicy | None = None

    def set_policy(self, policy: ControlPolicy) -> None:
        """Adopt a new supervisory policy, replacing the active one."""
        self._policy = policy

    def decide(
        self, state: BuildingState, history: Sequence[BuildingState]
    ) -> Decision:
        """Enforce the active policy, or synthesise the cheapest comfortable one."""
        comfort_required = self._comfort_required(state)
        policy = self._active_policy(state, history, comfort_required)
        requested = _action_from(policy)
        applied, clamped = self._clamp(requested, state, comfort_required)

        return Decision(
            step=state.step,
            sim_time=state.sim_time,
            action=applied,
            policy=policy,
            rationale=self._explain(applied, state, comfort_required, clamped),
            guard_clamped=clamped,
        )

    def reset(self) -> None:
        """Drop the active policy so the next run starts clean."""
        self._policy = None

    # -- internals ----------------------------------------------------------

    def _comfort_required(self, state: BuildingState) -> bool:
        """Whether comfort must hold right now: occupied, or about to be.

        Occupancy is only observable once people have already arrived, by which
        point the zone is at whatever temperature it drifted to overnight. The
        optimum-start lead is what closes that gap.
        """
        if state.occupancy > 0.0:
            return True
        return is_occupied(
            state.sim_time + self._precondition_lead(state), self._occupied_hours
        )

    def _target_band(self, state: BuildingState) -> tuple[float, float]:
        """The set-points the guard actually wants, already inside its own limits.

        The PMV-derived band and the absolute zone limits can disagree — light
        summer clothing tolerates 26.9 C while the installation caps out at 26.0
        — and whichever is tighter has to win. Resolving that here rather than in
        `_clamp` keeps `guard_clamped` meaning what it should: that a supervisor's
        policy was overridden, not that the guard corrected itself.
        """
        coolest_c, warmest_c = comfort_band(
            outdoor_temp_c=state.outdoor_temp_c,
            relative_humidity=state.relative_humidity,
            air_speed_ms=state.air_speed_ms,
            low_pmv=self._comfort_pmv_low,
            high_pmv=self._comfort_pmv_high,
        )
        heating_sp_c = max(coolest_c + self._comfort_margin_c, self._min_zone_temp_c)
        cooling_sp_c = min(warmest_c - self._comfort_margin_c, self._max_zone_temp_c)
        if cooling_sp_c - heating_sp_c < self._min_setpoint_gap_c:
            heating_sp_c = cooling_sp_c - self._min_setpoint_gap_c
        return heating_sp_c, cooling_sp_c

    def _precondition_lead(self, state: BuildingState) -> timedelta:
        """How early to start conditioning, scaled to how far the zone has drifted.

        A fixed lead cannot serve both seasons: recovering two degrees on a mild
        morning and eight degrees after a deep winter setback are different jobs,
        and sizing the lead for the easy one leaves occupants cold on the hard
        one. Scaling it to the observed deficit keeps the deep setback — which is
        where the overnight saving comes from — without paying for it at 08:00.
        """
        heating_sp_c, cooling_sp_c = self._target_band(state)
        deficit_k = max(
            heating_sp_c - state.zone_temp_c, state.zone_temp_c - cooling_sp_c, 0.0
        )
        minutes = (
            self._min_precondition_minutes
            + 60.0 * deficit_k / self._recovery_rate_k_per_h
        )
        return timedelta(minutes=min(minutes, self._max_precondition_minutes))

    def _active_policy(
        self,
        state: BuildingState,
        history: Sequence[BuildingState],
        comfort_required: bool,
    ) -> ControlPolicy:
        """The supervisor's policy if it is still valid, otherwise our own."""
        if self._policy is not None and state.step < self._policy.expires_at_step:
            return self._policy
        return self._minimum_energy_policy(state, history, comfort_required)

    def _minimum_energy_policy(
        self,
        state: BuildingState,
        history: Sequence[BuildingState],
        comfort_required: bool,
    ) -> ControlPolicy:
        """The cheapest policy that still satisfies comfort and air quality.

        While comfort is required this is the comfort band itself: heating to its
        lower edge and cooling to its upper edge means the plant runs only when
        the building would otherwise be genuinely uncomfortable. Outside those
        hours there is no comfort constraint, so the set-points open right out to
        the fabric-protection limits and the plant idles.
        """
        occupied = state.occupancy > 0.0
        if comfort_required:
            heating_sp_c, cooling_sp_c = self._target_band(state)
            lighting_level = state.occupancy
            if occupied or heating_sp_c <= state.zone_temp_c <= cooling_sp_c:
                # Already inside the band: nothing to pre-condition, just hold.
                strategy = STRATEGY_HOLD
            elif state.zone_temp_c > cooling_sp_c:
                strategy = STRATEGY_PRECOOL
            else:
                strategy = STRATEGY_PREHEAT
        else:
            heating_sp_c = self._unoccupied_min_temp_c
            cooling_sp_c = self._unoccupied_max_temp_c
            strategy = STRATEGY_SETBACK
            lighting_level = 0.0

        return ControlPolicy(
            strategy=strategy,
            heating_sp_c=heating_sp_c,
            cooling_sp_c=cooling_sp_c,
            lighting_level=lighting_level,
            ventilation_ach=self._ventilation_for_air_quality(
                state, history, occupied
            ),
            expires_at_step=state.step + 1,
        )

    def _ventilation_for_air_quality(
        self,
        state: BuildingState,
        history: Sequence[BuildingState],
        occupied: bool,
    ) -> float:
        """Demand-controlled ventilation from the measured CO2 concentration.

        At steady state the concentration rise above outdoor air is inversely
        proportional to the airflow, so the airflow needed to hold the target is

            Q_target = Q_now * (C_now - C_out) / (C_target - C_out)

        which needs no knowledge of the building's volume or how many people are
        in it — it calibrates itself from what the sensor reports.

        That relation only holds at equilibrium, though, and the morning fill is
        precisely when it does not: concentration is still climbing, so the
        measurement understates the load and the fan responds a step late. The
        one-step linear extrapolation below is what turns a lagging controller
        into a leading one, and it is the difference between holding the ceiling
        and having to be overridden at it.
        """
        if not occupied:
            return self._min_ventilation_ach

        headroom_ppm = self._co2_target_ppm - OUTDOOR_CO2_PPM
        if headroom_ppm <= 0.0:
            return self._max_ventilation_ach

        projected_ppm = state.co2_ppm
        if len(history) >= 2:
            projected_ppm += state.co2_ppm - history[-2].co2_ppm

        excess_ppm = projected_ppm - OUTDOOR_CO2_PPM
        if excess_ppm <= 0.0:
            return self._min_ventilation_ach

        current_ach = max(state.ventilation_ach, self._min_ventilation_ach)
        required_ach = current_ach * excess_ppm / headroom_ppm
        return min(
            self._max_ventilation_ach, max(self._min_ventilation_ach, required_ach)
        )

    def _clamp(
        self, action: ControlAction, state: BuildingState, comfort_required: bool
    ) -> tuple[ControlAction, bool]:
        """Force an action inside the hard limits, reporting whether it moved.

        This is the safety property the whole design rests on: whatever the
        supervisor asked for, what reaches the building is bounded by code.
        """
        floor_c = (
            self._min_zone_temp_c if comfort_required else self._unoccupied_min_temp_c
        )
        ceiling_c = (
            self._max_zone_temp_c if comfort_required else self._unoccupied_max_temp_c
        )

        heating_sp_c = min(max(action.heating_sp_c, floor_c), ceiling_c)
        cooling_sp_c = min(max(action.cooling_sp_c, floor_c), ceiling_c)

        # Preserve the dead-band, widening upward unless that would breach the
        # ceiling, in which case widen downward instead.
        if cooling_sp_c - heating_sp_c < self._min_setpoint_gap_c:
            cooling_sp_c = heating_sp_c + self._min_setpoint_gap_c
            if cooling_sp_c > ceiling_c:
                cooling_sp_c = ceiling_c
                heating_sp_c = cooling_sp_c - self._min_setpoint_gap_c

        lighting_level = min(1.0, max(0.0, action.lighting_level))

        ventilation_ach = min(
            self._max_ventilation_ach,
            max(self._min_ventilation_ach, action.ventilation_ach),
        )
        if state.co2_ppm > self._max_co2_ppm:
            ventilation_ach = self._max_ventilation_ach

        applied = ControlAction(
            heating_sp_c=heating_sp_c,
            cooling_sp_c=cooling_sp_c,
            lighting_level=lighting_level,
            ventilation_ach=ventilation_ach,
        )
        return applied, applied != action

    def _explain(
        self,
        applied: ControlAction,
        state: BuildingState,
        comfort_required: bool,
        clamped: bool,
    ) -> str:
        """A one-line account of what was done and why, for the dashboard."""
        band = f"{applied.heating_sp_c:.1f}/{applied.cooling_sp_c:.1f} C"
        if state.occupancy > 0.0:
            reason = (
                f"Occupied at {state.occupancy:.0%}; widest comfortable band is "
                f"{band}. CO2 {state.co2_ppm:.0f} ppm, ventilating at "
                f"{applied.ventilation_ach:.2f} ACH."
            )
        elif comfort_required:
            reason = (
                f"Empty but occupancy is due; pre-conditioning to {band} now, while "
                f"outdoor air is {state.outdoor_temp_c:.1f} C, rather than recovering "
                f"against a hotter afternoon."
            )
        else:
            reason = (
                f"Zone empty; setting back to {band}, lights off, ventilation at the "
                f"{applied.ventilation_ach:.2f} ACH minimum."
            )
        if clamped:
            reason += " Requested policy was clamped to hard limits."
        return reason


def _action_from(policy: ControlPolicy) -> ControlAction:
    """Project a policy onto the actuator command it implies."""
    return ControlAction(
        heating_sp_c=policy.heating_sp_c,
        cooling_sp_c=policy.cooling_sp_c,
        lighting_level=policy.lighting_level,
        ventilation_ach=policy.ventilation_ach,
    )
