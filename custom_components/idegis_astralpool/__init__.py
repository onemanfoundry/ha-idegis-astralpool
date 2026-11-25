"""Idegis / AstralPool pool chlorinator integration.

Native mode A (cloud-MITM): this integration registers its own HTTP
receiver (see cloud_proxy.py) that the LAN's reverse proxy / DNS override
for api.idegis.net forwards to, decodes the B0 protocol itself, and
exposes entities fed by a push-based coordinator. No external capturer
add-on is required any more for this mode.

Modbus RTU (path B) and Poolstation cloud (path C) are placeholders for
future releases — see docs/09-roadmap.md in the repository.
"""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .cloud_proxy import register_views
from .const import CONF_MODE, DOMAIN, MODE_CLOUD_MITM
from .coordinator import IdegisCloudMitmCoordinator
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

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await async_register_panel(hass)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
