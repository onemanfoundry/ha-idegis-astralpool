"""Config + options flow for the Idegis / AstralPool integration.

Mode A (cloud-MITM) is native: the integration registers its own
receiver on HA's own HTTP server (see cloud_proxy.py), so there is
nothing external to probe or configure at setup time — just confirm the
user wants to enable it and remind them of the one external prerequisite
(the LAN-side DNS override / reverse proxy for api.idegis.net must point
at this Home Assistant instance).

The options flow (phase zeta — notification routing) lets the user pick
which alert conditions are active and which existing HA ``notify.*``
service to route them to. The "does condition X fire" logic lives in
alerts.py; this flow only edits the AlertConfig fields stored in the
config entry's options.
"""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.helpers import selector

from .alerts import (
    DEFAULT_FILTRATION_MIN_COVERAGE_PCT,
    DEFAULT_PH_MAX,
    DEFAULT_PH_MIN,
    DEFAULT_SALINITY_MAX,
    DEFAULT_SALINITY_MIN,
    DEFAULT_SESSION_STALE_MINUTES,
)
from .const import CONF_MODE, DOMAIN, MODE_CLOUD_MITM

OPT_PUMP_POWER_ENTITY = "pump_power_entity"
OPT_GRID_POWER_ENTITY = "grid_power_entity"

OPT_PH_ENABLED = "ph_enabled"
OPT_PH_MIN = "ph_min"
OPT_PH_MAX = "ph_max"
OPT_SALINITY_ENABLED = "salinity_enabled"
OPT_SALINITY_MIN = "salinity_min"
OPT_SALINITY_MAX = "salinity_max"
OPT_SESSION_STALE_ENABLED = "session_stale_enabled"
OPT_SESSION_STALE_MINUTES = "session_stale_minutes"
OPT_FILTRATION_ENABLED = "filtration_enabled"
OPT_FILTRATION_MIN_COVERAGE_PCT = "filtration_min_coverage_pct"
OPT_NOTIFY_SERVICE = "notify_service"


class IdegisConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 2

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Single step: mode A is the only implemented mode so far."""
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        if user_input is not None:
            return self.async_create_entry(
                title="Idegis / AstralPool (cloud-MITM)",
                data={CONF_MODE: MODE_CLOUD_MITM},
            )

        return self.async_show_form(step_id="user")

    @staticmethod
    def async_get_options_flow(config_entry: ConfigEntry) -> IdegisOptionsFlow:
        return IdegisOptionsFlow(config_entry)


class IdegisOptionsFlow(OptionsFlow):
    """Alert conditions + notify.* routing (phase zeta)."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self._entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        opts = self._entry.options
        schema = vol.Schema(
            {
                vol.Optional(
                    OPT_PUMP_POWER_ENTITY, default=opts.get(OPT_PUMP_POWER_ENTITY, "")
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor", device_class="power")
                ),
                vol.Optional(
                    OPT_GRID_POWER_ENTITY, default=opts.get(OPT_GRID_POWER_ENTITY, "")
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor", device_class="power")
                ),
                vol.Required(OPT_PH_ENABLED, default=opts.get(OPT_PH_ENABLED, False)): bool,
                vol.Required(OPT_PH_MIN, default=opts.get(OPT_PH_MIN, DEFAULT_PH_MIN)): vol.Coerce(float),
                vol.Required(OPT_PH_MAX, default=opts.get(OPT_PH_MAX, DEFAULT_PH_MAX)): vol.Coerce(float),
                vol.Required(
                    OPT_SALINITY_ENABLED, default=opts.get(OPT_SALINITY_ENABLED, False)
                ): bool,
                vol.Required(
                    OPT_SALINITY_MIN, default=opts.get(OPT_SALINITY_MIN, DEFAULT_SALINITY_MIN)
                ): vol.Coerce(float),
                vol.Required(
                    OPT_SALINITY_MAX, default=opts.get(OPT_SALINITY_MAX, DEFAULT_SALINITY_MAX)
                ): vol.Coerce(float),
                vol.Required(
                    OPT_SESSION_STALE_ENABLED,
                    default=opts.get(OPT_SESSION_STALE_ENABLED, False),
                ): bool,
                vol.Required(
                    OPT_SESSION_STALE_MINUTES,
                    default=opts.get(OPT_SESSION_STALE_MINUTES, DEFAULT_SESSION_STALE_MINUTES),
                ): vol.Coerce(float),
                vol.Required(
                    OPT_FILTRATION_ENABLED, default=opts.get(OPT_FILTRATION_ENABLED, False)
                ): bool,
                vol.Required(
                    OPT_FILTRATION_MIN_COVERAGE_PCT,
                    default=opts.get(
                        OPT_FILTRATION_MIN_COVERAGE_PCT, DEFAULT_FILTRATION_MIN_COVERAGE_PCT
                    ),
                ): vol.Coerce(float),
                vol.Optional(
                    OPT_NOTIFY_SERVICE, default=opts.get(OPT_NOTIFY_SERVICE, "")
                ): str,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
