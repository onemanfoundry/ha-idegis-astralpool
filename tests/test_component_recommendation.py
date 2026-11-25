"""Regression tests for custom_components/idegis_astralpool/recommendation.py.

Guards two things:
  1. Byte-for-byte parity with the addon's `_compute_recommendation`
     math (same formula, ported verbatim) across a grid of synthetic
     session/temperature/production inputs.
  2. The calibration documented in project memory
     (feedback_filtration_defaults_overshoot / project_filtration_target):
     this module's *defaults* must land near ~55 min/day for Sergio's
     install, not the ~139 min/day the addon's shipped config.yaml
     defaults are known to overshoot to.
"""
from __future__ import annotations

import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
COMPONENT_RECOMMENDATION = ROOT / "custom_components" / "idegis_astralpool" / "recommendation.py"
ADDON_CAPTURER = ROOT / "addon" / "rootfs" / "opt" / "idegis" / "capturer.py"


def _load(name: str, path: pathlib.Path):
    import sys

    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    # dataclass field resolution looks the module up in sys.modules by
    # __module__ name; register it before exec so recommendation.py's
    # `@dataclass` class body can resolve its own annotations.
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="session")
def rec_mod():
    return _load("component_idegis_recommendation", COMPONENT_RECOMMENDATION)


@pytest.fixture(scope="session")
def addon_capturer(tmp_path_factory):
    import os
    import sys

    os.environ["IDEGIS_TESTING"] = "1"
    os.environ["IDEGIS_DATA"] = str(tmp_path_factory.mktemp("idegis_data"))
    sys.path.insert(0, str(ADDON_CAPTURER.parent))
    try:
        return _load("idegis_addon_capturer_for_recommendation", ADDON_CAPTURER)
    finally:
        sys.path.pop(0)


def test_calibrated_defaults_land_near_55_minutes(rec_mod):
    """The headline number this whole module exists to get right."""
    inputs = rec_mod.RecommendationInputs()
    calc = rec_mod.compute_recommendation(inputs)
    assert 45 <= calc["recommended_minutes_today"] <= 65
    assert calc["driver"] == "turnover"


def test_addon_shipped_defaults_overshoot_to_139(rec_mod):
    """Documents *why* this module doesn't reuse the addon's config.yaml
    defaults verbatim: they're the known-bad input, not a baseline to
    match."""
    inputs = rec_mod.RecommendationInputs(
        target_pct=40,
        chlorine_demand_ppm_day=0.6,
        min_turnovers_per_day=0.75,
        net_n_clean_installed=False,
    )
    calc = rec_mod.compute_recommendation(inputs)
    assert 130 <= calc["recommended_minutes_today"] <= 150


@pytest.mark.parametrize(
    "target_pct,demand,turnovers,net_n_clean,temp_c,apply_mult",
    [
        (40, 0.6, 0.75, False, None, False),
        (100, 0.25, 0.5, True, None, False),
        (40, 0.6, 0.75, False, 30.0, True),
        (100, 0.25, 0.5, True, 18.0, True),
        (60, 0.4, 1.0, True, 34.0, True),
    ],
)
def test_matches_addon_formula(
    rec_mod, addon_capturer, target_pct, demand, turnovers, net_n_clean, temp_c, apply_mult
):
    inputs = rec_mod.RecommendationInputs(
        target_pct=target_pct,
        chlorine_demand_ppm_day=demand,
        min_turnovers_per_day=turnovers,
        net_n_clean_installed=net_n_clean,
        temp_c=temp_c,
        apply_temp_multiplier=apply_mult,
    )
    ours = rec_mod.compute_recommendation(inputs)
    theirs = addon_capturer._compute_recommendation(
        volume_m3=inputs.volume_m3,
        flow_m3_h=inputs.flow_m3_h,
        cell_g_h=inputs.cell_g_h,
        target_pct=target_pct,
        chlorine_demand_ppm_day=demand,
        min_turnovers_per_day=turnovers,
        temp_c=temp_c,
        apply_temp_multiplier=apply_mult,
        net_n_clean_installed=net_n_clean,
    )
    assert ours == theirs


def test_temp_multiplier_matches_addon_bands(rec_mod, addon_capturer):
    for t in (None, -5, 15, 19.9, 20, 23.9, 24, 27.9, 28, 31.9, 32, 35.9, 36, 50):
        assert rec_mod.temp_multiplier(t) == addon_capturer._temp_multiplier(t)


def test_zero_flow_does_not_divide_by_zero(rec_mod):
    inputs = rec_mod.RecommendationInputs(flow_m3_h=0)
    calc = rec_mod.compute_recommendation(inputs)
    assert calc["turnover_minutes"] == 0


def test_zero_cell_capacity_does_not_divide_by_zero(rec_mod):
    inputs = rec_mod.RecommendationInputs(cell_g_h=0)
    calc = rec_mod.compute_recommendation(inputs)
    assert calc["chlorine_demand_minutes"] == 0


def test_seasonal_summer_is_one_hour_daily(rec_mod):
    import datetime

    for month in (6, 7, 8, 9):
        calc = rec_mod.compute_seasonal_recommendation(datetime.date(2026, month, 15))
        assert calc["recommended_minutes_today"] == 60
        assert calc["season"] == "summer"
        assert calc["driver"] == "seasonal_summer_daily"


def test_seasonal_winter_is_one_hour_on_mondays_only(rec_mod):
    import datetime

    monday = datetime.date(2026, 1, 5)
    assert monday.weekday() == 0
    calc = rec_mod.compute_seasonal_recommendation(monday)
    assert calc["recommended_minutes_today"] == 60
    assert calc["season"] == "winter"
    assert calc["driver"] == "seasonal_winter_weekly_session"

    for offset in range(1, 7):
        other_day = monday + datetime.timedelta(days=offset)
        calc = rec_mod.compute_seasonal_recommendation(other_day)
        assert calc["recommended_minutes_today"] == 0
        assert calc["season"] == "winter"
        assert calc["driver"] == "seasonal_winter_rest_day"
