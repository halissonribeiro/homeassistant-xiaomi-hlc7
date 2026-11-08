"""Xiaomi Mi Camera 2K Magnetic Mount (isa.camera.hlc7), local control."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import PLATFORMS
from .coordinator import Hlc7Coordinator

type Hlc7ConfigEntry = ConfigEntry[Hlc7Coordinator]


async def async_setup_entry(hass: HomeAssistant, entry: Hlc7ConfigEntry) -> bool:
    coordinator = Hlc7Coordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: Hlc7ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
