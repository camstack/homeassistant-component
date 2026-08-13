"""Media players built from the components CamStack pushes.

One conversion lives here and nowhere else: CamStack carries volume as
0..100 and Home Assistant as 0..1. Applying that factor on the wrong side
is a player that jumps to full volume, so it is done once, in both
directions, next to the field it converts.
"""

from __future__ import annotations

from homeassistant.components.media_player import (
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    RepeatMode,
)
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import CamStackConfigEntry
from .entity import CamStackPushEntity, as_bool, as_float

# `MediaPlayerStateSchema` on the hub → what Home Assistant has. Every one
# of the seven maps; `on` is the player awake with nothing loaded, which is
# HA's IDLE, and `standby` is its own state.
_STATES: dict[str, MediaPlayerState] = {
    "off": MediaPlayerState.OFF,
    "on": MediaPlayerState.IDLE,
    "idle": MediaPlayerState.IDLE,
    "playing": MediaPlayerState.PLAYING,
    "paused": MediaPlayerState.PAUSED,
    "buffering": MediaPlayerState.BUFFERING,
    "standby": MediaPlayerState.STANDBY,
}

_REPEAT_MODES: frozenset[str] = frozenset(mode.value for mode in RepeatMode)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CamStackConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Let the push hub create media players as it learns about them."""
    entry.runtime_data.push.async_register_platform(
        Platform.MEDIA_PLAYER, CamStackMediaPlayer, async_add_entities
    )


class CamStackMediaPlayer(CamStackPushEntity, MediaPlayerEntity):
    """A pushed player: transport, volume and source."""

    @property
    def supported_features(self) -> MediaPlayerEntityFeature:
        """Advertise exactly what the hub declared a route for."""
        features = MediaPlayerEntityFeature(0)
        if self._has_verb("play"):
            features |= MediaPlayerEntityFeature.PLAY
        if self._has_verb("pause"):
            features |= MediaPlayerEntityFeature.PAUSE
        if self._has_verb("stop"):
            features |= MediaPlayerEntityFeature.STOP
        if self._has_verb("next"):
            features |= MediaPlayerEntityFeature.NEXT_TRACK
        if self._has_verb("previous"):
            features |= MediaPlayerEntityFeature.PREVIOUS_TRACK
        if self._can_command("volume"):
            features |= (
                MediaPlayerEntityFeature.VOLUME_SET
                | MediaPlayerEntityFeature.VOLUME_STEP
            )
        if self._can_command("mute"):
            features |= MediaPlayerEntityFeature.VOLUME_MUTE
        if self._can_command("source"):
            features |= MediaPlayerEntityFeature.SELECT_SOURCE
        if self._can_command("shuffle"):
            features |= MediaPlayerEntityFeature.SHUFFLE_SET
        if self._can_command("repeat"):
            features |= MediaPlayerEntityFeature.REPEAT_SET
        return features

    @property
    def state(self) -> MediaPlayerState | None:
        """Return what the player is doing."""
        value = self._value
        return None if value is None else _STATES.get(value)

    @property
    def volume_level(self) -> float | None:
        """Return the volume as Home Assistant counts it, 0..1."""
        percent = as_float(self._control_value("volume"))
        return None if percent is None else max(0.0, min(1.0, percent / 100))

    @property
    def is_volume_muted(self) -> bool | None:
        """Return whether the player is muted."""
        return as_bool(self._control_value("mute"), self._component)

    @property
    def source(self) -> str | None:
        """Return the active input."""
        return self._control_value("source") or None

    @property
    def source_list(self) -> list[str] | None:
        """Return the inputs THIS player has, as it reported them."""
        raw = self._control("source").get("options")
        if not isinstance(raw, list):
            return None
        options = [entry for entry in raw if isinstance(entry, str)]
        return options or None

    @property
    def shuffle(self) -> bool | None:
        """Return whether shuffle is on."""
        return as_bool(self._control_value("shuffle"), self._component)

    @property
    def repeat(self) -> RepeatMode | None:
        """Return the repeat setting."""
        value = self._control_value("repeat")
        if value is None or value not in _REPEAT_MODES:
            return None
        return RepeatMode(value)

    async def async_media_play(self) -> None:
        """Start playback."""
        await self._async_verb("play", "PLAY")

    async def async_media_pause(self) -> None:
        """Hold playback."""
        await self._async_verb("pause", "PAUSE")

    async def async_media_stop(self) -> None:
        """End playback."""
        await self._async_verb("stop", "STOP")

    async def async_media_next_track(self) -> None:
        """Skip forward."""
        await self._async_verb("next", "NEXT")

    async def async_media_previous_track(self) -> None:
        """Skip back."""
        await self._async_verb("previous", "PREVIOUS")

    async def async_set_volume_level(self, volume: float) -> None:
        """Send the volume, converted to the 0..100 the hub carries."""
        await self._async_command_control("volume", str(round(volume * 100)))

    async def async_mute_volume(self, mute: bool) -> None:
        """Send the mute state."""
        await self._async_command_control("mute", "true" if mute else "false")

    async def async_select_source(self, source: str) -> None:
        """Send the input."""
        await self._async_command_control("source", source)

    async def async_set_shuffle(self, shuffle: bool) -> None:
        """Send the shuffle state."""
        await self._async_command_control("shuffle", "true" if shuffle else "false")

    async def async_set_repeat(self, repeat: RepeatMode) -> None:
        """Send the repeat setting."""
        await self._async_command_control("repeat", str(repeat))
