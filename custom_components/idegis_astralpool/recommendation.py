"""recommendation.py — filtration-minutes recommendation (phase gamma).

Pure port of ``addon/rootfs/opt/idegis/capturer.py``'s
``_temp_multiplier`` / ``_compute_recommendation`` (no ``homeassistant``
import, same pattern as ``state.py``/``cost.py``), so it's unit-testable
without installing HA.

Defaults are **not** copied verbatim from the add-on's shipped
``config.yaml`` (``target_production_pct: 40``,
``chlorine_demand_ppm_per_day: 0.6``, ``min_turnovers_per_day: 0.75``,
``net_n_clean_installed: false``) — those are the inputs documented as
*already known to overshoot* for this installation (project memory
``feedback_filtration_defaults_overshoot``: they yield ~139 min/day,
rejected by Sergio in favour of ~55 min/day, see
``project_filtration_target``). This module's defaults instead encode
the calibrated reading from that memory for Sergio's actual install
(Neo2-24PH/S, 37 m3 indoor covered pool, Net'N Clean installed):
``target_production_pct=100``, ``chlorine_demand_ppm_per_day=0.25``,
``min_turnovers_per_day=0.5``, ``net_n_clean_installed=True`` — which
compute to ~56 min/day (turnover-driven), matching the ~55 min/day
Sergio settled on. These become the integration's config/options_flow
defaults in a later iteration; they are not yet exposed anywhere for
Sergio to override, so flag it to him before trusting the number for
real automations.
"""
from __future__ import annotations

from dataclasses import dataclass

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
