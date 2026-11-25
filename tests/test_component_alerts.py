"""Regression tests for custom_components/idegis_astralpool/alerts.py.

Covers edge-triggering (fires once on the transition into the condition,
clears, can re-fire) and false-positive/negative avoidance for each
check: a disabled check must never fire regardless of input, and a
healthy reading must never fire regardless of thresholds.
"""
from __future__ import annotations

import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
COMPONENT_ALERTS = ROOT / "custom_components" / "idegis_astralpool" / "alerts.py"


def _load(name: str, path: pathlib.Path):
    import sys

    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="session")
def alerts_mod():
    return _load("component_idegis_alerts", COMPONENT_ALERTS)


def _snap(ph=None, salinity=None, seconds_since_last_seen=0) -> dict:
    live = {}
    if ph is not None:
        live["ph"] = {"value": ph}
    if salinity is not None:
        live["salinity"] = {"value": salinity}
    return {"live_measurements": live, "seconds_since_last_seen": seconds_since_last_seen}


def test_ph_disabled_never_fires(alerts_mod):
    cfg = alerts_mod.AlertConfig(ph_enabled=False)
    engine = alerts_mod.AlertEngine(config=cfg)
    assert engine.evaluate(_snap(ph=2.0)) == []


def test_ph_healthy_never_fires(alerts_mod):
    cfg = alerts_mod.AlertConfig(ph_enabled=True)
    engine = alerts_mod.AlertEngine(config=cfg)
    for ph in (7.0, 7.2, 7.4, 7.6):
        assert engine.evaluate(_snap(ph=ph)) == []


def test_ph_low_fires_once_on_rising_edge(alerts_mod):
    cfg = alerts_mod.AlertConfig(ph_enabled=True, ph_min=7.0, ph_max=7.6)
    engine = alerts_mod.AlertEngine(config=cfg)
    assert engine.evaluate(_snap(ph=7.3)) == []
    fired = engine.evaluate(_snap(ph=6.5))
    assert [a.alert_id for a in fired] == ["ph_low"]
    # Still low next tick: must NOT re-fire (edge-triggered, not level-triggered).
    assert engine.evaluate(_snap(ph=6.4)) == []
    # Recovers: clears silently.
    assert engine.evaluate(_snap(ph=7.3)) == []
    # Drops again: fires again (new edge).
    fired_again = engine.evaluate(_snap(ph=6.5))
    assert [a.alert_id for a in fired_again] == ["ph_low"]


def test_ph_high_and_low_are_distinct_alerts(alerts_mod):
    cfg = alerts_mod.AlertConfig(ph_enabled=True, ph_min=7.0, ph_max=7.6)
    engine = alerts_mod.AlertEngine(config=cfg)
    assert [a.alert_id for a in engine.evaluate(_snap(ph=8.0))] == ["ph_high"]


def test_salinity_out_of_range(alerts_mod):
    cfg = alerts_mod.AlertConfig(salinity_enabled=True, salinity_min=1.0, salinity_max=4.0)
    engine = alerts_mod.AlertEngine(config=cfg)
    assert engine.evaluate(_snap(salinity=2.5)) == []
    assert [a.alert_id for a in engine.evaluate(_snap(salinity=0.5))] == ["salinity_low"]


def test_session_stale_threshold(alerts_mod):
    cfg = alerts_mod.AlertConfig(session_stale_enabled=True, session_stale_minutes=60)
    engine = alerts_mod.AlertEngine(config=cfg)
    assert engine.evaluate(_snap(seconds_since_last_seen=3000)) == []  # 50 min
    fired = engine.evaluate(_snap(seconds_since_last_seen=4000))  # 66 min
    assert [a.alert_id for a in fired] == ["session_stale"]


def test_session_stale_none_seconds_does_not_crash(alerts_mod):
    cfg = alerts_mod.AlertConfig(session_stale_enabled=True)
    engine = alerts_mod.AlertEngine(config=cfg)
    assert engine.evaluate({"live_measurements": {}, "seconds_since_last_seen": None}) == []


def test_filtration_coverage_disabled_never_fires(alerts_mod):
    cfg = alerts_mod.AlertConfig(filtration_enabled=False)
    alert = alerts_mod.check_filtration_coverage(10, 56, cfg)
    assert alert is None


def test_filtration_coverage_under_target_fires(alerts_mod):
    cfg = alerts_mod.AlertConfig(filtration_enabled=True, filtration_min_coverage_pct=70)
    alert = alerts_mod.check_filtration_coverage(20, 56, cfg)  # ~36% coverage
    assert alert is not None
    assert alert.alert_id == "filtration_under_target"


def test_filtration_coverage_on_target_does_not_fire(alerts_mod):
    cfg = alerts_mod.AlertConfig(filtration_enabled=True, filtration_min_coverage_pct=70)
    alert = alerts_mod.check_filtration_coverage(50, 56, cfg)  # ~89% coverage
    assert alert is None


def test_engine_skips_filtration_when_not_fed(alerts_mod):
    """Documents the honest gap: without a wired actual-minutes source,
    passing None (the engine's default) means this check never runs."""
    cfg = alerts_mod.AlertConfig(filtration_enabled=True, filtration_min_coverage_pct=70)
    engine = alerts_mod.AlertEngine(config=cfg)
    assert engine.evaluate(_snap(ph=7.3)) == []


def test_multiple_simultaneous_alerts(alerts_mod):
    cfg = alerts_mod.AlertConfig(
        ph_enabled=True, salinity_enabled=True, salinity_min=1.0, salinity_max=4.0
    )
    engine = alerts_mod.AlertEngine(config=cfg)
    fired = engine.evaluate(_snap(ph=6.0, salinity=0.5))
    assert {a.alert_id for a in fired} == {"ph_low", "salinity_low"}
