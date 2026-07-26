"""Toy simulator tests: energy balance, set-point tracking, determinism across
identical runs (the property the savings comparison depends on).
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.sim.base import HVAC_COOLING, HVAC_HEATING, HVAC_OFF, ControlAction
from app.sim.toy import ToySimulator
from app.sim.weather import SyntheticWeather

START = datetime(2024, 7, 15)
IDLE = ControlAction(
    heating_sp_c=10.0, cooling_sp_c=40.0, lighting_level=0.0, ventilation_ach=0.0
)


def _simulator(horizon_steps: int = 96, **overrides) -> ToySimulator:
    """A simulator on a mild synthetic week, with parameters overridden per test."""
    weather = SyntheticWeather(
        start=START,
        timestep_seconds=900,
        mean_temp_c=overrides.pop("mean_temp_c", 20.0),
        daily_swing_c=overrides.pop("daily_swing_c", 8.0),
        peak_solar_w_m2=overrides.pop("peak_solar_w_m2", 0.0),
    )
    return ToySimulator(
        weather=weather, horizon_steps=horizon_steps, timestep_seconds=900, **overrides
    )


def test_reset_returns_the_initial_state():
    """Step 0 reports the configured starting temperature and no plant running."""
    state = _simulator(initial_zone_temp_c=22.0).reset()
    assert state.step == 0
    assert state.zone_temp_c == pytest.approx(22.0)
    assert state.hvac_mode == HVAC_OFF
    assert state.sim_time == START


def test_zone_drifts_towards_outdoor_temperature_when_idle():
    """With no plant and no gains, the zone can only relax towards ambient.

    Internal gains are stripped out deliberately: with them present the zone
    settles at ambient *plus* gains over the envelope conductance, which is a
    different property and is covered by the lighting test.
    """
    sim = _simulator(
        horizon_steps=200,
        initial_zone_temp_c=30.0,
        mean_temp_c=10.0,
        daily_swing_c=0.0,
        design_occupancy=0,
        base_plug_load_kw=0.0,
        occupied_plug_load_kw=0.0,
        infiltration_ach=0.0,
    )
    state = sim.reset()
    for _ in range(200):
        state = sim.step(IDLE)
    assert 10.0 < state.zone_temp_c < 30.0
    assert state.zone_temp_c == pytest.approx(10.0, abs=1.0)


def test_heating_holds_the_set_point_against_a_cold_outdoors():
    """The plant reaches and then holds the heating set-point."""
    sim = _simulator(initial_zone_temp_c=15.0, mean_temp_c=0.0, daily_swing_c=0.0)
    action = ControlAction(
        heating_sp_c=21.0, cooling_sp_c=25.0, lighting_level=0.0, ventilation_ach=0.5
    )
    state = sim.reset()
    for _ in range(48):
        state = sim.step(action)
    assert state.zone_temp_c == pytest.approx(21.0, abs=0.1)
    assert state.hvac_mode == HVAC_HEATING


def test_cooling_holds_the_set_point_against_a_hot_outdoors():
    """The same, in the other direction."""
    sim = _simulator(initial_zone_temp_c=30.0, mean_temp_c=35.0, daily_swing_c=0.0)
    action = ControlAction(
        heating_sp_c=20.0, cooling_sp_c=24.0, lighting_level=0.0, ventilation_ach=0.5
    )
    state = sim.reset()
    for _ in range(48):
        state = sim.step(action)
    assert state.zone_temp_c == pytest.approx(24.0, abs=0.1)
    assert state.hvac_mode == HVAC_COOLING


def test_plant_idles_inside_the_dead_band():
    """A zone already between the set-points draws no HVAC power."""
    sim = _simulator(initial_zone_temp_c=22.0, mean_temp_c=22.0, daily_swing_c=0.0)
    action = ControlAction(
        heating_sp_c=18.0, cooling_sp_c=26.0, lighting_level=0.0, ventilation_ach=0.0
    )
    sim.reset()
    state = sim.step(action)
    assert state.hvac_mode == HVAC_OFF


def test_a_wider_dead_band_uses_less_energy():
    """The premise of the whole savings argument, stated as a test."""
    def energy_for(heating_sp: float, cooling_sp: float) -> float:
        sim = _simulator(
            horizon_steps=192, initial_zone_temp_c=24.0, mean_temp_c=32.0
        )
        action = ControlAction(
            heating_sp_c=heating_sp,
            cooling_sp_c=cooling_sp,
            lighting_level=0.0,
            ventilation_ach=1.0,
        )
        sim.reset()
        return sum(sim.step(action).power_kw for _ in range(192))

    assert energy_for(21.0, 26.0) < energy_for(21.0, 24.0)


def test_hvac_power_is_bounded_by_plant_capacity():
    """An impossible set-point saturates the plant instead of inventing capacity."""
    sim = _simulator(
        initial_zone_temp_c=0.0,
        mean_temp_c=-20.0,
        daily_swing_c=0.0,
        hvac_capacity_kw=10.0,
        hvac_cop=2.0,
        base_plug_load_kw=0.0,
        occupied_plug_load_kw=0.0,
        fan_power_kw_per_ach=0.0,
    )
    action = ControlAction(
        heating_sp_c=30.0, cooling_sp_c=35.0, lighting_level=0.0, ventilation_ach=0.0
    )
    sim.reset()
    state = sim.step(action)
    assert state.power_kw == pytest.approx(10.0 / 2.0, abs=1e-6)


def test_co2_rises_with_occupancy_and_falls_with_ventilation():
    """Air quality responds to both the load and the airflow."""
    sim = _simulator(horizon_steps=200, mean_temp_c=20.0)
    starved = ControlAction(
        heating_sp_c=10.0, cooling_sp_c=40.0, lighting_level=0.0, ventilation_ach=0.0
    )
    sim.reset()
    for _ in range(40):  # steps 1-40 span the 08:00 occupancy start
        state = sim.step(starved)
    occupied_co2 = state.co2_ppm
    assert occupied_co2 > 500.0

    flushed = ControlAction(
        heating_sp_c=10.0, cooling_sp_c=40.0, lighting_level=0.0, ventilation_ach=6.0
    )
    for _ in range(20):
        state = sim.step(flushed)
    assert state.co2_ppm < occupied_co2


def test_lighting_adds_both_power_and_heat():
    """Lights are an electrical load and an internal gain at the same time."""
    def run(level: float) -> tuple[float, float]:
        sim = _simulator(initial_zone_temp_c=20.0, mean_temp_c=20.0, daily_swing_c=0.0)
        action = ControlAction(
            heating_sp_c=5.0,
            cooling_sp_c=45.0,
            lighting_level=level,
            ventilation_ach=0.0,
        )
        sim.reset()
        state = sim.step(action)
        return state.power_kw, state.zone_temp_c

    dark_power, dark_temp = run(0.0)
    lit_power, lit_temp = run(1.0)
    assert lit_power > dark_power
    assert lit_temp > dark_temp


def test_runs_are_deterministic():
    """Two identical runs must agree exactly, or no comparison means anything."""
    def trace() -> list[tuple[float, float]]:
        sim = _simulator(horizon_steps=96)
        action = ControlAction(
            heating_sp_c=20.0,
            cooling_sp_c=24.0,
            lighting_level=0.8,
            ventilation_ach=1.5,
        )
        sim.reset()
        return [
            (state.zone_temp_c, state.power_kw)
            for state in (sim.step(action) for _ in range(96))
        ]

    assert trace() == trace()


def test_horizon_is_enforced():
    """Stepping past the horizon is a programming error, not a silent extension."""
    sim = _simulator(horizon_steps=2)
    sim.reset()
    sim.step(IDLE)
    sim.step(IDLE)
    with pytest.raises(RuntimeError):
        sim.step(IDLE)


def test_closed_simulator_refuses_to_step():
    """Releasing resources means the simulator is genuinely done."""
    sim = _simulator()
    sim.reset()
    sim.close()
    sim.close()  # idempotent
    with pytest.raises(RuntimeError):
        sim.step(IDLE)


def test_inverted_set_points_are_rejected():
    """Heating above cooling would ask the plant to fight itself."""
    sim = _simulator()
    sim.reset()
    with pytest.raises(ValueError):
        sim.step(
            ControlAction(
                heating_sp_c=26.0,
                cooling_sp_c=20.0,
                lighting_level=0.0,
                ventilation_ach=1.0,
            )
        )


def test_out_of_range_actuator_commands_are_rejected():
    """The simulator validates its own inputs rather than trusting the controller."""
    sim = _simulator()
    sim.reset()
    with pytest.raises(ValueError):
        sim.step(
            ControlAction(
                heating_sp_c=20.0,
                cooling_sp_c=24.0,
                lighting_level=1.5,
                ventilation_ach=1.0,
            )
        )
    with pytest.raises(ValueError):
        sim.step(
            ControlAction(
                heating_sp_c=20.0,
                cooling_sp_c=24.0,
                lighting_level=0.5,
                ventilation_ach=-1.0,
            )
        )
