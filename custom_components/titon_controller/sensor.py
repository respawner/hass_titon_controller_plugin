"""Sensor entities for the Titon Controller integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]
    manager = data["manager"]

    entity = TitonErrorStatusSensor(coordinator, manager, entry)
    async_add_entities([entity])


class TitonErrorStatusSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True
    _attr_icon = "mdi:alert"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, manager, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._manager = manager
        self._attr_unique_id = f"{entry.entry_id}_error_status"
        self._attr_name = "Error Status"
        self._attr_device_info = manager.device_info

    @property
    def available(self) -> bool:
        return self._manager.is_running() and self.coordinator.last_update_success

    @property
    def native_value(self) -> str | None:
        data = self.coordinator.data or {}
        status = (data.get("state") or {}).get("status") or {}
        flags = status.get("flags") or []
        if not flags:
            return "No errors reported"
        return " / ".join(flags)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        status = (data.get("state") or {}).get("status") or {}
        return {
            "raw_status": status.get("raw"),
            "flags": status.get("flags") or [],
        }
