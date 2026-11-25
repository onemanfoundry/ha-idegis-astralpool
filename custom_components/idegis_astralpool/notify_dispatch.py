"""notify_dispatch.py — thin HA wiring between AlertEngine and notify.*.

Deliberately tiny: all the "does this condition fire" logic lives in
``alerts.py`` (pure, no HA import, independently tested). This module's
only job is to call the existing ``notify.*`` service HA already has
configured (telegram_bot, mail/smtp, mobile_app, ...) when
:class:`alerts.AlertEngine` reports a newly-triggered alert — the
integration never reimplements a notification transport itself, per
Sergio's requirement.
"""
from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant

from .alerts import Alert, AlertConfig, AlertEngine

_LOGGER = logging.getLogger(__name__)


async def dispatch_new_alerts(
    hass: HomeAssistant, engine: AlertEngine, snapshot: dict, device_name: str
) -> None:
    """Evaluate the engine against the latest snapshot and fire any newly
    triggered alert at the configured notify.* service.

    Kept for callers that want evaluation + dispatch in one call; the
    integration's own ``__init__.py`` evaluates synchronously itself
    (so ``AlertEngine.active_alerts`` is up to date the instant the
    coordinator pushes, for the sensor that surfaces it) and calls
    :func:`dispatch_alerts` directly instead."""
    alerts: list[Alert] = engine.evaluate(snapshot)
    await dispatch_alerts(hass, engine.config, alerts, device_name)


async def dispatch_alerts(
    hass: HomeAssistant, config: AlertConfig, alerts: list[Alert], device_name: str
) -> None:
    """Fire each already-evaluated alert at the configured notify.*
    service. Does no evaluation itself — see :func:`dispatch_new_alerts`
    for the evaluate-then-dispatch convenience wrapper."""
    if not alerts:
        return

    service = config.notify_service
    if not service:
        _LOGGER.warning(
            "idegis_astralpool: %d alert(s) triggered but no notify_service "
            "configured: %s",
            len(alerts),
            [a.alert_id for a in alerts],
        )
        return

    domain, _, service_name = service.partition(".")
    if not domain or not service_name:
        _LOGGER.error(
            "idegis_astralpool: notify_service %r is not a valid 'domain.service'",
            service,
        )
        return

    for alert in alerts:
        try:
            await hass.services.async_call(
                domain,
                service_name,
                {"message": f"[{device_name}] {alert.message}", "title": device_name},
                blocking=False,
            )
        except Exception as exc:  # noqa: BLE001
            _LOGGER.error(
                "idegis_astralpool: failed to dispatch alert %s via %s: %r",
                alert.alert_id, service, exc,
            )
