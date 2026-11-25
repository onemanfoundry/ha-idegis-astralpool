"""recommendation.py — filtration-minutes recommendation (phase gamma).

Pure port of ``addon/rootfs/opt/idegis/capturer.py``'s
``_temp_multiplier`` / ``_compute_recommendation`` (no ``homeassistant``
import, same pattern as ``state.py``/``cost.py``), so it's unit-testable
without installing HA.

``compute_recommendation``/``RecommendationInputs`` below are the
chemistry/turnover model (chlorine-demand vs. hydraulic-turnover
minutes) and are kept, with their original calibrated defaults, purely
for the byte-for-byte parity tests against the add-on's formula
(``tests/test_component_recommendation.py``) — they are **no longer
what the integration recommends day to day**.

Sergio confirmed (2026-11-25) the actual policy he wants for this
installation is much simpler than the chemistry model: **1h of daily
filtration in summer, 1h once a week in winter** — not the
chemistry-calibrated ~55 min/day this module used to expose on the
sensor. ``compute_seasonal_recommendation`` implements that policy and
is what ``history_glue.compute_recommendation_now`` actually calls.
Season boundary (June-September = summer) and the fixed winter weekly
session day (Monday) are reasonable defaults, not something Sergio
specified explicitly — flag to him if either needs adjusting.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

# Calibrated defaults for this installation — see module docstring.
POOL_VOLUME_M3 = 37.0
PUMP_NOMINAL_FLOW_M3_H = 12.0
CELL_CAPACITY_G_H = 24.0
TARGET_PRODUCTION_PCT = 100
CHLORINE_DEMAND_PPM_PER_DAY = 0.25
MIN_TURNOVERS_PER_DAY = 0.5
APPLY_TEMP_MULTIPLIER = False
NET_N_CLEAN_INSTALLED = True
NET_N_CLEAN_TURNOVER_FACTOR = 0.6


def temp_multiplier(temp_c: float | None) -> float:
    """Chlorine demand scales with water temperature: warmer water
    consumes chlorine faster (kinetics + bacteriostatic margin)."""
    if temp_c is None:
        return 1.0
    if temp_c < 20:
        return 0.7
    if temp_c < 24:
        return 0.85
    if temp_c < 28:
        return 1.0
    if temp_c < 32:
        return 1.15
    if temp_c < 36:
        return 1.3
    return 1.5


@dataclass(frozen=True, kw_only=True)
class RecommendationInputs:
    volume_m3: float = POOL_VOLUME_M3
    flow_m3_h: float = PUMP_NOMINAL_FLOW_M3_H
    cell_g_h: float = CELL_CAPACITY_G_H
    target_pct: int = TARGET_PRODUCTION_PCT
    chlorine_demand_ppm_day: float = CHLORINE_DEMAND_PPM_PER_DAY
    min_turnovers_per_day: float = MIN_TURNOVERS_PER_DAY
    temp_c: float | None = None
    apply_temp_multiplier: bool = APPLY_TEMP_MULTIPLIER
    net_n_clean_installed: bool = NET_N_CLEAN_INSTALLED


def compute_recommendation(inputs: RecommendationInputs) -> dict:
    """Combine chlorine-demand and filter-turnover constraints.

    The driver for an indoor pool is **chlorine production**, not
    turnover. With the cover on and zero UV exposure the chlorine decay
    is slow, so the pump's job is mainly to electrolyse and distribute.
    We compute two candidates and take the larger:

    1. **Chlorine-demand minutes** — how long the cell has to run at the
       target production percentage to replace the daily chlorine
       consumption.
    2. **Turnover minutes** — minimal hydraulic mixing.

    The ``driver`` field tells the caller which constraint won.
    """
    i = inputs
    cl_per_min = i.cell_g_h * (i.target_pct / 100) / 60.0
    daily_demand_g = i.chlorine_demand_ppm_day * i.volume_m3
    temp_mult = temp_multiplier(i.temp_c) if i.apply_temp_multiplier else 1.0
    chl_minutes = (daily_demand_g / cl_per_min) * temp_mult if cl_per_min > 0 else 0
    effective_turnovers = i.min_turnovers_per_day * (
        NET_N_CLEAN_TURNOVER_FACTOR if i.net_n_clean_installed else 1.0
    )
    turnover_minutes = (
        (i.volume_m3 / i.flow_m3_h) * 60 * effective_turnovers if i.flow_m3_h > 0 else 0
    )
    rec = max(chl_minutes, turnover_minutes)
    driver = "chlorine_demand" if chl_minutes >= turnover_minutes else "turnover"
    return {
        "recommended_minutes_today": round(rec),
        "driver": driver,
        "chlorine_demand_minutes": round(chl_minutes),
        "turnover_minutes": round(turnover_minutes),
        "temperature_multiplier": temp_mult,
        "temperature_multiplier_applied": i.apply_temp_multiplier,
        "net_n_clean_installed": i.net_n_clean_installed,
        "effective_turnovers_per_day": round(effective_turnovers, 3),
        "daily_chlorine_demand_g": round(daily_demand_g, 1),
        "cell_output_g_per_min": round(cl_per_min, 3),
    }


# Seasonal policy (confirmed by Sergio 2026-11-25) — see module docstring.
SUMMER_MONTHS = frozenset({6, 7, 8, 9})
SUMMER_DAILY_MINUTES = 60
WINTER_WEEKLY_MINUTES = 60
WINTER_WEEKLY_SESSION_WEEKDAY = 0  # Monday


def is_summer(today: date) -> bool:
    return today.month in SUMMER_MONTHS


def compute_seasonal_recommendation(today: date) -> dict:
    """1h/day in summer, a single 1h session on Mondays in winter
    (0 the other winter days) — Sergio's confirmed policy, replacing
    the chemistry-model calibration as the sensor-facing number."""
    summer = is_summer(today)
    if summer:
        minutes = SUMMER_DAILY_MINUTES
        driver = "seasonal_summer_daily"
    elif today.weekday() == WINTER_WEEKLY_SESSION_WEEKDAY:
        minutes = WINTER_WEEKLY_MINUTES
        driver = "seasonal_winter_weekly_session"
    else:
        minutes = 0
        driver = "seasonal_winter_rest_day"
    return {
        "recommended_minutes_today": minutes,
        "driver": driver,
        "season": "summer" if summer else "winter",
    }
