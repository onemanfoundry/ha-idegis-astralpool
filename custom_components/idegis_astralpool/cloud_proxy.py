"""cloud_proxy.py — native in-process cloud-MITM receiver (mode A).

Thin HA-specific layer: registers an unauthenticated
:class:`HomeAssistantView` that the LAN-side reverse proxy / DNS override
for ``api.idegis.net`` forwards to, instead of forwarding to the
capturer add-on's ``:8765``. All the protocol/session logic lives in the
HA-independent ``state.py`` (see its module docstring for what is and
isn't ported yet) so that logic stays unit-testable without installing
Home Assistant.
"""
from __future__ import annotations

import logging
from typing import Any

from aiohttp import ClientSession, web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .state import (
    UPSTREAM_HOST,
    UPSTREAM_HOST_HEADER,
    VIEW_URL_READ,
    VIEW_URL_WRITE,
    IdegisState,
    build_record,
)

_LOGGER = logging.getLogger(__name__)


class IdegisCloudProxyView(HomeAssistantView):
    """Unauthenticated view the chlorinator's HTTP requests land on.

    Registered at both the read.php and write.php paths because the two
    sub-paths need distinct routes under HA's HTTP component (which
    matches on exact/templated URLs, not a wildcard like aiohttp's
    ``add_route("*", ...)``).
    """

    requires_auth = False
    cors_allowed = False

    def __init__(
        self,
        url: str,
        state: IdegisState,
        session: ClientSession,
        on_update: Any = None,
    ) -> None:
        self.url = url
        self.name = f"api:idegis_astralpool:{url.rsplit('/', 1)[-1]}"
        self._state = state
        self._session = session
        self._on_update = on_update

    async def get(self, request: web.Request) -> web.StreamResponse:
        return await self._proxy(request)

    async def _proxy(self, request: web.Request) -> web.StreamResponse:
        path = request.path
        query_string = request.query_string
        url = f"http://{UPSTREAM_HOST}{path}"
        if query_string:
            url = f"{url}?{query_string}"

        headers = {k: v for k, v in request.headers.items() if k.lower() != "host"}
        headers["Host"] = UPSTREAM_HOST_HEADER

        upstream_status: int | None = None
        upstream_body = b""
        try:
            async with self._session.get(url, headers=headers, allow_redirects=False) as resp:
                upstream_status = resp.status
                upstream_body = await resp.read()
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("idegis_astralpool: upstream error: %r", exc)

        record = build_record(
            method=request.method,
            path=path,
            query=dict(request.query),
            remote=request.remote,
            upstream_status=upstream_status,
            upstream_body=upstream_body,
        )
        self._state.ingest(record)
        if self._on_update is not None:
            self._on_update()

        if upstream_status is None:
            return web.Response(status=502, text="upstream_unreachable")
        return web.Response(status=upstream_status, body=upstream_body)


def register_views(
    hass: HomeAssistant,
    state: IdegisState,
    session: ClientSession,
    on_update: Any = None,
) -> None:
    hass.http.register_view(IdegisCloudProxyView(VIEW_URL_READ, state, session, on_update))
    hass.http.register_view(IdegisCloudProxyView(VIEW_URL_WRITE, state, session, on_update))
