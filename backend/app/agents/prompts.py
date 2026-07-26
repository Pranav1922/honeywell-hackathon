"""Prompt construction, isolated so prompt engineering is reviewable as a diff.

Observations are assembled here rather than in `llm.py` to keep the control flow
and the wording independently testable. Long histories are compacted by
`utils.logsummary` before they reach a prompt.

`PROMPT_VERSION` is recorded on every reasoning log entry. When a prompt change
moves the savings figure, the version is what ties the two together.
"""

from __future__ import annotations

import json
from typing import Any, Sequence

from app.comfort import clothing_for_season, pmv, ppd
from app.sim.base import BuildingState
from app.utils.logsummary import summarise_telemetry

PROMPT_VERSION = "m2-groq-1"

SYSTEM_PROMPT = """\
You are the supervisory control agent for a commercial building's HVAC, lighting
and ventilation. You are consulted once per control window and you set the
operating policy for that window only.

MISSION, in strict priority order:
  1. SAFETY  — never propose set-points outside the stated hard limits.
  2. COMFORT — keep predicted mean vote (PMV) inside the acceptable band whenever
     the zone is occupied or about to be occupied.
  3. ENERGY  — subject to 1 and 2, minimise electricity, cost, carbon and peak
     demand.

Never buy energy savings with occupied-hours comfort. Do give up comfort margin
you do not need: the widest set-point band that still satisfies PMV is the
cheapest policy that is still comfortable.

WHERE THE SAVINGS COME FROM:
- Widen the dead-band out toward the edges of the comfort band while occupied.
  Plant runs only when the zone would otherwise be genuinely uncomfortable.
- Set back hard when the zone is empty and nobody is due (strategy `setback`),
  and turn lighting off.
- Pre-cool or pre-heat before occupancy while outdoor air is favourable, rather
  than recovering against the afternoon peak (`precool`, `preheat`).
- Dim lighting to measured occupancy, not to the schedule.
- Ventilate to measured CO2, not to a fixed design rate. Ventilation costs fan
  power and drags the zone toward outdoor temperature.
- Move toward the comfort-band edge when demand approaches the peak threshold
  (`peak_shave`).

TOOLS: you may call get_recent_telemetry, get_comfort_limits, get_energy_summary,
evaluate_policy and get_simulation_errors to inspect the run, and evaluate_policy
to score a candidate before committing to it. Call a tool only when the
observation does not already answer the question — every call costs latency
inside a live control loop.

OUTPUT CONTRACT — this is absolute:
- Finish by calling set_control_policy exactly once.
- Its arguments must be one valid JSON object with exactly these keys:
    strategy         string, one of: hold, precool, preheat, setback, peak_shave
    heating_sp_c     number, degrees Celsius
    cooling_sp_c     number, degrees Celsius, at least min_deadband_c above
                     heating_sp_c
    lighting_level   number, 0.0 to 1.0
    ventilation_ach  number, air changes per hour
    rationale        string, one or two sentences naming the trade-off you made
- Numbers only: no units, no ranges, no nulls, no arithmetic, no comments.
- Emit no prose outside the tool call, and do not restate the observation.
- Be deterministic: identical inputs must yield an identical policy.

A reactive guard enforces the hard limits on every timestep and will override
you. An overridden policy is a failed policy — stay inside the limits rather
than relying on the override.
"""


def build_observation(
    state: BuildingState,
    history: Sequence[BuildingState],
    targets: dict[str, Any],
) -> str:
    """Render current state, compacted history and scenario targets as a prompt.

    Everything the model is allowed to know arrives here, in one block, in a
    fixed order. Fixed order matters twice over: it makes the prompt diffable,
    and it keeps the observation stable enough that identical building conditions
    produce an identical decision.

    Args:
        state: The sensor snapshot the decision is being made against.
        history: Recent telemetry, compacted rather than pasted.
        targets: Goals and limits — comfort band, hard clamps, tariff, carbon
            intensity, peak threshold, energy consumed so far, and the
            supervisor's own previous decisions.

    Returns:
        The user-role observation text.
    """
    sections = [
        _window_section(state, targets),
        _zone_section(state, targets),
        _weather_section(state, history),
        _energy_section(targets),
        _limits_section(targets),
        _telemetry_section(history, targets),
        _previous_decisions_section(targets),
        _objective_section(targets),
    ]
    return "\n\n".join(section for section in sections if section)


def build_tool_result(
    name: str,
    result: dict[str, Any],
    tool_call_id: str | None = None,
) -> dict[str, Any]:
    """Format a tool result as a conversation message.

    Args:
        name: The tool that produced the result.
        result: Its return payload, serialised as compact JSON.
        tool_call_id: The id the model used when requesting the call. Required by
            the chat API when replying to a tool call; omitted only in tests.
    """
    message: dict[str, Any] = {
        "role": "tool",
        "name": name,
        "content": json.dumps(result, default=str, separators=(",", ":")),
    }
    if tool_call_id is not None:
        message["tool_call_id"] = tool_call_id
    return message


def build_correction(error: str) -> dict[str, Any]:
    """Format a validation failure as feedback the model can repair from.

    The validation error is handed back verbatim. That is the whole
    self-correction mechanism: the model is told precisely which constraint it
    broke, not merely that something was wrong, so the repair is a correction
    rather than another guess.
    """
    return {
        "role": "user",
        "content": (
            "Your proposed policy was REJECTED by the validator.\n"
            f"Reason: {error}\n\n"
            "Fix exactly that problem and call set_control_policy once more with "
            "a complete, valid JSON object. Keep every other field as you "
            "intended it, change nothing else, and add no prose."
        ),
    }


# -- observation sections ---------------------------------------------------


def _window_section(state: BuildingState, targets: dict[str, Any]) -> str:
    """Which control window this is, and how long it lasts."""
    cadence = int(targets.get("cadence_steps", 1))
    timestep_seconds = int(targets.get("timestep_seconds", 900))
    return (
        "== CONTROL WINDOW ==\n"
        f"step {state.step} | {state.sim_time:%Y-%m-%d %H:%M} "
        f"({state.sim_time:%A}) | this policy holds for {cadence} steps "
        f"({cadence * timestep_seconds / 60:.0f} min)"
    )


def _zone_section(state: BuildingState, targets: dict[str, Any]) -> str:
    """What the sensors report right now, including derived comfort."""
    pmv_value = current_pmv(state)
    low = float(targets.get("comfort_pmv_low", -0.5))
    high = float(targets.get("comfort_pmv_high", 0.5))
    verdict = "inside band" if low <= pmv_value <= high else "OUTSIDE BAND"
    return "\n".join(
        [
            "== ZONE NOW ==",
            f"zone air temperature    {state.zone_temp_c:6.2f} C",
            f"outdoor air temperature {state.outdoor_temp_c:6.2f} C",
            f"occupancy               {state.occupancy:6.2f} of design",
            f"CO2                     {state.co2_ppm:6.0f} ppm",
            f"relative humidity       {state.relative_humidity:6.2f}",
            f"PMV                     {pmv_value:+6.2f}  ({verdict})",
            f"PPD                     {ppd(pmv_value):6.1f} %",
            f"HVAC mode               {state.hvac_mode}",
            f"active set-points       {state.heating_sp_c:.1f} / "
            f"{state.cooling_sp_c:.1f} C",
            f"lighting level          {state.lighting_level:6.2f}",
            f"ventilation             {state.ventilation_ach:6.2f} ACH",
            f"instantaneous power     {state.power_kw:6.2f} kW",
        ]
    )


def _weather_section(
    state: BuildingState, history: Sequence[BuildingState]
) -> str:
    """Outdoor conditions now, and where they came from."""
    window = list(history)
    if len(window) < 2:
        return f"== WEATHER ==\noutdoor {state.outdoor_temp_c:.1f} C, no history yet"
    earliest = window[0]
    hours = (state.sim_time - earliest.sim_time).total_seconds() / 3600.0
    delta = state.outdoor_temp_c - earliest.outdoor_temp_c
    coldest = min(s.outdoor_temp_c for s in window)
    warmest = max(s.outdoor_temp_c for s in window)
    return (
        "== WEATHER ==\n"
        f"outdoor now {state.outdoor_temp_c:.1f} C; "
        f"{hours:.1f} h ago {earliest.outdoor_temp_c:.1f} C "
        f"({delta:+.1f} C over the window); "
        f"window range {coldest:.1f} .. {warmest:.1f} C"
    )


def _energy_section(targets: dict[str, Any]) -> str:
    """Consumption so far against the tariff, carbon and peak threshold."""
    total_kwh = float(targets.get("total_kwh", 0.0))
    peak_kw = float(targets.get("peak_kw", 0.0))
    threshold_kw = float(targets.get("peak_demand_kw", 0.0))
    tariff = float(targets.get("tariff_per_kwh", 0.0))
    carbon = float(targets.get("grid_carbon_kg_per_kwh", 0.0))
    headroom = (
        f"{threshold_kw - peak_kw:+.2f} kW headroom"
        if threshold_kw > 0.0
        else "no threshold set"
    )
    return "\n".join(
        [
            "== ENERGY SO FAR ==",
            f"consumed            {total_kwh:9.2f} kWh",
            f"cost                {total_kwh * tariff:9.2f}",
            f"carbon              {total_kwh * carbon:9.2f} kg CO2",
            f"peak demand         {peak_kw:9.2f} kW  "
            f"(threshold {threshold_kw:.2f} kW, {headroom})",
            f"tariff              {tariff:9.3f} per kWh",
            f"grid carbon         {carbon:9.3f} kg CO2 per kWh",
        ]
    )


def _limits_section(targets: dict[str, Any]) -> str:
    """The band to aim for and the clamps that will be applied regardless."""
    band = targets.get("comfort_band_c")
    band_line = (
        f"comfortable zone temp   {band[0]:.1f} .. {band[1]:.1f} C "
        "(from PMV at current humidity and clothing)"
        if band
        else "comfortable zone temp   unavailable"
    )
    occupied_hours = targets.get("occupied_hours", (8, 18))
    return "\n".join(
        [
            "== COMFORT AND SAFETY LIMITS (enforced downstream) ==",
            f"PMV band                {float(targets.get('comfort_pmv_low', -0.5)):+.2f}"
            f" .. {float(targets.get('comfort_pmv_high', 0.5)):+.2f}",
            band_line,
            f"hard zone temp limits   "
            f"{float(targets.get('min_zone_temp_c', 19.0)):.1f} .. "
            f"{float(targets.get('max_zone_temp_c', 26.0)):.1f} C when comfort is "
            f"required, {float(targets.get('unoccupied_min_temp_c', 12.0)):.1f} .. "
            f"{float(targets.get('unoccupied_max_temp_c', 32.0)):.1f} C when empty",
            f"min_deadband_c          "
            f"{float(targets.get('min_setpoint_gap_c', 1.5)):.1f} C",
            f"ventilation             "
            f"{float(targets.get('min_ventilation_ach', 0.5)):.2f} .. "
            f"{float(targets.get('max_ventilation_ach', 6.0)):.2f} ACH",
            f"CO2 ceiling             "
            f"{float(targets.get('max_co2_ppm', 1000.0)):.0f} ppm",
            f"scheduled occupancy     {int(occupied_hours[0]):02d}:00 .. "
            f"{int(occupied_hours[1]):02d}:00 on weekdays",
        ]
    )


def _telemetry_section(
    history: Sequence[BuildingState], targets: dict[str, Any]
) -> str:
    """Recent telemetry, statistically compacted rather than pasted."""
    window = int(targets.get("history_window_steps", 96))
    return "== RECENT TELEMETRY ==\n" + summarise_telemetry(history, window_steps=window)


def _previous_decisions_section(targets: dict[str, Any]) -> str:
    """What this supervisor already decided, so it can stay consistent.

    Without this the agent has no memory across windows and will oscillate
    between strategies on essentially identical conditions, which reads as
    indecision on the dashboard and wastes plant cycles in the building.
    """
    decisions = list(targets.get("previous_decisions") or [])
    if not decisions:
        return "== YOUR PREVIOUS DECISIONS ==\nnone yet; this is the first window"
    lines = ["== YOUR PREVIOUS DECISIONS (most recent last) =="]
    for record in decisions:
        lines.append(
            f"  step {int(record.get('step', 0)):>5}  "
            f"{str(record.get('strategy', '?')):<10} "
            f"{float(record.get('heating_sp_c', 0.0)):5.1f}/"
            f"{float(record.get('cooling_sp_c', 0.0)):5.1f} C  "
            f"light {float(record.get('lighting_level', 0.0)):.2f}  "
            f"vent {float(record.get('ventilation_ach', 0.0)):.2f}  "
            f"\"{str(record.get('rationale', ''))[:120]}\""
        )
    return "\n".join(lines)


def _objective_section(targets: dict[str, Any]) -> str:
    """Restate the ask last, because that is what the model acts on."""
    cadence = int(targets.get("cadence_steps", 1))
    return (
        "== YOUR TASK ==\n"
        f"Choose the policy for the next {cadence} steps that minimises kWh, cost, "
        "carbon and peak demand without letting PMV leave the band while the zone "
        "is occupied or about to be. Then call set_control_policy exactly once."
    )


def current_pmv(state: BuildingState) -> float:
    """PMV for a state, on the same assumptions the loop scores comfort with.

    Mean radiant temperature is taken as the zone air temperature — the RC model
    is well-mixed and carries no surface temperatures — which is the identical
    assumption `ClosedLoopRunner` makes, so the number the agent sees is the
    number it is graded on.
    """
    return pmv(
        air_temp_c=state.zone_temp_c,
        mean_radiant_temp_c=state.zone_temp_c,
        air_speed_ms=state.air_speed_ms,
        relative_humidity=state.relative_humidity,
        clothing_insulation=clothing_for_season(state.outdoor_temp_c),
    )
