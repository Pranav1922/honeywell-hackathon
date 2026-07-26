"""Typed, environment-driven configuration.

Everything that differs between machines lives here — the EnergyPlus install
path, the model endpoint, the database location — so nothing machine-specific is
committed. See `.env.example` for the template.

Loader implemented in Milestone 1.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

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


def load_settings() -> Settings:
    """Build `Settings` from environment variables, falling back to defaults."""
    raise NotImplementedError("Milestone 1")
