"""Energy accounting and the baseline-vs-agent savings comparison.

Pure functions, no I/O. `SavingsReport` produces the headline number the
hackathon is scored on: percentage kWh reduction achieved while comfort is
maintained.

The comfort check is not decoration. Any controller can save energy by letting
the building drift, so a saving is only reported as legitimate when the agent
did not leave occupants worse off than the baseline did.
"""

from __future__ import annotations

from dataclasses import dataclass

SECONDS_PER_HOUR = 3600.0


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

    @property
    def comfort_violation_rate(self) -> float:
        """Fraction of occupied steps that fell outside the comfort band."""
        if self.occupied_steps == 0:
            return 0.0
        return self.comfort_violations / self.occupied_steps


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
    if power_kw < 0:
        raise ValueError(f"power_kw must be non-negative, got {power_kw}")
    if timestep_seconds <= 0:
        raise ValueError(f"timestep_seconds must be positive, got {timestep_seconds}")
    return power_kw * timestep_seconds / SECONDS_PER_HOUR


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
    """Reduce a run's traces to its aggregate metrics.

    Comfort is scored over occupied steps only — an empty building cannot be
    uncomfortable, and counting its unoccupied hours would dilute the metric
    until it said nothing.
    """
    lengths = {
        len(energies_kwh),
        len(powers_kw),
        len(ppds),
        len(comfort_flags),
        len(occupancies),
    }
    if len(lengths) != 1:
        raise ValueError(f"trace lengths disagree: {sorted(lengths)}")

    total_kwh = sum(energies_kwh)
    peak_kw = max(powers_kw) if powers_kw else 0.0

    occupied_ppds = [
        ppd for ppd, occupancy in zip(ppds, occupancies) if occupancy > 0.0
    ]
    occupied_steps = len(occupied_ppds)
    violations = sum(
        1
        for ok, occupancy in zip(comfort_flags, occupancies)
        if occupancy > 0.0 and not ok
    )

    return RunSummary(
        run_id=run_id,
        total_kwh=total_kwh,
        peak_kw=peak_kw,
        cost=total_kwh * tariff_per_kwh,
        co2_kg=total_kwh * carbon_kg_per_kwh,
        comfort_violations=violations,
        occupied_steps=occupied_steps,
        mean_ppd=sum(occupied_ppds) / occupied_steps if occupied_steps else 0.0,
    )


def compare(baseline: RunSummary, agent: RunSummary) -> SavingsReport:
    """Difference two summaries.

    `comfort_maintained` is false when the agent bought its savings with
    occupant comfort — that is, when it produced more comfort violations than
    the baseline did.
    """
    kwh_saved = baseline.total_kwh - agent.total_kwh
    peak_reduction_kw = baseline.peak_kw - agent.peak_kw

    return SavingsReport(
        baseline=baseline,
        agent=agent,
        kwh_saved=kwh_saved,
        kwh_saved_pct=_percentage(kwh_saved, baseline.total_kwh),
        peak_reduction_kw=peak_reduction_kw,
        peak_reduction_pct=_percentage(peak_reduction_kw, baseline.peak_kw),
        cost_saved=baseline.cost - agent.cost,
        co2_saved_kg=baseline.co2_kg - agent.co2_kg,
        comfort_violation_delta=(
            agent.comfort_violations - baseline.comfort_violations
        ),
        comfort_maintained=(
            agent.comfort_violations <= baseline.comfort_violations
        ),
    )


def _percentage(delta: float, reference: float) -> float:
    """`delta` as a percentage of `reference`, zero when there is no reference."""
    if reference == 0.0:
        return 0.0
    return 100.0 * delta / reference
