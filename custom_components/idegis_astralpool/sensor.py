"""Sensor platform, fed by IdegisState.snapshot() (see cloud_proxy.py).

Live water readings (ph/salinity/temperature/production_percent) are the
*point* values read straight off the sticky fields. Per
feedback_idegis_use_session_avgs (project memory): the point value reads
the device's internal stagnant chamber and gives falsely low pH —
the "last session avg" sensors are the ones that should be trusted for
automations/alerts, the live ones are for "what is it reading right now".
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, MODEL_DEFAULT
from .coordinator import IdegisCloudMitmCoordinator, IdegisCostCoordinator


@dataclass(frozen=True, kw_only=True)
class IdegisSensorDescription(SensorEntityDescription):
    """Sensor description with an extractor function over the coordinator data."""
    value_fn: Callable[[dict[str, Any]], Any]


def _live(d: dict[str, Any], key: str) -> Any:
    return (d.get("live_measurements") or {}).get(key, {}).get("value")


def _session(d: dict[str, Any], key: str, agg: str = "avg") -> Any:
    return (
        ((d.get("last_session") or {}).get("measurements") or {})
        .get(key, {}).get(agg)
    )


SENSORS: tuple[IdegisSensorDescription, ...] = (
    IdegisSensorDescription(
        key="last_seen",
        translation_key="last_seen",
        name="Last seen",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda d: d.get("last_seen"),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    IdegisSensorDescription(
        key="seconds_since_last_seen",
        translation_key="seconds_since_last_seen",
        name="Seconds since last request",
        native_unit_of_measurement="s",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.get("seconds_since_last_seen"),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    IdegisSensorDescription(
        key="read_count",
        translation_key="read_count",
        name="Read.php calls",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda d: d.get("read_count"),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    IdegisSensorDescription(
        key="write_count",
        translation_key="write_count",
        name="Write.php calls",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda d: d.get("write_count"),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    IdegisSensorDescription(
        key="device_serial",
        translation_key="device_serial",
        name="Device serial",
        value_fn=lambda d: d.get("session_token"),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    # Live point values — see module docstring for why these are not the
    # ones to trust for automations.
    IdegisSensorDescription(
        key="ph",
        translation_key="ph",
        name="pH",
        native_unit_of_measurement="pH",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda d: _live(d, "ph"),
    ),
    IdegisSensorDescription(
        key="salinity",
        translation_key="salinity",
        name="Salinity",
        native_unit_of_measurement="g/L",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda d: _live(d, "salinity"),
    ),
    IdegisSensorDescription(
        key="water_temperature",
        translation_key="water_temperature",
        name="Water temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement="°C",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda d: _live(d, "temperature"),
    ),
    IdegisSensorDescription(
        key="production_percent",
        translation_key="production_percent",
        name="Chlorine production",
        native_unit_of_measurement="%",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda d: _live(d, "production_percent"),
    ),
    # Session aggregates — the trustworthy ones.
    IdegisSensorDescription(
        key="last_session_ph_avg",
        translation_key="last_session_ph_avg",
        name="Last session pH avg",
        native_unit_of_measurement="pH",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda d: _session(d, "ph"),
    ),
    IdegisSensorDescription(
        key="last_session_temperature_avg",
        translation_key="last_session_temperature_avg",
        name="Last session temperature avg",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement="°C",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda d: _session(d, "temperature"),
    ),
    IdegisSensorDescription(
        key="last_session_salinity_avg",
        translation_key="last_session_salinity_avg",
        name="Last session salinity avg",
        native_unit_of_measurement="g/L",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda d: _session(d, "salinity"),
    ),
    IdegisSensorDescription(
        key="last_session_production_avg",
        translation_key="last_session_production_avg",
        name="Last session production avg",
        native_unit_of_measurement="%",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda d: _session(d, "production_percent"),
    ),
    IdegisSensorDescription(
        key="last_session_duration",
        translation_key="last_session_duration",
        name="Last session duration",
        native_unit_of_measurement="s",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: (d.get("last_session") or {}).get("duration_s"),
    ),
    IdegisSensorDescription(
        key="last_session_n_writes",
        translation_key="last_session_n_writes",
        name="Last session writes",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: (d.get("last_session") or {}).get("n_writes"),
    ),
    IdegisSensorDescription(
        key="last_session_end",
        translation_key="last_session_end",
        name="Last session end",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: (d.get("last_session") or {}).get("last_ts"),
    ),
)


def _cost(d: dict[str, Any], window: str, field: str) -> Any:
    cost = d.get("cost")
    if not cost:
        return None
    return cost.get(window, {}).get(field)


# Fed by IdegisCostCoordinator (history_glue.py), not the chlorinator's
# own push updates — see coordinator.py's module docstring. Stay
# unavailable (native_value -> None) until a pump_power_entity is picked
# in the options flow, same honesty-over-fake-data pattern as alerts.py's
# filtration check.
COST_SENSORS: tuple[IdegisSensorDescription, ...] = (
    IdegisSensorDescription(
        key="recommended_filtration_minutes_today",
        translation_key="recommended_filtration_minutes_today",
        name="Recommended filtration minutes today",
        native_unit_of_measurement="min",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: (d.get("recommendation") or {}).get("recommended_minutes_today"),
    ),
    IdegisSensorDescription(
        key="filtration_cost_24h",
        translation_key="filtration_cost_24h",
        name="Filtration cost (24h)",
        native_unit_of_measurement="EUR",
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        value_fn=lambda d: _cost(d, "24h", "grid_eur"),
    ),
    IdegisSensorDescription(
        key="filtration_cost_7d",
        translation_key="filtration_cost_7d",
        name="Filtration cost (7d)",
        native_unit_of_measurement="EUR",
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        value_fn=lambda d: _cost(d, "7d", "grid_eur"),
    ),
    IdegisSensorDescription(
        key="filtration_cost_30d",
        translation_key="filtration_cost_30d",
        name="Filtration cost (30d)",
        native_unit_of_measurement="EUR",
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        value_fn=lambda d: _cost(d, "30d", "grid_eur"),
    ),
    IdegisSensorDescription(
        key="filtration_solar_pct_30d",
        translation_key="filtration_solar_pct_30d",
        name="Filtration solar share (30d)",
        native_unit_of_measurement="%",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: _cost(d, "30d", "solar_pct"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinators = hass.data[DOMAIN][entry.entry_id]
    coordinator: IdegisCloudMitmCoordinator = coordinators["cloud_mitm"]
    cost_coordinator: IdegisCostCoordinator = coordinators["cost"]
    async_add_entities(
        [IdegisSensor(coordinator, desc, entry.entry_id) for desc in SENSORS]
        + [IdegisCostSensor(cost_coordinator, desc, entry.entry_id) for desc in COST_SENSORS]
    )


class _IdegisSensorBase(SensorEntity):
    entity_description: IdegisSensorDescription
    _attr_has_entity_name = True

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_identifier())},
            manufacturer=MANUFACTURER,
            model=MODEL_DEFAULT,
            name="Idegis chlorinator",
        )

    def _device_identifier(self) -> str:
        raise NotImplementedError


class IdegisSensor(_IdegisSensorBase, CoordinatorEntity[IdegisCloudMitmCoordinator]):
    def __init__(
        self,
        coordinator: IdegisCloudMitmCoordinator,
        description: IdegisSensorDescription,
        entry_id: str,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._entry_id = entry_id
        self._attr_unique_id = f"{entry_id}_{description.key}"

    def _device_identifier(self) -> str:
        return (self.coordinator.data or {}).get("device_id") or self._entry_id

    @property
    def native_value(self) -> Any:
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)


class IdegisCostSensor(_IdegisSensorBase, CoordinatorEntity[IdegisCostCoordinator]):
    """Fed by IdegisCostCoordinator — unavailable until a pump_power_entity
    is configured, see sensor.py's COST_SENSORS comment."""

    def __init__(
        self,
        coordinator: IdegisCostCoordinator,
        description: IdegisSensorDescription,
        entry_id: str,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._entry_id = entry_id
        self._attr_unique_id = f"{entry_id}_{description.key}"

    def _device_identifier(self) -> str:
        return self._entry_id

    @property
    def native_value(self) -> Any:
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)
