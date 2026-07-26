"""Typed, environment-driven configuration and scenario loading.

Everything that differs between machines lives here — the EnergyPlus install
path, the model endpoint, the database location — so nothing machine-specific is
committed. See `.env.example` for the template.

Scenario definitions are configuration too, so their loader lives here rather
than in a module of its own. A scenario fixes everything a run needs *except*
the controller, which is what makes a baseline run and an agent run comparable.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BACKEND_DIR.parent


@dataclass(frozen=True)
class Settings:
    """Runtime configuration for the whole backend."""

    # storage
    database_path: Path = BACKEND_DIR / "ecoloop.db"
    scenarios_dir: Path = BACKEND_DIR / "config" / "scenarios"

    # language model
    llm_base_url: str = "http://localhost:11434/v1"
    llm_model: str = "qwen2.5:7b-instruct"
    llm_api_key: str = "ollama"
    llm_timeout_seconds: float = 30.0
    llm_temperature: float = 0.2

    # agent cadence and self-correction budget
    decision_cadence_steps: int = 4
    max_tool_iterations: int = 5
    max_retries: int = 2
    history_window_steps: int = 96

    # EnergyPlus
    energyplus_dir: Path = Path("/Applications/EnergyPlus-25-1-0")
    baseline_idf_dir: Path = BACKEND_DIR / "models" / "baseline"
    generated_idf_dir: Path = BACKEND_DIR / "models" / "generated"

    # comfort limits, enforced by the guard
    comfort_pmv_low: float = -0.5
    comfort_pmv_high: float = 0.5
    min_zone_temp_c: float = 19.0
    max_zone_temp_c: float = 26.0

    # energy accounting
    tariff_per_kwh: float = 0.18
    grid_carbon_kg_per_kwh: float = 0.42


@dataclass(frozen=True)
class ScenarioTargets:
    """The objectives a run is judged against."""

    comfort_pmv_low: float = -0.5
    comfort_pmv_high: float = 0.5
    peak_demand_kw: float = 10.0
    tariff_per_kwh: float = 0.18
    grid_carbon_kg_per_kwh: float = 0.42


@dataclass(frozen=True)
class Scenario:
    """One experiment setup: building, weather, occupancy and targets.

    Everything except the controller, so that two runs of the same scenario are
    directly comparable.
    """

    id: str
    label: str
    start: datetime
    days: int
    timestep_seconds: int
    weather: dict[str, object]
    occupied_hours: tuple[int, int]
    targets: ScenarioTargets
    building: dict[str, object] = field(default_factory=dict)
    energyplus: dict[str, object] = field(default_factory=dict)

    @property
    def horizon_steps(self) -> int:
        """Number of simulation steps in the scenario's horizon."""
        return self.days * 24 * 3600 // self.timestep_seconds


def _env_str(key: str, default: str) -> str:
    value = os.environ.get(key)
    return default if value is None or value == "" else value


def _env_float(key: str, default: float) -> float:
    raw = os.environ.get(key)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{key} must be a number, got {raw!r}") from exc


def _env_int(key: str, default: int) -> int:
    raw = os.environ.get(key)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{key} must be an integer, got {raw!r}") from exc


def _env_path(key: str, default: Path) -> Path:
    raw = os.environ.get(key)
    if raw is None or raw == "":
        return default
    path = Path(raw).expanduser()
    return path if path.is_absolute() else (REPO_DIR / path).resolve()


def load_settings(env_file: Path | None = None) -> Settings:
    """Build `Settings` from environment variables, falling back to defaults.

    A `.env` at the repository root is loaded first if present; real environment
    variables always win over file values.
    """
    dotenv_path = env_file if env_file is not None else REPO_DIR / ".env"
    if dotenv_path.exists():
        load_dotenv(dotenv_path, override=False)

    return Settings(
        database_path=_env_path("DATABASE_PATH", Settings.database_path),
        scenarios_dir=_env_path("SCENARIOS_DIR", Settings.scenarios_dir),
        llm_base_url=_env_str("LLM_BASE_URL", Settings.llm_base_url),
        llm_model=_env_str("LLM_MODEL", Settings.llm_model),
        llm_api_key=_env_str("LLM_API_KEY", Settings.llm_api_key),
        llm_timeout_seconds=_env_float(
            "LLM_TIMEOUT_SECONDS", Settings.llm_timeout_seconds
        ),
        llm_temperature=_env_float("LLM_TEMPERATURE", Settings.llm_temperature),
        decision_cadence_steps=_env_int(
            "DECISION_CADENCE_STEPS", Settings.decision_cadence_steps
        ),
        max_tool_iterations=_env_int(
            "MAX_TOOL_ITERATIONS", Settings.max_tool_iterations
        ),
        max_retries=_env_int("MAX_RETRIES", Settings.max_retries),
        history_window_steps=_env_int(
            "HISTORY_WINDOW_STEPS", Settings.history_window_steps
        ),
        energyplus_dir=_env_path("ENERGYPLUS_DIR", Settings.energyplus_dir),
        baseline_idf_dir=_env_path("BASELINE_IDF_DIR", Settings.baseline_idf_dir),
        generated_idf_dir=_env_path("GENERATED_IDF_DIR", Settings.generated_idf_dir),
        comfort_pmv_low=_env_float("COMFORT_PMV_LOW", Settings.comfort_pmv_low),
        comfort_pmv_high=_env_float("COMFORT_PMV_HIGH", Settings.comfort_pmv_high),
        min_zone_temp_c=_env_float("MIN_ZONE_TEMP_C", Settings.min_zone_temp_c),
        max_zone_temp_c=_env_float("MAX_ZONE_TEMP_C", Settings.max_zone_temp_c),
        tariff_per_kwh=_env_float("TARIFF_PER_KWH", Settings.tariff_per_kwh),
        grid_carbon_kg_per_kwh=_env_float(
            "GRID_CARBON_KG_PER_KWH", Settings.grid_carbon_kg_per_kwh
        ),
    )


def _parse_scenario(payload: dict, source: Path) -> Scenario:
    """Turn a scenario JSON document into a `Scenario`, validating as we go."""
    missing = [
        key
        for key in ("id", "label", "start", "days", "timestep_seconds", "weather")
        if key not in payload
    ]
    if missing:
        raise ValueError(f"{source.name} is missing required keys: {', '.join(missing)}")

    timestep_seconds = int(payload["timestep_seconds"])
    if timestep_seconds <= 0 or 3600 % timestep_seconds != 0:
        raise ValueError(
            f"{source.name}: timestep_seconds must divide 3600, got {timestep_seconds}"
        )

    days = int(payload["days"])
    if days <= 0:
        raise ValueError(f"{source.name}: days must be positive, got {days}")

    occupied = payload.get("occupied_hours", [8, 18])
    if len(occupied) != 2 or not 0 <= occupied[0] < occupied[1] <= 24:
        raise ValueError(f"{source.name}: occupied_hours must be [start, end] in 0..24")

    return Scenario(
        id=str(payload["id"]),
        label=str(payload["label"]),
        start=datetime.fromisoformat(str(payload["start"])),
        days=days,
        timestep_seconds=timestep_seconds,
        weather=dict(payload["weather"]),
        occupied_hours=(int(occupied[0]), int(occupied[1])),
        targets=ScenarioTargets(**payload.get("targets", {})),
        building=dict(payload.get("building", {})),
        energyplus=dict(payload.get("energyplus", {})),
    )


def load_scenario(scenario_id: str, scenarios_dir: Path) -> Scenario:
    """Load one scenario by id. Raises `FileNotFoundError` if it does not exist."""
    path = scenarios_dir / f"{scenario_id}.json"
    if not path.exists():
        available = ", ".join(s.id for s in list_scenarios(scenarios_dir)) or "none"
        raise FileNotFoundError(
            f"Unknown scenario {scenario_id!r}. Available: {available}"
        )
    with path.open(encoding="utf-8") as handle:
        return _parse_scenario(json.load(handle), path)


def list_scenarios(scenarios_dir: Path) -> list[Scenario]:
    """Every scenario definition in `scenarios_dir`, ordered by id."""
    if not scenarios_dir.exists():
        return []
    scenarios = []
    for path in sorted(scenarios_dir.glob("*.json")):
        with path.open(encoding="utf-8") as handle:
            scenarios.append(_parse_scenario(json.load(handle), path))
    return scenarios
