"""Regression tests for custom_components/idegis_astralpool/cost.py.

Synthetic history fixtures (shaped exactly like HA's
history.get_significant_states output) exercise the ported
tariff/solar-attribution math, cross-checked against the addon's
cost_breakdown (addon v0.6.11) for parity.
"""
from __future__ import annotations

import importlib.util
import pathlib
from datetime import datetime, timezone

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
COMPONENT_COST = ROOT / "custom_components" / "idegis_astralpool" / "cost.py"
ADDON_CAPTURER = ROOT / "addon" / "rootfs" / "opt" / "idegis" / "capturer.py"


def _load(name: str, path: pathlib.Path):
    import sys

    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="session")
def cost_mod():
    return _load("component_idegis_cost", COMPONENT_COST)


@pytest.fixture(scope="session")
def addon_capturer(tmp_path_factory):
    import os
    import sys

    os.environ["IDEGIS_TESTING"] = "1"
    os.environ["IDEGIS_DATA"] = str(tmp_path_factory.mktemp("idegis_data"))
    sys.path.insert(0, str(ADDON_CAPTURER.parent))
    try:
        return _load("idegis_addon_capturer_for_cost", ADDON_CAPTURER)
    finally:
        sys.path.pop(0)


def _hist(samples: list[tuple[str, float]]) -> list[dict]:
    """samples: list of (iso_ts, watts)."""
    return [{"last_changed": ts, "state": str(w)} for ts, w in samples]


# A pump running at 1000 W continuously across a 2-hour window, no grid
# sensor wired -> everything is grid, priced by whatever ToU period each
# hour falls into.
PUMP_HIST_VALLEY = _hist([
    ("2026-11-25T02:00:00+00:00", 1000.0),
    ("2026-11-25T03:00:00+00:00", 1000.0),
    ("2026-11-25T04:00:00+00:00", 0.0),
])


def test_tariff_period_valley_hours():
    mod = _load("cost_tp1", COMPONENT_COST)
    dt = datetime(2026, 11, 25, 3, 0, tzinfo=timezone.utc)  # 04:00 Madrid (CET)
    assert mod.tariff_period(dt) == "valley"


def test_tariff_period_weekend_is_always_valley():
    mod = _load("cost_tp2", COMPONENT_COST)
    # 2026-11-28 is a Saturday.
    dt = datetime(2026, 11, 28, 12, 0, tzinfo=timezone.utc)
    assert mod.tariff_period(dt) == "valley"


def test_kwh_from_history_matches_addon(cost_mod, addon_capturer):
    # Default cap_seconds=600 clips any gap wider than 10 min (guards
    # against stale readings after HA downtime) — our fixture's 1h-spaced
    # samples hit that cap on purpose, so pass it explicitly to measure
    # the real 2h @ 1000W = 2 kWh this test is meant to check.
    ours = cost_mod.kwh_from_history(PUMP_HIST_VALLEY, cap_seconds=7200)
    theirs = addon_capturer._kwh_from_history(PUMP_HIST_VALLEY, cap_seconds=7200)
    assert ours == theirs
    kwh, motor_s = ours
    assert kwh == pytest.approx(2.0, abs=0.01)
    assert motor_s == pytest.approx(7200.0, abs=1)


def test_kwh_from_history_caps_stale_gaps_by_default(cost_mod, addon_capturer):
    """The 600s default cap is the point, not a bug: a stale/overdue
    sample shouldn't bill as if the pump ran at that power the whole gap."""
    ours = cost_mod.kwh_from_history(PUMP_HIST_VALLEY)
    theirs = addon_capturer._kwh_from_history(PUMP_HIST_VALLEY)
    assert ours == theirs
    kwh, _motor_s = ours
    assert kwh == pytest.approx(1000 * 600 / 3600 / 1000 * 2, abs=0.001)


def test_cost_breakdown_no_grid_sensor_is_all_grid(cost_mod, addon_capturer):
    start = datetime(2026, 11, 25, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 11, 25, 6, 0, tzinfo=timezone.utc)
    ours = cost_mod.cost_breakdown(PUMP_HIST_VALLEY, [], start, end, cap_seconds=7200)
    theirs = addon_capturer.cost_breakdown(PUMP_HIST_VALLEY, [], start, end, cap_seconds=7200)
    assert ours == theirs
    assert ours["solar_kwh"] == 0.0
    assert ours["grid_kwh"] == pytest.approx(2.0, abs=0.01)


def test_cost_breakdown_solar_when_exporting(cost_mod, addon_capturer):
    """With a grid sensor showing export (positive = exporting), the
    pump's draw during that interval must be attributed to solar, not
    grid — the whole point of the feature."""
    start = datetime(2026, 11, 25, 10, 0, tzinfo=timezone.utc)
    end = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)
    pump_hist = _hist([
        ("2026-11-25T10:00:00+00:00", 1000.0),
        ("2026-11-25T11:00:00+00:00", 1000.0),
        ("2026-11-25T12:00:00+00:00", 0.0),
    ])
    grid_hist = _hist([
        ("2026-11-25T09:00:00+00:00", 500.0),  # exporting 500W
    ])
    ours = cost_mod.cost_breakdown(
        pump_hist, grid_hist, start, end, export_positive=True, cap_seconds=7200
    )
    theirs = addon_capturer.cost_breakdown(
        pump_hist, grid_hist, start, end, export_positive=True, cap_seconds=7200
    )
    assert ours == theirs
    assert ours["grid_kwh"] == 0.0
    assert ours["solar_kwh"] == pytest.approx(2.0, abs=0.01)
    assert ours["solar_pct"] == 100.0


def test_cost_breakdown_by_period_sums_to_grid_eur(cost_mod):
    start = datetime(2026, 11, 25, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 11, 25, 23, 0, tzinfo=timezone.utc)
    out = cost_mod.cost_breakdown(PUMP_HIST_VALLEY, [], start, end)
    assert sum(out["by_period_eur"].values()) == pytest.approx(out["grid_eur"], abs=0.0001)


def test_tariff_price_matches_period(cost_mod):
    dt_valley = datetime(2026, 11, 25, 3, 0, tzinfo=timezone.utc)
    assert cost_mod.tariff_price(dt_valley) == cost_mod.DEFAULT_TARIFF["valley"]
