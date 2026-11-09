"""Per-camera runtime: miIO property polling + P2P streamer + RTSP server."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DEFAULT_SCAN_INTERVAL_S, DOMAIN, POLLED_PROPERTIES
from .miio import MiioClient, MiioError
from .rtsp import RtspServer
from .streamer import CameraStreamer
from .xiaomi_cloud import XiaomiCloud

_LOGGER = logging.getLogger(__name__)

Props = dict[tuple[int, int], Any]


@dataclass
class CameraInfo:
    did: str
    name: str
    model: str
    host: str
    token: str
    mac: str | None = None


class Hlc7Coordinator(DataUpdateCoordinator[Props]):
    """One camera: controls over local miIO, media over local P2P."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        info: CameraInfo,
        cloud: XiaomiCloud,
        cloud_lock: threading.Lock,
        region: str,
        quality: int,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_{info.did}",
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL_S),
        )
        self.info = info
        self.client = MiioClient(info.host, info.token)
        self.streamer = CameraStreamer(cloud, cloud_lock, region, info.did, info.host, quality)
        self.rtsp = RtspServer(self.streamer)

    async def async_start(self) -> None:
        await self.rtsp.start()

    async def async_stop(self) -> None:
        await self.rtsp.stop()
        await self.hass.async_add_executor_job(self.streamer.shutdown)

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


@dataclass
class Hlc7Data:
    cloud: XiaomiCloud
    cameras: dict[str, Hlc7Coordinator] = field(default_factory=dict)
