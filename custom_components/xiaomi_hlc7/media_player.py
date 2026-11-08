"""Camera speaker: plays audio/TTS through go2rtc's two-way audio channel.

    action: tts.speak
    target: {entity_id: tts.google_translate_pt_br}
    data:
      media_player_entity_id: media_player.<camera>_alto_falante
      message: "Olá!"

go2rtc fetches the URL itself, transcodes it to PCMA 8 kHz with FFmpeg and
pushes it to the camera (POST /api/streams?dst=<stream>&src=ffmpeg:...).
"""

from __future__ import annotations

from typing import Any

from homeassistant.components import media_source
from homeassistant.components.media_player import (
    BrowseMedia,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
    async_process_play_media_url,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import Hlc7Entity


async def async_setup_entry(
    hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities([Hlc7Speaker(entry.runtime_data)])


class Hlc7Speaker(Hlc7Entity, MediaPlayerEntity):
    _attr_icon = "mdi:account-voice"
    _attr_media_content_type = MediaType.MUSIC
    _attr_supported_features = (
        MediaPlayerEntityFeature.PLAY_MEDIA
        | MediaPlayerEntityFeature.BROWSE_MEDIA
        | MediaPlayerEntityFeature.STOP
    )

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "speaker")
        self._attr_state = MediaPlayerState.IDLE

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
            resolved = await media_source.async_resolve_media(
                self.hass, media_id, self.entity_id
            )
            media_id = resolved.url
        url = async_process_play_media_url(self.hass, media_id)
        self._attr_state = MediaPlayerState.PLAYING
        self.async_write_ha_state()
        try:
            await self.coordinator.async_go2rtc(
                "POST",
                "/api/streams",
                dst=self.coordinator.stream,
                src=f"ffmpeg:{url}#audio=pcma#input=file",
            )
        finally:
            self._attr_state = MediaPlayerState.IDLE
            self.async_write_ha_state()

    async def async_media_stop(self) -> None:
        await self.coordinator.async_go2rtc(
            "POST", "/api/streams", dst=self.coordinator.stream, src=""
        )
