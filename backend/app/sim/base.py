"""Simulation contracts shared by every simulator implementation.

This module defines the seam that makes EnergyPlus swappable: the sensor record
(`BuildingState`), the actuator record (`ControlAction`), and the `Simulator`
protocol. It deliberately contains no physics and no I/O — both `toy.py` and
`energyplus.py` satisfy this contract, and no other layer knows which is running.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

HVAC_OFF = "off"
HVAC_HEATING = "heating"
HVAC_COOLING = "cooling"


@dataclass(frozen=True)
class BuildingState:
    """Immutable sensor snapshot for a single simulation timestep."""

    step: int
    sim_time: datetime
    zone_temp_c: float
    outdoor_temp_c: float
    occupancy: float          # 0.0 - 1.0 fraction of design occupancy
    hvac_mode: str            # HVAC_OFF | HVAC_HEATING | HVAC_COOLING
    heating_sp_c: float
    cooling_sp_c: float
    lighting_level: float     # 0.0 - 1.0
    ventilation_ach: float    # air changes per hour
    power_kw: float
    co2_ppm: float
    relative_humidity: float  # 0.0 - 1.0
    air_speed_ms: float


@dataclass(frozen=True)
class ControlAction:
    """Immutable actuator command injected into the simulator."""

    heating_sp_c: float
    cooling_sp_c: float
    lighting_level: float
    ventilation_ach: float


@runtime_checkable
class Simulator(Protocol):
    """The contract every simulation backend implements."""

    timestep_seconds: int
    horizon_steps: int

    def reset(self) -> BuildingState:
        """Initialise the simulation and return the state at step 0."""
        ...

    def step(self, action: ControlAction) -> BuildingState:
        """Apply `action`, advance one timestep, and return the resulting state."""
        ...

    def close(self) -> None:
        """Release simulator resources. Safe to call more than once."""
        ...
