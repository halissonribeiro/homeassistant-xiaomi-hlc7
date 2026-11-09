from unittest.mock import patch, MagicMock
import pytest
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from custom_components.xiaomi_hlc7.const import DOMAIN
from custom_components.xiaomi_hlc7.xiaomi_cloud import XiaomiLoginStep

DEVICES = [
    {"did": "1023929615", "name": "Lavanderia", "model": "isa.camera.hlc7", "localip": "192.168.80.60", "token": "00112233445566778899aabbccddeeff", "mac": "E4:AA:EC:46:6A:3A"},
    {"did": "999", "name": "Lamp", "model": "yeelink.light.x", "localip": "1.2.3.4", "token": "ff"*16},
]

class FakeCloud:
    user_id = None; pass_token = None
    def login(self, u, p): raise XiaomiLoginStep(verify_email="h***@gmail.com")
    def login_verify(self, code):
        assert code == "123456"; self.user_id = "6901481259"; self.pass_token = "PT"
    def login_with_token(self, uid, pt): self.user_id = uid; self.pass_token = pt
    def devices(self, region): return DEVICES

async def test_flow_and_setup(hass):
    with patch("custom_components.xiaomi_hlc7.config_flow.XiaomiCloud", FakeCloud):
        r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
        assert r["step_id"] == "user"
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {"username": "a@b.c", "password": "x", "region": "cn"})
        assert r["step_id"] == "verify", r
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {"code": "123456"})
        assert r["step_id"] == "cameras", r
        assert list(r["data_schema"].schema)[0] == "cameras"
        async def fake_start(self): self.port = 18554
        with patch("custom_components.xiaomi_hlc7.XiaomiCloud", FakeCloud), \
             patch("custom_components.xiaomi_hlc7.rtsp.RtspServer.start", fake_start), \
             patch("custom_components.xiaomi_hlc7.coordinator.MiioClient.get_properties", return_value={(2,1): True, (5,1): False, (2,3): 2, (4,1): 0, (5,2): 5}):
            r = await hass.config_entries.flow.async_configure(r["flow_id"], {"cameras": ["1023929615"]})
            assert r["type"] is FlowResultType.CREATE_ENTRY, r
            assert r["data"]["user_id"] == "6901481259"
            await hass.async_block_till_done()
            states = {s.entity_id: s.state for s in hass.states.async_all()}
            print(states)
            assert any(e.startswith("camera.") for e in states)
            assert any(e.startswith("media_player.") for e in states)
            sw = [e for e in states if e.startswith("switch.")]
            assert len(sw) == 4
            cam = [e for e in states if e.startswith("camera.")][0]
            from homeassistant.components.camera import async_get_stream_source
            src = await async_get_stream_source(hass, cam)
            assert src.startswith("rtsp://127.0.0.1:")
            entry = hass.config_entries.async_entries(DOMAIN)[0]
            assert await hass.config_entries.async_unload(entry.entry_id)


async def test_options_lan_rtsp_requires_password(hass):
    from pytest_homeassistant_custom_component.common import MockConfigEntry
    entry = MockConfigEntry(domain=DOMAIN, unique_id="1", data={"user_id": "1", "pass_token": "p", "region": "cn", "cameras": []})
    entry.add_to_hass(hass)
    with patch("custom_components.xiaomi_hlc7.async_setup_entry", return_value=True):
        r = await hass.config_entries.options.async_init(entry.entry_id)
        r = await hass.config_entries.options.async_configure(r["flow_id"], {"quality": "hd", "lan_rtsp": True, "lan_rtsp_port": 8556, "lan_rtsp_username": "frigate", "lan_rtsp_password": "123"})
        assert r["errors"] == {"lan_rtsp_password": "weak_password"}
        r = await hass.config_entries.options.async_configure(r["flow_id"], {"quality": "hd", "lan_rtsp": True, "lan_rtsp_port": 8556, "lan_rtsp_username": "frigate", "lan_rtsp_password": "segredo123"})
        assert r["type"] is FlowResultType.CREATE_ENTRY
        assert entry.options["lan_rtsp_password"] == "segredo123"
