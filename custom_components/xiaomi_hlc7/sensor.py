"""SD card sensors of the hlc7 camera."""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import P_SD_FREE, P_SD_STATUS, P_SD_TOTAL, P_SD_USED, SD_STATUS_OPTIONS
from .entity import Hlc7Entity


async def async_setup_entry(
    hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback
) -> None:
    c = entry.runtime_data
    async_add_entities(
        [
            Hlc7SdStatus(c),
            Hlc7SdSpace(c, "sd_total", P_SD_TOTAL),
            Hlc7SdSpace(c, "sd_free", P_SD_FREE),
            Hlc7SdSpace(c, "sd_used", P_SD_USED),
        ]
    )


class Hlc7SdStatus(Hlc7Entity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(SD_STATUS_OPTIONS.values())
    _attr_icon = "mdi:sd"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "sd_status")

    @property
    def native_value(self) -> str | None:
        return SD_STATUS_OPTIONS.get(self._value(P_SD_STATUS))


class Hlc7SdSpace(Hlc7Entity, SensorEntity):
    """Raw value reported by the camera (unit to be confirmed on hardware)."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:harddisk"

    def __init__(self, coordinator, key, prop) -> None:
        super().__init__(coordinator, key)
        self._prop = prop

    @property
    def native_value(self) -> int | None:
        return self._value(self._prop)
