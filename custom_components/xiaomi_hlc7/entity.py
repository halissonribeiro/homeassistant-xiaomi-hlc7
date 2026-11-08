"""Base entity for the hlc7 camera."""

from __future__ import annotations

from homeassistant.const import CONF_HOST
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, MODEL
from .coordinator import Hlc7Coordinator


class Hlc7Entity(CoordinatorEntity[Hlc7Coordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: Hlc7Coordinator, key: str) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._device_key = entry.unique_id or entry.entry_id
        self._attr_unique_id = f"{self._device_key}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._device_key)},
            manufacturer=MANUFACTURER,
            model=MODEL,
            model_id=MODEL,
            name=entry.title,
            configuration_url=coordinator.go2rtc_url,
        )
        self._host = entry.data[CONF_HOST]

    def _value(self, prop: tuple[int, int]):
        data = self.coordinator.data or {}
        return data.get(prop)
