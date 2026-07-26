"""Comfort model tests: PMV sign and magnitude, PPD curve, band evaluation.

The PMV cases are the ISO 7730 Annex D validation table. If these drift, the
comfort metric that 20% of the evaluation rests on is wrong, and every savings
claim built on it becomes unsafe.
"""

from __future__ import annotations

import pytest

from app.comfort import (
    CLO_SUMMER,
    CLO_WINTER,
    clothing_for_season,
    comfort_band,
    is_comfortable,
    pmv,
    ppd,
    temperature_for_pmv,
)

# (air temp, radiant temp, air speed, RH fraction, met, clo, expected PMV)
ISO_7730_CASES = [
    (22.0, 22.0, 0.1, 0.60, 1.2, 0.5, -0.75),
    (27.0, 27.0, 0.1, 0.60, 1.2, 0.5, 0.77),
    (27.0, 27.0, 0.3, 0.60, 1.2, 0.5, 0.44),
    (23.5, 23.5, 0.1, 0.60, 1.2, 1.0, 0.50),
]


@pytest.mark.parametrize(
    "air_temp,radiant_temp,air_speed,humidity,met,clo,expected", ISO_7730_CASES
)
def test_pmv_matches_iso_7730_reference(
    air_temp, radiant_temp, air_speed, humidity, met, clo, expected
):
    """PMV reproduces the standard's own validation table."""
    result = pmv(air_temp, radiant_temp, air_speed, humidity, met, clo)
    assert result == pytest.approx(expected, abs=0.02)


def test_pmv_increases_with_temperature():
    """Warmer air can never read as colder — the property bisection relies on."""
    previous = -99.0
    for air_temp in range(15, 35):
        current = pmv(float(air_temp), float(air_temp), 0.1, 0.5)
        assert current > previous
        previous = current


def test_pmv_is_clamped_to_the_scale():
    """Extreme conditions saturate rather than running off the seven-point scale."""
    assert pmv(-10.0, -10.0, 0.1, 0.5) == -3.0
    assert pmv(50.0, 50.0, 0.1, 0.5) == 3.0


def test_pmv_rejects_percentage_humidity():
    """Humidity is a fraction here; passing 60 instead of 0.6 must not sail through."""
    with pytest.raises(ValueError):
        pmv(22.0, 22.0, 0.1, 60.0)


def test_ppd_is_minimised_at_neutral():
    """PPD bottoms out at 5% for a neutral vote and rises symmetrically."""
    assert ppd(0.0) == pytest.approx(5.0, abs=0.01)
    assert ppd(-1.0) == pytest.approx(ppd(1.0), abs=1e-9)
    assert ppd(2.0) > ppd(1.0) > ppd(0.0)


def test_ppd_at_the_band_edges_is_ten_percent():
    """The +/-0.5 band is the ASHRAE-55 10% dissatisfied contour.

    The standard quotes "10%" as the design figure; its own formula puts the
    edge at 10.23%, so the tolerance is set around the exact value rather than
    the rounded one.
    """
    assert ppd(0.5) == pytest.approx(10.23, abs=0.02)
    assert ppd(-0.5) == pytest.approx(10.23, abs=0.02)


def test_unoccupied_zones_are_never_uncomfortable():
    """An empty building cannot have a comfort violation."""
    assert is_comfortable(2.5, occupancy=0.0) is True
    assert is_comfortable(2.5, occupancy=0.5) is False


def test_comfort_band_edges_evaluate_to_the_target_votes():
    """The inverse really is the inverse."""
    coolest, warmest = comfort_band(28.0, 0.5, 0.1)
    clothing = clothing_for_season(28.0)
    assert pmv(coolest, coolest, 0.1, 0.5, 1.1, clothing) == pytest.approx(
        -0.5, abs=0.01
    )
    assert pmv(warmest, warmest, 0.1, 0.5, 1.1, clothing) == pytest.approx(
        0.5, abs=0.01
    )


def test_comfort_band_is_warmer_in_summer_clothing():
    """Lighter clothing shifts the whole acceptable band upward."""
    summer_low, summer_high = comfort_band(28.0, 0.5, 0.1)
    winter_low, winter_high = comfort_band(2.0, 0.5, 0.1)
    assert summer_low > winter_low
    assert summer_high > winter_high


def test_clothing_switches_at_twenty_degrees():
    """The season boundary is where the standard's two ensembles meet."""
    assert clothing_for_season(25.0) == CLO_SUMMER
    assert clothing_for_season(19.9) == CLO_WINTER


def test_temperature_for_pmv_clamps_outside_the_bracket():
    """An unreachable target returns the bracket edge rather than diverging."""
    assert temperature_for_pmv(-3.0, 0.1, 0.5, 1.1, 1.0, 10.0, 40.0) == 10.0
    assert temperature_for_pmv(3.0, 0.1, 0.5, 1.1, 1.0, 10.0, 40.0) == 40.0
