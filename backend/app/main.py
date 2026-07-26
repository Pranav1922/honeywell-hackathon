"""FastAPI application: the HTTP surface and run lifecycle.

Contains no domain logic. Routes validate input, delegate to `loop.py` and
`db.py`, and serialise the result. Runs execute as background tasks so a request
returns immediately and the dashboard can stream progress.

Endpoints are specified in `docs/ARCHITECTURE.md` §6.

Implemented in Milestone 1; the SSE stream is exercised by the dashboard in
Milestone 3.
"""

from __future__ import annotations

from fastapi import FastAPI

from app.schemas import (
    DecisionResponse,
    HealthResponse,
    RunResponse,
    SavingsResponse,
    StartRunRequest,
    TimestepResponse,
)

app = FastAPI(title="Eco-Loop Building Agents", version="0.1.0")


@app.get("/api/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Liveness, plus whether EnergyPlus and the model endpoint are reachable."""
    raise NotImplementedError("Milestone 1")


@app.get("/api/config")
def get_config() -> dict:
    """Available controllers, simulators, models, scenarios and comfort limits."""
    raise NotImplementedError("Milestone 1")


@app.get("/api/scenarios")
def list_scenarios() -> list[dict]:
    """Scenario definitions from `backend/config/scenarios/`."""
    raise NotImplementedError("Milestone 1")


@app.post("/api/runs", response_model=RunResponse, status_code=202)
def start_run(request: StartRunRequest) -> RunResponse:
    """Create a run and execute it in the background."""
    raise NotImplementedError("Milestone 1")


@app.get("/api/runs", response_model=list[RunResponse])
def list_runs(limit: int = 50) -> list[RunResponse]:
    raise NotImplementedError("Milestone 1")


@app.get("/api/runs/{run_id}", response_model=RunResponse)
def get_run(run_id: int) -> RunResponse:
    raise NotImplementedError("Milestone 1")


@app.delete("/api/runs/{run_id}", status_code=204)
def delete_run(run_id: int) -> None:
    raise NotImplementedError("Milestone 1")


@app.post("/api/runs/{run_id}/stop", response_model=RunResponse)
def stop_run(run_id: int) -> RunResponse:
    """Request cooperative cancellation of an in-flight run."""
    raise NotImplementedError("Milestone 1")


@app.get("/api/runs/{run_id}/timeseries", response_model=list[TimestepResponse])
def get_timeseries(run_id: int, since_step: int = 0, stride: int = 1):
    """Telemetry rows; `since_step` for incremental polling, `stride` to downsample."""
    raise NotImplementedError("Milestone 1")


@app.get("/api/runs/{run_id}/decisions", response_model=list[DecisionResponse])
def get_decisions(run_id: int, since_step: int = 0):
    raise NotImplementedError("Milestone 1")


@app.get("/api/runs/{run_id}/summary")
def get_summary(run_id: int) -> dict:
    raise NotImplementedError("Milestone 1")


@app.get("/api/runs/{run_id}/stream")
def stream_run(run_id: int):
    """Server-Sent Events: live telemetry and decisions while a run executes."""
    raise NotImplementedError("Milestone 1")


@app.get("/api/compare", response_model=SavingsResponse)
def compare_runs(baseline_run_id: int, agent_run_id: int) -> SavingsResponse:
    """The savings report — percentage kWh reduction and the comfort check."""
    raise NotImplementedError("Milestone 1")


@app.get("/api/runs/{run_id}/export")
def export_run(run_id: int):
    """CSV export of a full run."""
    raise NotImplementedError("Milestone 1")
