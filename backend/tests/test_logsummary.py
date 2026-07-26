"""Log and telemetry compaction tests.

The claim being tested is the one deliverable 4 makes: that a long trace can be
reduced to a bounded prompt *without* losing the anomalies. So every test here
checks both halves — that the output is small, and that something specific
survived.
"""

from __future__ import annotations

import pytest

from app.utils.logsummary import (
    SEVERITY_ORDER,
    compact_errors,
    dedupe_log_lines,
    filter_by_severity,
    severity_of,
    summarise_telemetry,
)
from tests.conftest import build_history, build_state

RAW_ERR = """\
Program Version,EnergyPlus, Version 25.1.0
   ** Warning ** Weather file location differs from the model location
   **   ~~~   ** Model latitude 51.5, weather file latitude 40.7
   ** Warning ** Output:Meter requested for a meter that does not exist
   ** Warning ** Output:Meter requested for a meter that does not exist
   ** Warning ** Output:Meter requested for a meter that does not exist
   ** Severe  ** Node connection error for Node=OA INLET NODE
   **  Fatal  ** IP: Errors occurred on processing input file
   ************* Beginning Simulation
"""


def test_an_empty_trace_says_so_rather_than_failing():
    """The first supervisory call happens before there is any history."""
    assert summarise_telemetry([]) == "No telemetry recorded yet."


def test_a_long_trace_compacts_to_a_bounded_digest():
    """672 rows of telemetry — a simulated week — must not reach a prompt raw."""
    summary = summarise_telemetry(build_history(672), window_steps=672, buckets=8)

    assert len(summary.splitlines()) == 11  # header, column titles, 8 buckets, trend
    assert len(summary) < 900
    assert "trend:" in summary


def test_bucket_count_is_honoured_and_capped_by_the_data():
    """Asking for more buckets than there are steps yields one bucket per step."""
    assert len(summarise_telemetry(build_history(3), buckets=8).splitlines()) == 3 + 3


def test_compaction_preserves_the_range_within_each_window():
    """min/mean/max is what stops an overnight setback averaging into the day."""
    history = (
        build_state(step=0, zone_temp_c=16.0),
        build_state(step=1, zone_temp_c=26.0),
    )
    summary = summarise_telemetry(history, buckets=1)

    assert "16.0" in summary and "26.0" in summary and "21.0" in summary


def test_the_trend_line_names_the_direction_of_travel():
    """A rising zone temperature is the signal the supervisor acts on."""
    summary = summarise_telemetry(build_history(96), window_steps=96, buckets=4)

    assert "zone rising" in summary
    assert "CO2 rising" in summary


def test_a_flat_trace_reads_as_steady_not_as_noise():
    """A dead-band on the trend, so float jitter is not reported as movement."""
    flat = tuple(build_state(step=i, zone_temp_c=22.0, power_kw=3.0, co2_ppm=500.0)
                 for i in range(20))

    assert "zone steady" in summarise_telemetry(flat, buckets=4)


def test_window_and_bucket_arguments_are_validated():
    """A nonsensical window is a caller bug, not something to guess around."""
    with pytest.raises(ValueError, match="window_steps must be positive"):
        summarise_telemetry(build_history(4), window_steps=0)
    with pytest.raises(ValueError, match="buckets must be positive"):
        summarise_telemetry(build_history(4), buckets=0)


def test_only_the_tail_of_a_trace_is_summarised():
    """The window is the most recent steps, not the first ones found."""
    summary = summarise_telemetry(build_history(200), window_steps=8, buckets=1)

    assert "8 steps in 1 windows" in summary


def test_repeated_messages_collapse_into_counts():
    """One warning repeated 3,000 times is one fact, not 3,000."""
    lines = ["disk almost full"] * 3000 + ["node error"]

    assert dedupe_log_lines(lines) == [("disk almost full", 3000), ("node error", 1)]


def test_dedupe_preserves_first_occurrence_order_and_drops_blanks():
    """The digest should still read chronologically."""
    assert dedupe_log_lines(["b", "", "  ", "a", "b"]) == [("b", 2), ("a", 1)]


def test_severity_filtering_keeps_faults_and_drops_chatter():
    """An `.err` file is mostly informational; the faults are the point."""
    lines = RAW_ERR.splitlines()
    kept = filter_by_severity(lines, "warning")

    assert any("Severe" in line for line in kept)
    assert any("Fatal" in line for line in kept)
    assert not any("Program Version" in line for line in kept)
    assert not any("Beginning Simulation" in line for line in kept)


def test_severity_filtering_can_be_tightened_to_severe_only():
    """`severe` is the top of SEVERITY_ORDER, so nothing else survives it."""
    kept = filter_by_severity(RAW_ERR.splitlines(), "severe")

    assert len(kept) == 1
    assert "Node connection error" in kept[0]


def test_unrecognised_lines_are_treated_as_informational():
    """The safe direction: never silently promote noise to a fault."""
    assert severity_of("something happened") == "info"
    assert severity_of("   ** Severe  ** boom") == "severe"
    assert set(SEVERITY_ORDER) == {"severe", "fatal", "warning", "info"}


def test_an_unknown_min_severity_is_rejected():
    """A typo here would silently filter everything out."""
    with pytest.raises(ValueError, match="min_severity must be one of"):
        filter_by_severity(["x"], "critical")


def test_compact_errors_produces_a_worst_first_bounded_digest():
    """Severity first, then frequency — the order an engineer would read in."""
    digest = compact_errors(RAW_ERR)
    lines = digest.splitlines()

    assert "distinct diagnostics" in lines[0]
    assert "[severe x1]" in lines[1]
    assert "[fatal x1]" in lines[2]
    assert "[warning x3]" in digest  # the repeated meter warning, counted
    assert "Program Version" not in digest


def test_continuation_lines_stay_attached_to_their_entry():
    """`~~~` lines carry the detail; orphaned, they say nothing."""
    digest = compact_errors(RAW_ERR)

    assert "Model latitude 51.5" in digest
    assert digest.count("Weather file location differs") == 1


def test_compact_errors_bounds_how_many_entries_it_reports():
    """A file with hundreds of distinct faults still has to fit in a prompt."""
    raw = "\n".join(f"   ** Warning ** distinct problem {i}" for i in range(200))
    digest = compact_errors(raw, max_entries=5)

    assert len(digest.splitlines()) == 7  # header, 5 entries, omission notice
    assert "195 further distinct entries omitted" in digest


def test_a_clean_run_reports_no_diagnostics():
    """The normal case, and it must not read as a missing feature."""
    assert compact_errors("") == "No simulation diagnostics reported."
    assert compact_errors("   \n  \n") == "No simulation diagnostics reported."


def test_a_very_long_single_entry_is_truncated_visibly():
    """One pathological line cannot be allowed to blow the token budget."""
    digest = compact_errors("   ** Severe ** " + "x" * 5000)

    assert len(digest) < 400
    assert digest.rstrip().endswith("...")
