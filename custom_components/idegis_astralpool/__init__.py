"""Idegis / AstralPool pool chlorinator integration.

Native mode A (cloud-MITM): this integration registers its own HTTP
receiver (see cloud_proxy.py) that the LAN's reverse proxy / DNS override
for api.idegis.net forwards to, decodes the B0 protocol itself, and
exposes entities fed by a push-based coordinator. No external capturer
add-on is required any more for this mode.

Alert routing (phase zeta): after every coordinator push, the latest
snapshot is run through alerts.AlertEngine (configured from the options
flow) and any newly-triggered alert is dispatched to the user's chosen
notify.* service via notify_dispatch.dispatch_new_alerts.

Modbus RTU (path B) and Poolstation cloud (path C) are placeholders for
future releases — see docs/09-roadmap.md in the repository.
"""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .alerts import AlertEngine, alert_config_from_options
from .cloud_proxy import register_views
from .config_flow import OPT_GRID_POWER_ENTITY, OPT_PUMP_POWER_ENTITY
from .const import CONF_MODE, DOMAIN, MODE_CLOUD_MITM
from .coordinator import IdegisCloudMitmCoordinator, IdegisCostCoordinator
from .notify_dispatch import dispatch_new_alerts
from .panel import async_register_panel
from .state import IdegisState

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Idegis from a config entry."""
    mode = entry.data.get(CONF_MODE, MODE_CLOUD_MITM)

    if mode != MODE_CLOUD_MITM:
        _LOGGER.error("mode %s is not implemented yet", mode)
        return False

    state = IdegisState()
    coordinator = IdegisCloudMitmCoordinator(hass, state)
    alert_engine = AlertEngine(config=alert_config_from_options(entry.options))

    def _on_coordinator_update() -> None:
        if coordinator.data is None:
            return
        hass.async_create_task(
            dispatch_new_alerts(hass, alert_engine, coordinator.data, "Idegis chlorinator")
        )

    coordinator.async_add_listener(_on_coordinator_update)

    # HA's HTTP router has no unregister_view: registering the same URL
    # twice (e.g. on a config entry reload) raises. Only the very first
    # setup in this hass run actually registers the views; later entries
    # just update what they feed, which is fine since mode A only ever
    # expects a single physical chlorinator/config entry.
    if not hass.data.get(f"{DOMAIN}_views_registered"):
        session = async_get_clientsession(hass)
        register_views(hass, state, session, on_update=coordinator.push_update)
        hass.data[f"{DOMAIN}_views_registered"] = True

    await coordinator.async_config_entry_first_refresh()

    cost_coordinator = IdegisCostCoordinator(
        hass,
        pump_entity=entry.options.get(OPT_PUMP_POWER_ENTITY) or "",
        grid_entity=entry.options.get(OPT_GRID_POWER_ENTITY) or None,
        cloud_mitm_coordinator=coordinator,
    )
    await cost_coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "cloud_mitm": coordinator,
        "cost": cost_coordinator,
    }

    await async_register_panel(hass)

    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Options (alert thresholds / notify_service) changed: reload the
    entry so the new AlertConfig takes effect."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
