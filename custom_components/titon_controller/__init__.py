"""Titon Controller custom integration."""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.components import frontend
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType

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
    PANEL_URL_PATH,
)

_LOGGER = logging.getLogger(__name__)


def _prepare_sensor_payload(raw: Optional[Sequence[Dict[str, Any]]]) -> List[List[str]]:
    if not raw:
        return []

    prepared: List[List[str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        entity_id = item.get("entity_id")
        if not name or not entity_id:
            continue
        prepared.append([str(name), str(entity_id)])
    return prepared


def _make_state_provider(hass: HomeAssistant):
    loop = hass.loop

    async def _async_get(entity_id: str) -> Optional[float]:
        state = hass.states.get(entity_id)
        if state is None:
            return None
        try:
            return float(state.state)
        except (TypeError, ValueError):
            return None

    def _provider(entity_id: str) -> Optional[float]:
        future = asyncio.run_coroutine_threadsafe(_async_get(entity_id), loop)
        try:
            return future.result(timeout=2)
        except Exception as exc:  # pragma: no cover - defensive
            _LOGGER.debug("Failed to resolve %s from HA state machine: %s", entity_id, exc)
            return None

    return _provider


def _guess_panel_url(hass: HomeAssistant, host: str, port: int, explicit: Optional[str]) -> str:
    if explicit:
        return explicit

    candidate = host
    if candidate in {"0.0.0.0", "127.0.0.1"}:
        internal_url = hass.config.internal_url
        if internal_url:
            from urllib.parse import urlparse

            parsed = urlparse(internal_url)
            if parsed.hostname:
                candidate = parsed.hostname
    return f"http://{candidate}:{port}"


def _apply_state_provider(simple_webui, provider) -> None:
    setter = getattr(simple_webui, "set_ha_state_provider", None)
    if callable(setter):
        setter(provider)
        return

    simple_webui.HA_STATE_PROVIDER = provider  # type: ignore[attr-defined]


class TitonControllerManager:
    """Runtime manager for the Titon controller web UI."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self._hass = hass
        self._entry = entry
        data = dict(entry.data)
        options = dict(entry.options)

        sensors_raw = options.get(CONF_HUMIDITY_SENSORS)
        if sensors_raw is None:
            sensors_raw = data.get(CONF_HUMIDITY_SENSORS)

        self.sensors = _prepare_sensor_payload(sensors_raw)
        self.serial_port = data.get(CONF_SERIAL_PORT, DEFAULT_SERIAL_PORT)
        self.web_host = data.get(CONF_WEB_HOST, DEFAULT_WEB_HOST)
        self.web_port = int(data.get(CONF_WEB_PORT, DEFAULT_WEB_PORT))
        self.settings_path = data.get(CONF_SETTINGS_PATH) or hass.config.path("titon_controller_settings.json")
        self.log_path = data.get(CONF_LOG_PATH) or hass.config.path("titon_controller_webui.log")
        self.panel_url = _guess_panel_url(hass, self.web_host, self.web_port, data.get(CONF_PANEL_URL))

        self._server = None
        self._server_thread = None

    def start(self) -> None:
        os.environ["TITON_SERIAL_PORT"] = self.serial_port
        os.environ["TITON_WEBUI_HOST"] = self.web_host
        os.environ["TITON_WEBUI_PORT"] = str(self.web_port)
        os.environ["TITON_SETTINGS_PATH"] = self.settings_path
        os.environ["TITON_LOG_PATH"] = self.log_path
        bundle_root = Path(__file__).parent / "webui_runtime"
        os.environ["TITON_WEBUI_DIR"] = str(bundle_root / "webui")

        settings_dir = Path(self.settings_path).parent
        log_dir = Path(self.log_path).parent
        settings_dir.mkdir(parents=True, exist_ok=True)
        log_dir.mkdir(parents=True, exist_ok=True)

        if self.sensors:
            os.environ["TITON_SENSOR_ENTITIES"] = json.dumps(self.sensors)
        else:
            os.environ.pop("TITON_SENSOR_ENTITIES", None)

        try:
            from .webui_runtime import simple_webui  # type: ignore
        except ModuleNotFoundError:
            simple_webui = importlib.import_module("titon_controller_webui.simple_webui")  # type: ignore

        _apply_state_provider(simple_webui, _make_state_provider(self._hass))
        self._server, self._server_thread = simple_webui.create_server(host=self.web_host, port=self.web_port)
        _LOGGER.info(
            "Titon controller web UI started on http://%s:%s (settings=%s)",
            self.web_host,
            self.web_port,
            self.settings_path,
        )

    def stop(self) -> None:
        try:
            from .webui_runtime import simple_webui  # type: ignore
        except ModuleNotFoundError:
            simple_webui = importlib.import_module("titon_controller_webui.simple_webui")  # type: ignore

        _apply_state_provider(simple_webui, None)
        if self._server is not None:
            try:
                simple_webui.shutdown_server(self._server)
            finally:
                self._server = None
        self._server_thread = None
        _LOGGER.info("Titon controller web UI stopped")


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Handle integration setup via YAML or config entries."""

    hass.data.setdefault(DOMAIN, {})

    yaml_conf = config.get(DOMAIN)
    if yaml_conf is not None:
        hass.async_create_task(
            hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": SOURCE_IMPORT},
                data=dict(yaml_conf),
            )
        )

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Titon Controller from a config entry."""

    hass.data.setdefault(DOMAIN, {})
    manager = TitonControllerManager(hass, entry)
    await hass.async_add_executor_job(manager.start)

    frontend.async_register_built_in_panel(
        hass,
        component_name="iframe",
        sidebar_title="Titon Controller",
        sidebar_icon="mdi:fan",
        url_path=PANEL_URL_PATH,
        config={"url": manager.panel_url},
        require_admin=False,
        update=True,
    )

    async def _async_on_stop(event) -> None:
        await hass.async_add_executor_job(manager.stop)
        frontend.async_remove_panel(hass, PANEL_URL_PATH)

    remove_stop = hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _async_on_stop)

    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    hass.data[DOMAIN][entry.entry_id] = {
        "manager": manager,
        "remove_stop": remove_stop,
    }

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a Titon Controller config entry."""

    stored = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if stored:
        remove_stop = stored.get("remove_stop")
        if callable(remove_stop):
            remove_stop()
        manager: TitonControllerManager = stored["manager"]
        await hass.async_add_executor_job(manager.stop)

    frontend.async_remove_panel(hass, PANEL_URL_PATH)
    return True


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the config entry when data or options change."""

    await hass.config_entries.async_reload(entry.entry_id)
