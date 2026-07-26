"""Headless entrypoint: run a scenario and print the savings.

The fastest way to verify the loop without the API or the dashboard, and the
form used in CI.

    python backend/cli.py --scenario summer_week --controller baseline
    python backend/cli.py --scenario summer_week --controller rule --compare 1
    python backend/cli.py --list-scenarios
    python backend/cli.py --list-runs
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

if __package__ in (None, ""):  # invoked as a script rather than a module
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import db
from app.agents.client import LLMConfigError
from app.config import Scenario, Settings, list_scenarios, load_scenario, load_settings
from app.energy import RunSummary, SavingsReport, compare
from app.loop import (
    CONTROLLERS,
    SIMULATORS,
    ClosedLoopRunner,
    build_controller,
    build_simulator,
    settings_for_scenario,
)
from app.utils.timeutil import steps_for_days

PROGRESS_EVERY_STEPS = 96  # one dot per simulated day at a 15-minute timestep


def build_parser() -> argparse.ArgumentParser:
    """Command-line surface: scenario, controller, simulator, horizon, compare."""
    parser = argparse.ArgumentParser(
        prog="cli.py",
        description="Run an autonomous building control simulation.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--scenario", default="summer_week", help="Scenario id to run."
    )
    parser.add_argument(
        "--controller",
        default="baseline",
        choices=CONTROLLERS,
        help="Control strategy to run.",
    )
    parser.add_argument(
        "--simulator",
        default="toy",
        choices=SIMULATORS,
        help="Simulation backend.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="Override the scenario's horizon, in days.",
    )
    parser.add_argument(
        "--label", default=None, help="Human-readable name for this run."
    )
    parser.add_argument(
        "--compare",
        type=int,
        default=None,
        metavar="RUN_ID",
        help="Report savings against an earlier run, normally the baseline.",
    )
    parser.add_argument(
        "--database", type=Path, default=None, help="Override the database path."
    )
    parser.add_argument(
        "--list-scenarios", action="store_true", help="List scenarios and exit."
    )
    parser.add_argument(
        "--list-runs", action="store_true", help="List recorded runs and exit."
    )
    parser.add_argument(
        "--quiet", action="store_true", help="Suppress the progress indicator."
    )
    return parser


def main() -> int:
    """Run the requested scenario and print kWh, savings and comfort violations."""
    args = build_parser().parse_args()
    settings = load_settings()
    if args.database is not None:
        settings = _with_database(settings, args.database)

    if args.list_scenarios:
        _print_scenarios(settings)
        return 0
    if args.list_runs:
        _print_runs(settings)
        return 0

    try:
        scenario = load_scenario(args.scenario, settings.scenarios_dir)
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    settings = settings_for_scenario(settings, scenario)
    horizon_steps = (
        steps_for_days(args.days, scenario.timestep_seconds)
        if args.days is not None
        else scenario.horizon_steps
    )

    try:
        summary = _execute(args, scenario, settings, horizon_steps)
    except LLMConfigError as exc:
        # A missing API key is a setup problem, not a crash. Print the guidance,
        # not a traceback that buries it.
        print(f"error: {exc}", file=sys.stderr)
        return 2
    _print_summary(args, scenario, summary, horizon_steps)

    if args.compare is not None:
        report = _build_report(settings, args.compare, summary)
        if report is None:
            print(f"\nerror: run {args.compare} not found or never completed.",
                  file=sys.stderr)
            return 2
        _print_savings(report)

    return 0


# -- execution --------------------------------------------------------------


def _execute(
    args: argparse.Namespace,
    scenario: Scenario,
    settings: Settings,
    horizon_steps: int,
) -> RunSummary:
    """Create the run record, run the loop, and return its summary."""
    simulator = build_simulator(scenario, args.simulator, horizon_steps, settings)
    controller = build_controller(args.controller, scenario, settings)

    conn = db.connect(settings.database_path)
    try:
        run_id = db.create_run(
            conn,
            label=args.label or f"{scenario.id} / {args.controller}",
            controller=args.controller,
            simulator=args.simulator,
            scenario=scenario.id,
            model=settings.llm_model if args.controller == "llm" else None,
            baseline_run_id=args.compare,
            horizon_steps=horizon_steps,
            timestep_seconds=scenario.timestep_seconds,
            started_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
    finally:
        conn.close()

    print(
        f"Run {run_id}: {scenario.label} under '{args.controller}' control, "
        f"{horizon_steps} steps of {scenario.timestep_seconds}s "
        f"({horizon_steps * scenario.timestep_seconds / 86400:.0f} days)"
    )

    progress = None if args.quiet else _progress_printer()
    runner = ClosedLoopRunner(
        run_id=run_id,
        simulator=simulator,
        controller=controller,
        settings=settings,
        on_progress=progress,
    )
    summary = runner.run()
    if progress is not None:
        print()
    return summary


def _progress_printer():
    """A dot per simulated day, so a long horizon visibly advances."""

    def emit(state) -> None:
        if state.step % PROGRESS_EVERY_STEPS == 0:
            print(".", end="", flush=True)

    return emit


def _build_report(
    settings: Settings, baseline_run_id: int, agent: RunSummary
) -> SavingsReport | None:
    """Load an earlier run's stored metrics and difference them against this one."""
    conn = db.connect(settings.database_path)
    try:
        record = db.get_run(conn, baseline_run_id)
    finally:
        conn.close()

    if record is None or record["total_kwh"] is None:
        return None

    baseline = RunSummary(
        run_id=baseline_run_id,
        total_kwh=record["total_kwh"],
        peak_kw=record["peak_kw"],
        cost=record["cost"],
        co2_kg=record["co2_kg"],
        comfort_violations=record["comfort_violations"],
        occupied_steps=agent.occupied_steps,
        mean_ppd=record["mean_ppd"],
    )
    return compare(baseline, agent)


def _with_database(settings: Settings, database_path: Path) -> Settings:
    """Point the settings at a different database file."""
    import dataclasses

    return dataclasses.replace(settings, database_path=database_path.resolve())


# -- output -----------------------------------------------------------------


def _print_scenarios(settings: Settings) -> None:
    """List every scenario definition found on disk."""
    scenarios = list_scenarios(settings.scenarios_dir)
    if not scenarios:
        print(f"No scenarios found in {settings.scenarios_dir}")
        return
    print(f"{'ID':<16} {'DAYS':>5} {'STEP':>6}  LABEL")
    for scenario in scenarios:
        print(
            f"{scenario.id:<16} {scenario.days:>5} "
            f"{scenario.timestep_seconds:>5}s  {scenario.label}"
        )


def _print_runs(settings: Settings) -> None:
    """List recorded runs newest first."""
    conn = db.connect(settings.database_path)
    try:
        runs = db.list_runs(conn)
    finally:
        conn.close()

    if not runs:
        print("No runs recorded yet.")
        return
    print(
        f"{'ID':>4}  {'STATUS':<9} {'CONTROLLER':<10} {'SCENARIO':<14} "
        f"{'kWh':>9} {'PEAK kW':>8} {'VIOL':>5}"
    )
    for run in runs:
        kwh = "-" if run["total_kwh"] is None else f"{run['total_kwh']:9.2f}"
        peak = "-" if run["peak_kw"] is None else f"{run['peak_kw']:8.2f}"
        violations = (
            "-" if run["comfort_violations"] is None else f"{run['comfort_violations']:5d}"
        )
        print(
            f"{run['id']:>4}  {run['status']:<9} {run['controller']:<10} "
            f"{run['scenario']:<14} {kwh} {peak} {violations}"
        )


def _print_summary(
    args: argparse.Namespace,
    scenario: Scenario,
    summary: RunSummary,
    horizon_steps: int,
) -> None:
    """The headline metrics for a single run."""
    print()
    print("=" * 58)
    print(f"  RUN {summary.run_id} COMPLETE  —  {scenario.label} / {args.controller}")
    print("=" * 58)
    print(f"  Total Energy         {summary.total_kwh:10.2f} kWh")
    print(f"  Peak Demand          {summary.peak_kw:10.2f} kW")
    print(f"  Comfort Violations   {summary.comfort_violations:10d} "
          f"of {summary.occupied_steps} occupied steps "
          f"({summary.comfort_violation_rate:.1%})")
    print(f"  Average PMV          {_mean_pmv(args, summary):10.3f}")
    print(f"  Average PPD          {summary.mean_ppd:10.2f} %")
    print(f"  Energy Cost          {summary.cost:10.2f}")
    print(f"  Carbon               {summary.co2_kg:10.2f} kg CO2")
    print(f"  Simulated Steps      {horizon_steps:10d}")
    print("=" * 58)


def _mean_pmv(args: argparse.Namespace, summary: RunSummary) -> float:
    """Mean PMV over occupied steps, read back from the stored telemetry.

    PMV is signed and averaging it in the runner would hide whether a building
    ran cold or hot, so it is aggregated here from the persisted rows instead.
    """
    settings = load_settings()
    if args.database is not None:
        settings = _with_database(settings, args.database)
    conn = db.connect(settings.database_path)
    try:
        row = conn.execute(
            """
            SELECT AVG(pmv) AS mean_pmv FROM timesteps
            WHERE run_id = ? AND occupancy > 0
            """,
            (summary.run_id,),
        ).fetchone()
    finally:
        conn.close()
    return float(row["mean_pmv"]) if row["mean_pmv"] is not None else 0.0


def _print_savings(report: SavingsReport) -> None:
    """The baseline-versus-agent comparison."""
    print()
    print("=" * 58)
    print(f"  SAVINGS  —  run {report.agent.run_id} vs baseline run "
          f"{report.baseline.run_id}")
    print("=" * 58)
    print(f"  Baseline Energy      {report.baseline.total_kwh:10.2f} kWh")
    print(f"  Agent Energy         {report.agent.total_kwh:10.2f} kWh")
    print(f"  Energy Saved         {report.kwh_saved:10.2f} kWh "
          f"({report.kwh_saved_pct:+.1f}%)")
    print(f"  Peak Reduction       {report.peak_reduction_kw:10.2f} kW "
          f"({report.peak_reduction_pct:+.1f}%)")
    print(f"  Cost Saved           {report.cost_saved:10.2f}")
    print(f"  Carbon Avoided       {report.co2_saved_kg:10.2f} kg CO2")
    print(f"  Comfort Violations   {report.baseline.comfort_violations:10d} "
          f"-> {report.agent.comfort_violations} "
          f"({report.comfort_violation_delta:+d})")
    verdict = "MAINTAINED" if report.comfort_maintained else "DEGRADED"
    print(f"  Comfort              {verdict:>10}")
    print("=" * 58)


if __name__ == "__main__":
    raise SystemExit(main())
