"""panel.py — native sidebar panel registration (phase epsilon).

Registers a plain HA custom panel (``panel_custom``), not a Lovelace
dashboard and not an iframe into the capturer add-on's ingress UI. The
panel's web component (``panel_assets/idegis-astralpool-panel.js``) reads
entity state straight from the ``hass`` object HA injects into every
panel, exactly like a Lovelace card would — no HTTP fetch of its own, so
it doesn't depend on any backend route beyond what the integration's own
entities already provide.

Served via a dedicated :class:`HomeAssistantView` (rather than
``hass.http.register_static_path``) so the exact file is read fresh from
disk through code we test, instead of depending on a static-path API
whose signature has changed across HA core versions.
"""
from __future__ import annotations

import pathlib

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

PANEL_URL = "/api/idegis_astralpool/panel/idegis-astralpool-panel.js"
PANEL_NAME = "idegis-astralpool-panel"
PANEL_TITLE = "Idegis / AstralPool"
PANEL_ICON = "mdi:pool"

_JS_PATH = pathlib.Path(__file__).parent / "panel_assets" / "idegis-astralpool-panel.js"


class IdegisPanelJSView(HomeAssistantView):
    """Serves the panel's web component module."""

    url = PANEL_URL
    name = "api:idegis_astralpool:panel_js"
    requires_auth = False
    cors_allowed = True

    async def get(self, request):  # noqa: ANN001
        from aiohttp import web

        return web.Response(
            text=_JS_PATH.read_text(encoding="utf-8"),
            content_type="application/javascript",
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )


async def async_register_panel(hass: HomeAssistant) -> None:
    """Idempotently register the JS view + sidebar panel for this hass run."""
    if hass.data.get("idegis_astralpool_panel_registered"):
        return

    hass.http.register_view(IdegisPanelJSView())

    from homeassistant.components import panel_custom

    await panel_custom.async_register_panel(
        hass,
        webcomponent_name=PANEL_NAME,
        frontend_url_path="idegis_astralpool",
        module_url=PANEL_URL,
        sidebar_title=PANEL_TITLE,
        sidebar_icon=PANEL_ICON,
        require_admin=False,
    )
    hass.data["idegis_astralpool_panel_registered"] = True
