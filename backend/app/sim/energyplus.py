"""EnergyPlus simulator driven through the runtime Python API.

Set-points are injected into the *active* EnergyPlus instance via actuators
inside a per-timestep callback — no file rewriting and no restart between
decisions.

Two details shape this module:

* `pyenergyplus` is not a PyPI package. It ships inside the EnergyPlus
  installation, so the install directory from `Settings.energyplus_dir` is
  placed on `sys.path` before import.
* `run_energyplus()` blocks for the entire simulation while callbacks fire on
  the simulation thread. EnergyPlus therefore runs on a worker thread, bridged
  to the caller by a pair of bounded queues: the callback publishes a
  `BuildingState` and blocks awaiting a `ControlAction`. The runner sees an
  ordinary step-wise simulator and control stays synchronous with the physics.
"""

from __future__ import annotations

import queue
import re
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path

from app.sim.base import (
    HVAC_COOLING,
    HVAC_HEATING,
    HVAC_OFF,
    BuildingState,
    ControlAction,
)

SENSOR_VARIABLES: tuple[tuple[str, str], ...] = (
    ("Zone Mean Air Temperature", "ZONE ONE"),
    ("Site Outdoor Air Drybulb Temperature", "Environment"),
    ("Zone People Occupant Count", "ZONE ONE"),
    ("Facility Total Electricity Demand Rate", "Whole Building"),
    ("Zone Air CO2 Concentration", "ZONE ONE"),
    ("Zone Air Relative Humidity", "ZONE ONE"),
    # The ideal-loads system is "purchased air": its thermal load never appears in
    # facility electricity, so reading only that meter would measure lighting and
    # plug load and call it the building. These two are the HVAC load itself.
    ("Zone Ideal Loads Supply Air Total Heating Rate", "ZONE1AIR"),
    ("Zone Ideal Loads Supply Air Total Cooling Rate", "ZONE1AIR"),
)

ACTUATORS: tuple[tuple[str, str, str], ...] = (
    ("Schedule:Constant", "Schedule Value", "HEATING_SETPOINT_SCH"),
    ("Schedule:Constant", "Schedule Value", "COOLING_SETPOINT_SCH"),
    ("Schedule:Constant", "Schedule Value", "LIGHTING_SCH"),
    ("Schedule:Constant", "Schedule Value", "VENTILATION_SCH"),
)

# Design occupant count for the reference zone, used to normalise the People
# Occupant Count sensor into the 0..1 fraction the rest of the system speaks.
DESIGN_OCCUPANCY = 20.0

# Coefficient of performance used to convert the ideal-loads thermal load into the
# electrical equivalent. Ideal loads deliberately models no plant, which is what
# makes the comparison measure the control strategy rather than one chiller's
# efficiency curve — but a kWh figure still needs a conversion, and this is the
# commissioning constant to change if a real plant is modelled later. It matches
# ToySimulator's hvac_cop so the two simulators report comparable energy.
HVAC_COP = 3.2

# The queues are depth-1 on purpose: the callback must block until the runner has
# supplied an action for the state it just published. Any deeper and control
# would drift out of step with the physics, which is the one thing the runtime
# API is being used to avoid.
QUEUE_DEPTH = 1
HANDOVER_TIMEOUT_SECONDS = 600.0

# EnergyPlus KindOfSim: 1 design day, 2 run-period design, 3 run-period weather.
# Sizing design days run *before* the weather period and are not real days — they
# carry design-day day types, so an occupancy schedule reports nobody present. A
# horizon consumed by them would control two fictional days and report a building
# that was empty the whole time.
KIND_OF_SIM_RUN_PERIOD_WEATHER = 3
OUTDOOR_CO2_PPM = 420.0
DEFAULT_AIR_SPEED_MS = 0.1


class EnergyPlusNotAvailable(RuntimeError):
    """The EnergyPlus installation could not be found or imported."""


class EnergyPlusSimulator:
    """Live EnergyPlus run exposed through the `Simulator` protocol."""

    def __init__(
        self,
        idf_path: Path,
        epw_path: Path,
        output_dir: Path,
        energyplus_dir: Path,
        horizon_steps: int,
        timestep_seconds: int = 900,
        calendar_year: int | None = None,
    ) -> None:
        """Configure the run. Nothing is started until `reset()`.

        Args:
            idf_path: Building model to simulate.
            epw_path: Weather file for the run.
            output_dir: Where EnergyPlus writes its output, including `.err`.
            energyplus_dir: Installation directory containing `pyenergyplus`.
            horizon_steps: Steps to run before the simulator reports completion.
            timestep_seconds: Simulation timestep, matched to the `.idf`.
            calendar_year: Year to stamp on reported timestamps. A TMY weather
                file carries real historical years, so EnergyPlus would report
                1988 for a scenario dated 2024 — and then the weekday shown on
                the dashboard would disagree with the occupancy schedule
                EnergyPlus actually ran. The month, day and time always come from
                EnergyPlus; only the year is overridden.
        """
        if horizon_steps <= 0:
            raise ValueError(f"horizon_steps must be positive, got {horizon_steps}")
        if timestep_seconds <= 0:
            raise ValueError(
                f"timestep_seconds must be positive, got {timestep_seconds}"
            )
        for label, path in (("idf", idf_path), ("epw", epw_path)):
            if not Path(path).is_file():
                raise FileNotFoundError(f"{label} file not found: {path}")

        self.timestep_seconds = timestep_seconds
        self.horizon_steps = horizon_steps

        self._idf_path = Path(idf_path)
        self._epw_path = Path(epw_path)
        self._output_dir = Path(output_dir)
        self._energyplus_dir = Path(energyplus_dir)
        self._calendar_year = calendar_year

        self._states: queue.Queue = queue.Queue(maxsize=QUEUE_DEPTH)
        self._actions: queue.Queue = queue.Queue(maxsize=QUEUE_DEPTH)
        self._worker: threading.Thread | None = None
        self._stop = threading.Event()
        self._finished = threading.Event()

        self._api = None
        self._state_handle = None
        self._handles: dict[str, int] = {}
        self._handles_ready = False
        self._step = 0
        self._failure: BaseException | None = None
        self._last_action = ControlAction(
            heating_sp_c=21.0, cooling_sp_c=24.0, lighting_level=1.0, ventilation_ach=2.0
        )
        self._closed = False

    # -- Simulator protocol -------------------------------------------------

    def reset(self) -> BuildingState:
        """Start EnergyPlus on a worker thread and return the first post-warmup state."""
        if self._worker is not None:
            raise RuntimeError("reset() called on an already-running simulator")

        api_module = _import_pyenergyplus(self._energyplus_dir)
        self._api = api_module.EnergyPlusAPI()
        self._state_handle = self._api.state_manager.new_state()
        self._output_dir.mkdir(parents=True, exist_ok=True)

        exchange = self._api.exchange
        runtime = self._api.runtime

        # Output variables must be requested before the run starts. Requesting
        # them inside the timestep callback is too late: EnergyPlus has already
        # fixed its output list by then, every handle comes back -1, and the
        # sensors silently read as zero instead of failing.
        for name, key in SENSOR_VARIABLES:
            exchange.request_variable(self._state_handle, name, key)

        runtime.callback_begin_new_environment(self._state_handle, self._on_new_environment)
        # Sensors are read and actuators written in the same callback, before the
        # zone predictor runs, so a set-point written now governs the timestep
        # that is about to be solved rather than the one after it.
        runtime.callback_begin_system_timestep_before_predictor(
            self._state_handle, self._on_timestep
        )

        self._worker = threading.Thread(
            target=self._run, name="energyplus", daemon=True
        )
        self._worker.start()

        self._step = 0
        return self._await_state()

    def step(self, action: ControlAction) -> BuildingState:
        """Release the callback with `action` and wait for the next published state."""
        if self._closed:
            raise RuntimeError("step() called on a closed simulator")
        if self._worker is None:
            raise RuntimeError("step() called before reset()")
        if self._step >= self.horizon_steps:
            raise RuntimeError(
                f"simulation horizon of {self.horizon_steps} steps already reached"
            )
        if action.heating_sp_c > action.cooling_sp_c:
            raise ValueError(
                f"heating set-point {action.heating_sp_c} exceeds cooling "
                f"set-point {action.cooling_sp_c}"
            )

        self._last_action = action
        self._actions.put(action, timeout=HANDOVER_TIMEOUT_SECONDS)
        self._step += 1
        return self._await_state()

    def close(self) -> None:
        """Stop the run, join the worker thread and release API handles.

        Safe to call more than once, and safe to call mid-run: the stop flag makes
        the callback stop blocking for an action, so the worker unwinds instead of
        leaving an orphaned EnergyPlus process behind.
        """
        if self._closed:
            return
        self._closed = True
        self._stop.set()

        # Drain so a callback already blocked on put() can proceed and see the flag.
        _drain(self._states)
        try:
            self._actions.put_nowait(self._last_action)
        except queue.Full:
            pass

        if self._worker is not None:
            self._worker.join(timeout=30.0)
            self._worker = None
        if self._api is not None and self._state_handle is not None:
            self._api.state_manager.delete_state(self._state_handle)
        self._api = None
        self._state_handle = None

    def collect_errors(self) -> list[str]:
        """Parse the run's `.err` file so the agent can react to runtime problems.

        Returned raw, one entry per line; `utils.logsummary.compact_errors` is what
        turns them into something a prompt can afford.
        """
        err_path = self._output_dir / "eplusout.err"
        if not err_path.is_file():
            return []
        return err_path.read_text(encoding="utf-8", errors="replace").splitlines()

    # -- worker thread ------------------------------------------------------

    def _run(self) -> None:
        """Run EnergyPlus to completion on the worker thread."""
        try:
            self._api.runtime.run_energyplus(
                self._state_handle,
                [
                    "-d",
                    str(self._output_dir),
                    "-w",
                    str(self._epw_path),
                    str(self._idf_path),
                ],
            )
        except BaseException as exc:  # surfaced to the caller through _await_state
            self._failure = exc
        finally:
            self._finished.set()
            # Unblock a runner waiting for a state that will never arrive.
            try:
                self._states.put_nowait(None)
            except queue.Full:
                pass

    def _on_new_environment(self, state) -> None:
        """Handles are only resolvable once a new environment begins."""
        self._handles_ready = False

    def _on_timestep(self, state) -> None:
        """Publish sensors, block for an action, then write actuators.

        Executes on the simulation thread. Warmup is suppressed so telemetry
        begins at the first real timestep and the energy comparison is not
        polluted by EnergyPlus settling the model.
        """
        exchange = self._api.exchange
        if exchange.warmup_flag(state):
            return
        if not exchange.api_data_fully_ready(state):
            return
        # Only control the weather run period; skip sizing design days.
        if exchange.kind_of_sim(state) != KIND_OF_SIM_RUN_PERIOD_WEATHER:
            return
        if self._stop.is_set() or self._step > self.horizon_steps:
            return
        if not self._handles_ready:
            self._resolve_handles(state)
            if not self._handles_ready:
                return

        published = self._read_state(state)
        try:
            self._states.put(published, timeout=HANDOVER_TIMEOUT_SECONDS)
        except queue.Full:
            return

        if self._stop.is_set():
            return
        try:
            action = self._actions.get(timeout=HANDOVER_TIMEOUT_SECONDS)
        except queue.Empty:
            return
        if self._stop.is_set():
            return

        self._write_actuators(state, action)

    def _resolve_handles(self, state) -> None:
        """Look up every sensor and actuator handle once per environment."""
        exchange = self._api.exchange
        handles: dict[str, int] = {}

        for name, key in SENSOR_VARIABLES:
            handles[name] = exchange.get_variable_handle(state, name, key)

        for component, control, key in ACTUATORS:
            handles[key] = exchange.get_actuator_handle(state, component, control, key)

        # A missing handle means the .idf does not carry the object this module
        # expects. Zone temperature and the two set-point schedules are the
        # minimum for closed-loop control; the rest degrade to a default.
        required = (
            "Zone Mean Air Temperature",
            "HEATING_SETPOINT_SCH",
            "COOLING_SETPOINT_SCH",
        )
        missing = [name for name in required if handles.get(name, -1) < 0]
        if missing:
            raise RuntimeError(
                "EnergyPlus model is missing required handles: "
                f"{', '.join(missing)}. Check that {self._idf_path.name} defines "
                "ZONE ONE and the HEATING_SETPOINT_SCH / COOLING_SETPOINT_SCH "
                "Schedule:Constant objects."
            )

        self._handles = handles
        self._handles_ready = True

    def _read_state(self, state) -> BuildingState:
        """Assemble a `BuildingState` from the exchange, filling absent sensors."""
        exchange = self._api.exchange
        read = lambda name: (  # noqa: E731 - a local alias, not a policy
            exchange.get_variable_value(state, self._handles[name])
            if self._handles.get(name, -1) >= 0
            else None
        )

        zone_temp_c = read("Zone Mean Air Temperature") or 0.0
        outdoor_temp_c = read("Site Outdoor Air Drybulb Temperature")
        occupants = read("Zone People Occupant Count")
        power_w = read("Facility Total Electricity Demand Rate")
        co2_ppm = read("Zone Air CO2 Concentration")
        humidity_pct = read("Zone Air Relative Humidity")
        heating_w = read("Zone Ideal Loads Supply Air Total Heating Rate") or 0.0
        cooling_w = read("Zone Ideal Loads Supply Air Total Cooling Rate") or 0.0

        # Facility electricity covers lighting and plug load; the HVAC thermal load
        # is separate under ideal loads and has to be added at a COP or the savings
        # figure would ignore the very thing the agent controls.
        total_power_w = (power_w or 0.0) + (heating_w + cooling_w) / HVAC_COP

        action = self._last_action
        # Reported by the plant rather than inferred from the set-points, so the
        # dashboard shows what the building actually did.
        if heating_w > 1.0:
            mode = HVAC_HEATING
        elif cooling_w > 1.0:
            mode = HVAC_COOLING
        else:
            mode = HVAC_OFF

        return BuildingState(
            step=self._step,
            sim_time=_sim_time(self._api, state, self._calendar_year),
            zone_temp_c=zone_temp_c,
            outdoor_temp_c=outdoor_temp_c if outdoor_temp_c is not None else 0.0,
            occupancy=(
                min(1.0, max(0.0, occupants / DESIGN_OCCUPANCY))
                if occupants is not None
                else 0.0
            ),
            hvac_mode=mode,
            heating_sp_c=action.heating_sp_c,
            cooling_sp_c=action.cooling_sp_c,
            lighting_level=action.lighting_level,
            ventilation_ach=action.ventilation_ach,
            power_kw=total_power_w / 1000.0,
            # A model without a CO2 balance reports outdoor air rather than zero,
            # so the guard's demand-controlled ventilation stays well defined.
            co2_ppm=co2_ppm if co2_ppm is not None else OUTDOOR_CO2_PPM,
            relative_humidity=(
                min(1.0, max(0.0, humidity_pct / 100.0))
                if humidity_pct is not None
                else 0.5
            ),
            air_speed_ms=DEFAULT_AIR_SPEED_MS,
        )

    def _write_actuators(self, state, action: ControlAction) -> None:
        """Inject the action into the running instance. This is forward injection."""
        exchange = self._api.exchange
        values = {
            "HEATING_SETPOINT_SCH": action.heating_sp_c,
            "COOLING_SETPOINT_SCH": action.cooling_sp_c,
            "LIGHTING_SCH": action.lighting_level,
            "VENTILATION_SCH": action.ventilation_ach,
        }
        for key, value in values.items():
            handle = self._handles.get(key, -1)
            if handle >= 0:
                exchange.set_actuator_value(state, handle, float(value))

    def _await_state(self) -> BuildingState:
        """Block for the next published state, or explain why none is coming."""
        try:
            published = self._states.get(timeout=HANDOVER_TIMEOUT_SECONDS)
        except queue.Empty as exc:
            raise RuntimeError(
                "EnergyPlus produced no timestep within "
                f"{HANDOVER_TIMEOUT_SECONDS:.0f}s; check "
                f"{self._output_dir / 'eplusout.err'}"
            ) from exc

        if published is None:
            if self._failure is not None:
                raise RuntimeError(
                    f"EnergyPlus failed: {type(self._failure).__name__}: "
                    f"{self._failure}"
                ) from self._failure
            raise RuntimeError(
                "EnergyPlus finished before the horizon was reached; check "
                f"{self._output_dir / 'eplusout.err'}"
            )
        return published


def write_dated_variant(
    idf_path: Path, output_dir: Path, start: datetime, days: int
) -> Path:
    """Write a copy of the model whose RunPeriod matches the scenario's dates.

    The baseline `.idf` carries whatever RunPeriod it was authored with, and
    EnergyPlus takes its calendar from that file — not from the scenario. Without
    this, a scenario declaring a July week would be simulated in January against
    the same weather file, and the "summer" savings figure would be a winter one.

    This is also deliverable 2's "modified versions generated during runtime
    evaluation": the baseline stays untouched in `models/baseline/` and the variant
    actually simulated is written to `models/generated/` and kept with the run.

    Returns the path to the variant.
    """
    if days <= 0:
        raise ValueError(f"days must be positive, got {days}")

    source = Path(idf_path).read_text(encoding="utf-8", errors="replace")
    # A couple of days of margin so the simulation cannot end before the runner's
    # horizon does, which would surface as a confusing early-finish error.
    end = start + timedelta(days=days + 2)
    block = (
        "  RunPeriod,\n"
        "    EcoLoop Run Period,      !- Name\n"
        f"    {start.month},{' ' * (24 - len(str(start.month)))}!- Begin Month\n"
        f"    {start.day},{' ' * (24 - len(str(start.day)))}!- Begin Day of Month\n"
        "    ,                        !- Begin Year\n"
        f"    {end.month},{' ' * (24 - len(str(end.month)))}!- End Month\n"
        f"    {end.day},{' ' * (24 - len(str(end.day)))}!- End Day of Month\n"
        "    ,                        !- End Year\n"
        f"    {start.strftime('%A')},{' ' * (24 - len(start.strftime('%A')))}!- Day of Week for Start Day\n"
        "    No,                      !- Use Weather File Holidays and Special Days\n"
        "    No,                      !- Use Weather File Daylight Saving Period\n"
        "    No,                      !- Apply Weekend Holiday Rule\n"
        "    Yes,                     !- Use Weather File Rain Indicators\n"
        "    Yes;                     !- Use Weather File Snow Indicators\n"
    )

    pattern = re.compile(r"^[ \t]*RunPeriod,.*?;[^\n]*\n", re.DOTALL | re.MULTILINE)
    if pattern.search(source) is None:
        raise ValueError(f"{Path(idf_path).name} contains no RunPeriod object")
    variant = pattern.sub(block, source, count=1)

    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / f"{Path(idf_path).stem}-generated.idf"
    destination.write_text(variant, encoding="utf-8")
    return destination


def _import_pyenergyplus(energyplus_dir: Path):
    """Import `pyenergyplus.api` from the installation directory.

    Not a pip package: it ships inside the EnergyPlus install, so the directory
    goes on `sys.path` first. The error names the setting to fix, because a wrong
    path here is by far the most likely way this module fails on a new machine.
    """
    directory = Path(energyplus_dir)
    if not (directory / "pyenergyplus").is_dir():
        raise EnergyPlusNotAvailable(
            f"pyenergyplus was not found in {directory}. Install EnergyPlus from "
            "https://github.com/NREL/EnergyPlus/releases and set ENERGYPLUS_DIR "
            "in .env to the installation directory (the one containing the "
            "'pyenergyplus' folder). Runs with --simulator=toy need no install."
        )
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))
    try:
        from pyenergyplus import api as api_module
    except ImportError as exc:
        raise EnergyPlusNotAvailable(
            f"pyenergyplus is present in {directory} but could not be imported: "
            f"{exc}. This usually means the EnergyPlus build does not match this "
            "Python interpreter's version or architecture."
        ) from exc
    return api_module


def energyplus_available(energyplus_dir: Path) -> bool:
    """Whether an EnergyPlus installation is usable, for `/api/health`."""
    try:
        _import_pyenergyplus(energyplus_dir)
    except EnergyPlusNotAvailable:
        return False
    return True


def _sim_time(api, state, calendar_year: int | None = None):
    """Simulated wall-clock for the current timestep, from EnergyPlus's own clock."""
    exchange = api.exchange
    year = calendar_year or int(exchange.year(state)) or 2024
    month = int(exchange.month(state))
    day = int(exchange.day_of_month(state))
    hour = int(exchange.hour(state))
    minutes = float(exchange.minutes(state))

    # EnergyPlus reports hour 0-23 with minutes 1-60, so the end of an hour
    # arrives as 60 rather than as the next hour's zero.
    base = datetime(year, max(1, month), max(1, day), hour, 0)
    return base + timedelta(minutes=minutes)


def _drain(target: queue.Queue) -> None:
    """Empty a queue without blocking."""
    while True:
        try:
            target.get_nowait()
        except queue.Empty:
            return
