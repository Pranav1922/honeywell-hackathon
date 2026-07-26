"""RC thermal-network building simulator.

A single-zone building modelled as a resistance-capacitance network: conduction
to outdoors, solar gain, internal gains from occupants and lighting, ventilation
losses, and HVAC delivered power. Fast, deterministic and dependency-free.

This is a real building model, not a mock. It exists so the loop, agents,
persistence and dashboard can be built and stress-tested before EnergyPlus is
installed, and so the demonstration has a working fallback.

Implemented in Milestone 1.
"""

from __future__ import annotations

from app.sim.base import BuildingState, ControlAction
from app.sim.weather import WeatherProvider


class ToySimulator:
    """Single-zone RC thermal network implementing the `Simulator` protocol."""

    def __init__(
        self,
        weather: WeatherProvider,
        horizon_steps: int,
        timestep_seconds: int = 900,
        floor_area_m2: float = 200.0,
        thermal_capacitance_jk: float = 1.2e7,
        envelope_ua_wk: float = 250.0,
        hvac_capacity_kw: float = 12.0,
        hvac_cop: float = 3.2,
        lighting_capacity_kw: float = 2.0,
    ) -> None:
        raise NotImplementedError("Milestone 1")

    def reset(self) -> BuildingState:
        raise NotImplementedError("Milestone 1")

    def step(self, action: ControlAction) -> BuildingState:
        raise NotImplementedError("Milestone 1")

    def close(self) -> None:
        raise NotImplementedError("Milestone 1")
