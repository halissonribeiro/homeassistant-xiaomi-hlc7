"""Base entity for the hlc7 camera."""

from __future__ import annotations

from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import Hlc7Coordinator


class Hlc7Entity(CoordinatorEntity[Hlc7Coordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: Hlc7Coordinator, key: str) -> None:
        super().__init__(coordinator)
        info = coordinator.info
        self._attr_unique_id = f"{info.did}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, info.did)},
            connections={(CONNECTION_NETWORK_MAC, info.mac.lower())} if info.mac else set(),
            manufacturer=MANUFACTURER,
            model=info.model,
            model_id=info.model,
            name=info.name,
        )

    def _value(self, prop: tuple[int, int]):
        data = self.coordinator.data or {}
        return data.get(prop)


def cameras(entry):
    """All camera coordinators of a config entry."""
    return entry.runtime_data.cameras.values()
