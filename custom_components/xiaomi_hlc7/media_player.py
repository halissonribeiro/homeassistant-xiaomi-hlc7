"""Camera speaker: TTS / audio files sent straight over the P2P talk channel.

    action: tts.speak
    target: {entity_id: tts.google_translate_pt_br}
    data:
      media_player_entity_id: media_player.<camera>_alto_falante
      message: "Olá!"
"""

from __future__ import annotations

import asyncio
from typing import Any

from homeassistant.components import media_source
from homeassistant.components.ffmpeg import get_ffmpeg_manager
from homeassistant.components.media_player import (
    BrowseMedia,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
    async_process_play_media_url,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import Hlc7Entity, cameras

MAX_DURATION_S = 60
FFMPEG_TIMEOUT_S = 30
DEFAULT_VOLUME = 0.5


async def async_setup_entry(
    hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback
) -> None:
    binary = get_ffmpeg_manager(hass).binary
    async_add_entities(Hlc7Speaker(c, binary) for c in cameras(entry))


async def convert_to_alaw(ffmpeg: str, source: str, gain: float) -> bytes:
    proc = await asyncio.create_subprocess_exec(
        ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error",
        "-i", source, "-t", str(MAX_DURATION_S),
        "-ac", "1", "-ar", "8000",
        "-af", f"volume={gain:.2f},alimiter=limit=0.9",
        "-f", "alaw", "-",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )  # fmt: skip
    try:
        out, err = await asyncio.wait_for(proc.communicate(), FFMPEG_TIMEOUT_S)
    except TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise HomeAssistantError("Timed out converting audio") from exc
    if proc.returncode != 0 or not out:
        detail = (err.decode(errors="replace").strip().splitlines() or [""])[-1]
        raise HomeAssistantError(f"Could not convert audio: {detail}")
    return out


class Hlc7Speaker(Hlc7Entity, MediaPlayerEntity):
    _attr_icon = "mdi:account-voice"
    _attr_media_content_type = MediaType.MUSIC
    _attr_supported_features = (
        MediaPlayerEntityFeature.PLAY_MEDIA
        | MediaPlayerEntityFeature.BROWSE_MEDIA
        | MediaPlayerEntityFeature.VOLUME_SET
    )

    def __init__(self, coordinator, ffmpeg: str) -> None:
        super().__init__(coordinator, "speaker")
        self._ffmpeg = ffmpeg
        self._attr_state = MediaPlayerState.IDLE
        self._attr_volume_level = DEFAULT_VOLUME

    async def async_set_volume_level(self, volume: float) -> None:
        """Software gain: 0.5 = unchanged, 1.0 = 2x."""
        self._attr_volume_level = volume
        self.async_write_ha_state()

    async def async_browse_media(
        self,
        media_content_type: MediaType | str | None = None,
        media_content_id: str | None = None,
    ) -> BrowseMedia:
        return await media_source.async_browse_media(
            self.hass,
            media_content_id,
            content_filter=lambda item: item.media_content_type.startswith("audio/"),
        )

    async def async_play_media(
        self, media_type: MediaType | str, media_id: str, **kwargs: Any
    ) -> None:
        if media_source.is_media_source_id(media_id):
            resolved = await media_source.async_resolve_media(self.hass, media_id, self.entity_id)
            media_id = resolved.url
        source = async_process_play_media_url(self.hass, media_id)
        alaw = await convert_to_alaw(self._ffmpeg, source, 2 * (self._attr_volume_level or 0))
        self._attr_state = MediaPlayerState.PLAYING
        self.async_write_ha_state()
        try:
            await self.hass.async_add_executor_job(self.coordinator.streamer.play_alaw, alaw)
        except Exception as err:  # noqa: BLE001
            raise HomeAssistantError(f"Speaker failed: {err}") from err
        finally:
            self._attr_state = MediaPlayerState.IDLE
            self.async_write_ha_state()
