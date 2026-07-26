"""FastAPI application: the HTTP surface and run lifecycle.

Contains no domain logic. Routes validate input, delegate to `loop.py` and
`db.py`, and serialise the result. Runs execute on a background thread so a
request returns immediately and the dashboard can stream progress.

Endpoints are specified in `docs/ARCHITECTURE.md` section 6.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from datetime import datetime, timezone
from typing import Iterator
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse

from app import db
from app.config import Settings, list_scenarios, load_scenario, load_settings
from app.energy import RunSummary, compare
from app.loop import (
    CONTROLLERS,
    SIMULATORS,
    ClosedLoopRunner,
    build_controller,
    build_simulator,
    settings_for_scenario,
)
from app.schemas import (
    DecisionResponse,
    HealthResponse,
    RunResponse,
    SavingsResponse,
    StartRunRequest,
    TimestepResponse,
)
from app.utils.timeutil import steps_for_days

app = FastAPI(title="Eco-Loop Building Agents", version="0.1.0")

SETTINGS = load_settings()
STREAM_POLL_SECONDS = 0.5
STREAM_IDLE_TIMEOUT_SECONDS = 30.0

_runners: dict[int, ClosedLoopRunner] = {}
_runners_lock = threading.Lock()


@app.get("/api/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Liveness, plus whether EnergyPlus and the model endpoint are reachable."""
    return HealthResponse(
        status="ok",
        energyplus_available=(SETTINGS.energyplus_dir / "pyenergyplus").is_dir(),
        llm_available=_endpoint_reachable(SETTINGS.llm_base_url),
        llm_model=SETTINGS.llm_model,
        database=str(SETTINGS.database_path),
    )


@app.get("/api/config")
def get_config() -> dict:
    """Available controllers, simulators, models, scenarios and comfort limits."""
    return {
        "controllers": list(CONTROLLERS),
        "simulators": list(SIMULATORS),
        "scenarios": [s.id for s in list_scenarios(SETTINGS.scenarios_dir)],
        "llm_model": SETTINGS.llm_model,
        "comfort": {
            "pmv_low": SETTINGS.comfort_pmv_low,
            "pmv_high": SETTINGS.comfort_pmv_high,
            "min_zone_temp_c": SETTINGS.min_zone_temp_c,
            "max_zone_temp_c": SETTINGS.max_zone_temp_c,
        },
        "tariff_per_kwh": SETTINGS.tariff_per_kwh,
        "grid_carbon_kg_per_kwh": SETTINGS.grid_carbon_kg_per_kwh,
    }


@app.get("/api/scenarios")
def list_scenario_definitions() -> list[dict]:
    """Scenario definitions from `backend/config/scenarios/`."""
    return [
        {
            "id": scenario.id,
            "label": scenario.label,
            "start": scenario.start.isoformat(),
            "days": scenario.days,
            "timestep_seconds": scenario.timestep_seconds,
            "horizon_steps": scenario.horizon_steps,
            "occupied_hours": list(scenario.occupied_hours),
        }
        for scenario in list_scenarios(SETTINGS.scenarios_dir)
    ]


@app.post("/api/runs", response_model=RunResponse, status_code=202)
def start_run(request: StartRunRequest) -> RunResponse:
    """Create a run and execute it in the background."""
    try:
        scenario = load_scenario(request.scenario, SETTINGS.scenarios_dir)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if request.controller not in CONTROLLERS:
        raise HTTPException(
            status_code=400, detail=f"unknown controller {request.controller!r}"
        )
    if request.simulator not in SIMULATORS:
        raise HTTPException(
            status_code=400, detail=f"unknown simulator {request.simulator!r}"
        )

    settings = settings_for_scenario(SETTINGS, scenario)
    horizon_steps = request.horizon_steps or scenario.horizon_steps
    if horizon_steps <= 0:
        raise HTTPException(status_code=400, detail="horizon_steps must be positive")

    conn = db.connect(settings.database_path)
    try:
        run_id = db.create_run(
            conn,
            label=request.label or f"{scenario.id} / {request.controller}",
            controller=request.controller,
            simulator=request.simulator,
            scenario=scenario.id,
            model=None,
            baseline_run_id=request.baseline_run_id,
            horizon_steps=horizon_steps,
            timestep_seconds=scenario.timestep_seconds,
            started_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
    finally:
        conn.close()

    runner = ClosedLoopRunner(
        run_id=run_id,
        simulator=build_simulator(scenario, request.simulator, horizon_steps),
        controller=build_controller(request.controller, scenario, settings),
        settings=settings,
    )
    with _runners_lock:
        _runners[run_id] = runner

    threading.Thread(
        target=_execute, args=(run_id, runner), name=f"run-{run_id}", daemon=True
    ).start()

    return _run_response(settings, run_id)


@app.get("/api/runs", response_model=list[RunResponse])
def list_runs(limit: int = 50) -> list[RunResponse]:
    """Runs newest first, with their summary metrics."""
    conn = db.connect(SETTINGS.database_path)
    try:
        return [RunResponse(**_normalise_run(row)) for row in db.list_runs(conn, limit)]
    finally:
        conn.close()


@app.get("/api/runs/{run_id}", response_model=RunResponse)
def get_run(run_id: int) -> RunResponse:
    """One run's full record and summary."""
    return _run_response(SETTINGS, run_id)


@app.delete("/api/runs/{run_id}", status_code=204)
def delete_run(run_id: int) -> None:
    """Delete a run and, by cascade, its telemetry."""
    conn = db.connect(SETTINGS.database_path)
    try:
        if db.get_run(conn, run_id) is None:
            raise HTTPException(status_code=404, detail=f"run {run_id} not found")
        db.delete_run(conn, run_id)
    finally:
        conn.close()
    with _runners_lock:
        _runners.pop(run_id, None)


@app.post("/api/runs/{run_id}/stop", response_model=RunResponse)
def stop_run(run_id: int) -> RunResponse:
    """Request cooperative cancellation of an in-flight run."""
    with _runners_lock:
        runner = _runners.get(run_id)
    if runner is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} is not executing")
    runner.stop()
    return _run_response(SETTINGS, run_id)


@app.get("/api/runs/{run_id}/timeseries", response_model=list[TimestepResponse])
def get_timeseries(
    run_id: int, since_step: int = 0, stride: int = 1
) -> list[TimestepResponse]:
    """Telemetry rows; `since_step` for incremental polling, `stride` to downsample."""
    if stride < 1:
        raise HTTPException(status_code=400, detail="stride must be at least 1")
    conn = db.connect(SETTINGS.database_path)
    try:
        rows = db.get_timeseries(conn, run_id, since_step, stride)
    finally:
        conn.close()
    return [TimestepResponse(**row) for row in rows]


@app.get("/api/runs/{run_id}/decisions", response_model=list[DecisionResponse])
def get_decisions(run_id: int, since_step: int = 0) -> list[DecisionResponse]:
    """Agent decisions with rationale, tool calls and latency."""
    conn = db.connect(SETTINGS.database_path)
    try:
        rows = db.get_decisions(conn, run_id, since_step)
    finally:
        conn.close()
    return [DecisionResponse(**row) for row in rows]


@app.get("/api/runs/{run_id}/summary")
def get_summary(run_id: int) -> dict:
    """Aggregated KPIs for one run, including how far it has progressed."""
    conn = db.connect(SETTINGS.database_path)
    try:
        record = db.get_run(conn, run_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"run {run_id} not found")
        completed = db.count_timesteps(conn, run_id)
    finally:
        conn.close()

    horizon = record["horizon_steps"]
    return {
        "run_id": run_id,
        "status": record["status"],
        "completed_steps": completed,
        "horizon_steps": horizon,
        "progress": completed / horizon if horizon else 0.0,
        "total_kwh": record["total_kwh"],
        "peak_kw": record["peak_kw"],
        "cost": record["cost"],
        "co2_kg": record["co2_kg"],
        "comfort_violations": record["comfort_violations"],
        "mean_ppd": record["mean_ppd"],
    }


@app.get("/api/runs/{run_id}/stream")
def stream_run(run_id: int) -> StreamingResponse:
    """Server-Sent Events: live telemetry and decisions while a run executes."""
    conn = db.connect(SETTINGS.database_path)
    try:
        if db.get_run(conn, run_id) is None:
            raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    finally:
        conn.close()

    return StreamingResponse(
        _stream_events(run_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/compare", response_model=SavingsResponse)
def compare_runs(baseline_run_id: int, agent_run_id: int) -> SavingsResponse:
    """The savings report — percentage kWh reduction and the comfort check."""
    conn = db.connect(SETTINGS.database_path)
    try:
        baseline = _summary_from_record(conn, baseline_run_id)
        agent = _summary_from_record(conn, agent_run_id)
    finally:
        conn.close()

    report = compare(baseline, agent)
    return SavingsResponse(
        baseline_run_id=baseline_run_id,
        agent_run_id=agent_run_id,
        baseline_kwh=report.baseline.total_kwh,
        agent_kwh=report.agent.total_kwh,
        kwh_saved=report.kwh_saved,
        kwh_saved_pct=report.kwh_saved_pct,
        peak_reduction_kw=report.peak_reduction_kw,
        peak_reduction_pct=report.peak_reduction_pct,
        cost_saved=report.cost_saved,
        co2_saved_kg=report.co2_saved_kg,
        baseline_comfort_violations=report.baseline.comfort_violations,
        agent_comfort_violations=report.agent.comfort_violations,
        comfort_maintained=report.comfort_maintained,
    )


@app.get("/api/runs/{run_id}/export")
def export_run(run_id: int) -> StreamingResponse:
    """CSV export of a full run."""
    conn = db.connect(SETTINGS.database_path)
    if db.get_run(conn, run_id) is None:
        conn.close()
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")

    def emit() -> Iterator[str]:
        try:
            yield from db.export_csv(conn, run_id)
        finally:
            conn.close()

    return StreamingResponse(
        emit(),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="run-{run_id}.csv"'
        },
    )


# -- internals --------------------------------------------------------------


def _execute(run_id: int, runner: ClosedLoopRunner) -> None:
    """Run the loop on a background thread; failures are already persisted."""
    try:
        runner.run()
    except Exception:
        # `ClosedLoopRunner.run` records the failure on the run row before it
        # re-raises, so the API can report it. Nothing further to do here, and
        # the exception must not escape and kill the thread silently.
        pass
    finally:
        with _runners_lock:
            _runners.pop(run_id, None)


def _stream_events(run_id: int) -> Iterator[str]:
    """Yield SSE frames for new telemetry and decisions until the run settles.

    Driven from the database rather than from an in-memory queue, so a browser
    that reconnects mid-run resumes cleanly and a completed run can be replayed.
    """
    next_timestep_step = 0
    next_decision_step = 0
    idle_since = time.monotonic()

    while True:
        conn = db.connect(SETTINGS.database_path)
        try:
            record = db.get_run(conn, run_id)
            timesteps = db.get_timeseries(conn, run_id, since_step=next_timestep_step)
            decisions = db.get_decisions(conn, run_id, since_step=next_decision_step)
        finally:
            conn.close()

        if record is None:
            yield _sse("error", {"detail": f"run {run_id} was deleted"})
            return

        for row in timesteps:
            yield _sse("timestep", row)
            next_timestep_step = row["step"] + 1
        for row in decisions:
            yield _sse("decision", row)
            next_decision_step = row["step"] + 1

        if timesteps or decisions:
            idle_since = time.monotonic()

        if record["status"] != "running":
            yield _sse("complete", {"run_id": run_id, "status": record["status"]})
            return
        if time.monotonic() - idle_since > STREAM_IDLE_TIMEOUT_SECONDS:
            yield _sse("error", {"detail": "run produced no data; stream timed out"})
            return

        time.sleep(STREAM_POLL_SECONDS)


def _sse(event: str, payload: dict) -> str:
    """One Server-Sent Event frame."""
    return f"event: {event}\ndata: {json.dumps(payload, default=str)}\n\n"


def _run_response(settings: Settings, run_id: int) -> RunResponse:
    """Load a run record and shape it for the wire."""
    conn = db.connect(settings.database_path)
    try:
        record = db.get_run(conn, run_id)
    finally:
        conn.close()
    if record is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    return RunResponse(**_normalise_run(record))


def _normalise_run(record: dict) -> dict:
    """Drop columns the wire model does not carry."""
    return {key: value for key, value in record.items() if key in RunResponse.model_fields}


def _summary_from_record(conn, run_id: int) -> RunSummary:
    """Rebuild a `RunSummary` from a completed run's stored metrics."""
    record = db.get_run(conn, run_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    if record["total_kwh"] is None:
        raise HTTPException(
            status_code=409,
            detail=f"run {run_id} has not completed; nothing to compare",
        )
    occupied = conn.execute(
        "SELECT COUNT(*) AS n FROM timesteps WHERE run_id = ? AND occupancy > 0",
        (run_id,),
    ).fetchone()["n"]
    return RunSummary(
        run_id=run_id,
        total_kwh=record["total_kwh"],
        peak_kw=record["peak_kw"],
        cost=record["cost"],
        co2_kg=record["co2_kg"],
        comfort_violations=record["comfort_violations"],
        occupied_steps=int(occupied),
        mean_ppd=record["mean_ppd"],
    )


def _endpoint_reachable(base_url: str, timeout_seconds: float = 0.25) -> bool:
    """Whether something is listening at `base_url`, without sending a request."""
    parsed = urlparse(base_url)
    if parsed.hostname is None:
        return False
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection(
            (parsed.hostname, port), timeout=timeout_seconds
        ):
            return True
    except OSError:
        return False
