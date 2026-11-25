"""alerts.py — pure alert-condition evaluation (phase zeta, routing part 1).

No ``homeassistant`` import — same pattern as ``state.py``/``cost.py``/
``recommendation.py`` — so the "does condition X fire?" logic is
unit-testable without installing HA. The thin HA-specific wiring (calling
``hass.services.async_call("notify", ...)`` when :class:`AlertEngine`
reports a new trigger) lives in ``notify_dispatch.py``.

Design notes:
  * Each check is **edge-triggered**: it fires once when the condition
    transitions from not-met to met, not on every evaluation — otherwise
    every push update while e.g. pH stays low would re-notify. It clears
    (and can fire again) once the condition stops being met.
  * ``AlertEngine`` is intentionally stateful (tracks which alerts are
    currently "active" between calls), mirroring ``IdegisState``'s own
    stateful-but-HA-independent design.
  * The filtration-coverage check is implemented and tested here, but is
    **not fed live data yet** — there is no wired source for "actual
    filtration minutes today" (that needs the JSONL-based activity log
    the addon has, not yet ported — see docs/09-roadmap.md). Callers
    simply don't pass ``actual_filtration_minutes_today`` until that
    exists, and the check is skipped (never fires) when it's ``None``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Conservative defaults for a low-salinity Neolysis cell — see codec.py's
# docstring (IT measured 0.0-3.8 g/L range on this install). Overridable
# via options_flow.
DEFAULT_PH_MIN = 7.0
DEFAULT_PH_MAX = 7.6
DEFAULT_SALINITY_MIN = 1.0
DEFAULT_SALINITY_MAX = 4.0
DEFAULT_SESSION_STALE_MINUTES = 60
DEFAULT_FILTRATION_MIN_COVERAGE_PCT = 70


@dataclass(frozen=True, kw_only=True)
class AlertConfig:
    ph_enabled: bool = False
    ph_min: float = DEFAULT_PH_MIN
    ph_max: float = DEFAULT_PH_MAX

    salinity_enabled: bool = False
    salinity_min: float = DEFAULT_SALINITY_MIN
    salinity_max: float = DEFAULT_SALINITY_MAX

    session_stale_enabled: bool = False
    session_stale_minutes: float = DEFAULT_SESSION_STALE_MINUTES

    filtration_enabled: bool = False
    filtration_min_coverage_pct: float = DEFAULT_FILTRATION_MIN_COVERAGE_PCT

    notify_service: str | None = None  # e.g. "notify.telegram_jarvis"


@dataclass(frozen=True, kw_only=True)
class Alert:
    alert_id: str
    message: str


def _check_ph(d: dict[str, Any], cfg: AlertConfig) -> Alert | None:
    if not cfg.ph_enabled:
        return None
    ph = (d.get("live_measurements") or {}).get("ph", {}).get("value")
    if ph is None:
        return None
    if ph < cfg.ph_min:
        return Alert(alert_id="ph_low", message=f"pH too low: {ph:.2f} (< {cfg.ph_min})")
    if ph > cfg.ph_max:
        return Alert(alert_id="ph_high", message=f"pH too high: {ph:.2f} (> {cfg.ph_max})")
    return None


def _check_salinity(d: dict[str, Any], cfg: AlertConfig) -> Alert | None:
    if not cfg.salinity_enabled:
        return None
    salinity = (d.get("live_measurements") or {}).get("salinity", {}).get("value")
    if salinity is None:
        return None
    if salinity < cfg.salinity_min:
        return Alert(
            alert_id="salinity_low",
            message=f"Salinity too low: {salinity:.2f} g/L (< {cfg.salinity_min})",
        )
    if salinity > cfg.salinity_max:
        return Alert(
            alert_id="salinity_high",
            message=f"Salinity too high: {salinity:.2f} g/L (> {cfg.salinity_max})",
        )
    return None


def _check_session_stale(d: dict[str, Any], cfg: AlertConfig) -> Alert | None:
    if not cfg.session_stale_enabled:
        return None
    seconds = d.get("seconds_since_last_seen")
    if seconds is None:
        return None
    if seconds > cfg.session_stale_minutes * 60:
        minutes = round(seconds / 60)
        return Alert(
            alert_id="session_stale",
            message=f"Chlorinator has not reported in {minutes} min "
            f"(threshold: {cfg.session_stale_minutes} min)",
        )
    return None


def check_filtration_coverage(
    actual_minutes_today: float, recommended_minutes_today: float, cfg: AlertConfig
) -> Alert | None:
    """Pure check, called separately from :meth:`AlertEngine.evaluate` —
    see module docstring: not fed live data yet, no wired data source."""
    if not cfg.filtration_enabled or recommended_minutes_today <= 0:
        return None
    coverage_pct = 100 * actual_minutes_today / recommended_minutes_today
    if coverage_pct < cfg.filtration_min_coverage_pct:
        return Alert(
            alert_id="filtration_under_target",
            message=(
                f"Filtration coverage {coverage_pct:.0f}% of target "
                f"({actual_minutes_today:.0f}/{recommended_minutes_today:.0f} min)"
            ),
        )
    return None


def alert_config_from_options(options: dict[str, Any]) -> AlertConfig:
    """Build an AlertConfig from a config entry's ``options`` dict (see
    config_flow.py's ``IdegisOptionsFlow`` for the matching keys)."""
    return AlertConfig(
        ph_enabled=options.get("ph_enabled", False),
        ph_min=options.get("ph_min", DEFAULT_PH_MIN),
        ph_max=options.get("ph_max", DEFAULT_PH_MAX),
        salinity_enabled=options.get("salinity_enabled", False),
        salinity_min=options.get("salinity_min", DEFAULT_SALINITY_MIN),
        salinity_max=options.get("salinity_max", DEFAULT_SALINITY_MAX),
        session_stale_enabled=options.get("session_stale_enabled", False),
        session_stale_minutes=options.get(
            "session_stale_minutes", DEFAULT_SESSION_STALE_MINUTES
        ),
        filtration_enabled=options.get("filtration_enabled", False),
        filtration_min_coverage_pct=options.get(
            "filtration_min_coverage_pct", DEFAULT_FILTRATION_MIN_COVERAGE_PCT
        ),
        notify_service=options.get("notify_service") or None,
    )


_CHECKS = (_check_ph, _check_salinity, _check_session_stale)


@dataclass
class AlertEngine:
    """Tracks which alert_ids are currently active, across calls, so each
    one is reported only on its rising edge (not-met -> met).

    ``active_alerts`` (the full ``Alert`` objects, not just the ids) is
    kept up to date on every :meth:`evaluate` call so a caller — e.g. a
    coordinator feeding a diagnostic sensor — can show the current state
    of all active alerts, not just the ones that just triggered. This is
    what lets the native panel show "active alerts" without depending on
    them being routed through a particular ``notify_service`` (see
    docs/09-roadmap.md phase epsilon)."""

    config: AlertConfig
    _active: set[str] = field(default_factory=set)
    _active_details: dict[str, Alert] = field(default_factory=dict)

    @property
    def active_alerts(self) -> list[Alert]:
        return list(self._active_details.values())

    def evaluate(
        self,
        snapshot: dict[str, Any],
        *,
        actual_filtration_minutes_today: float | None = None,
        recommended_filtration_minutes_today: float | None = None,
    ) -> list[Alert]:
        checks = list(_CHECKS)
        current: dict[str, Alert] = {}
        for check in checks:
            alert = check(snapshot, self.config)
            if alert is not None:
                current[alert.alert_id] = alert

        if (
            actual_filtration_minutes_today is not None
            and recommended_filtration_minutes_today is not None
        ):
            alert = check_filtration_coverage(
                actual_filtration_minutes_today,
                recommended_filtration_minutes_today,
                self.config,
            )
            if alert is not None:
                current[alert.alert_id] = alert

        new_ids = set(current) - self._active
        self._active = set(current)
        self._active_details = current
        return [current[i] for i in new_ids]
