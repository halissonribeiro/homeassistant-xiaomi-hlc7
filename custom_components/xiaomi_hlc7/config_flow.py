"""Config flow: Mi account login (captcha / e-mail or SMS code) + camera pick."""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    CONF_CAMERAS,
    CONF_PASS_TOKEN,
    CONF_QUALITY,
    CONF_REGION,
    CONF_USER_ID,
    DEFAULT_QUALITY,
    DEFAULT_REGION,
    DOMAIN,
    QUALITY_OPTIONS,
    REGIONS,
)
from .xiaomi_cloud import XiaomiCloud, XiaomiCloudError, XiaomiLoginStep

_LOGGER = logging.getLogger(__name__)

CAPTCHA_FILE = "www/xiaomi_hlc7_captcha.jpg"


def _is_camera(dev: dict[str, Any]) -> bool:
    return ".camera." in str(dev.get("model", ""))


class Hlc7ConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._cloud: XiaomiCloud | None = None
        self._region = DEFAULT_REGION
        self._username = ""
        self._verify_target = ""
        self._devices: list[dict[str, Any]] = []

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> OptionsFlow:
        return Hlc7OptionsFlow()

    # ------------------------------------------------------------ login
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self._cloud = XiaomiCloud()
            self._region = user_input[CONF_REGION]
            self._username = user_input[CONF_USERNAME]
            return await self._run_login(
                self._cloud.login, user_input[CONF_USERNAME], user_input[CONF_PASSWORD]
            )
        schema = vol.Schema(
            {
                vol.Required(CONF_USERNAME, default=self._username): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Required(CONF_REGION, default=self._region): vol.In(REGIONS),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema)

    async def _run_login(self, func, *args) -> ConfigFlowResult:
        assert self._cloud is not None
        try:
            await self.hass.async_add_executor_job(func, *args)
        except XiaomiLoginStep as step:
            if step.captcha:
                await self.hass.async_add_executor_job(self._save_captcha, step.captcha)
                return await self.async_step_captcha()
            self._verify_target = step.verify_email or step.verify_phone or ""
            return await self.async_step_verify()
        except XiaomiCloudError as err:
            _LOGGER.warning("Xiaomi login failed: %s", err)
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema(
                    {
                        vol.Required(CONF_USERNAME, default=self._username): str,
                        vol.Required(CONF_PASSWORD): str,
                        vol.Required(CONF_REGION, default=self._region): vol.In(REGIONS),
                    }
                ),
                errors={"base": "invalid_auth"},
                description_placeholders={"detail": str(err)[:200]},
            )
        return await self._after_login()

    def _save_captcha(self, image: bytes) -> None:
        path = self.hass.config.path(CAPTCHA_FILE)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(image)

    async def async_step_captcha(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            assert self._cloud is not None
            return await self._run_login(self._cloud.login_captcha, user_input["code"])
        return self.async_show_form(
            step_id="captcha",
            data_schema=vol.Schema({vol.Required("code"): str}),
            description_placeholders={
                "url": f"/local/xiaomi_hlc7_captcha.jpg?t={int(time.time())}"
            },
        )

    async def async_step_verify(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            assert self._cloud is not None
            return await self._run_login(self._cloud.login_verify, user_input["code"].strip())
        return self.async_show_form(
            step_id="verify",
            data_schema=vol.Schema({vol.Required("code"): str}),
            description_placeholders={"target": self._verify_target or "?"},
        )

    # ---------------------------------------------------------- cameras
    async def _after_login(self) -> ConfigFlowResult:
        assert self._cloud is not None
        if self.source == "reauth":
            entry = self._get_reauth_entry()
            if str(self._cloud.user_id) != str(entry.unique_id):
                return self.async_abort(reason="wrong_account")
            return self.async_update_reload_and_abort(
                entry,
                data={
                    **entry.data,
                    CONF_PASS_TOKEN: self._cloud.pass_token,
                },
            )
        await self.async_set_unique_id(str(self._cloud.user_id))
        self._abort_if_unique_id_configured()
        try:
            devices = await self.hass.async_add_executor_job(self._cloud.devices, self._region)
        except XiaomiCloudError as err:
            _LOGGER.warning("Xiaomi device list failed: %s", err)
            return self.async_abort(reason="cannot_connect")
        self._devices = [d for d in devices if _is_camera(d)]
        if not self._devices:
            return self.async_abort(reason="no_cameras")
        return await self.async_step_cameras()

    async def async_step_cameras(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        assert self._cloud is not None
        if user_input is not None:
            return self.async_create_entry(
                title=f"Xiaomi {self._username}",
                data={
                    CONF_USER_ID: str(self._cloud.user_id),
                    CONF_PASS_TOKEN: self._cloud.pass_token,
                    CONF_REGION: self._region,
                    CONF_CAMERAS: user_input[CONF_CAMERAS],
                },
            )
        options = {
            str(d["did"]): f"{d.get('name')} ({d.get('model')}, {d.get('localip')})"
            for d in self._devices
        }
        return self.async_show_form(
            step_id="cameras",
            data_schema=vol.Schema(
                {vol.Required(CONF_CAMERAS, default=list(options)): cv.multi_select(options)}
            ),
        )

    # ------------------------------------------------------------ reauth
    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        self._region = entry_data.get(CONF_REGION, DEFAULT_REGION)
        return await self.async_step_user()


class Hlc7OptionsFlow(OptionsFlow):
    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        current = self.config_entry.options.get(CONF_QUALITY, DEFAULT_QUALITY)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_QUALITY, default=current): SelectSelector(
                        SelectSelectorConfig(
                            options=list(QUALITY_OPTIONS),
                            mode=SelectSelectorMode.LIST,
                            translation_key="quality",
                        )
                    )
                }
            ),
        )
