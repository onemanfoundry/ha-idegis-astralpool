"""history_glue.py — thin HA layer feeding cost.py / recommendation.py
with real history (phase delta, "wire to live entities" step).

The pure modules (cost.py, recommendation.py) take plain
``{"last_changed": iso_str, "state": "<value>"}`` dicts and do no I/O.
This module is the only place that:
  * fetches real state history for a configured entity via HA's recorder,
  * adapts ``homeassistant.core.State`` objects into that plain dict shape,
  * calls the pure functions with the result.

Kept intentionally small and un-unit-tested in isolation (same tradeoff
``cloud_proxy.py`` makes for ``state.py``): the logic worth protecting
with synthetic tests already lives in cost.py/recommendation.py, which
are fully covered. What's here is just history-fetch plumbing, exercised
indirectly once `tests/test_component_smoke.py` is extended to cover it
(tracked as a follow-up in docs/09-roadmap.md phase delta).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.core import HomeAssistant, State

from .cost import DEFAULT_TARIFF, cost_breakdown
from .recommendation import compute_seasonal_recommendation


def _states_to_dicts(states: list[State]) -> list[dict[str, Any]]:
    return [{"last_changed": s.last_changed.isoformat(), "state": s.state} for s in states]


async def _fetch_history(hass: HomeAssistant, entity_id: str, start: datetime, end: datetime) -> list[dict[str, Any]]:
    if not entity_id:
        return []
    from homeassistant.components.recorder import history

    result = await hass.async_add_executor_job(
        history.get_significant_states, hass, start, end, [entity_id]
    )
    return _states_to_dicts(result.get(entity_id, []))


async def async_compute_cost(
    hass: HomeAssistant,
    *,
    pump_entity: str,
    grid_entity: str | None,
    export_positive: bool = True,
) -> dict[str, Any] | None:
    """24h/7d/30d cost breakdown for the configured pump-power entity."""
    if not pump_entity:
        return None

    now = datetime.now(timezone.utc)
    pump_hist = await _fetch_history(hass, pump_entity, now - timedelta(days=30), now)
    grid_hist = (
        await _fetch_history(hass, grid_entity, now - timedelta(days=30), now)
        if grid_entity else []
    )

    windows = {
        "24h": now - timedelta(hours=24),
        "7d": now - timedelta(days=7),
        "30d": now - timedelta(days=30),
    }
    out: dict[str, Any] = {}
    for key, start in windows.items():
        breakdown = cost_breakdown(
            pump_hist, grid_hist, start, now,
            export_positive=export_positive, tariff=DEFAULT_TARIFF,
        )
        total_kwh = breakdown["grid_kwh"] + breakdown["solar_kwh"]
        breakdown["solar_pct"] = (
            round(100.0 * breakdown["solar_kwh"] / total_kwh, 1) if total_kwh else 0.0
        )
        out[key] = breakdown
    return out


def compute_recommendation_now(temp_c: float | None) -> dict[str, Any]:
    """Thin wrapper so callers don't need to import recommendation.py
    directly. ``temp_c`` is accepted for backwards-compatible call sites
    but no longer used: the recommendation is now Sergio's seasonal
    policy (1h/day summer, 1h/week winter), not the temperature-aware
    chemistry model — see recommendation.py's module docstring."""
    return compute_seasonal_recommendation(datetime.now().date())
