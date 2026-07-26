"""EnergyPlus integration tests against a fake `pyenergyplus`.

EnergyPlus is a 500 MB native install that cannot be assumed present in CI or on
a reviewer's machine, but the part of this integration most likely to be wrong is
not EnergyPlus — it is our thread bridge: the depth-1 queue handover between the
simulation thread and the runner, warmup suppression, handle resolution, and
whether a set-point written in the callback actually reaches the actuator.

All of that is testable with a stand-in that drives the real callbacks the real
runtime API would, which is what this module does. It exercises the genuine
`EnergyPlusSimulator` — only `pyenergyplus` itself is substituted.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from app.sim.base import ControlAction

IDF_BODY = "Schedule:Constant, HEATING_SETPOINT_SCH, Temperature, 21.0;\n"
WARMUP_STEPS = 3
DESIGN_DAY_STEPS = 2  # design-day timesteps the fake emits before the weather period


class FakeExchange:
    """The `api.exchange` surface `EnergyPlusSimulator` actually uses."""

    def __init__(self, sim) -> None:
        self._sim = sim
        self.actuator_writes: list[dict[str, float]] = []
        self._handles: dict[int, str] = {}
        self._next_handle = 1
        self.requested_variables: list[tuple[str, str]] = []
        self.missing: set[str] = set()

    # -- warmup and readiness ------------------------------------------------

    def warmup_flag(self, state) -> int:
        return 1 if self._sim.iteration < WARMUP_STEPS else 0

    def api_data_fully_ready(self, state) -> bool:
        return True

    def kind_of_sim(self, state) -> int:
        # 3 is the weather run period. The fake reports design days first, so the
        # simulator's skip guard is exercised rather than assumed.
        return 1 if self._sim.iteration < DESIGN_DAY_STEPS else 3

    # -- handle resolution ---------------------------------------------------

    def request_variable(self, state, name, key) -> None:
        self.requested_variables.append((name, key))

    def _handle_for(self, key: str) -> int:
        if key in self.missing:
            return -1
        handle = self._next_handle
        self._next_handle += 1
        self._handles[handle] = key
        return handle

    def get_variable_handle(self, state, name, key) -> int:
        return self._handle_for(name)

    def get_actuator_handle(self, state, component, control, key) -> int:
        return self._handle_for(key)

    # -- sensors -------------------------------------------------------------

    def get_variable_value(self, state, handle) -> float:
        return {
            "Zone Mean Air Temperature": self._sim.zone_temp_c,
            "Site Outdoor Air Drybulb Temperature": 30.0,
            "Zone People Occupant Count": 10.0,
            "Facility Total Electricity Demand Rate": 6400.0,
            "Zone Air CO2 Concentration": 780.0,
            "Zone Air Relative Humidity": 45.0,
            "Zone Ideal Loads Supply Air Total Heating Rate": self._sim.heating_w,
            "Zone Ideal Loads Supply Air Total Cooling Rate": self._sim.cooling_w,
        }.get(self._handles.get(handle, ""), 0.0)

    # -- actuators -----------------------------------------------------------

    def set_actuator_value(self, state, handle, value) -> None:
        key = self._handles.get(handle, "")
        self._sim.pending_writes[key] = value

    # -- clock ---------------------------------------------------------------

    def year(self, state) -> int:
        return 2024

    def month(self, state) -> int:
        return 7

    def day_of_month(self, state) -> int:
        return 15

    def hour(self, state) -> int:
        return (self._sim.published * 15 // 60) % 24

    def minutes(self, state) -> float:
        return float((self._sim.published * 15) % 60)


class FakeRuntime:
    """Registers callbacks and then drives them, as `run_energyplus` does."""

    def __init__(self, sim) -> None:
        self._sim = sim
        self.new_environment = None
        self.timestep = None
        self.run_args: list[str] | None = None

    def callback_begin_new_environment(self, state, handler) -> None:
        self.new_environment = handler

    def callback_begin_system_timestep_before_predictor(self, state, handler) -> None:
        self.timestep = handler

    def run_energyplus(self, state, args) -> int:
        """Fire the callbacks on this thread, exactly as EnergyPlus would."""
        self.run_args = args
        if self._sim.fail_immediately:
            raise RuntimeError("EnergyPlus exited with a fatal error")

        self.new_environment(state)
        for _ in range(WARMUP_STEPS + DESIGN_DAY_STEPS + self._sim.total_timesteps):
            self.timestep(state)
            self._sim.iteration += 1
            if self._sim.stopped:
                break
        return 0


class FakeStateManager:
    def __init__(self) -> None:
        self.deleted: list[int] = []

    def new_state(self) -> int:
        return 42

    def delete_state(self, handle) -> None:
        self.deleted.append(handle)


class FakeEnergyPlus:
    """A minimal EnergyPlus stand-in whose zone tracks the commanded set-points."""

    def __init__(self, total_timesteps: int = 8, fail_immediately: bool = False) -> None:
        self.iteration = 0
        self.published = 0
        self.zone_temp_c = 26.0
        self.total_timesteps = total_timesteps
        self.fail_immediately = fail_immediately
        self.stopped = False
        self.pending_writes: dict[str, float] = {}
        self.applied: list[dict[str, float]] = []
        # The ideal-loads plant's reported thermal load, which is what the real
        # simulator derives both HVAC mode and HVAC energy from.
        self.heating_w = 0.0
        self.cooling_w = 0.0

        self.exchange = FakeExchange(self)
        self.runtime = FakeRuntime(self)
        self.state_manager = FakeStateManager()

    def apply(self) -> None:
        """Move the zone toward the commanded band, as the physics would."""
        if not self.pending_writes:
            return
        self.applied.append(dict(self.pending_writes))
        heating = self.pending_writes.get("HEATING_SETPOINT_SCH", 21.0)
        cooling = self.pending_writes.get("COOLING_SETPOINT_SCH", 24.0)
        self.heating_w = 0.0
        self.cooling_w = 0.0
        if self.zone_temp_c > cooling:
            self.cooling_w = 3200.0
            self.zone_temp_c -= min(1.0, self.zone_temp_c - cooling)
        elif self.zone_temp_c < heating:
            self.heating_w = 3200.0
            self.zone_temp_c += min(1.0, heating - self.zone_temp_c)
        self.published += 1
        self.pending_writes.clear()


@pytest.fixture
def model(tmp_path) -> tuple[Path, Path]:
    """An idf and epw on disk; only their existence is checked."""
    idf = tmp_path / "small_office.idf"
    epw = tmp_path / "weather.epw"
    idf.write_text(IDF_BODY)
    epw.write_text("LOCATION,Test,,,,,,,0,0,0\n" * 8)
    return idf, epw


@pytest.fixture
def install(monkeypatch, tmp_path):
    """A fake EnergyPlus installation, injected as an importable module."""
    directory = tmp_path / "EnergyPlus-25-1-0"
    (directory / "pyenergyplus").mkdir(parents=True)

    def make(**kwargs):
        fake = FakeEnergyPlus(**kwargs)
        module = types.ModuleType("pyenergyplus.api")
        module.EnergyPlusAPI = lambda: fake
        package = types.ModuleType("pyenergyplus")
        package.api = module
        monkeypatch.setitem(sys.modules, "pyenergyplus", package)
        monkeypatch.setitem(sys.modules, "pyenergyplus.api", module)
        return fake, directory

    return make


def build(model, install, horizon_steps=4, **kwargs):
    """Construct the real simulator over the fake install."""
    from app.sim.energyplus import EnergyPlusSimulator

    idf, epw = model
    fake, directory = install(**kwargs)

    simulator = EnergyPlusSimulator(
        idf_path=idf,
        epw_path=epw,
        output_dir=idf.parent / "out",
        energyplus_dir=directory,
        horizon_steps=horizon_steps,
        timestep_seconds=900,
    )
    # Wrap the callback so the fake applies actuator writes after each timestep,
    # which is what makes the loop genuinely closed rather than write-only.
    def wrap(handler):
        def wrapped(state):
            handler(state)
            fake.apply()

        return wrapped

    simulator._on_timestep = wrap(simulator._on_timestep)  # noqa: SLF001
    return simulator, fake


# -- availability and configuration -----------------------------------------


def test_a_missing_installation_names_the_setting_to_fix(tmp_path):
    """The commonest failure on a new machine, and it must be self-explaining."""
    from app.sim.energyplus import (
        EnergyPlusNotAvailable,
        _import_pyenergyplus,
        energyplus_available,
    )

    with pytest.raises(EnergyPlusNotAvailable) as excinfo:
        _import_pyenergyplus(tmp_path / "nowhere")

    message = str(excinfo.value)
    assert "ENERGYPLUS_DIR" in message
    assert "pyenergyplus" in message
    assert "--simulator=toy" in message
    assert energyplus_available(tmp_path / "nowhere") is False


def test_a_missing_model_file_fails_before_anything_starts(tmp_path, model):
    """Cheaper to fail here than after EnergyPlus has spawned a thread."""
    from app.sim.energyplus import EnergyPlusSimulator

    idf, epw = model
    with pytest.raises(FileNotFoundError, match="idf file not found"):
        EnergyPlusSimulator(
            idf_path=tmp_path / "absent.idf",
            epw_path=epw,
            output_dir=tmp_path,
            energyplus_dir=tmp_path,
            horizon_steps=4,
        )
    with pytest.raises(FileNotFoundError, match="epw file not found"):
        EnergyPlusSimulator(
            idf_path=idf,
            epw_path=tmp_path / "absent.epw",
            output_dir=tmp_path,
            energyplus_dir=tmp_path,
            horizon_steps=4,
        )


def test_construction_arguments_are_validated(model, tmp_path):
    from app.sim.energyplus import EnergyPlusSimulator

    idf, epw = model
    with pytest.raises(ValueError, match="horizon_steps must be positive"):
        EnergyPlusSimulator(
            idf_path=idf, epw_path=epw, output_dir=tmp_path,
            energyplus_dir=tmp_path, horizon_steps=0,
        )


# -- the closed loop ---------------------------------------------------------


def test_reset_returns_the_first_post_warmup_state(model, install):
    """Warmup must not be logged or controlled, or the energy comparison is polluted."""
    simulator, fake = build(model, install)
    try:
        state = simulator.reset()
    finally:
        simulator.close()

    assert state.step == 0
    assert state.zone_temp_c == 26.0
    assert state.outdoor_temp_c == 30.0
    assert state.occupancy == 0.5           # 10 of 20 design occupants
    assert state.power_kw == 6.4            # 6400 W
    assert state.co2_ppm == 780.0
    assert state.relative_humidity == 0.45
    # Warmup fired WARMUP_STEPS times and published nothing.
    assert fake.iteration >= WARMUP_STEPS


def test_set_points_reach_the_actuators_of_the_running_instance(model, install):
    """Forward injection: the requirement this whole module exists to satisfy."""
    simulator, fake = build(model, install)
    try:
        simulator.reset()
        simulator.step(
            ControlAction(
                heating_sp_c=19.5,
                cooling_sp_c=23.5,
                lighting_level=0.4,
                ventilation_ach=1.25,
            )
        )
    finally:
        simulator.close()

    assert fake.applied, "no actuator write reached the running instance"
    written = fake.applied[-1]
    assert written["HEATING_SETPOINT_SCH"] == 19.5
    assert written["COOLING_SETPOINT_SCH"] == 23.5
    assert written["LIGHTING_SCH"] == 0.4
    assert written["VENTILATION_SCH"] == 1.25


def test_the_loop_closes_the_zone_responds_and_the_state_comes_back(model, install):
    """EnergyPlus -> state -> action -> EnergyPlus, for several steps."""
    simulator, fake = build(model, install, horizon_steps=4)
    temperatures = []
    try:
        state = simulator.reset()
        temperatures.append(state.zone_temp_c)
        for _ in range(4):
            state = simulator.step(
                ControlAction(
                    heating_sp_c=20.0,
                    cooling_sp_c=22.0,
                    lighting_level=0.5,
                    ventilation_ach=1.0,
                )
            )
            temperatures.append(state.zone_temp_c)
    finally:
        simulator.close()

    # The zone was above the commanded cooling set-point and was driven down by
    # it, which is only possible if the action actually reached the actuator and
    # the resulting state came back through the queue.
    assert temperatures[0] == 26.0
    assert temperatures[-1] < temperatures[0]
    assert temperatures == sorted(temperatures, reverse=True)
    assert len(fake.applied) >= 4


def test_steps_are_numbered_and_carry_the_simulation_clock(model, install):
    simulator, _ = build(model, install, horizon_steps=3)
    try:
        simulator.reset()
        first = simulator.step(_action())
        second = simulator.step(_action())
    finally:
        simulator.close()

    assert (first.step, second.step) == (1, 2)
    assert second.sim_time > first.sim_time
    assert first.sim_time.year == 2024 and first.sim_time.month == 7


def test_hvac_mode_is_derived_from_the_commanded_band(model, install):
    simulator, _ = build(model, install, horizon_steps=3)
    try:
        simulator.reset()
        cooling = simulator.step(
            ControlAction(heating_sp_c=18.0, cooling_sp_c=20.0,
                          lighting_level=0.0, ventilation_ach=0.5)
        )
        heating = simulator.step(
            ControlAction(heating_sp_c=30.0, cooling_sp_c=32.0,
                          lighting_level=0.0, ventilation_ach=0.5)
        )
    finally:
        simulator.close()

    assert cooling.hvac_mode == "cooling"
    assert heating.hvac_mode == "heating"


def test_an_inverted_set_point_pair_is_refused(model, install):
    """The plant could not accept it, so it never reaches the callback."""
    simulator, _ = build(model, install)
    try:
        simulator.reset()
        with pytest.raises(ValueError, match="exceeds cooling"):
            simulator.step(
                ControlAction(heating_sp_c=26.0, cooling_sp_c=22.0,
                              lighting_level=0.0, ventilation_ach=1.0)
            )
    finally:
        simulator.close()


def test_stepping_past_the_horizon_is_refused(model, install):
    simulator, _ = build(model, install, horizon_steps=2)
    try:
        simulator.reset()
        simulator.step(_action())
        simulator.step(_action())
        with pytest.raises(RuntimeError, match="horizon of 2 steps already reached"):
            simulator.step(_action())
    finally:
        simulator.close()


def test_step_before_reset_is_refused(model, install):
    simulator, _ = build(model, install)
    with pytest.raises(RuntimeError, match="before reset"):
        simulator.step(_action())
    simulator.close()


def test_close_is_idempotent_and_releases_the_state(model, install):
    """A second close must not raise, and no EnergyPlus state may be left behind."""
    simulator, fake = build(model, install)
    simulator.reset()
    simulator.close()
    simulator.close()

    assert fake.state_manager.deleted == [42]
    with pytest.raises(RuntimeError, match="closed simulator"):
        simulator.step(_action())


def test_close_mid_run_unwinds_the_worker_thread(model, install):
    """No orphaned EnergyPlus process, even when a run is abandoned early."""
    simulator, _ = build(model, install, horizon_steps=1000)
    simulator.reset()
    simulator.step(_action())
    simulator.close()

    assert simulator._worker is None  # noqa: SLF001 - asserting the thread joined


def test_a_model_missing_required_handles_says_which(model, install):
    """A wrong .idf is a configuration error and must name the objects it lacks."""
    simulator, fake = build(model, install)
    fake.exchange.missing = {"HEATING_SETPOINT_SCH", "COOLING_SETPOINT_SCH"}

    with pytest.raises(RuntimeError, match="missing required handles"):
        simulator.reset()
    simulator.close()


def test_an_energyplus_failure_is_reported_to_the_caller(model, install):
    """The worker thread's exception must not vanish silently."""
    simulator, _ = build(model, install, fail_immediately=True)

    with pytest.raises(RuntimeError, match="EnergyPlus failed"):
        simulator.reset()
    simulator.close()


def test_a_run_that_ends_early_points_at_the_err_file(model, install):
    """A short simulation is a modelling problem; say where to look."""
    simulator, _ = build(model, install, horizon_steps=50)
    try:
        simulator.reset()
        with pytest.raises(RuntimeError, match="eplusout.err"):
            for _ in range(50):
                simulator.step(_action())
    finally:
        simulator.close()


def test_sensors_and_actuators_are_requested_by_name(model, install):
    """Output variables must be requested before EnergyPlus will report them."""
    from app.sim.energyplus import SENSOR_VARIABLES

    simulator, fake = build(model, install)
    try:
        simulator.reset()
    finally:
        simulator.close()

    assert set(fake.exchange.requested_variables) == set(SENSOR_VARIABLES)


def test_the_run_is_launched_with_the_weather_file_and_output_directory(model, install):
    simulator, fake = build(model, install)
    try:
        simulator.reset()
    finally:
        simulator.close()

    args = fake.runtime.run_args
    assert "-w" in args and "-d" in args
    assert args[-1].endswith("small_office.idf")


def test_collect_errors_reads_the_err_file(model, install, tmp_path):
    """This is what `get_simulation_errors` surfaces to the agent."""
    simulator, _ = build(model, install)
    output_dir = tmp_path / "out"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "eplusout.err").write_text(
        "   ** Warning ** Weather file location differs\n"
        "   ** Severe  ** Node connection error\n"
    )

    errors = simulator.collect_errors()
    simulator.close()

    assert len(errors) == 2
    assert any("Severe" in line for line in errors)


def test_collect_errors_is_empty_before_a_run_writes_one(model, install):
    simulator, _ = build(model, install)
    assert simulator.collect_errors() == []
    simulator.close()


def _action() -> ControlAction:
    return ControlAction(
        heating_sp_c=21.0, cooling_sp_c=24.0, lighting_level=0.5, ventilation_ach=1.0
    )
