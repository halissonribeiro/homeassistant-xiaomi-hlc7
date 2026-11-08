"""Config flow: camera IP + miIO token + go2rtc stream name."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_NAME
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_GO2RTC_URL,
    CONF_STREAM,
    CONF_TOKEN,
    DEFAULT_GO2RTC_URL,
    DOMAIN,
    P_CAMERA_ON,
)
from .miio import MiioClient, MiioError, MiioTimeout

_LOGGER = logging.getLogger(__name__)

TOKEN_RE = re.compile(r"^[0-9a-fA-F]{32}$")


def _schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_NAME, default=defaults.get(CONF_NAME, "Câmera Xiaomi")): str,
            vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, "")): str,
            vol.Required(CONF_TOKEN, default=defaults.get(CONF_TOKEN, "")): str,
            vol.Required(
                CONF_GO2RTC_URL, default=defaults.get(CONF_GO2RTC_URL, DEFAULT_GO2RTC_URL)
            ): str,
            vol.Required(CONF_STREAM, default=defaults.get(CONF_STREAM, "")): str,
        }
    )


class Hlc7ConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input[CONF_TOKEN] = user_input[CONF_TOKEN].strip().lower()
            user_input[CONF_GO2RTC_URL] = user_input[CONF_GO2RTC_URL].strip().rstrip("/")
            if not TOKEN_RE.match(user_input[CONF_TOKEN]):
                errors[CONF_TOKEN] = "invalid_token"
            else:
                client = MiioClient(user_input[CONF_HOST].strip(), user_input[CONF_TOKEN])
                try:
                    await client.hello()
                except MiioError:
                    errors["base"] = "cannot_connect"
                else:
                    try:
                        await client.get_properties([P_CAMERA_ON])
                    except MiioTimeout:
                        # Device answers hello but ignores packets signed
                        # with a wrong token.
                        errors[CONF_TOKEN] = "invalid_token"
                    except MiioError:
                        _LOGGER.exception("miIO check failed")
                        errors[CONF_TOKEN] = "invalid_token"
                if not errors:
                    errors.update(await self._check_go2rtc(user_input))
                if not errors:
                    await self.async_set_unique_id(str(client.did))
                    self._abort_if_unique_id_configured()
                    name = user_input.pop(CONF_NAME)
                    return self.async_create_entry(title=name, data=user_input)

        return self.async_show_form(
            step_id="user", data_schema=_schema(user_input or {}), errors=errors
        )

    async def _check_go2rtc(self, data: dict[str, Any]) -> dict[str, str]:
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(f"{data[CONF_GO2RTC_URL]}/api/streams", timeout=10) as resp:
                resp.raise_for_status()
                streams = json.loads(await resp.text())
        except Exception:  # noqa: BLE001
            return {CONF_GO2RTC_URL: "go2rtc_unreachable"}
        if data[CONF_STREAM] not in streams:
            return {CONF_STREAM: "stream_not_found"}
        return {}
