"""Constants and MIoT mapping for the Xiaomi isa.camera.hlc7.

Spec: https://home.miot-spec.com/spec/isa.camera.hlc7
"""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "xiaomi_hlc7"
MANUFACTURER = "Xiaomi"
MODEL = "isa.camera.hlc7"

CONF_TOKEN = "token"
CONF_GO2RTC_URL = "go2rtc_url"
CONF_STREAM = "stream"

DEFAULT_GO2RTC_URL = "http://127.0.0.1:1984"
DEFAULT_SCAN_INTERVAL_S = 30

PLATFORMS = [
    Platform.BUTTON,
    Platform.CAMERA,
    Platform.MEDIA_PLAYER,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]

# (siid, piid)
P_CAMERA_ON = (2, 1)
P_ROLLOVER = (2, 2)
P_NIGHT_SHOT = (2, 3)
P_WATERMARK = (2, 4)
P_RECORDING_MODE = (2, 7)
P_INDICATOR = (3, 1)
P_SD_STATUS = (4, 1)
P_SD_TOTAL = (4, 2)
P_SD_FREE = (4, 3)
P_SD_USED = (4, 4)
P_MOTION = (5, 1)
P_ALARM_INTERVAL = (5, 2)
P_SENSITIVITY = (5, 3)
P_MOTION_START = (5, 4)
P_MOTION_END = (5, 5)

POLLED_PROPERTIES = [
    P_CAMERA_ON,
    P_ROLLOVER,
    P_NIGHT_SHOT,
    P_WATERMARK,
    P_RECORDING_MODE,
    P_INDICATOR,
    P_SD_STATUS,
    P_SD_TOTAL,
    P_SD_FREE,
    P_SD_USED,
    P_MOTION,
    P_ALARM_INTERVAL,
    P_SENSITIVITY,
    P_MOTION_START,
    P_MOTION_END,
]

# (siid, aiid)
A_RESTART = (6, 1)

NIGHT_SHOT_OPTIONS = {0: "off", 1: "on", 2: "auto"}
RECORDING_MODE_OPTIONS = {0: "continuous", 1: "motion", 2: "stop"}
ROLLOVER_OPTIONS = {0: "0", 180: "180"}
SENSITIVITY_OPTIONS = {1: "low", 3: "medium", 2: "high"}
SD_STATUS_OPTIONS = {
    0: "normal",
    1: "not_inserted",
    2: "insufficient_space",
    3: "error",
    4: "formatting",
    5: "ejected",
}
