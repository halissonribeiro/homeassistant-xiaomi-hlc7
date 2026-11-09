"""Xiaomi Mi Camera 2K Magnetic Mount (isa.camera.hlc7), local, no go2rtc."""

from __future__ import annotations

import logging
import threading

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

from .const import (
    CONF_CAMERAS,
    CONF_PASS_TOKEN,
    CONF_QUALITY,
    CONF_REGION,
    CONF_USER_ID,
    DEFAULT_QUALITY,
    PLATFORMS,
    QUALITY_OPTIONS,
)
from .coordinator import CameraInfo, Hlc7Coordinator, Hlc7Data
from .xiaomi_cloud import XiaomiCloud, XiaomiCloudError

_LOGGER = logging.getLogger(__name__)

type Hlc7ConfigEntry = ConfigEntry[Hlc7Data]


def _login_and_list(user_id: str, pass_token: str, region: str):
    cloud = XiaomiCloud()
    cloud.login_with_token(user_id, pass_token)
    return cloud, cloud.devices(region)


async def async_setup_entry(hass: HomeAssistant, entry: Hlc7ConfigEntry) -> bool:
    region = entry.data[CONF_REGION]
    try:
        cloud, devices = await hass.async_add_executor_job(
            _login_and_list, entry.data[CONF_USER_ID], entry.data[CONF_PASS_TOKEN], region
        )
    except XiaomiCloudError as err:
        if "expired" in str(err) or "log in again" in str(err):
            raise ConfigEntryAuthFailed(str(err)) from err
        raise ConfigEntryNotReady(f"Xiaomi cloud: {err}") from err

    if cloud.pass_token and cloud.pass_token != entry.data[CONF_PASS_TOKEN]:
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_PASS_TOKEN: cloud.pass_token}
        )

    quality = QUALITY_OPTIONS[entry.options.get(CONF_QUALITY, DEFAULT_QUALITY)]
    by_did = {str(d.get("did")): d for d in devices}
    data = Hlc7Data(cloud=cloud)
    cloud_lock = threading.Lock()
    for did in entry.options.get(CONF_CAMERAS, entry.data[CONF_CAMERAS]):
        dev = by_did.get(did)
        if not dev or not dev.get("localip") or not dev.get("token"):
            _LOGGER.warning("Camera %s not found in the Xiaomi account (offline?)", did)
            continue
        info = CameraInfo(
            did=did,
            name=dev.get("name") or did,
            model=dev.get("model", ""),
            host=dev["localip"],
            token=dev["token"],
            mac=dev.get("mac"),
        )
        coordinator = Hlc7Coordinator(hass, entry, info, cloud, cloud_lock, region, quality)
        data.cameras[did] = coordinator
        try:
            await coordinator.async_start()
        except OSError as err:
            for started in data.cameras.values():
                await started.async_stop()
            raise ConfigEntryNotReady(f"cannot start local RTSP server: {err}") from err
        await coordinator.async_refresh()

    if not data.cameras:
        raise ConfigEntryNotReady("none of the selected cameras is available")

    entry.runtime_data = data
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_reload_on_options))
    return True


async def _reload_on_options(hass: HomeAssistant, entry: Hlc7ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: Hlc7ConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        for coordinator in entry.runtime_data.cameras.values():
            await coordinator.async_stop()
    return ok
