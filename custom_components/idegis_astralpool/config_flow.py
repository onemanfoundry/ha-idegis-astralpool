"""Config flow for the Idegis / AstralPool integration.

Mode A (cloud-MITM) is now native: the integration registers its own
receiver on HA's own HTTP server (see cloud_proxy.py), so there is
nothing external to probe or configure at setup time — just confirm the
user wants to enable it and remind them of the one external prerequisite
(the LAN-side DNS override / reverse proxy for api.idegis.net must point
at this Home Assistant instance).
"""
from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .const import CONF_MODE, DOMAIN, MODE_CLOUD_MITM


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
