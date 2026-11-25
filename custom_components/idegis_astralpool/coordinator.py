"""DataUpdateCoordinators for the integration.

:class:`IdegisCloudMitmCoordinator` — Unlike the phase-1 prototype
(which polled an external capturer add-on over HTTP every
``scan_interval`` seconds), this one is push-fed: the chlorinator
decides when data arrives (it polls the cloud every few seconds on its
own), so :class:`IdegisCloudProxyView` calls :meth:`push_update` straight
from the request handler. There is no ``update_interval`` / periodic
refresh — ``_async_update_data`` only serves the initial "first refresh"
HA requires before entities are added.

:class:`IdegisCostCoordinator` — unlike the chlorinator's own data, cost
and the filtration recommendation depend on a *pump power entity's*
history, which doesn't push updates to us; this one polls on a regular
timer instead (see history_glue.py for the actual history fetch + pure
math call).
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .history_glue import async_compute_cost, compute_recommendation_now
from .state import IdegisState

_LOGGER = logging.getLogger(__name__)

COST_UPDATE_INTERVAL = timedelta(minutes=15)


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


class IdegisCostCoordinator(DataUpdateCoordinator[dict[str, Any] | None]):
    """Polls pump/grid power history every 15 min and recomputes
    electricity cost + the filtration recommendation.

    Returns ``None`` (not an error) when no ``pump_power_entity`` is
    configured yet — sensor.py's cost/recommendation entities just stay
    unavailable until the user picks one in the options flow, rather
    than this coordinator raising ``UpdateFailed``.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        pump_entity: str,
        grid_entity: str | None,
        cloud_mitm_coordinator: IdegisCloudMitmCoordinator,
    ) -> None:
        self._pump_entity = pump_entity
        self._grid_entity = grid_entity
        self._cloud_mitm_coordinator = cloud_mitm_coordinator
        super().__init__(
            hass,
            _LOGGER,
            name="idegis_astralpool_cost",
            update_interval=COST_UPDATE_INTERVAL,
        )

    async def _async_update_data(self) -> dict[str, Any] | None:
        if not self._pump_entity:
            return None
        cost = await async_compute_cost(
            self.hass, pump_entity=self._pump_entity, grid_entity=self._grid_entity
        )
        live = (self._cloud_mitm_coordinator.data or {}).get("live_measurements") or {}
        temp_c = live.get("temperature", {}).get("value")
        recommendation = compute_recommendation_now(temp_c)
        return {"cost": cost, "recommendation": recommendation}
