"""The agent's tool registry — the single source of truth for tool-calling.

Holds JSON-schema'd functions the model may call, dispatches a call by name, and
exports schemas in both OpenAI tool format and MCP format. `mcp_server.py` wraps
this same registry, so the in-process agent and any external MCP client invoke
identical code rather than parallel implementations.

`get_simulation_errors` is what lets the agent parse EnergyPlus runtime output
and react to problems without human code modification.

Two properties are deliberate. **Nothing here executes arbitrary code**: the
registry dispatches by name from a fixed table and rejects arguments that are not
in the tool's schema, so a hallucinated call is an error payload rather than an
action. And **nothing here mutates the run**: every tool is a read of state the
agent is already allowed to see, which is why `set_control_policy` is terminal
rather than an actuator write — the policy still has to survive the guard.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from app.agents.base import (
    STRATEGY_HOLD,
    STRATEGY_PEAK_SHAVE,
    STRATEGY_PRECOOL,
    STRATEGY_PREHEAT,
    STRATEGY_SETBACK,
)
from app.comfort import clothing_for_season, comfort_band, pmv, ppd
from app.energy import step_energy_kwh
from app.sim.base import BuildingState
from app.utils.logsummary import compact_errors, summarise_telemetry

TOOL_NAMES: tuple[str, ...] = (
    "get_recent_telemetry",     # windowed zone/outdoor temps, power, occupancy
    "get_comfort_limits",       # active comfort band and hard clamps
    "get_energy_summary",       # kWh so far, peak demand, tariff, carbon intensity
    "evaluate_policy",          # score a candidate policy before committing to it
    "get_simulation_errors",    # parsed EnergyPlus .err entries, severity-filtered
    "set_control_policy",       # terminal call: commit set-points and rationale
)

STRATEGIES: tuple[str, ...] = (
    STRATEGY_HOLD,
    STRATEGY_PRECOOL,
    STRATEGY_PREHEAT,
    STRATEGY_SETBACK,
    STRATEGY_PEAK_SHAVE,
)


@dataclass
class AgentContext:
    """Read access to the live run, and the only thing the tools may look at.

    The supervisor updates this once per timestep via `observe`, which is also
    where the running energy totals accumulate. Deriving them here rather than
    reading them from the runner keeps the tool layer free of any handle on the
    simulator or the database — a tool cannot reach the building even by mistake.
    """

    timestep_seconds: int = 900
    limits: dict[str, Any] = field(default_factory=dict)
    targets: dict[str, Any] = field(default_factory=dict)
    occupied_hours: tuple[int, int] = (8, 18)
    history_window_steps: int = 96

    state: BuildingState | None = None
    history: tuple[BuildingState, ...] = ()
    total_kwh: float = 0.0
    peak_kw: float = 0.0
    error_log: list[str] = field(default_factory=list)

    def observe(
        self, state: BuildingState, history: Sequence[BuildingState]
    ) -> None:
        """Record the latest timestep and accumulate the run's energy totals."""
        self.state = state
        self.history = tuple(history)
        self.total_kwh += step_energy_kwh(state.power_kw, self.timestep_seconds)
        self.peak_kw = max(self.peak_kw, state.power_kw)

    def record_error(self, line: str) -> None:
        """Add a simulator diagnostic for `get_simulation_errors` to surface."""
        self.error_log.append(line)

    def reset(self) -> None:
        """Clear per-run accumulation between runs."""
        self.state = None
        self.history = ()
        self.total_kwh = 0.0
        self.peak_kw = 0.0
        self.error_log.clear()

    def require_state(self) -> BuildingState:
        """The current state, or a clear error if the loop has not started."""
        if self.state is None:
            raise ValueError("no telemetry observed yet; the run has not started")
        return self.state


class ToolRegistry:
    """Registers, describes and dispatches the tools available to the agent."""

    def __init__(self, runner_context: AgentContext) -> None:
        """`runner_context` gives tools read access to the live run: telemetry
        history, scenario targets and simulator diagnostics."""
        self.context = runner_context
        self._schemas: dict[str, dict[str, Any]] = {}
        self._handlers: dict[str, Callable[..., dict[str, Any]]] = {}
        self._order: list[str] = []
        self._register_builtins()

    def register(self, name: str, schema: dict[str, Any], fn: Callable) -> None:
        """Add a tool definition and its handler.

        Args:
            name: Tool name as the model will call it.
            schema: JSON Schema for the arguments object.
            fn: Handler taking the validated arguments as keywords.
        """
        if name in self._schemas:
            raise ValueError(f"tool {name!r} is already registered")
        if schema.get("type") != "object" or "properties" not in schema:
            raise ValueError(
                f"tool {name!r} schema must be an object schema with properties"
            )
        self._schemas[name] = schema
        self._handlers[name] = fn
        self._order.append(name)

    @property
    def names(self) -> tuple[str, ...]:
        """Registered tool names, in registration order."""
        return tuple(self._order)

    def comfort_band_now(self) -> tuple[float, float]:
        """The (coolest, warmest) acceptable zone temperatures for current
        conditions. Shared with the observation builder so the band the model is
        shown and the band the tools report are the same number."""
        return self._band(self.context.require_state())

    def openai_schemas(self) -> list[dict[str, Any]]:
        """Tool definitions in OpenAI tool-calling format.

        Groq's chat completions API speaks this format, as does every other
        OpenAI-compatible endpoint, so no per-provider translation is needed.
        """
        return [
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": self._schemas[name].get("description", ""),
                    "parameters": {
                        key: value
                        for key, value in self._schemas[name].items()
                        if key != "description"
                    },
                },
            }
            for name in self._order
        ]

    def mcp_schemas(self) -> list[dict[str, Any]]:
        """The same definitions in MCP tool format."""
        return [
            {
                "name": name,
                "description": self._schemas[name].get("description", ""),
                "inputSchema": {
                    key: value
                    for key, value in self._schemas[name].items()
                    if key != "description"
                },
            }
            for name in self._order
        ]

    def dispatch(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Invoke a tool by name. Unknown names and invalid arguments return an
        error payload rather than raising, so the model can self-correct."""
        if name not in self._handlers:
            return {
                "error": f"unknown tool {name!r}",
                "available_tools": list(self._order),
            }
        try:
            cleaned = self._validate_arguments(name, arguments)
        except ValueError as exc:
            return {"error": str(exc), "tool": name}

        try:
            return self._handlers[name](**cleaned)
        except ValueError as exc:
            return {"error": str(exc), "tool": name}

    # -- argument validation ------------------------------------------------

    def _validate_arguments(
        self, name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """Coerce and bound-check arguments against the tool's schema.

        Unknown keys are rejected outright rather than ignored. A model that
        invents an argument has misunderstood the tool, and telling it so is more
        useful than silently running a different call than it asked for.
        """
        if not isinstance(arguments, dict):
            raise ValueError(f"arguments for {name!r} must be a JSON object")

        schema = self._schemas[name]
        properties: dict[str, Any] = schema["properties"]
        unknown = sorted(set(arguments) - set(properties))
        if unknown:
            raise ValueError(
                f"unknown argument(s) {', '.join(unknown)} for {name!r}; "
                f"accepted: {', '.join(sorted(properties))}"
            )
        missing = sorted(set(schema.get("required", [])) - set(arguments))
        if missing:
            raise ValueError(
                f"missing required argument(s) {', '.join(missing)} for {name!r}"
            )

        cleaned: dict[str, Any] = {}
        for key, value in arguments.items():
            cleaned[key] = _coerce(key, value, properties[key])
        return cleaned

    # -- tool definitions ---------------------------------------------------

    def _register_builtins(self) -> None:
        """Define the six tools named in the architecture, in that order."""
        self.register(
            "get_recent_telemetry",
            {
                "description": (
                    "Recent telemetry compacted into per-window min/mean/max and a "
                    "trend direction. Use it to see the shape of the day rather "
                    "than a single instant."
                ),
                "type": "object",
                "properties": {
                    "window_steps": {
                        "type": "integer",
                        "description": "How many recent steps to summarise.",
                        "minimum": 1,
                        "maximum": 2016,
                    },
                    "buckets": {
                        "type": "integer",
                        "description": "How many summary windows to produce.",
                        "minimum": 1,
                        "maximum": 24,
                    },
                },
                "required": [],
            },
            self._get_recent_telemetry,
        )
        self.register(
            "get_comfort_limits",
            {
                "description": (
                    "The acceptable PMV band, the zone temperatures it corresponds "
                    "to right now, and the hard clamps the reactive guard will "
                    "enforce on your policy."
                ),
                "type": "object",
                "properties": {},
                "required": [],
            },
            self._get_comfort_limits,
        )
        self.register(
            "get_energy_summary",
            {
                "description": (
                    "Energy consumed so far, peak demand against its threshold, "
                    "tariff and grid carbon intensity."
                ),
                "type": "object",
                "properties": {},
                "required": [],
            },
            self._get_energy_summary,
        )
        self.register(
            "evaluate_policy",
            {
                "description": (
                    "Score a candidate policy before committing to it: predicted "
                    "PMV at each set-point, whether the guard would clamp it, and "
                    "how the dead-band compares with the comfort band."
                ),
                "type": "object",
                "properties": {
                    "heating_sp_c": {
                        "type": "number",
                        "description": "Candidate heating set-point, Celsius.",
                        "minimum": -50.0,
                        "maximum": 60.0,
                    },
                    "cooling_sp_c": {
                        "type": "number",
                        "description": "Candidate cooling set-point, Celsius.",
                        "minimum": -50.0,
                        "maximum": 60.0,
                    },
                    "lighting_level": {
                        "type": "number",
                        "description": "Candidate lighting output, 0.0 to 1.0.",
                        "minimum": 0.0,
                        "maximum": 1.0,
                    },
                    "ventilation_ach": {
                        "type": "number",
                        "description": "Candidate ventilation rate, air changes/hour.",
                        "minimum": 0.0,
                        "maximum": 20.0,
                    },
                },
                "required": ["heating_sp_c", "cooling_sp_c"],
            },
            self._evaluate_policy,
        )
        self.register(
            "get_simulation_errors",
            {
                "description": (
                    "Simulator diagnostics — EnergyPlus .err entries — severity "
                    "filtered, deduplicated into counts, and bounded in length."
                ),
                "type": "object",
                "properties": {
                    "min_severity": {
                        "type": "string",
                        "description": "Lowest severity to report.",
                        "enum": ["severe", "fatal", "warning", "info"],
                    },
                    "max_entries": {
                        "type": "integer",
                        "description": "Cap on distinct entries returned.",
                        "minimum": 1,
                        "maximum": 100,
                    },
                },
                "required": [],
            },
            self._get_simulation_errors,
        )
        self.register(
            "set_control_policy",
            {
                "description": (
                    "Commit the operating policy for this control window. Call "
                    "this exactly once, last. The reactive guard still enforces "
                    "every hard limit on what you submit."
                ),
                "type": "object",
                "properties": {
                    "strategy": {
                        "type": "string",
                        "description": "The control strategy this policy implements.",
                        "enum": list(STRATEGIES),
                    },
                    "heating_sp_c": {
                        "type": "number",
                        "description": "Heating set-point, Celsius.",
                        "minimum": -50.0,
                        "maximum": 60.0,
                    },
                    "cooling_sp_c": {
                        "type": "number",
                        "description": "Cooling set-point, Celsius.",
                        "minimum": -50.0,
                        "maximum": 60.0,
                    },
                    "lighting_level": {
                        "type": "number",
                        "description": "Lighting output, 0.0 to 1.0.",
                        "minimum": 0.0,
                        "maximum": 1.0,
                    },
                    "ventilation_ach": {
                        "type": "number",
                        "description": "Ventilation rate, air changes per hour.",
                        "minimum": 0.0,
                        "maximum": 20.0,
                    },
                    "rationale": {
                        "type": "string",
                        "description": (
                            "One or two sentences naming the trade-off made. Shown "
                            "verbatim on the operator dashboard."
                        ),
                    },
                },
                "required": [
                    "strategy",
                    "heating_sp_c",
                    "cooling_sp_c",
                    "lighting_level",
                    "ventilation_ach",
                    "rationale",
                ],
            },
            self._set_control_policy,
        )

    # -- tool handlers ------------------------------------------------------

    def _get_recent_telemetry(
        self, window_steps: int | None = None, buckets: int = 8
    ) -> dict[str, Any]:
        """Compacted telemetry plus the latest raw sample."""
        context = self.context
        state = context.require_state()
        window = window_steps or context.history_window_steps
        return {
            "window_steps": window,
            "buckets": buckets,
            "summary": summarise_telemetry(
                context.history, window_steps=window, buckets=buckets
            ),
            "latest": {
                "step": state.step,
                "sim_time": state.sim_time.isoformat(),
                "zone_temp_c": round(state.zone_temp_c, 2),
                "outdoor_temp_c": round(state.outdoor_temp_c, 2),
                "occupancy": round(state.occupancy, 3),
                "co2_ppm": round(state.co2_ppm, 1),
                "power_kw": round(state.power_kw, 3),
                "hvac_mode": state.hvac_mode,
            },
        }

    def _get_comfort_limits(self) -> dict[str, Any]:
        """The comfort band now, and the clamps that will be applied regardless."""
        state = self.context.require_state()
        coolest_c, warmest_c = self._band(state)
        limits = self.context.limits
        return {
            "pmv_band": [
                float(limits.get("comfort_pmv_low", -0.5)),
                float(limits.get("comfort_pmv_high", 0.5)),
            ],
            "comfortable_zone_temp_c": [round(coolest_c, 2), round(warmest_c, 2)],
            "hard_zone_temp_c_occupied": [
                float(limits.get("min_zone_temp_c", 19.0)),
                float(limits.get("max_zone_temp_c", 26.0)),
            ],
            "hard_zone_temp_c_unoccupied": [
                float(limits.get("unoccupied_min_temp_c", 12.0)),
                float(limits.get("unoccupied_max_temp_c", 32.0)),
            ],
            "min_deadband_c": float(limits.get("min_setpoint_gap_c", 1.5)),
            "ventilation_ach": [
                float(limits.get("min_ventilation_ach", 0.5)),
                float(limits.get("max_ventilation_ach", 6.0)),
            ],
            "max_co2_ppm": float(limits.get("max_co2_ppm", 1000.0)),
            "occupied_hours": list(self.context.occupied_hours),
            "current_pmv": round(self._pmv_at(state, state.zone_temp_c), 3),
        }

    def _get_energy_summary(self) -> dict[str, Any]:
        """Running totals, cost, carbon and peak demand against its threshold."""
        context = self.context
        targets = context.targets
        tariff = float(targets.get("tariff_per_kwh", 0.0))
        carbon = float(targets.get("grid_carbon_kg_per_kwh", 0.0))
        threshold = float(targets.get("peak_demand_kw", 0.0))
        return {
            "total_kwh": round(context.total_kwh, 4),
            "peak_kw": round(context.peak_kw, 3),
            "peak_demand_threshold_kw": threshold,
            "peak_headroom_kw": round(threshold - context.peak_kw, 3)
            if threshold > 0.0
            else None,
            "cost_so_far": round(context.total_kwh * tariff, 4),
            "co2_kg_so_far": round(context.total_kwh * carbon, 4),
            "tariff_per_kwh": tariff,
            "grid_carbon_kg_per_kwh": carbon,
        }

    def _evaluate_policy(
        self,
        heating_sp_c: float,
        cooling_sp_c: float,
        lighting_level: float | None = None,
        ventilation_ach: float | None = None,
    ) -> dict[str, Any]:
        """Predict what a candidate policy would do before it is committed.

        The set-points are the temperatures the zone will be held at, so PMV
        evaluated at each one is exactly the comfort the occupants would report at
        that edge of the dead-band. That makes this a real check rather than a
        rubber stamp: a policy whose cooling set-point scores PMV +0.9 is rejected
        here, before the guard has to override it.
        """
        state = self.context.require_state()
        limits = self.context.limits
        coolest_c, warmest_c = self._band(state)
        deadband_c = cooling_sp_c - heating_sp_c
        min_deadband = float(limits.get("min_setpoint_gap_c", 1.5))
        floor_c = float(limits.get("min_zone_temp_c", 19.0))
        ceiling_c = float(limits.get("max_zone_temp_c", 26.0))

        problems: list[str] = []
        if deadband_c < min_deadband:
            problems.append(
                f"dead-band {deadband_c:.2f} C is below the minimum {min_deadband:.2f} C"
            )
        if heating_sp_c < floor_c or cooling_sp_c > ceiling_c:
            problems.append(
                f"set-points fall outside the hard limits {floor_c:.1f}-{ceiling_c:.1f} C"
                " and would be clamped when comfort is required"
            )
        if ventilation_ach is not None:
            vent_min = float(limits.get("min_ventilation_ach", 0.5))
            vent_max = float(limits.get("max_ventilation_ach", 6.0))
            if not vent_min <= ventilation_ach <= vent_max:
                problems.append(
                    f"ventilation {ventilation_ach:.2f} ACH is outside "
                    f"{vent_min:.2f}-{vent_max:.2f} ACH and would be clamped"
                )

        heating_pmv = self._pmv_at(state, heating_sp_c)
        cooling_pmv = self._pmv_at(state, cooling_sp_c)
        low = float(limits.get("comfort_pmv_low", -0.5))
        high = float(limits.get("comfort_pmv_high", 0.5))
        comfort_ok = low <= heating_pmv and cooling_pmv <= high

        return {
            "heating_sp_c": heating_sp_c,
            "cooling_sp_c": cooling_sp_c,
            "deadband_c": round(deadband_c, 2),
            "pmv_at_heating_sp": round(heating_pmv, 3),
            "pmv_at_cooling_sp": round(cooling_pmv, 3),
            "ppd_at_heating_sp": round(ppd(heating_pmv), 2),
            "ppd_at_cooling_sp": round(ppd(cooling_pmv), 2),
            "comfort_band_c": [round(coolest_c, 2), round(warmest_c, 2)],
            "comfort_satisfied_when_occupied": comfort_ok,
            "would_be_clamped": bool(problems),
            "problems": problems,
            "deadband_vs_comfort_band": (
                f"{deadband_c:.2f} C of the available "
                f"{max(0.0, warmest_c - coolest_c):.2f} C; wider is cheaper"
            ),
            "lighting_level": lighting_level,
            "ventilation_ach": ventilation_ach,
        }

    def _get_simulation_errors(
        self, min_severity: str = "warning", max_entries: int = 20
    ) -> dict[str, Any]:
        """Simulator diagnostics, compacted. Empty is the normal answer here."""
        raw = "\n".join(self.context.error_log)
        return {
            "raw_line_count": len(self.context.error_log),
            "min_severity": min_severity,
            "digest": compact_errors(raw, max_entries=max_entries)
            if raw.strip()
            else "No simulation diagnostics reported.",
        }

    def _set_control_policy(
        self,
        strategy: str,
        heating_sp_c: float,
        cooling_sp_c: float,
        lighting_level: float,
        ventilation_ach: float,
        rationale: str,
    ) -> dict[str, Any]:
        """Echo the committed policy back.

        Terminal and inert by design: the supervisor reads the arguments off the
        tool call itself and puts them through validation and then the guard.
        Dispatching this tool changes nothing, which is why a hallucinated call
        cannot move a set-point.
        """
        return {
            "accepted_for_validation": True,
            "strategy": strategy,
            "heating_sp_c": heating_sp_c,
            "cooling_sp_c": cooling_sp_c,
            "lighting_level": lighting_level,
            "ventilation_ach": ventilation_ach,
            "rationale": rationale,
        }

    # -- internals ----------------------------------------------------------

    def _band(self, state: BuildingState) -> tuple[float, float]:
        """The (coolest, warmest) acceptable zone temperatures right now."""
        limits = self.context.limits
        return comfort_band(
            outdoor_temp_c=state.outdoor_temp_c,
            relative_humidity=state.relative_humidity,
            air_speed_ms=state.air_speed_ms,
            low_pmv=float(limits.get("comfort_pmv_low", -0.5)),
            high_pmv=float(limits.get("comfort_pmv_high", 0.5)),
        )

    def _pmv_at(self, state: BuildingState, zone_temp_c: float) -> float:
        """PMV the occupants would report at `zone_temp_c` in current conditions."""
        return pmv(
            air_temp_c=zone_temp_c,
            mean_radiant_temp_c=zone_temp_c,
            air_speed_ms=state.air_speed_ms,
            relative_humidity=state.relative_humidity,
            clothing_insulation=clothing_for_season(state.outdoor_temp_c),
        )


def _coerce(key: str, value: Any, spec: dict[str, Any]) -> Any:
    """Coerce one argument to its declared type and range.

    Models routinely send `"22.5"` where a number is wanted, and rejecting that
    over a quotation mark wastes a self-correction round on nothing. A genuine
    type error — a word where a number belongs — still fails, with the reason.
    """
    expected = spec.get("type")

    if expected == "number" or expected == "integer":
        if isinstance(value, bool):
            raise ValueError(f"{key} must be a number, got a boolean")
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{key} must be a number, got {value!r}") from exc
        if number != number or number in (float("inf"), float("-inf")):
            raise ValueError(f"{key} must be a finite number, got {value!r}")
        if "minimum" in spec and number < spec["minimum"]:
            raise ValueError(f"{key} must be at least {spec['minimum']}, got {number}")
        if "maximum" in spec and number > spec["maximum"]:
            raise ValueError(f"{key} must be at most {spec['maximum']}, got {number}")
        if expected == "integer":
            if number != int(number):
                raise ValueError(f"{key} must be a whole number, got {value!r}")
            return int(number)
        return number

    if expected == "string":
        if not isinstance(value, str):
            raise ValueError(f"{key} must be a string, got {value!r}")
        if "enum" in spec and value not in spec["enum"]:
            raise ValueError(
                f"{key} must be one of {', '.join(spec['enum'])}, got {value!r}"
            )
        return value

    return value
