"""The closed-loop orchestrator — the only module that knows the loop's shape.

Steps the simulator, asks the controller to decide, injects the action back into
the simulator, evaluates comfort and energy, and persists every record. Baseline
and agent runs use this identical path with a different `Controller`, which is
what makes the savings figure a controlled experiment.

One iteration:

    read state -> controller decides -> apply action -> advance simulator
    -> evaluate PMV -> accumulate energy -> persist -> repeat
"""

from __future__ import annotations

import dataclasses
import sqlite3
from collections import deque
from datetime import datetime, timezone
from typing import Callable, Sequence

from app import db
from app.agents.base import Controller
from app.agents.rule import BaselineScheduler, ReactiveGuard
from app.comfort import clothing_for_season, is_comfortable, ppd, pmv
from app.config import Scenario, Settings
from app.energy import RunSummary, step_energy_kwh, summarise
from app.sim.base import BuildingState, Simulator
from app.sim.toy import ToySimulator
from app.sim.weather import SyntheticWeather

COMMIT_INTERVAL_STEPS = 200

CONTROLLERS = ("baseline", "rule")
SIMULATORS = ("toy",)


class ClosedLoopRunner:
    """Runs one scenario under one controller to completion."""

    def __init__(
        self,
        run_id: int,
        simulator: Simulator,
        controller: Controller,
        settings: Settings,
        on_progress: Callable[[BuildingState], None] | None = None,
    ) -> None:
        """Wire the loop together.

        Args:
            run_id: The `runs` row this execution writes into.
            simulator: Any implementation of the `Simulator` protocol.
            controller: Any implementation of the `Controller` protocol.
            settings: Comfort limits, tariff, carbon intensity, database path.
            on_progress: Called with each new state as it is produced. Feeds the
                SSE stream; persistence happens regardless of whether it is set.
        """
        self._run_id = run_id
        self._simulator = simulator
        self._controller = controller
        self._settings = settings
        self._on_progress = on_progress

        self._history: deque[BuildingState] = deque(
            maxlen=max(1, settings.history_window_steps)
        )
        self._stopped = False

    def run(self) -> RunSummary:
        """Execute the full horizon and return the run's aggregate metrics.

        A simulator or controller failure marks the run `failed` and closes the
        simulator; it never leaves an orphaned EnergyPlus process.
        """
        conn = db.connect(self._settings.database_path)
        energies_kwh: list[float] = []
        powers_kw: list[float] = []
        ppds: list[float] = []
        comfort_flags: list[bool] = []
        occupancies: list[float] = []
        last_decision_key: tuple | None = None

        try:
            self._controller.reset()
            state = self._simulator.reset()
            self._history.clear()
            self._history.append(state)

            for _ in range(self._simulator.horizon_steps):
                if self._stopped:
                    break

                decision = self._controller.decide(state, tuple(self._history))
                state = self._simulator.step(decision.action)

                comfort_value = self._evaluate_comfort(state)
                energy_kwh = step_energy_kwh(
                    state.power_kw, self._simulator.timestep_seconds
                )

                db.insert_timestep(
                    conn,
                    self._run_id,
                    state,
                    energy_kwh=energy_kwh,
                    pmv=comfort_value.pmv,
                    ppd=comfort_value.ppd,
                    comfort_ok=comfort_value.ok,
                )

                decision_key = _decision_key(decision)
                if decision_key != last_decision_key or decision.latency_ms is not None:
                    db.insert_decision(conn, self._run_id, decision)
                    last_decision_key = decision_key

                energies_kwh.append(energy_kwh)
                powers_kw.append(state.power_kw)
                ppds.append(comfort_value.ppd)
                comfort_flags.append(comfort_value.ok)
                occupancies.append(state.occupancy)

                self._history.append(state)
                if self._on_progress is not None:
                    self._on_progress(state)

                if state.step % COMMIT_INTERVAL_STEPS == 0:
                    conn.commit()

            conn.commit()
            summary = summarise(
                run_id=self._run_id,
                energies_kwh=energies_kwh,
                powers_kw=powers_kw,
                ppds=ppds,
                comfort_flags=comfort_flags,
                occupancies=occupancies,
                tariff_per_kwh=self._settings.tariff_per_kwh,
                carbon_kg_per_kwh=self._settings.grid_carbon_kg_per_kwh,
            )
            self._finish(
                conn,
                status="stopped" if self._stopped else "complete",
                summary=summary,
            )
            return summary

        except Exception as exc:
            conn.rollback()
            db.finish_run(
                conn,
                self._run_id,
                status="failed",
                error=f"{type(exc).__name__}: {exc}",
                finished_at=_now(),
            )
            raise
        finally:
            self._simulator.close()
            conn.close()

    def stop(self) -> None:
        """Request cooperative cancellation. The loop exits at the next step."""
        self._stopped = True

    @property
    def history(self) -> Sequence[BuildingState]:
        """Recent telemetry, bounded by `Settings.history_window_steps`.

        This is the window the agent's tools read from.
        """
        return tuple(self._history)

    # -- internals ----------------------------------------------------------

    def _evaluate_comfort(self, state: BuildingState) -> _ComfortValue:
        """PMV, PPD and the comfort verdict for one state.

        Mean radiant temperature is taken as the zone air temperature: the RC
        model is well-mixed and carries no surface temperatures. EnergyPlus
        reports a real MRT in Milestone 4 and this is the line that will use it.
        """
        clothing = clothing_for_season(state.outdoor_temp_c)
        value = pmv(
            air_temp_c=state.zone_temp_c,
            mean_radiant_temp_c=state.zone_temp_c,
            air_speed_ms=state.air_speed_ms,
            relative_humidity=state.relative_humidity,
            clothing_insulation=clothing,
        )
        return _ComfortValue(
            pmv=value,
            ppd=ppd(value),
            ok=is_comfortable(
                value,
                state.occupancy,
                low=self._settings.comfort_pmv_low,
                high=self._settings.comfort_pmv_high,
            ),
        )

    def _finish(
        self, conn: sqlite3.Connection, status: str, summary: RunSummary
    ) -> None:
        """Record the run's outcome and aggregate metrics."""
        db.finish_run(
            conn,
            self._run_id,
            status=status,
            finished_at=_now(),
            total_kwh=summary.total_kwh,
            peak_kw=summary.peak_kw,
            cost=summary.cost,
            co2_kg=summary.co2_kg,
            comfort_violations=summary.comfort_violations,
            mean_ppd=summary.mean_ppd,
        )


class _ComfortValue:
    """PMV, PPD and the pass/fail verdict for a single timestep."""

    __slots__ = ("pmv", "ppd", "ok")

    def __init__(self, pmv: float, ppd: float, ok: bool) -> None:
        self.pmv = pmv
        self.ppd = ppd
        self.ok = ok


def settings_for_scenario(settings: Settings, scenario: Scenario) -> Settings:
    """Overlay a scenario's targets onto the base settings.

    The scenario owns the objectives a run is judged against — comfort band,
    tariff, carbon intensity — so that both arms of a comparison are scored by
    identical rules regardless of the machine they run on.
    """
    return dataclasses.replace(
        settings,
        comfort_pmv_low=scenario.targets.comfort_pmv_low,
        comfort_pmv_high=scenario.targets.comfort_pmv_high,
        tariff_per_kwh=scenario.targets.tariff_per_kwh,
        grid_carbon_kg_per_kwh=scenario.targets.grid_carbon_kg_per_kwh,
    )


def build_simulator(
    scenario: Scenario, simulator: str = "toy", horizon_steps: int | None = None
) -> Simulator:
    """Construct the simulator a scenario calls for.

    Milestone 1 ships the toy simulator only; `EnergyPlusSimulator` joins the
    same branch in Milestone 4 without any other module changing.
    """
    if simulator not in SIMULATORS:
        raise ValueError(
            f"unknown simulator {simulator!r}; available: {', '.join(SIMULATORS)}"
        )

    weather = SyntheticWeather(
        start=scenario.start,
        timestep_seconds=scenario.timestep_seconds,
        mean_temp_c=float(scenario.weather.get("mean_temp_c", 18.0)),
        daily_swing_c=float(scenario.weather.get("daily_swing_c", 8.0)),
        peak_solar_w_m2=float(scenario.weather.get("peak_solar_w_m2", 700.0)),
        occupied_hours=scenario.occupied_hours,
    )
    return ToySimulator(
        weather=weather,
        horizon_steps=horizon_steps or scenario.horizon_steps,
        timestep_seconds=scenario.timestep_seconds,
        **scenario.building,
    )


def build_controller(
    controller: str, scenario: Scenario, settings: Settings
) -> Controller:
    """Construct a controller by name, configured from the scenario's targets."""
    if controller == "baseline":
        return BaselineScheduler(occupied_hours=scenario.occupied_hours)
    if controller == "rule":
        return ReactiveGuard(
            min_zone_temp_c=settings.min_zone_temp_c,
            max_zone_temp_c=settings.max_zone_temp_c,
            comfort_pmv_low=settings.comfort_pmv_low,
            comfort_pmv_high=settings.comfort_pmv_high,
            occupied_hours=scenario.occupied_hours,
        )
    raise ValueError(
        f"unknown controller {controller!r}; available: {', '.join(CONTROLLERS)}"
    )


def _decision_key(decision) -> tuple:
    """What makes a decision materially different from the previous one.

    Rounded so that continuous set-point trimming does not write a row per
    timestep — the decision log is meant to be readable, and every telemetry
    detail is already in `timesteps`.
    """
    return (
        decision.policy.strategy,
        round(decision.action.heating_sp_c, 1),
        round(decision.action.cooling_sp_c, 1),
        round(decision.action.lighting_level, 2),
        round(decision.action.ventilation_ach, 1),
        decision.guard_clamped,
        decision.fallback_used,
    )


def _now() -> str:
    """Current wall-clock time, ISO-8601 UTC."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
