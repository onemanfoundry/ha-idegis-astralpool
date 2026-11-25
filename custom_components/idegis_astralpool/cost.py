"""cost.py — electricity cost with solar attribution + ToU tariff (phase gamma).

Pure port of ``addon/rootfs/opt/idegis/capturer.py``'s
``_kwh_from_history`` / ``tariff_period`` / ``tariff_price`` /
``cost_breakdown`` (addon v0.6.11). No ``homeassistant`` import — same
pattern as ``state.py``/``recommendation.py`` — so it's unit-testable
without installing HA.

Both inputs (``pump_hist``/``grid_hist``) are plain lists of
``{"last_changed"|"last_updated": iso_str, "state": "<float-ish>"}``
dicts, the exact shape HA's ``history.get_significant_states`` /
``/api/history/period`` return for a numeric sensor. Whoever wires this
into the integration (a later iteration, once a pump-power entity is
selected in the options flow) is responsible for fetching that history
and handing it to :func:`cost_breakdown` — this module does no I/O.

Tariff defaults are Spain's 2.0TD geometry, same as the addon — these
are generic market defaults, not specific to Sergio's calibration (unlike
``recommendation.py``'s filtration constants), so they're safe to reuse
unchanged pending a real options_flow.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

DEFAULT_TARIFF: dict = {
    "tz": "Europe/Madrid",
    "peak": 0.2234,
    "mid": 0.1483,
    "valley": 0.1147,
    "export": 0.04,
    "peak_hours": {10, 11, 12, 13, 18, 19, 20, 21},
    "valley_hours": {0, 1, 2, 3, 4, 5, 6, 7},
    "weekend_days": {5, 6},
}


def tariff_period(dt: datetime, tariff: dict | None = None) -> str:
    """Time-of-use period ('peak'|'mid'|'valley') for a UTC datetime.

    Spain 2.0TD geometry by default: weekends are valley all day; on
    weekdays the configured peak/valley hours apply and everything else
    is mid (llano). Evaluated in the tariff's local timezone so DST is
    handled correctly."""
    t = tariff or DEFAULT_TARIFF
    try:
        local = dt.astimezone(ZoneInfo(t["tz"]))
    except Exception:  # noqa: BLE001 — bad tz string -> fall back to UTC
        local = dt
    if local.weekday() in t["weekend_days"]:
        return "valley"
    h = local.hour
    if h in t["peak_hours"]:
        return "peak"
    if h in t["valley_hours"]:
        return "valley"
    return "mid"


def tariff_price(dt: datetime, tariff: dict | None = None) -> float:
    """Import price (EUR/kWh) in force at ``dt``."""
    t = tariff or DEFAULT_TARIFF
    return float(t[tariff_period(dt, t)])


def _hist_ts(rec: dict) -> datetime | None:
    raw = rec.get("last_changed") or rec.get("last_updated")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:  # noqa: BLE001
        return None


def _hist_w(rec: dict) -> float | None:
    try:
        return float(rec.get("state"))
    except (TypeError, ValueError):
        return None


def kwh_from_history(
    history: list[dict],
    floor_w: float = 5.0,
    cap_seconds: float = 600.0,
) -> tuple[float, float]:
    """Integrate kWh from a list of HA state changes (power, W).

    Returns (kwh_total, motor_running_seconds): the second value sums
    only intervals where power was above ``floor_w`` so the caller can
    distinguish real motor activity from contactor coil draw. Intervals
    longer than ``cap_seconds`` are clipped (HA may have been down or the
    entity unavailable)."""
    if not history:
        return 0.0, 0.0
    kwh = 0.0
    motor_s = 0.0
    prev_ts: datetime | None = None
    prev_w: float | None = None
    for rec in history:
        ts = _hist_ts(rec)
        w = _hist_w(rec)
        if ts is None:
            continue
        if prev_ts is not None and prev_w is not None:
            dt = min((ts - prev_ts).total_seconds(), cap_seconds)
            if dt > 0:
                kwh += prev_w * dt / 3600 / 1000
                if prev_w > floor_w:
                    motor_s += dt
        prev_ts = ts
        prev_w = w
    return round(kwh, 3), round(motor_s, 1)


def cost_breakdown(
    pump_hist: list[dict],
    grid_hist: list[dict],
    start: datetime,
    end: datetime,
    *,
    export_positive: bool = True,
    tariff: dict | None = None,
    floor_w: float = 5.0,
    cap_seconds: float = 600.0,
) -> dict:
    """Split a pump's energy over [start, end] into grid vs solar and
    price the grid part by tariff period.

    For each running interval we look up the grid-power sign at its
    start: if the house was exporting (PV surplus) the pump's draw is
    counted as **solar** (0 EUR paid, but its forgone export value is
    tracked); otherwise it is **grid**, priced at the tariff period in
    force. With no grid sensor wired (empty ``grid_hist``) everything
    counts as grid — a safe, if pessimistic, fallback."""
    t = tariff or DEFAULT_TARIFF
    out = {
        "grid_kwh": 0.0,
        "solar_kwh": 0.0,
        "grid_eur": 0.0,
        "solar_export_value_eur": 0.0,
        "by_period_eur": {"peak": 0.0, "mid": 0.0, "valley": 0.0},
    }
    grid = sorted(
        ((ts, w) for r in grid_hist
         if (ts := _hist_ts(r)) is not None and (w := _hist_w(r)) is not None),
        key=lambda x: x[0],
    )
    gi = 0
    last_gw: float | None = None
    prev_ts: datetime | None = None
    prev_w: float | None = None
    for rec in pump_hist:
        ts = _hist_ts(rec)
        w = _hist_w(rec)
        if prev_ts is not None and prev_w is not None and ts is not None:
            iv_start = max(prev_ts, start)
            iv_end = min(ts, end)
            dt = (iv_end - iv_start).total_seconds()
            dt = min(dt, cap_seconds)
            if dt > 0 and prev_w > floor_w:
                energy = prev_w * dt / 3600 / 1000  # kWh
                while gi < len(grid) and grid[gi][0] <= iv_start:
                    last_gw = grid[gi][1]
                    gi += 1
                exporting = last_gw is not None and (
                    last_gw > 0 if export_positive else last_gw < 0
                )
                if exporting:
                    out["solar_kwh"] += energy
                    out["solar_export_value_eur"] += energy * t["export"]
                else:
                    period = tariff_period(iv_start, t)
                    eur = energy * float(t[period])
                    out["grid_kwh"] += energy
                    out["grid_eur"] += eur
                    out["by_period_eur"][period] += eur
        prev_ts = ts
        prev_w = w
    total = out["grid_kwh"] + out["solar_kwh"]
    out["solar_pct"] = round(100 * out["solar_kwh"] / total, 1) if total > 0 else 0.0
    out["grid_kwh"] = round(out["grid_kwh"], 3)
    out["solar_kwh"] = round(out["solar_kwh"], 3)
    out["grid_eur"] = round(out["grid_eur"], 4)
    out["solar_export_value_eur"] = round(out["solar_export_value_eur"], 4)
    out["by_period_eur"] = {k: round(v, 4) for k, v in out["by_period_eur"].items()}
    return out
