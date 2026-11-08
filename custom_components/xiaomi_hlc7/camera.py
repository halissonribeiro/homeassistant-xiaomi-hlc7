"""Live video via the go2rtc stream that already reads the camera over LAN."""

from __future__ import annotations

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import P_CAMERA_ON
from .entity import Hlc7Entity


async def async_setup_entry(
    hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities([Hlc7Camera(entry.runtime_data)])


class Hlc7Camera(Hlc7Entity, Camera):
    _attr_name = None
    _attr_supported_features = CameraEntityFeature.STREAM
    _attr_brand = "Xiaomi"
    _attr_model = "isa.camera.hlc7"

    def __init__(self, coordinator) -> None:
        Hlc7Entity.__init__(self, coordinator, "camera")
        Camera.__init__(self)
        self._attr_translation_key = None

    @property
    def is_on(self) -> bool:
        value = self._value(P_CAMERA_ON)
        return True if value is None else bool(value)

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        return {"go2rtc_stream": self.coordinator.stream}

    async def stream_source(self) -> str | None:
        return self.coordinator.rtsp_url

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        return await self.coordinator.async_go2rtc(
            "GET", "/api/frame.jpeg", src=self.coordinator.stream
        )
