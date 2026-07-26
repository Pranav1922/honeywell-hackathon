"""SQLite persistence. Every SQL statement in the project lives in this module.

Three tables — `runs`, `timesteps`, `decisions`. Decisions are stored separately
because the supervisor's cadence differs from the simulation's: one decision
spans many timesteps, so merging them would duplicate rationales or leave most
rows null. Schema is documented in `docs/ARCHITECTURE.md` §7.

Implemented in Milestone 1.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Iterable

from app.agents.base import Decision
from app.sim.base import BuildingState

SCHEMA_VERSION = 1


def connect(database_path: Path) -> sqlite3.Connection:
    """Open a connection with foreign keys and WAL enabled."""
    raise NotImplementedError("Milestone 1")


def init_schema(conn: sqlite3.Connection) -> None:
    """Create tables and indexes if absent. Idempotent."""
    raise NotImplementedError("Milestone 1")


def create_run(conn: sqlite3.Connection, **fields: Any) -> int:
    """Insert a run in `running` status and return its id."""
    raise NotImplementedError("Milestone 1")


def finish_run(conn: sqlite3.Connection, run_id: int, **summary: Any) -> None:
    """Mark a run complete, stopped or failed and store its aggregate metrics."""
    raise NotImplementedError("Milestone 1")


def insert_timestep(
    conn: sqlite3.Connection,
    run_id: int,
    state: BuildingState,
    energy_kwh: float,
    pmv: float,
    ppd: float,
    comfort_ok: bool,
) -> None:
    raise NotImplementedError("Milestone 1")


def insert_decision(conn: sqlite3.Connection, run_id: int, decision: Decision) -> None:
    raise NotImplementedError("Milestone 1")


def get_run(conn: sqlite3.Connection, run_id: int) -> dict[str, Any] | None:
    raise NotImplementedError("Milestone 1")


def list_runs(conn: sqlite3.Connection, limit: int = 50) -> list[dict[str, Any]]:
    raise NotImplementedError("Milestone 1")


def delete_run(conn: sqlite3.Connection, run_id: int) -> None:
    raise NotImplementedError("Milestone 1")


def get_timeseries(
    conn: sqlite3.Connection,
    run_id: int,
    since_step: int = 0,
    stride: int = 1,
) -> list[dict[str, Any]]:
    """Telemetry rows, optionally downsampled for long horizons."""
    raise NotImplementedError("Milestone 1")


def get_decisions(
    conn: sqlite3.Connection,
    run_id: int,
    since_step: int = 0,
) -> list[dict[str, Any]]:
    raise NotImplementedError("Milestone 1")


def export_csv(conn: sqlite3.Connection, run_id: int) -> Iterable[str]:
    """Yield CSV lines for a full run, for offline analysis."""
    raise NotImplementedError("Milestone 1")
