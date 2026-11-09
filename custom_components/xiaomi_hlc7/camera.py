"""Live video from the camera's own P2P stream, served on a local RTSP URL."""

from __future__ import annotations

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.components.ffmpeg import async_get_image
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import P_CAMERA_ON
from .entity import Hlc7Entity, cameras


async def async_setup_entry(
    hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities(Hlc7Camera(c) for c in cameras(entry))


class Hlc7Camera(Hlc7Entity, Camera):
    _attr_name = None
    _attr_supported_features = CameraEntityFeature.STREAM
    _attr_brand = "Xiaomi"

    def __init__(self, coordinator) -> None:
        Hlc7Entity.__init__(self, coordinator, "camera")
        Camera.__init__(self)
        self._attr_translation_key = None
        self._attr_model = coordinator.info.model

    @property
    def is_on(self) -> bool:
        value = self._value(P_CAMERA_ON)
        return True if value is None else bool(value)

    @property
    def extra_state_attributes(self) -> dict[str, str | None]:
        s = self.coordinator.streamer
        return {
            "p2p_protocol": s.protocol,
            "p2p_remote": s.remote,
            "p2p_last_error": s.last_error,
        }

    async def stream_source(self) -> str | None:
        return self.coordinator.rtsp.url

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        return await async_get_image(
            self.hass,
            self.coordinator.rtsp.url,
            width=width,
            height=height,
        )
