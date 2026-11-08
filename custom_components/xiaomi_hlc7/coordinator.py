"""Polls the camera's MIoT properties over local miIO."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any
from urllib.parse import quote

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_GO2RTC_URL,
    CONF_STREAM,
    CONF_TOKEN,
    DEFAULT_SCAN_INTERVAL_S,
    DOMAIN,
    POLLED_PROPERTIES,
)
from .miio import MiioClient, MiioError

_LOGGER = logging.getLogger(__name__)

Props = dict[tuple[int, int], Any]


class Hlc7Coordinator(DataUpdateCoordinator[Props]):
    """Holds the miIO client, the go2rtc location and the latest property values."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL_S),
        )
        self.client = MiioClient(entry.data[CONF_HOST], entry.data[CONF_TOKEN])
        self.go2rtc_url: str = entry.data[CONF_GO2RTC_URL].rstrip("/")
        self.stream: str = entry.data[CONF_STREAM]

    @property
    def rtsp_url(self) -> str:
        host = self.go2rtc_url.split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0]
        return f"rtsp://{host}:8554/{quote(self.stream, safe='')}"

    async def _async_update_data(self) -> Props:
        try:
            return await self.client.get_properties(POLLED_PROPERTIES)
        except MiioError as err:
            raise UpdateFailed(f"miIO: {err}") from err

    async def async_set(self, prop: tuple[int, int], value: Any) -> None:
        try:
            await self.client.set_property(prop[0], prop[1], value)
        except MiioError as err:
            raise HomeAssistantError(f"Câmera não aceitou o comando: {err}") from err
        if self.data is not None:
            self.data[prop] = value
            self.async_update_listeners()

    async def async_action(self, action: tuple[int, int]) -> None:
        try:
            await self.client.action(*action)
        except MiioError as err:
            raise HomeAssistantError(f"Ação falhou: {err}") from err

    async def async_go2rtc(self, method: str, path: str, **params: str) -> bytes:
        """Call the go2rtc HTTP API."""
        session = async_get_clientsession(self.hass)
        try:
            async with session.request(
                method, f"{self.go2rtc_url}{path}", params=params, timeout=15
            ) as resp:
                resp.raise_for_status()
                return await resp.read()
        except Exception as err:  # noqa: BLE001
            raise HomeAssistantError(f"go2rtc: {err}") from err
