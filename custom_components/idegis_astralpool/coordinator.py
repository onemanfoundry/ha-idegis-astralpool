"""DataUpdateCoordinator for the native cloud-MITM receiver (mode A).

Unlike the phase-1 prototype (which polled an external capturer add-on
over HTTP every ``scan_interval`` seconds), this coordinator is push-fed:
the chlorinator decides when data arrives (it polls the cloud every few
seconds on its own), so :class:`IdegisCloudProxyView` calls
:meth:`push_update` straight from the request handler. There is no
``update_interval`` / periodic refresh — ``_async_update_data`` only
serves the initial "first refresh" HA requires before entities are added.
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .state import IdegisState

_LOGGER = logging.getLogger(__name__)


class IdegisCloudMitmCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Push-based coordinator fed by the in-process cloud-MITM receiver."""

    def __init__(self, hass: HomeAssistant, state: IdegisState) -> None:
        self._state = state
        super().__init__(
            hass,
            _LOGGER,
            name="idegis_astralpool_cloud_mitm",
            update_interval=None,
        )

    async def _async_update_data(self) -> dict[str, Any]:
        return self._state.snapshot()

    def push_update(self) -> None:
        """Called by the proxy view after every ingested request."""
        self.async_set_updated_data(self._state.snapshot())
