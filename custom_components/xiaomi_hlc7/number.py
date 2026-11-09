"""Numeric settings of the hlc7 camera."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import P_ALARM_INTERVAL
from .entity import Hlc7Entity, cameras


async def async_setup_entry(
    hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities(Hlc7AlarmInterval(c) for c in cameras(entry))


class Hlc7AlarmInterval(Hlc7Entity, NumberEntity):
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:timer-sand"
    _attr_native_min_value = 1
    _attr_native_max_value = 30
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "alarm_interval")

    @property
    def native_value(self) -> float | None:
        return self._value(P_ALARM_INTERVAL)

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set(P_ALARM_INTERVAL, int(value))
