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
from pathlib import Path
from datetime import datetime, timezone
from typing import Callable, Sequence

from app import db
from app.agents.base import Controller
from app.agents.client import LLMClient
from app.agents.llm import LLMSupervisor
from app.agents.rule import BaselineScheduler, ReactiveGuard
from app.agents.tools import AgentContext, ToolRegistry
from app.comfort import clothing_for_season, is_comfortable, ppd, pmv
from app.config import Scenario, Settings, load_settings
from app.energy import RunSummary, step_energy_kwh, summarise
from app.sim.base import BuildingState, Simulator
from app.sim.toy import ToySimulator
from app.sim.weather import SyntheticWeather

COMMIT_INTERVAL_STEPS = 200

CONTROLLERS = ("baseline", "rule", "llm")
SIMULATORS = ("toy", "energyplus")


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
            self._attach_diagnostics()

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
                    # Re-read the simulator's diagnostics so the agent's
                    # get_simulation_errors tool reports faults raised during the
                    # run, not only those present when it started.
                    self._attach_diagnostics()

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

    def _attach_diagnostics(self) -> None:
        """Feed the simulator's runtime errors to the agent's tool context.

        This is what makes `get_simulation_errors` real rather than always empty:
        the EnergyPlus `.err` file is read once the run has started and handed to
        the same context the model queries, so the agent can react to a runtime
        problem without anybody editing code. Both sides are optional — a toy run
        has no `.err`, and the baseline controller has no context — so this is a
        no-op unless an LLM run is driving EnergyPlus.
        """
        collect = getattr(self._simulator, "collect_errors", None)
        tools = getattr(self._controller, "_tools", None)
        if collect is None or tools is None:
            return
        # Replaced rather than appended: EnergyPlus writes the .err file
        # progressively, so each read returns a superset of the last one and
        # appending would inflate every occurrence count.
        context = tools.context
        context.error_log.clear()
        for line in collect():
            context.record_error(line)

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
    scenario: Scenario,
    simulator: str = "toy",
    horizon_steps: int | None = None,
    settings: Settings | None = None,
) -> Simulator:
    """Construct the simulator a scenario calls for.

    Both implementations satisfy the same protocol, so nothing downstream — the
    runner, the controllers, the persistence layer, the dashboard — can tell them
    apart. `settings` is only needed for the EnergyPlus install path and is
    optional so every existing caller keeps working unchanged.
    """
    if simulator not in SIMULATORS:
        raise ValueError(
            f"unknown simulator {simulator!r}; available: {', '.join(SIMULATORS)}"
        )

    if simulator == "energyplus":
        return _build_energyplus(
            scenario, horizon_steps or scenario.horizon_steps, settings or load_settings()
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
        return ReactiveGuard(**guard_limits(settings), occupied_hours=scenario.occupied_hours)
    if controller == "llm":
        return _build_supervisor(scenario, settings)
    raise ValueError(
        f"unknown controller {controller!r}; available: {', '.join(CONTROLLERS)}"
    )


def _build_energyplus(
    scenario: Scenario, horizon_steps: int, settings: Settings
) -> Simulator:
    """Construct an `EnergyPlusSimulator` from the scenario's `energyplus` block.

    Output goes to a per-run directory under `generated_idf_dir` so concurrent
    runs cannot overwrite each other's `.err` file — which the agent reads through
    `get_simulation_errors`, so a shared one would attribute another run's faults
    to this one.
    """
    from app.sim.energyplus import EnergyPlusSimulator, write_dated_variant

    config = scenario.energyplus or {}
    idf_path = _resolve_model_path(config.get("idf"), settings, "idf")
    epw_path = _resolve_model_path(config.get("epw"), settings, "epw")
    output_dir = settings.generated_idf_dir / f"{scenario.id}-{horizon_steps}"

    # EnergyPlus takes its calendar from the .idf, not from the scenario, so the
    # baseline model is re-dated to the scenario's own window and the variant that
    # actually runs is kept alongside the run's output.
    days = max(1, -(-horizon_steps * scenario.timestep_seconds // 86400))
    generated_idf = write_dated_variant(idf_path, output_dir, scenario.start, days)

    return EnergyPlusSimulator(
        idf_path=generated_idf,
        epw_path=epw_path,
        output_dir=output_dir,
        energyplus_dir=settings.energyplus_dir,
        horizon_steps=horizon_steps,
        timestep_seconds=scenario.timestep_seconds,
        calendar_year=scenario.start.year,
    )


def _resolve_model_path(value, settings: Settings, kind: str):
    """Resolve a scenario's model path relative to the models directory."""
    if not value:
        raise ValueError(
            f"scenario does not declare an {kind} file; add "
            f'"energyplus": {{"{kind}": "baseline/..."}} to the scenario JSON'
        )
    path = Path(str(value)).expanduser()
    if path.is_absolute():
        return path
    return (settings.baseline_idf_dir.parent / path).resolve()


def guard_limits(settings: Settings) -> dict[str, float]:
    """The hard limits the guard enforces, as one dict.

    Stated explicitly rather than left to `ReactiveGuard`'s defaults so this dict
    can be passed both to the guard and to the agent's tool context. The limits
    the model is shown are then by construction the limits it is held to; two
    copies of these numbers in two modules is exactly how those drift apart.
    """
    return {
        "min_zone_temp_c": settings.min_zone_temp_c,
        "max_zone_temp_c": settings.max_zone_temp_c,
        "comfort_pmv_low": settings.comfort_pmv_low,
        "comfort_pmv_high": settings.comfort_pmv_high,
        "min_setpoint_gap_c": 1.5,
        "min_ventilation_ach": 0.5,
        "max_ventilation_ach": 6.0,
        "max_co2_ppm": 1000.0,
        "unoccupied_min_temp_c": 12.0,
        "unoccupied_max_temp_c": 32.0,
    }


def _build_supervisor(scenario: Scenario, settings: Settings) -> Controller:
    """Assemble the two-tier LLM controller: supervisor over guard over fallback."""
    limits = guard_limits(settings)
    guard = ReactiveGuard(**limits, occupied_hours=scenario.occupied_hours)

    context = AgentContext(
        timestep_seconds=scenario.timestep_seconds,
        limits=limits,
        targets={
            "tariff_per_kwh": scenario.targets.tariff_per_kwh,
            "grid_carbon_kg_per_kwh": scenario.targets.grid_carbon_kg_per_kwh,
            "peak_demand_kw": scenario.targets.peak_demand_kw,
        },
        occupied_hours=scenario.occupied_hours,
        history_window_steps=settings.history_window_steps,
    )

    return LLMSupervisor(
        client=LLMClient(
            model=settings.llm_model,
            api_key=settings.llm_api_key or None,
            timeout_seconds=settings.llm_timeout_seconds,
            temperature=settings.llm_temperature,
        ),
        tools=ToolRegistry(context),
        guard=guard,
        fallback=BaselineScheduler(occupied_hours=scenario.occupied_hours),
        cadence_steps=settings.decision_cadence_steps,
        max_tool_iterations=settings.max_tool_iterations,
        max_retries=settings.max_retries,
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
