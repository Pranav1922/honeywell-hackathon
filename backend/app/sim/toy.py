"""RC thermal-network building simulator.

A single-zone building modelled as a resistance-capacitance network: conduction
to outdoors, solar gain, internal gains from occupants and lighting, ventilation
losses, and HVAC delivered power. Fast, deterministic and dependency-free.

This is a real building model, not a mock. It exists so the loop, agents,
persistence and dashboard can be built and stress-tested before EnergyPlus is
installed, and so the demonstration has a working fallback.

Integration is analytical rather than Euler. Over one timestep the inputs are
constant, so the zone is a first-order system with an exact solution:

    T(t+dt) = T_eq + (T(t) - T_eq) * exp(-dt / tau)

with `tau = C / UA_total` and `T_eq` the temperature the zone would settle at.
This is unconditionally stable and exact for the model, which matters because a
15-minute step against a ventilation-dominated time constant would push explicit
Euler close to its accuracy limit.
"""

from __future__ import annotations

import math

from app.sim.base import (
    HVAC_COOLING,
    HVAC_HEATING,
    HVAC_OFF,
    BuildingState,
    ControlAction,
)
from app.sim.weather import WeatherProvider

AIR_DENSITY_KG_M3 = 1.2
AIR_HEAT_CAPACITY_J_KGK = 1005.0
OUTDOOR_CO2_PPM = 420.0
SENSIBLE_HEAT_PER_PERSON_W = 75.0
CO2_PER_PERSON_M3_S = 5.0e-6


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
        hvac_capacity_kw: float = 18.0,
        hvac_cop: float = 3.2,
        lighting_capacity_kw: float = 2.0,
        ceiling_height_m: float = 3.0,
        window_area_m2: float = 20.0,
        solar_heat_gain_coefficient: float = 0.35,
        design_occupancy: int = 20,
        base_plug_load_kw: float = 0.3,
        occupied_plug_load_kw: float = 1.2,
        fan_power_kw_per_ach: float = 0.15,
        infiltration_ach: float = 0.2,
        indoor_relative_humidity: float = 0.5,
        indoor_air_speed_ms: float = 0.1,
        initial_zone_temp_c: float = 22.0,
    ) -> None:
        """Configure the building.

        Args:
            weather: Supplies outdoor conditions and occupancy per step.
            horizon_steps: Number of steps before the run is complete.
            timestep_seconds: Simulation timestep.
            floor_area_m2: Conditioned floor area.
            thermal_capacitance_jk: Effective thermal mass of the zone.
            envelope_ua_wk: Conductance of the envelope to outdoors.
            hvac_capacity_kw: Maximum thermal power the plant can deliver.
            hvac_cop: Coefficient of performance, thermal out per electrical in.
            lighting_capacity_kw: Installed lighting power at full output.
            ceiling_height_m: Used with floor area to derive zone volume.
            window_area_m2: Glazed area receiving solar radiation.
            solar_heat_gain_coefficient: Fraction of incident solar transmitted.
            design_occupancy: People present at an occupancy fraction of 1.0.
            base_plug_load_kw: Equipment load present at all times.
            occupied_plug_load_kw: Additional equipment load when fully occupied.
            fan_power_kw_per_ach: Fan electrical power per air change per hour.
            infiltration_ach: Uncontrolled air exchange through the envelope,
                always present. Real buildings leak, so ventilation never truly
                reaches zero and overnight CO2 decays back towards outdoor air.
            indoor_relative_humidity: Held constant; the RC model carries no
                moisture balance. EnergyPlus supplies the measured value in
                Milestone 4, and PMV is weakly sensitive to it inside the
                comfort band.
            indoor_air_speed_ms: Still-air assumption for the comfort model.
            initial_zone_temp_c: Zone temperature at step 0.
        """
        if horizon_steps <= 0:
            raise ValueError(f"horizon_steps must be positive, got {horizon_steps}")
        if timestep_seconds <= 0:
            raise ValueError(
                f"timestep_seconds must be positive, got {timestep_seconds}"
            )
        if hvac_cop <= 0:
            raise ValueError(f"hvac_cop must be positive, got {hvac_cop}")
        if thermal_capacitance_jk <= 0 or envelope_ua_wk <= 0:
            raise ValueError("thermal capacitance and envelope UA must be positive")

        self.timestep_seconds = timestep_seconds
        self.horizon_steps = horizon_steps

        self._weather = weather
        self._floor_area_m2 = floor_area_m2
        self._capacitance = thermal_capacitance_jk
        self._envelope_ua = envelope_ua_wk
        self._hvac_capacity_w = hvac_capacity_kw * 1000.0
        self._hvac_cop = hvac_cop
        self._lighting_capacity_w = lighting_capacity_kw * 1000.0
        self._volume_m3 = floor_area_m2 * ceiling_height_m
        self._solar_aperture_m2 = window_area_m2 * solar_heat_gain_coefficient
        self._design_occupancy = design_occupancy
        self._base_plug_load_w = base_plug_load_kw * 1000.0
        self._occupied_plug_load_w = occupied_plug_load_kw * 1000.0
        self._fan_power_w_per_ach = fan_power_kw_per_ach * 1000.0
        self._infiltration_ach = infiltration_ach
        self._relative_humidity = indoor_relative_humidity
        self._air_speed_ms = indoor_air_speed_ms
        self._initial_zone_temp_c = initial_zone_temp_c

        self._step = 0
        self._zone_temp_c = initial_zone_temp_c
        self._co2_ppm = OUTDOOR_CO2_PPM
        self._closed = False

    # -- Simulator protocol -------------------------------------------------

    def reset(self) -> BuildingState:
        """Initialise the simulation and return the state at step 0."""
        self._step = 0
        self._zone_temp_c = self._initial_zone_temp_c
        self._co2_ppm = OUTDOOR_CO2_PPM
        self._closed = False

        sample = self._weather.sample(0)
        plug_load_w = self._plug_load_w(sample.occupancy)
        return BuildingState(
            step=0,
            sim_time=sample.sim_time,
            zone_temp_c=self._zone_temp_c,
            outdoor_temp_c=sample.outdoor_temp_c,
            occupancy=sample.occupancy,
            hvac_mode=HVAC_OFF,
            heating_sp_c=0.0,
            cooling_sp_c=0.0,
            lighting_level=0.0,
            ventilation_ach=0.0,
            power_kw=plug_load_w / 1000.0,
            co2_ppm=self._co2_ppm,
            relative_humidity=self._relative_humidity,
            air_speed_ms=self._air_speed_ms,
        )

    def step(self, action: ControlAction) -> BuildingState:
        """Apply `action`, advance one timestep, and return the resulting state."""
        if self._closed:
            raise RuntimeError("step() called on a closed simulator")
        if self._step >= self.horizon_steps:
            raise RuntimeError(
                f"simulation horizon of {self.horizon_steps} steps already reached"
            )
        self._validate(action)

        next_step = self._step + 1
        sample = self._weather.sample(next_step)

        # Mechanical ventilation plus envelope leakage. Their conductance rides
        # on top of the fabric conductance; only the mechanical part costs fan
        # power.
        air_change_rate = action.ventilation_ach + self._infiltration_ach
        air_exchange_m3_s = air_change_rate * self._volume_m3 / 3600.0
        ventilation_ua = (
            air_exchange_m3_s * AIR_DENSITY_KG_M3 * AIR_HEAT_CAPACITY_J_KGK
        )
        total_ua = self._envelope_ua + ventilation_ua

        lighting_w = action.lighting_level * self._lighting_capacity_w
        plug_load_w = self._plug_load_w(sample.occupancy)
        gains_w = (
            sample.solar_w_m2 * self._solar_aperture_m2
            + sample.occupancy * self._design_occupancy * SENSIBLE_HEAT_PER_PERSON_W
            + lighting_w
            + plug_load_w
        )

        decay = math.exp(-self.timestep_seconds * total_ua / self._capacitance)
        free_float_c = self._project(
            self._zone_temp_c, sample.outdoor_temp_c, gains_w, 0.0, total_ua, decay
        )

        hvac_w = self._required_hvac_w(
            free_float_c=free_float_c,
            action=action,
            outdoor_temp_c=sample.outdoor_temp_c,
            gains_w=gains_w,
            total_ua=total_ua,
            decay=decay,
        )

        self._zone_temp_c = self._project(
            self._zone_temp_c,
            sample.outdoor_temp_c,
            gains_w,
            hvac_w,
            total_ua,
            decay,
        )
        self._co2_ppm = self._project_co2(sample.occupancy, air_exchange_m3_s)
        self._step = next_step

        hvac_electric_w = abs(hvac_w) / self._hvac_cop
        fan_w = action.ventilation_ach * self._fan_power_w_per_ach
        power_kw = (hvac_electric_w + lighting_w + plug_load_w + fan_w) / 1000.0

        if hvac_w > 1.0:
            mode = HVAC_HEATING
        elif hvac_w < -1.0:
            mode = HVAC_COOLING
        else:
            mode = HVAC_OFF

        return BuildingState(
            step=next_step,
            sim_time=sample.sim_time,
            zone_temp_c=self._zone_temp_c,
            outdoor_temp_c=sample.outdoor_temp_c,
            occupancy=sample.occupancy,
            hvac_mode=mode,
            heating_sp_c=action.heating_sp_c,
            cooling_sp_c=action.cooling_sp_c,
            lighting_level=action.lighting_level,
            ventilation_ach=action.ventilation_ach,
            power_kw=power_kw,
            co2_ppm=self._co2_ppm,
            relative_humidity=self._relative_humidity,
            air_speed_ms=self._air_speed_ms,
        )

    def close(self) -> None:
        """Release simulator resources. Safe to call more than once."""
        self._closed = True

    # -- internals ----------------------------------------------------------

    def _validate(self, action: ControlAction) -> None:
        """Reject actuator commands the plant could not physically accept."""
        if action.heating_sp_c > action.cooling_sp_c:
            raise ValueError(
                f"heating set-point {action.heating_sp_c} exceeds cooling "
                f"set-point {action.cooling_sp_c}"
            )
        if not 0.0 <= action.lighting_level <= 1.0:
            raise ValueError(
                f"lighting_level must be 0.0-1.0, got {action.lighting_level}"
            )
        if action.ventilation_ach < 0.0:
            raise ValueError(
                f"ventilation_ach must be non-negative, got {action.ventilation_ach}"
            )

    def _plug_load_w(self, occupancy: float) -> float:
        """Equipment load: a standing base plus an occupancy-driven component."""
        return self._base_plug_load_w + occupancy * self._occupied_plug_load_w

    def _project(
        self,
        temp_c: float,
        outdoor_temp_c: float,
        gains_w: float,
        hvac_w: float,
        total_ua: float,
        decay: float,
    ) -> float:
        """Exact first-order response of the zone over one timestep."""
        equilibrium_c = outdoor_temp_c + (gains_w + hvac_w) / total_ua
        return equilibrium_c + (temp_c - equilibrium_c) * decay

    def _required_hvac_w(
        self,
        free_float_c: float,
        action: ControlAction,
        outdoor_temp_c: float,
        gains_w: float,
        total_ua: float,
        decay: float,
    ) -> float:
        """Thermal power needed to land on a set-point, clipped to plant capacity.

        Positive is heating, negative is cooling. Inside the dead-band the plant
        stays off, which is what makes a wider dead-band cheaper to run.
        """
        if free_float_c < action.heating_sp_c:
            target_c = action.heating_sp_c
        elif free_float_c > action.cooling_sp_c:
            target_c = action.cooling_sp_c
        else:
            return 0.0

        # Invert the first-order response for the equilibrium that lands on the
        # target, then back out the thermal power that produces it.
        equilibrium_c = (target_c - self._zone_temp_c * decay) / (1.0 - decay)
        required_w = (equilibrium_c - outdoor_temp_c) * total_ua - gains_w
        return max(-self._hvac_capacity_w, min(self._hvac_capacity_w, required_w))

    def _project_co2(self, occupancy: float, air_exchange_m3_s: float) -> float:
        """Well-mixed CO2 mass balance over one timestep.

        Same first-order form as the thermal model. With no air exchange at all
        the concentration accumulates linearly instead, which the branch handles
        rather than dividing by zero.
        """
        generation_m3_s = (
            occupancy * self._design_occupancy * CO2_PER_PERSON_M3_S
        )
        if air_exchange_m3_s <= 0.0:
            rise_ppm = (
                generation_m3_s * 1e6 * self.timestep_seconds / self._volume_m3
            )
            return self._co2_ppm + rise_ppm

        equilibrium_ppm = OUTDOOR_CO2_PPM + generation_m3_s * 1e6 / air_exchange_m3_s
        decay = math.exp(
            -air_exchange_m3_s * self.timestep_seconds / self._volume_m3
        )
        return equilibrium_ppm + (self._co2_ppm - equilibrium_ppm) * decay
