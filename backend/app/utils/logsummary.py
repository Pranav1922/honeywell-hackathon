"""Compaction of long simulation logs and telemetry into a bounded token budget.

Raw EnergyPlus output and a full telemetry trace exceed any practical context
window, and blind truncation throws away exactly the anomalies the agent needs.
Three techniques instead: severity filtering, deduplication of repeated warnings
into counted entries, and statistical windowing of long numeric traces.

This module is the answer to the "handling lengthy simulation logs" requirement
in deliverable 4.

Implemented in Milestone 2.
"""

from __future__ import annotations

from typing import Sequence

from app.sim.base import BuildingState

SEVERITY_ORDER = ("severe", "fatal", "warning", "info")


def summarise_telemetry(
    history: Sequence[BuildingState],
    window_steps: int = 96,
    buckets: int = 8,
) -> str:
    """Compress a long trace into per-bucket min/mean/max and a trend direction."""
    raise NotImplementedError("Milestone 2")


def dedupe_log_lines(lines: Sequence[str]) -> list[tuple[str, int]]:
    """Collapse repeated messages into (message, occurrence_count) pairs."""
    raise NotImplementedError("Milestone 2")


def filter_by_severity(lines: Sequence[str], min_severity: str = "warning") -> list[str]:
    """Keep only entries at or above `min_severity`."""
    raise NotImplementedError("Milestone 2")


def compact_errors(raw_err: str, max_entries: int = 20) -> str:
    """Turn a raw EnergyPlus `.err` file into a bounded, prompt-ready digest."""
    raise NotImplementedError("Milestone 2")
