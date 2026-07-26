"""Pydantic models for the HTTP boundary.

Kept distinct from the internal dataclasses in `sim/base.py` and `agents/base.py`
so the wire format can change without disturbing the domain, and so the domain
carries no serialisation concerns.

Implemented in Milestone 1.
"""

from __future__ import annotations

from pydantic import BaseModel


class StartRunRequest(BaseModel):
    """POST /api/runs"""

    scenario: str
    controller: str                      # 'baseline' | 'rule' | 'llm'
    simulator: str = "toy"               # 'toy' | 'energyplus'
    horizon_steps: int | None = None     # defaults to the scenario's horizon
    label: str | None = None
    baseline_run_id: int | None = None


class RunResponse(BaseModel):
    """A run record with its aggregate metrics."""

    id: int
    label: str
    controller: str
    simulator: str
    scenario: str
    model: str | None
    status: str
    error: str | None
    horizon_steps: int
    timestep_seconds: int
    started_at: str
    finished_at: str | None
    total_kwh: float | None
    peak_kw: float | None
    cost: float | None
    co2_kg: float | None
    comfort_violations: int | None
    mean_ppd: float | None
    baseline_run_id: int | None


class TimestepResponse(BaseModel):
    """One telemetry row."""

    step: int
    sim_time: str
    zone_temp_c: float
    outdoor_temp_c: float
    occupancy: float
    hvac_mode: str
    heating_sp_c: float
    cooling_sp_c: float
    lighting_level: float
    ventilation_ach: float
    power_kw: float
    energy_kwh: float
    co2_ppm: float | None
    pmv: float
    ppd: float
    comfort_ok: bool


class DecisionResponse(BaseModel):
    """One agent decision, including its explanation and latency."""

    step: int
    sim_time: str
    strategy: str
    heating_sp_c: float
    cooling_sp_c: float
    lighting_level: float
    ventilation_ach: float
    rationale: str
    tool_calls: list[dict] | None
    latency_ms: int | None
    prompt_tokens: int | None
    completion_tokens: int | None
    retries: int
    fallback_used: bool
    guard_clamped: bool


class SavingsResponse(BaseModel):
    """GET /api/compare — the headline result."""

    baseline_run_id: int
    agent_run_id: int
    baseline_kwh: float
    agent_kwh: float
    kwh_saved: float
    kwh_saved_pct: float
    peak_reduction_kw: float
    peak_reduction_pct: float
    cost_saved: float
    co2_saved_kg: float
    baseline_comfort_violations: int
    agent_comfort_violations: int
    comfort_maintained: bool


class HealthResponse(BaseModel):
    """GET /api/health"""

    status: str
    energyplus_available: bool
    llm_available: bool
    llm_model: str
    database: str
