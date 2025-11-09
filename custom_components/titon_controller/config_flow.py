"""Config flow for the Titon Controller integration."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector

from .const import (
    CONF_HUMIDITY_SENSORS,
    CONF_LOG_PATH,
    CONF_PANEL_URL,
    CONF_SERIAL_PORT,
    CONF_SETTINGS_PATH,
    CONF_WEB_HOST,
    CONF_WEB_PORT,
    DEFAULT_SERIAL_PORT,
    DEFAULT_WEB_HOST,
    DEFAULT_WEB_PORT,
    DOMAIN,
)

TITLE = "Titon Controller"


def _normalize_sensor_definitions(raw: Any) -> List[Dict[str, str]]:
    sensors: List[Dict[str, str]] = []
    if not raw:
        return sensors

    for item in raw:
        name: Optional[str] = None
        entity_id: Optional[str] = None
        if isinstance(item, dict):
            name = item.get("name") or item.get("title")
            entity_id = item.get("entity_id") or item.get("id")
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            name, entity_id = item
        elif isinstance(item, str):
            name = item
            entity_id = item

        if not name or not entity_id:
            continue
        sensors.append({"name": str(name), "entity_id": str(entity_id)})

    return sensors


def _extract_entity_ids(raw: Any) -> List[str]:
    entity_ids: List[str] = []
    if not raw:
        return entity_ids

    for item in raw:
        if isinstance(item, dict) and item.get("entity_id"):
            entity_ids.append(str(item["entity_id"]))
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            entity_ids.append(str(item[1]))
        elif isinstance(item, str):
            entity_ids.append(item)
    return entity_ids


async def _async_resolve_entities(hass: HomeAssistant, entity_ids: List[str]) -> List[Dict[str, str]]:
    if not entity_ids:
        return []

    registry = er.async_get(hass)
    resolved: List[Dict[str, str]] = []

    for entity_id in entity_ids:
        name: Optional[str] = None
        if registry:
            entry = registry.async_get(entity_id)
            if entry:
                name = entry.original_name or entry.name
        if not name:
            state = hass.states.get(entity_id)
            if state and state.name:
                name = state.name
        if not name:
            name = entity_id
        resolved.append({"name": name, "entity_id": entity_id})

    return resolved


class TitonControllerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Titon Controller."""

    VERSION = 1

    async def async_step_user(self, user_input: Optional[Dict[str, Any]] = None):
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        if user_input is not None:
            entity_ids: List[str] = user_input.pop(CONF_HUMIDITY_SENSORS, [])
            sensors = await _async_resolve_entities(self.hass, entity_ids)

            data: Dict[str, Any] = {}
            for key, value in user_input.items():
                if isinstance(value, str) and not value.strip():
                    continue
                data[key] = value
            if sensors:
                data[CONF_HUMIDITY_SENSORS] = sensors

            return self.async_create_entry(title=TITLE, data=data)

        data_schema = vol.Schema(
            {
                vol.Required(CONF_SERIAL_PORT, default=DEFAULT_SERIAL_PORT): str,
                vol.Optional(CONF_WEB_HOST, default=DEFAULT_WEB_HOST): str,
                vol.Optional(CONF_WEB_PORT, default=DEFAULT_WEB_PORT): vol.All(
                    int, vol.Range(min=1, max=65535)
                ),
                vol.Optional(CONF_PANEL_URL, default=""): str,
                vol.Optional(CONF_SETTINGS_PATH, default=""): str,
                vol.Optional(CONF_LOG_PATH, default=""): str,
                vol.Optional(CONF_HUMIDITY_SENSORS, default=[]): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor", multiple=True)
                ),
            }
        )

        return self.async_show_form(step_id="user", data_schema=data_schema)

    async def async_step_import(self, import_config: Dict[str, Any]):
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        data = dict(import_config)
        sensors = _normalize_sensor_definitions(data.get(CONF_HUMIDITY_SENSORS))
        if sensors:
            data[CONF_HUMIDITY_SENSORS] = sensors
        else:
            data.pop(CONF_HUMIDITY_SENSORS, None)
        return self.async_create_entry(title=TITLE, data=data)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        return TitonControllerOptionsFlow(config_entry)


class TitonControllerOptionsFlow(config_entries.OptionsFlow):
    """Options flow for Titon Controller."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(self, user_input: Optional[Dict[str, Any]] = None):
        if user_input is not None:
            entity_ids: List[str] = user_input.get(CONF_HUMIDITY_SENSORS, [])
            sensors = await _async_resolve_entities(self.hass, entity_ids)
            data: Dict[str, Any] = {}
            if sensors:
                data[CONF_HUMIDITY_SENSORS] = sensors
            return self.async_create_entry(data=data)

        current = _extract_entity_ids(
            self._config_entry.options.get(CONF_HUMIDITY_SENSORS)
            or self._config_entry.data.get(CONF_HUMIDITY_SENSORS, [])
        )

        data_schema = vol.Schema(
            {
                vol.Optional(CONF_HUMIDITY_SENSORS, default=current): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor", multiple=True)
                )
            }
        )

        return self.async_show_form(step_id="init", data_schema=data_schema)
