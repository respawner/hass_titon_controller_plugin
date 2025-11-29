"""Select entities for the Titon Controller integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

FAN_OPTIONS = ["Off", "Level 1", "Level 2", "Level 3", "Level 4"]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]
    manager = data["manager"]

    entity = TitonFanSpeedSelect(coordinator, manager, entry)
    async_add_entities([entity])


class TitonFanSpeedSelect(CoordinatorEntity, SelectEntity):
    _attr_has_entity_name = True
    _attr_icon = "mdi:fan-speed-3"
    _attr_options = FAN_OPTIONS

    def __init__(self, coordinator, manager, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._manager = manager
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_fan_speed"
        self._attr_name = "Fan Speed"
        self._attr_device_info = manager.device_info

    @property
    def available(self) -> bool:
        return self._manager.is_running() and self.coordinator.last_update_success

    @property
    def current_option(self) -> str | None:
        data = self.coordinator.data or {}
        state = data.get("state") or {}
        level = state.get("current_level")
        if not level:
            return "Off"
        try:
            level_int = int(level)
        except (TypeError, ValueError):
            return "Off"
        level_int = max(1, min(4, level_int))
        return f"Level {level_int}"

    async def async_select_option(self, option: str) -> None:
        success = await self.hass.async_add_executor_job(self._manager.set_fan_speed, option)
        if not success:
            raise HomeAssistantError(f"Titon controller rejected fan speed '{option}'")
        await self.coordinator.async_request_refresh()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        state = data.get("state") or {}
        return {
            "boost_active": state.get("boost_active"),
            "manual_override_until": state.get("manual_override_until"),
            "current_level": state.get("current_level"),
        }
