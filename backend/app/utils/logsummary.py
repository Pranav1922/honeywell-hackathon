"""Compaction of long simulation logs and telemetry into a bounded token budget.

Raw EnergyPlus output and a full telemetry trace exceed any practical context
window, and blind truncation throws away exactly the anomalies the agent needs.
Three techniques instead: severity filtering, deduplication of repeated warnings
into counted entries, and statistical windowing of long numeric traces.

This module is the answer to the "handling lengthy simulation logs" requirement
in deliverable 4.
"""

from __future__ import annotations

from typing import Sequence

from app.sim.base import BuildingState

SEVERITY_ORDER = ("severe", "fatal", "warning", "info")

MAX_ENTRY_CHARS = 220
CONTINUATION_MARKER = "~~~"


def summarise_telemetry(
    history: Sequence[BuildingState],
    window_steps: int = 96,
    buckets: int = 8,
) -> str:
    """Compress a long trace into per-bucket min/mean/max and a trend direction.

    A day of 15-minute telemetry is 96 rows of ten fields; a week is 672. Neither
    belongs in a prompt verbatim, and neither is *needed* verbatim — what the
    supervisor reasons about is the shape of the day and which way things are
    moving. Contiguous buckets preserve both: an overnight setback stays visible
    as a distinct cold window rather than being averaged into the afternoon.

    Args:
        history: Telemetry in chronological order; only the tail is used.
        window_steps: How many of the most recent steps to consider.
        buckets: How many summary windows to divide them into.

    Returns:
        A fixed-width table plus one trend line, bounded regardless of input size.
    """
    if window_steps <= 0:
        raise ValueError(f"window_steps must be positive, got {window_steps}")
    if buckets <= 0:
        raise ValueError(f"buckets must be positive, got {buckets}")

    window = list(history)[-window_steps:]
    if not window:
        return "No telemetry recorded yet."

    count = min(buckets, len(window))
    edges = [len(window) * index // count for index in range(count + 1)]
    groups = [window[edges[i] : edges[i + 1]] for i in range(count)]

    minutes = _window_minutes(window)
    lines = [
        f"{len(window)} steps in {count} windows"
        + (f" of ~{minutes:.0f} min each" if minutes else "")
        + " (oldest first):",
        "  time         zone C min/mean/max   out C   occ    kW    CO2",
    ]
    for group in groups:
        temps = [state.zone_temp_c for state in group]
        lines.append(
            f"  {group[0].sim_time:%m-%d %H:%M}  "
            f"{min(temps):5.1f}/{_mean(temps):5.1f}/{max(temps):5.1f}  "
            f"{_mean([s.outdoor_temp_c for s in group]):6.1f}  "
            f"{_mean([s.occupancy for s in group]):4.2f}  "
            f"{_mean([s.power_kw for s in group]):5.2f}  "
            f"{_mean([s.co2_ppm for s in group]):5.0f}"
        )

    lines.append("  trend: " + _trends(groups[0], groups[-1]))
    return "\n".join(lines)


def dedupe_log_lines(lines: Sequence[str]) -> list[tuple[str, int]]:
    """Collapse repeated messages into (message, occurrence_count) pairs.

    EnergyPlus emits the same warning once per timestep, so a week-long run can
    repeat one line thousands of times. The count is the information; the
    repetition is not. First-occurrence order is preserved so the digest still
    reads chronologically.
    """
    counts: dict[str, int] = {}
    for line in lines:
        text = line.strip()
        if not text:
            continue
        counts[text] = counts.get(text, 0) + 1
    return list(counts.items())


def filter_by_severity(
    lines: Sequence[str], min_severity: str = "warning"
) -> list[str]:
    """Keep only entries at or above `min_severity`.

    Severity is read from the text itself, because that is all an `.err` file
    carries. Anything unrecognised is treated as informational — the safe
    direction, since the alternative is dropping a real fault that happened to be
    worded unusually.
    """
    if min_severity not in SEVERITY_ORDER:
        raise ValueError(
            f"min_severity must be one of {SEVERITY_ORDER}, got {min_severity!r}"
        )
    limit = SEVERITY_ORDER.index(min_severity)
    return [line for line in lines if SEVERITY_ORDER.index(severity_of(line)) <= limit]


def severity_of(line: str) -> str:
    """The severity a log line reports, defaulting to `info`."""
    lowered = line.lower()
    for name in SEVERITY_ORDER:
        if name in lowered:
            return name
    return "info"


def compact_errors(raw_err: str, max_entries: int = 20) -> str:
    """Turn a raw EnergyPlus `.err` file into a bounded, prompt-ready digest.

    Continuation lines are folded into the entry they qualify, then the file is
    severity-filtered, deduplicated and ordered worst-first. What survives is
    what an engineer would actually read: the distinct faults, how often each
    fired, and an honest count of what was left out.
    """
    if not raw_err or not raw_err.strip():
        return "No simulation diagnostics reported."
    if max_entries <= 0:
        raise ValueError(f"max_entries must be positive, got {max_entries}")

    raw_lines = raw_err.splitlines()
    entries = _fold_continuations(raw_lines)
    kept = filter_by_severity(entries, "warning")
    deduped = dedupe_log_lines(kept)
    deduped.sort(key=lambda item: (SEVERITY_ORDER.index(severity_of(item[0])), -item[1]))

    shown = deduped[:max_entries]
    header = (
        f"{len(deduped)} distinct diagnostics from {len(raw_lines)} lines "
        f"({len(entries) - len(kept)} informational lines dropped)."
    )
    body = [
        f"  [{severity_of(text)} x{count}] {_truncate(text)}" for text, count in shown
    ]
    if len(deduped) > len(shown):
        body.append(f"  ... {len(deduped) - len(shown)} further distinct entries omitted.")
    return "\n".join([header, *body])


# -- internals --------------------------------------------------------------


def _fold_continuations(lines: Sequence[str]) -> list[str]:
    """Attach EnergyPlus `~~~` continuation lines to the entry above them."""
    entries: list[str] = []
    for line in lines:
        text = line.strip()
        if not text:
            continue
        if CONTINUATION_MARKER in text and entries:
            entries[-1] = f"{entries[-1]} | {text}"
        else:
            entries.append(text)
    return entries


def _truncate(text: str, limit: int = MAX_ENTRY_CHARS) -> str:
    """Bound one entry's length without hiding that it was cut."""
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _mean(values: Sequence[float]) -> float:
    """Arithmetic mean; zero for an empty window, which cannot occur here."""
    return sum(values) / len(values) if values else 0.0


def _window_minutes(window: Sequence[BuildingState]) -> float:
    """Simulated minutes spanned per bucket, inferred from the timestamps."""
    if len(window) < 2:
        return 0.0
    span = (window[-1].sim_time - window[0].sim_time).total_seconds()
    return span / 60.0 / max(1, len(window) - 1)


def _trends(first: Sequence[BuildingState], last: Sequence[BuildingState]) -> str:
    """Direction of travel between the oldest and newest buckets."""
    parts = [
        _trend("zone", _mean([s.zone_temp_c for s in last]) - _mean([s.zone_temp_c for s in first]), "C", 0.2),
        _trend("outdoor", _mean([s.outdoor_temp_c for s in last]) - _mean([s.outdoor_temp_c for s in first]), "C", 0.2),
        _trend("power", _mean([s.power_kw for s in last]) - _mean([s.power_kw for s in first]), "kW", 0.1),
        _trend("CO2", _mean([s.co2_ppm for s in last]) - _mean([s.co2_ppm for s in first]), "ppm", 20.0),
    ]
    return ", ".join(parts)


def _trend(label: str, delta: float, unit: str, threshold: float) -> str:
    """One labelled trend, with a dead-band so noise does not read as movement."""
    if abs(delta) < threshold:
        return f"{label} steady"
    direction = "rising" if delta > 0 else "falling"
    return f"{label} {direction} {delta:+.1f} {unit}"
