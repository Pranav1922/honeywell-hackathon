"""Energy accounting and the baseline-vs-agent savings comparison.

Pure functions, no I/O. `SavingsReport` produces the headline number the
hackathon is scored on: percentage kWh reduction achieved while comfort is
maintained.

Implemented in Milestone 1.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RunSummary:
    """Aggregate metrics for one run."""

    run_id: int
    total_kwh: float
    peak_kw: float
    cost: float
    co2_kg: float
    comfort_violations: int
    occupied_steps: int
    mean_ppd: float


@dataclass(frozen=True)
class SavingsReport:
    """Baseline versus agent — the deliverable-3 result."""

    baseline: RunSummary
    agent: RunSummary
    kwh_saved: float
    kwh_saved_pct: float
    peak_reduction_kw: float
    peak_reduction_pct: float
    cost_saved: float
    co2_saved_kg: float
    comfort_violation_delta: int
    comfort_maintained: bool


def step_energy_kwh(power_kw: float, timestep_seconds: int) -> float:
    """Energy consumed over one timestep."""
    raise NotImplementedError("Milestone 1")


def summarise(
    run_id: int,
    energies_kwh: list[float],
    powers_kw: list[float],
    ppds: list[float],
    comfort_flags: list[bool],
    occupancies: list[float],
    tariff_per_kwh: float,
    carbon_kg_per_kwh: float,
) -> RunSummary:
    """Reduce a run's traces to its aggregate metrics."""
    raise NotImplementedError("Milestone 1")


def compare(baseline: RunSummary, agent: RunSummary) -> SavingsReport:
    """Difference two summaries. `comfort_maintained` is false if the agent
    achieved savings by degrading comfort relative to the baseline."""
    raise NotImplementedError("Milestone 1")
