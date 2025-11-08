"""Titon Controller custom integration."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence
from urllib.parse import urlparse

from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType

DOMAIN = "titon_controller"
CONF_SERIAL_PORT = "serial_port"
CONF_WEB_HOST = "web_host"
CONF_WEB_PORT = "web_port"
CONF_PANEL_URL = "panel_url"
CONF_HUMIDITY_SENSORS = "humidity_sensors"
CONF_SETTINGS_PATH = "settings_path"
CONF_LOG_PATH = "log_path"

DEFAULT_SERIAL_PORT = "/dev/ttyUSB1"
DEFAULT_WEB_HOST = "0.0.0.0"
DEFAULT_WEB_PORT = 8050
PANEL_URL_PATH = "titon-controller"

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
            parsed = urlparse(internal_url)
            if parsed.hostname:
                candidate = parsed.hostname
    return f"http://{candidate}:{port}"


class TitonControllerManager:
    def __init__(self, hass: HomeAssistant, conf: Dict[str, Any]) -> None:
        self._hass = hass
        self._conf = conf
        self._server = None
        self._server_thread = None
        self.serial_port = conf.get(CONF_SERIAL_PORT, DEFAULT_SERIAL_PORT)
        self.web_host = conf.get(CONF_WEB_HOST, DEFAULT_WEB_HOST)
        self.web_port = int(conf.get(CONF_WEB_PORT, DEFAULT_WEB_PORT))
        self.settings_path = conf.get(CONF_SETTINGS_PATH) or hass.config.path("titon_controller_settings.json")
        self.log_path = conf.get(CONF_LOG_PATH) or hass.config.path("titon_controller_webui.log")
        self.sensors = _prepare_sensor_payload(conf.get(CONF_HUMIDITY_SENSORS))
        self.panel_url = _guess_panel_url(hass, self.web_host, self.web_port, conf.get(CONF_PANEL_URL))

    def start(self) -> None:
        os.environ["TITON_SERIAL_PORT"] = self.serial_port
        os.environ["TITON_WEBUI_HOST"] = self.web_host
        os.environ["TITON_WEBUI_PORT"] = str(self.web_port)
        os.environ["TITON_SETTINGS_PATH"] = self.settings_path
        os.environ["TITON_LOG_PATH"] = self.log_path

        settings_dir = Path(self.settings_path).parent
        log_dir = Path(self.log_path).parent
        settings_dir.mkdir(parents=True, exist_ok=True)
        log_dir.mkdir(parents=True, exist_ok=True)

        if self.sensors:
            os.environ["TITON_SENSOR_ENTITIES"] = json.dumps(self.sensors)
        else:
            os.environ.pop("TITON_SENSOR_ENTITIES", None)

        from titon_controller_webui import simple_webui

        simple_webui.set_ha_state_provider(_make_state_provider(self._hass))
        self._server, self._server_thread = simple_webui.create_server(host=self.web_host, port=self.web_port)
        _LOGGER.info(
            "Titon controller web UI started on http://%s:%s (settings=%s)",
            self.web_host,
            self.web_port,
            self.settings_path,
        )

    def stop(self) -> None:
        from titon_controller_webui import simple_webui

        simple_webui.set_ha_state_provider(None)
        if self._server is not None:
            try:
                simple_webui.shutdown_server(self._server)
            finally:
                self._server = None
        self._server_thread = None
        _LOGGER.info("Titon controller web UI stopped")


def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    conf = config.get(DOMAIN)
    if conf is None:
        return True

    manager = TitonControllerManager(hass, conf)
    hass.data[DOMAIN] = manager

    hass.async_create_task(_async_start(hass, manager))
    return True


async def _async_start(hass: HomeAssistant, manager: TitonControllerManager) -> None:
    await hass.async_add_executor_job(manager.start)

    hass.components.frontend.async_register_built_in_panel(
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
        hass.components.frontend.async_remove_panel(PANEL_URL_PATH)

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _async_on_stop)
