"""Closed-loop tests: the loop completes a horizon, persists every timestep,
honours cooperative stop, records failures, and enforces the guard's hard limits.

The final test is the milestone's actual claim — that the reactive controller
uses less energy than the fixed schedule without costing comfort — exercised
end to end through the real loop and the real database.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime
from typing import Sequence

import pytest

from app import db
from app.agents.base import STRATEGY_HOLD, ControlPolicy, Decision
from app.agents.rule import BaselineScheduler, ReactiveGuard
from app.config import Scenario, ScenarioTargets, Settings
from app.loop import ClosedLoopRunner, build_controller, build_simulator
from app.sim.base import BuildingState, ControlAction

HORIZON_STEPS = 192  # two simulated days at a 15-minute timestep


@pytest.fixture
def settings(tmp_path) -> Settings:
    """Settings pointed at a throwaway database."""
    return dataclasses.replace(Settings(), database_path=tmp_path / "test.db")


@pytest.fixture
def scenario() -> Scenario:
    """A two-day summer scenario, small enough to run inside a test."""
    return Scenario(
        id="test_summer",
        label="Test summer",
        start=datetime(2024, 7, 15),
        days=2,
        timestep_seconds=900,
        weather={
            "provider": "synthetic",
            "mean_temp_c": 28.0,
            "daily_swing_c": 9.0,
            "peak_solar_w_m2": 850.0,
        },
        occupied_hours=(8, 18),
        targets=ScenarioTargets(),
        building={"initial_zone_temp_c": 26.0},
    )


def _new_run(settings: Settings, scenario: Scenario, controller: str) -> int:
    """Insert a run row and return its id."""
    conn = db.connect(settings.database_path)
    try:
        return db.create_run(
            conn,
            label=f"test/{controller}",
            controller=controller,
            simulator="toy",
            scenario=scenario.id,
            horizon_steps=HORIZON_STEPS,
            timestep_seconds=scenario.timestep_seconds,
            started_at="2024-01-01T00:00:00+00:00",
        )
    finally:
        conn.close()


def _run(settings: Settings, scenario: Scenario, controller: str):
    """Execute one controller over the scenario and return (run_id, summary)."""
    run_id = _new_run(settings, scenario, controller)
    runner = ClosedLoopRunner(
        run_id=run_id,
        simulator=build_simulator(scenario, "toy", HORIZON_STEPS),
        controller=build_controller(controller, scenario, settings),
        settings=settings,
    )
    return run_id, runner.run()


def test_loop_completes_the_horizon_and_persists_every_step(settings, scenario):
    """Every simulated timestep lands in the database exactly once."""
    run_id, summary = _run(settings, scenario, "baseline")

    conn = db.connect(settings.database_path)
    try:
        rows = db.get_timeseries(conn, run_id)
        record = db.get_run(conn, run_id)
    finally:
        conn.close()

    assert len(rows) == HORIZON_STEPS
    assert [row["step"] for row in rows] == list(range(1, HORIZON_STEPS + 1))
    assert record["status"] == "complete"
    assert record["finished_at"] is not None
    assert record["total_kwh"] == pytest.approx(summary.total_kwh)
    assert record["comfort_violations"] == summary.comfort_violations


def test_persisted_telemetry_is_physically_sensible(settings, scenario):
    """Stored rows carry real values, not defaults that happen to serialise."""
    run_id, _ = _run(settings, scenario, "rule")

    conn = db.connect(settings.database_path)
    try:
        rows = db.get_timeseries(conn, run_id)
    finally:
        conn.close()

    assert all(row["power_kw"] > 0.0 for row in rows)
    assert all(row["energy_kwh"] > 0.0 for row in rows)
    assert all(5.0 <= row["ppd"] <= 100.0 for row in rows)
    assert all(-3.0 <= row["pmv"] <= 3.0 for row in rows)
    assert all(row["co2_ppm"] >= 400.0 for row in rows)
    assert all(row["heating_sp_c"] <= row["cooling_sp_c"] for row in rows)
    assert any(row["occupancy"] > 0.0 for row in rows)
    assert {row["hvac_mode"] for row in rows} <= {"off", "heating", "cooling"}


def test_decisions_are_recorded_with_rationales(settings, scenario):
    """Every stored decision explains itself, and repeats are not duplicated."""
    run_id, _ = _run(settings, scenario, "rule")

    conn = db.connect(settings.database_path)
    try:
        decisions = db.get_decisions(conn, run_id)
    finally:
        conn.close()

    assert decisions
    assert len(decisions) < HORIZON_STEPS  # unchanged policies are not re-logged
    assert all(row["rationale"] for row in decisions)
    assert all(row["strategy"] for row in decisions)
    assert {row["strategy"] for row in decisions} > {"hold"}


def test_stop_ends_the_run_cooperatively(settings, scenario):
    """A stopped run finishes cleanly and keeps the data it already gathered."""
    run_id = _new_run(settings, scenario, "baseline")
    runner = ClosedLoopRunner(
        run_id=run_id,
        simulator=build_simulator(scenario, "toy", HORIZON_STEPS),
        controller=build_controller("baseline", scenario, settings),
        settings=settings,
    )

    def stop_after_ten(state: BuildingState) -> None:
        if state.step >= 10:
            runner.stop()

    runner._on_progress = stop_after_ten  # noqa: SLF001 - exercising the hook
    runner.run()

    conn = db.connect(settings.database_path)
    try:
        record = db.get_run(conn, run_id)
        rows = db.get_timeseries(conn, run_id)
    finally:
        conn.close()

    assert record["status"] == "stopped"
    assert 0 < len(rows) < HORIZON_STEPS
    assert record["total_kwh"] is not None


def test_a_failing_controller_marks_the_run_failed(settings, scenario):
    """Failures are recorded on the run row and the simulator is still closed."""

    class BrokenController:
        name = "broken"

        def decide(
            self, state: BuildingState, history: Sequence[BuildingState]
        ) -> Decision:
            raise RuntimeError("controller exploded")

        def reset(self) -> None:
            return None

    run_id = _new_run(settings, scenario, "baseline")
    simulator = build_simulator(scenario, "toy", HORIZON_STEPS)
    runner = ClosedLoopRunner(
        run_id=run_id,
        simulator=simulator,
        controller=BrokenController(),
        settings=settings,
    )

    with pytest.raises(RuntimeError, match="controller exploded"):
        runner.run()

    conn = db.connect(settings.database_path)
    try:
        record = db.get_run(conn, run_id)
    finally:
        conn.close()

    assert record["status"] == "failed"
    assert "controller exploded" in record["error"]
    with pytest.raises(RuntimeError):
        simulator.step(
            ControlAction(
                heating_sp_c=20.0,
                cooling_sp_c=24.0,
                lighting_level=0.0,
                ventilation_ach=1.0,
            )
        )


def test_history_is_bounded_by_the_configured_window(settings, scenario):
    """The agent's observation window cannot grow without limit over a long run."""
    settings = dataclasses.replace(settings, history_window_steps=16)
    run_id = _new_run(settings, scenario, "baseline")
    runner = ClosedLoopRunner(
        run_id=run_id,
        simulator=build_simulator(scenario, "toy", HORIZON_STEPS),
        controller=build_controller("baseline", scenario, settings),
        settings=settings,
    )
    runner.run()
    assert len(runner.history) == 16


def test_guard_clamps_an_unsafe_supervisory_policy():
    """A policy that would freeze the occupants is overridden, and the override
    is reported rather than hidden."""
    guard = ReactiveGuard(min_zone_temp_c=19.0, max_zone_temp_c=26.0)
    guard.set_policy(
        ControlPolicy(
            strategy=STRATEGY_HOLD,
            heating_sp_c=40.0,
            cooling_sp_c=45.0,
            lighting_level=3.0,
            ventilation_ach=99.0,
            expires_at_step=100,
        )
    )
    state = BuildingState(
        step=0,
        sim_time=datetime(2024, 7, 15, 12, 0),
        zone_temp_c=23.0,
        outdoor_temp_c=30.0,
        occupancy=1.0,
        hvac_mode="off",
        heating_sp_c=21.0,
        cooling_sp_c=24.0,
        lighting_level=1.0,
        ventilation_ach=2.0,
        power_kw=3.0,
        co2_ppm=700.0,
        relative_humidity=0.5,
        air_speed_ms=0.1,
    )

    decision = guard.decide(state, [state])

    assert decision.guard_clamped is True
    assert decision.action.heating_sp_c <= 26.0
    assert decision.action.cooling_sp_c <= 26.0
    assert 0.0 <= decision.action.lighting_level <= 1.0
    assert decision.action.ventilation_ach <= 6.0
    assert decision.action.cooling_sp_c - decision.action.heating_sp_c >= 1.5
    assert "clamped" in decision.rationale


def test_baseline_ignores_occupancy_but_the_guard_does_not():
    """The two controllers differ exactly where the savings come from."""
    empty_building = BuildingState(
        step=0,
        sim_time=datetime(2024, 7, 15, 12, 0),  # midday, scheduled occupied
        zone_temp_c=26.0,
        outdoor_temp_c=30.0,
        occupancy=0.0,  # but nobody is actually there
        hvac_mode="off",
        heating_sp_c=21.0,
        cooling_sp_c=24.0,
        lighting_level=1.0,
        ventilation_ach=2.0,
        power_kw=3.0,
        co2_ppm=500.0,
        relative_humidity=0.5,
        air_speed_ms=0.1,
    )

    baseline = BaselineScheduler().decide(empty_building, [empty_building])
    guard = ReactiveGuard().decide(empty_building, [empty_building])

    assert baseline.action.lighting_level == 1.0
    assert guard.action.lighting_level == 0.0
    assert guard.action.ventilation_ach < baseline.action.ventilation_ach


def test_reactive_control_beats_the_fixed_schedule(settings, scenario):
    """The milestone's claim, end to end: less energy, no more discomfort."""
    _, baseline = _run(settings, scenario, "baseline")
    _, guarded = _run(settings, scenario, "rule")

    assert guarded.total_kwh < baseline.total_kwh
    assert guarded.comfort_violations <= baseline.comfort_violations


def test_identical_runs_produce_identical_results(settings, scenario):
    """Two runs of one configuration agree, so a comparison isolates the controller."""
    _, first = _run(settings, scenario, "rule")
    _, second = _run(settings, scenario, "rule")

    assert first.total_kwh == pytest.approx(second.total_kwh)
    assert first.peak_kw == pytest.approx(second.peak_kw)
    assert first.comfort_violations == second.comfort_violations
