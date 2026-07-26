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

Implemented in Milestone 4.
"""

from __future__ import annotations

from pathlib import Path

from app.sim.base import BuildingState, ControlAction

SENSOR_VARIABLES: tuple[tuple[str, str], ...] = (
    ("Zone Mean Air Temperature", "ZONE ONE"),
    ("Site Outdoor Air Drybulb Temperature", "Environment"),
    ("Zone People Occupant Count", "ZONE ONE"),
    ("Facility Total Electricity Demand Rate", "Whole Building"),
    ("Zone Air CO2 Concentration", "ZONE ONE"),
    ("Zone Air Relative Humidity", "ZONE ONE"),
)

ACTUATORS: tuple[tuple[str, str, str], ...] = (
    ("Schedule:Constant", "Schedule Value", "HEATING_SETPOINT_SCH"),
    ("Schedule:Constant", "Schedule Value", "COOLING_SETPOINT_SCH"),
    ("Schedule:Constant", "Schedule Value", "LIGHTING_SCH"),
    ("Schedule:Constant", "Schedule Value", "VENTILATION_SCH"),
)


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
    ) -> None:
        raise NotImplementedError("Milestone 4")

    def reset(self) -> BuildingState:
        """Start EnergyPlus on a worker thread and return the first post-warmup state."""
        raise NotImplementedError("Milestone 4")

    def step(self, action: ControlAction) -> BuildingState:
        """Release the callback with `action` and wait for the next published state."""
        raise NotImplementedError("Milestone 4")

    def close(self) -> None:
        """Stop the run, join the worker thread and release API handles."""
        raise NotImplementedError("Milestone 4")

    def collect_errors(self) -> list[str]:
        """Parse the run's `.err` file so the agent can react to runtime problems."""
        raise NotImplementedError("Milestone 4")
