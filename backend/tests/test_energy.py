"""Energy accounting tests: kWh integration, peak detection, savings arithmetic,
and that comfort degradation is flagged rather than counted as a win.
"""

from __future__ import annotations

import pytest

from app.energy import RunSummary, compare, step_energy_kwh, summarise


def _summary(**overrides) -> RunSummary:
    """A completed run's metrics, with fields overridden per test."""
    defaults = {
        "run_id": 1,
        "total_kwh": 100.0,
        "peak_kw": 10.0,
        "cost": 18.0,
        "co2_kg": 42.0,
        "comfort_violations": 5,
        "occupied_steps": 200,
        "mean_ppd": 8.0,
    }
    return RunSummary(**{**defaults, **overrides})


def test_step_energy_integrates_power_over_time():
    """One kilowatt for one hour is one kilowatt-hour."""
    assert step_energy_kwh(1.0, 3600) == pytest.approx(1.0)
    assert step_energy_kwh(4.0, 900) == pytest.approx(1.0)
    assert step_energy_kwh(0.0, 900) == 0.0


def test_step_energy_rejects_impossible_inputs():
    """Negative power would silently manufacture savings."""
    with pytest.raises(ValueError):
        step_energy_kwh(-1.0, 900)
    with pytest.raises(ValueError):
        step_energy_kwh(1.0, 0)


def test_summarise_totals_and_peak():
    """Totals add up and the peak is the largest instantaneous demand."""
    summary = summarise(
        run_id=1,
        energies_kwh=[1.0, 2.0, 3.0],
        powers_kw=[4.0, 8.0, 12.0],
        ppds=[5.0, 6.0, 7.0],
        comfort_flags=[True, True, True],
        occupancies=[1.0, 1.0, 1.0],
        tariff_per_kwh=0.2,
        carbon_kg_per_kwh=0.5,
    )
    assert summary.total_kwh == pytest.approx(6.0)
    assert summary.peak_kw == pytest.approx(12.0)
    assert summary.cost == pytest.approx(1.2)
    assert summary.co2_kg == pytest.approx(3.0)


def test_summarise_scores_comfort_over_occupied_steps_only():
    """Unoccupied steps neither violate comfort nor dilute the mean PPD."""
    summary = summarise(
        run_id=1,
        energies_kwh=[1.0] * 4,
        powers_kw=[1.0] * 4,
        ppds=[10.0, 20.0, 90.0, 90.0],
        comfort_flags=[True, False, False, False],
        occupancies=[1.0, 1.0, 0.0, 0.0],
        tariff_per_kwh=0.0,
        carbon_kg_per_kwh=0.0,
    )
    assert summary.occupied_steps == 2
    assert summary.comfort_violations == 1
    assert summary.mean_ppd == pytest.approx(15.0)
    assert summary.comfort_violation_rate == pytest.approx(0.5)


def test_summarise_rejects_ragged_traces():
    """Mismatched traces mean a bug upstream; failing loudly beats misalignment."""
    with pytest.raises(ValueError):
        summarise(
            run_id=1,
            energies_kwh=[1.0, 2.0],
            powers_kw=[1.0],
            ppds=[5.0, 5.0],
            comfort_flags=[True, True],
            occupancies=[1.0, 1.0],
            tariff_per_kwh=0.0,
            carbon_kg_per_kwh=0.0,
        )


def test_compare_reports_percentage_reduction():
    """The headline number: a quarter less energy is a 25% saving."""
    report = compare(
        _summary(run_id=1, total_kwh=100.0, peak_kw=10.0, cost=20.0, co2_kg=40.0),
        _summary(run_id=2, total_kwh=75.0, peak_kw=8.0, cost=15.0, co2_kg=30.0),
    )
    assert report.kwh_saved == pytest.approx(25.0)
    assert report.kwh_saved_pct == pytest.approx(25.0)
    assert report.peak_reduction_kw == pytest.approx(2.0)
    assert report.peak_reduction_pct == pytest.approx(20.0)
    assert report.cost_saved == pytest.approx(5.0)
    assert report.co2_saved_kg == pytest.approx(10.0)


def test_compare_flags_savings_bought_with_comfort():
    """Letting the building drift is not a saving, and must not be reported as one."""
    report = compare(
        _summary(run_id=1, total_kwh=100.0, comfort_violations=2),
        _summary(run_id=2, total_kwh=50.0, comfort_violations=40),
    )
    assert report.kwh_saved_pct == pytest.approx(50.0)
    assert report.comfort_violation_delta == 38
    assert report.comfort_maintained is False


def test_compare_accepts_equal_comfort():
    """Matching the baseline's comfort is good enough to count the saving."""
    report = compare(
        _summary(run_id=1, total_kwh=100.0, comfort_violations=3),
        _summary(run_id=2, total_kwh=90.0, comfort_violations=3),
    )
    assert report.comfort_maintained is True


def test_compare_survives_a_zero_baseline():
    """A baseline that used nothing must not divide by zero."""
    report = compare(
        _summary(run_id=1, total_kwh=0.0, peak_kw=0.0),
        _summary(run_id=2, total_kwh=0.0, peak_kw=0.0),
    )
    assert report.kwh_saved_pct == 0.0
    assert report.peak_reduction_pct == 0.0
