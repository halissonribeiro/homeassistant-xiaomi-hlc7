"""Enumerated settings of the hlc7 camera."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    NIGHT_SHOT_OPTIONS,
    P_NIGHT_SHOT,
    P_RECORDING_MODE,
    P_ROLLOVER,
    P_SENSITIVITY,
    RECORDING_MODE_OPTIONS,
    ROLLOVER_OPTIONS,
    SENSITIVITY_OPTIONS,
)
from .entity import Hlc7Entity, cameras

SELECTS = [
    ("night_vision", P_NIGHT_SHOT, NIGHT_SHOT_OPTIONS, "mdi:weather-night"),
    ("recording_mode", P_RECORDING_MODE, RECORDING_MODE_OPTIONS, "mdi:record-rec"),
    ("image_rotation", P_ROLLOVER, ROLLOVER_OPTIONS, "mdi:rotate-3d-variant"),
    ("motion_sensitivity", P_SENSITIVITY, SENSITIVITY_OPTIONS, "mdi:tune"),
]


async def async_setup_entry(
    hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities(
        Hlc7Select(coordinator, key, prop, mapping, icon)
        for coordinator in cameras(entry)
        for key, prop, mapping, icon in SELECTS
    )


class Hlc7Select(Hlc7Entity, SelectEntity):
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, key, prop, mapping: dict[int, str], icon) -> None:
        super().__init__(coordinator, key)
        self._prop = prop
        self._to_option = mapping
        self._to_value = {v: k for k, v in mapping.items()}
        self._attr_options = list(mapping.values())
        self._attr_icon = icon

    @property
    def current_option(self) -> str | None:
        return self._to_option.get(self._value(self._prop))

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_set(self._prop, self._to_value[option])
