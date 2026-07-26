"""SQLite persistence. Every SQL statement in the project lives in this module.

Three tables — `runs`, `timesteps`, `decisions`. Decisions are stored separately
because the supervisor's cadence differs from the simulation's: one decision
spans many timesteps, so merging them would duplicate rationales or leave most
rows null. Schema is documented in `docs/ARCHITECTURE.md` section 7.

The database is created on first connection; there is no migration step and no
setup command to forget before a demonstration.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from app.agents.base import Decision
from app.sim.base import BuildingState

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    label                TEXT    NOT NULL,
    controller           TEXT    NOT NULL,
    simulator            TEXT    NOT NULL,
    scenario             TEXT    NOT NULL,
    model                TEXT,
    baseline_run_id      INTEGER REFERENCES runs(id) ON DELETE SET NULL,
    status               TEXT    NOT NULL,
    error                TEXT,
    horizon_steps        INTEGER NOT NULL,
    timestep_seconds     INTEGER NOT NULL,
    started_at           TEXT    NOT NULL,
    finished_at          TEXT,
    total_kwh            REAL,
    peak_kw              REAL,
    cost                 REAL,
    co2_kg               REAL,
    comfort_violations   INTEGER,
    mean_ppd             REAL
);

CREATE TABLE IF NOT EXISTS timesteps (
    run_id           INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    step             INTEGER NOT NULL,
    sim_time         TEXT    NOT NULL,
    zone_temp_c      REAL    NOT NULL,
    outdoor_temp_c   REAL    NOT NULL,
    occupancy        REAL    NOT NULL,
    hvac_mode        TEXT    NOT NULL,
    heating_sp_c     REAL    NOT NULL,
    cooling_sp_c     REAL    NOT NULL,
    lighting_level   REAL    NOT NULL,
    ventilation_ach  REAL    NOT NULL,
    power_kw         REAL    NOT NULL,
    energy_kwh       REAL    NOT NULL,
    co2_ppm          REAL,
    pmv              REAL    NOT NULL,
    ppd              REAL    NOT NULL,
    comfort_ok       INTEGER NOT NULL,
    PRIMARY KEY (run_id, step)
);

CREATE TABLE IF NOT EXISTS decisions (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id            INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    step              INTEGER NOT NULL,
    sim_time          TEXT    NOT NULL,
    strategy          TEXT    NOT NULL,
    heating_sp_c      REAL    NOT NULL,
    cooling_sp_c      REAL    NOT NULL,
    lighting_level    REAL    NOT NULL,
    ventilation_ach   REAL    NOT NULL,
    rationale         TEXT    NOT NULL,
    tool_calls        TEXT,
    latency_ms        INTEGER,
    prompt_tokens     INTEGER,
    completion_tokens INTEGER,
    retries           INTEGER NOT NULL DEFAULT 0,
    fallback_used     INTEGER NOT NULL DEFAULT 0,
    guard_clamped     INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_timesteps_run_step ON timesteps(run_id, step);
CREATE INDEX IF NOT EXISTS idx_decisions_run_step ON decisions(run_id, step);
"""

_RUN_FIELDS = (
    "label",
    "controller",
    "simulator",
    "scenario",
    "model",
    "baseline_run_id",
    "status",
    "horizon_steps",
    "timestep_seconds",
    "started_at",
)

_SUMMARY_FIELDS = (
    "status",
    "error",
    "finished_at",
    "total_kwh",
    "peak_kw",
    "cost",
    "co2_kg",
    "comfort_violations",
    "mean_ppd",
)

_TIMESTEP_COLUMNS = (
    "step",
    "sim_time",
    "zone_temp_c",
    "outdoor_temp_c",
    "occupancy",
    "hvac_mode",
    "heating_sp_c",
    "cooling_sp_c",
    "lighting_level",
    "ventilation_ach",
    "power_kw",
    "energy_kwh",
    "co2_ppm",
    "pmv",
    "ppd",
    "comfort_ok",
)


def connect(database_path: Path) -> sqlite3.Connection:
    """Open a connection with foreign keys and WAL enabled, creating the schema.

    WAL matters because the API reads a run's telemetry from one connection while
    the background runner is still writing it from another.
    """
    database_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(database_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    init_schema(conn)
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Create tables and indexes if absent. Idempotent."""
    conn.executescript(_SCHEMA)
    conn.commit()


def create_run(conn: sqlite3.Connection, **fields: Any) -> int:
    """Insert a run in `running` status and return its id."""
    missing = [
        key
        for key in _RUN_FIELDS
        if key not in fields and key not in ("model", "baseline_run_id", "status")
    ]
    if missing:
        raise ValueError(f"create_run is missing fields: {', '.join(missing)}")

    values = {key: fields.get(key) for key in _RUN_FIELDS}
    values["status"] = fields.get("status", "running")

    columns = ", ".join(_RUN_FIELDS)
    placeholders = ", ".join(f":{name}" for name in _RUN_FIELDS)
    cursor = conn.execute(
        f"INSERT INTO runs ({columns}) VALUES ({placeholders})", values
    )
    conn.commit()
    return int(cursor.lastrowid)


def finish_run(conn: sqlite3.Connection, run_id: int, **summary: Any) -> None:
    """Mark a run complete, stopped or failed and store its aggregate metrics."""
    unknown = set(summary) - set(_SUMMARY_FIELDS)
    if unknown:
        raise ValueError(f"finish_run got unknown fields: {', '.join(sorted(unknown))}")

    assignments = ", ".join(f"{name} = :{name}" for name in summary)
    conn.execute(
        f"UPDATE runs SET {assignments} WHERE id = :run_id",
        {**summary, "run_id": run_id},
    )
    conn.commit()


def insert_timestep(
    conn: sqlite3.Connection,
    run_id: int,
    state: BuildingState,
    energy_kwh: float,
    pmv: float,
    ppd: float,
    comfort_ok: bool,
) -> None:
    """Persist one telemetry row.

    Does not commit — the runner batches commits so that a long horizon is not
    one fsync per timestep.
    """
    conn.execute(
        """
        INSERT INTO timesteps (
            run_id, step, sim_time, zone_temp_c, outdoor_temp_c, occupancy,
            hvac_mode, heating_sp_c, cooling_sp_c, lighting_level,
            ventilation_ach, power_kw, energy_kwh, co2_ppm, pmv, ppd, comfort_ok
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            state.step,
            state.sim_time.isoformat(),
            state.zone_temp_c,
            state.outdoor_temp_c,
            state.occupancy,
            state.hvac_mode,
            state.heating_sp_c,
            state.cooling_sp_c,
            state.lighting_level,
            state.ventilation_ach,
            state.power_kw,
            energy_kwh,
            state.co2_ppm,
            pmv,
            ppd,
            int(comfort_ok),
        ),
    )


def insert_decision(
    conn: sqlite3.Connection, run_id: int, decision: Decision
) -> None:
    """Persist one control decision. Does not commit; see `insert_timestep`."""
    conn.execute(
        """
        INSERT INTO decisions (
            run_id, step, sim_time, strategy, heating_sp_c, cooling_sp_c,
            lighting_level, ventilation_ach, rationale, tool_calls, latency_ms,
            prompt_tokens, completion_tokens, retries, fallback_used, guard_clamped
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            decision.step,
            decision.sim_time.isoformat(),
            decision.policy.strategy,
            decision.action.heating_sp_c,
            decision.action.cooling_sp_c,
            decision.action.lighting_level,
            decision.action.ventilation_ach,
            decision.rationale,
            json.dumps(decision.tool_calls) if decision.tool_calls else None,
            decision.latency_ms,
            decision.prompt_tokens,
            decision.completion_tokens,
            decision.retries,
            int(decision.fallback_used),
            int(decision.guard_clamped),
        ),
    )


def get_run(conn: sqlite3.Connection, run_id: int) -> dict[str, Any] | None:
    """One run record, or None if it does not exist."""
    row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    return dict(row) if row is not None else None


def list_runs(conn: sqlite3.Connection, limit: int = 50) -> list[dict[str, Any]]:
    """Runs newest first."""
    rows = conn.execute(
        "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(row) for row in rows]


def delete_run(conn: sqlite3.Connection, run_id: int) -> None:
    """Delete a run and, by cascade, its telemetry and decisions."""
    conn.execute("DELETE FROM runs WHERE id = ?", (run_id,))
    conn.commit()


def get_timeseries(
    conn: sqlite3.Connection,
    run_id: int,
    since_step: int = 0,
    stride: int = 1,
) -> list[dict[str, Any]]:
    """Telemetry rows, optionally downsampled for long horizons."""
    if stride < 1:
        raise ValueError(f"stride must be at least 1, got {stride}")
    columns = ", ".join(_TIMESTEP_COLUMNS)
    rows = conn.execute(
        f"""
        SELECT {columns} FROM timesteps
        WHERE run_id = ? AND step >= ? AND step % ? = 0
        ORDER BY step
        """,
        (run_id, since_step, stride),
    ).fetchall()
    return [_decode_timestep(row) for row in rows]


def get_decisions(
    conn: sqlite3.Connection,
    run_id: int,
    since_step: int = 0,
) -> list[dict[str, Any]]:
    """Agent decisions with rationale, tool calls and latency."""
    rows = conn.execute(
        """
        SELECT step, sim_time, strategy, heating_sp_c, cooling_sp_c,
               lighting_level, ventilation_ach, rationale, tool_calls, latency_ms,
               prompt_tokens, completion_tokens, retries, fallback_used,
               guard_clamped
        FROM decisions
        WHERE run_id = ? AND step >= ?
        ORDER BY step
        """,
        (run_id, since_step),
    ).fetchall()
    return [_decode_decision(row) for row in rows]


def count_timesteps(conn: sqlite3.Connection, run_id: int) -> int:
    """How many telemetry rows a run has written so far."""
    row = conn.execute(
        "SELECT COUNT(*) AS total FROM timesteps WHERE run_id = ?", (run_id,)
    ).fetchone()
    return int(row["total"])


def export_csv(conn: sqlite3.Connection, run_id: int) -> Iterable[str]:
    """Yield CSV lines for a full run, for offline analysis."""
    yield ",".join(_TIMESTEP_COLUMNS) + "\n"
    cursor = conn.execute(
        f"""
        SELECT {", ".join(_TIMESTEP_COLUMNS)} FROM timesteps
        WHERE run_id = ? ORDER BY step
        """,
        (run_id,),
    )
    for row in cursor:
        yield ",".join("" if value is None else str(value) for value in row) + "\n"


def _decode_timestep(row: sqlite3.Row) -> dict[str, Any]:
    """Row to dict, restoring the boolean that SQLite stores as an integer."""
    record = dict(row)
    record["comfort_ok"] = bool(record["comfort_ok"])
    return record


def _decode_decision(row: sqlite3.Row) -> dict[str, Any]:
    """Row to dict, restoring booleans and the JSON tool-call payload."""
    record = dict(row)
    record["fallback_used"] = bool(record["fallback_used"])
    record["guard_clamped"] = bool(record["guard_clamped"])
    record["tool_calls"] = (
        json.loads(record["tool_calls"]) if record["tool_calls"] else None
    )
    return record
