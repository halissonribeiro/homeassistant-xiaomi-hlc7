"""On/off controls of the hlc7 camera."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import P_CAMERA_ON, P_INDICATOR, P_MOTION, P_WATERMARK
from .entity import Hlc7Entity

SWITCHES = [
    ("camera_power", P_CAMERA_ON, "mdi:cctv", None),
    ("motion_detection", P_MOTION, "mdi:motion-sensor", None),
    ("indicator_light", P_INDICATOR, "mdi:led-on", EntityCategory.CONFIG),
    ("time_watermark", P_WATERMARK, "mdi:clock-outline", EntityCategory.CONFIG),
]


async def async_setup_entry(
    hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        Hlc7Switch(coordinator, key, prop, icon, category)
        for key, prop, icon, category in SWITCHES
    )


class Hlc7Switch(Hlc7Entity, SwitchEntity):
    def __init__(self, coordinator, key, prop, icon, category) -> None:
        super().__init__(coordinator, key)
        self._prop = prop
        self._attr_icon = icon
        self._attr_entity_category = category

    @property
    def is_on(self) -> bool | None:
        value = self._value(self._prop)
        return None if value is None else bool(value)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set(self._prop, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set(self._prop, False)
