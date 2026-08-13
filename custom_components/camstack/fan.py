"""Fans built from the components CamStack pushes.

CamStack's fan has no on/off field: `percentage` IS its state and 0 is
off, which is what Home Assistant's fan does too — `fan.turn_off` writes
0. So `is_on` is derived from the speed rather than from a second value
that could disagree with it.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import CamStackConfigEntry
from .entity import CamStackPushEntity, as_bool, as_int

# `FanDirectionSchema` on the hub, which is Home Assistant's vocabulary.
DIRECTION_FORWARD = "forward"
DIRECTION_REVERSE = "reverse"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CamStackConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Let the push hub create fans as it learns about them."""
    entry.runtime_data.push.async_register_platform(
        Platform.FAN, CamStackFan, async_add_entities
    )


class CamStackFan(CamStackPushEntity, FanEntity):
    """A pushed fan: a speed, and whatever else the hardware has."""

    @property
    def supported_features(self) -> FanEntityFeature:
        """Advertise exactly what the hub declared a route for."""
        features = FanEntityFeature(0)
        if self._command_topic is not None:
            features |= (
                FanEntityFeature.SET_SPEED
                | FanEntityFeature.TURN_ON
                | FanEntityFeature.TURN_OFF
            )
        if self._can_command("oscillation"):
            features |= FanEntityFeature.OSCILLATE
        if self._can_command("direction"):
            features |= FanEntityFeature.DIRECTION
        if self._can_command("preset"):
            features |= FanEntityFeature.PRESET_MODE
        return features

    @property
    def percentage(self) -> int | None:
        """Return the speed the hub pushed, 0..100."""
        return as_int(self._value)

    @property
    def percentage_step(self) -> float:
        """Return the granularity the DEVICE accepts.

        A four-speed fan reports 25, and offering it 37 % is offering it a
        speed it will round to something the operator did not ask for.
        """
        step = self._component.get("step")
        return float(step) if isinstance(step, (int, float)) and step else 1.0

    @property
    def is_on(self) -> bool | None:
        """Return whether the fan is turning."""
        percentage = self.percentage
        return None if percentage is None else percentage > 0

    @property
    def oscillating(self) -> bool | None:
        """Return whether the head is sweeping."""
        return as_bool(self._control_value("oscillation"), self._component)

    @property
    def current_direction(self) -> str | None:
        """Return the blade direction."""
        value = self._control_value("direction")
        return value if value in (DIRECTION_FORWARD, DIRECTION_REVERSE) else None

    @property
    def preset_mode(self) -> str | None:
        """Return the active preset."""
        return self._control_value("preset") or None

    @property
    def preset_modes(self) -> list[str] | None:
        """Return the presets the device accepts, as it reported them."""
        raw = self._control("preset").get("options")
        if not isinstance(raw, list):
            return None
        options = [entry for entry in raw if isinstance(entry, str)]
        return options or None

    async def async_set_percentage(self, percentage: int) -> None:
        """Send the speed to the hub."""
        await self._async_command(str(int(percentage)))

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Start the fan.

        Full speed when Home Assistant did not say: the capability has no
        "last speed" to restore and inventing one — 50 %, say — is a value
        the operator never chose.
        """
        if preset_mode is not None:
            await self.async_set_preset_mode(preset_mode)
            return
        await self.async_set_percentage(100 if percentage is None else percentage)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Stop the fan, which for this capability is speed 0."""
        await self.async_set_percentage(0)

    async def async_oscillate(self, oscillating: bool) -> None:
        """Send the oscillation state."""
        await self._async_command_control(
            "oscillation", "true" if oscillating else "false"
        )

    async def async_set_direction(self, direction: str) -> None:
        """Send the blade direction."""
        await self._async_command_control("direction", direction)

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Send the preset."""
        await self._async_command_control("preset", preset_mode)
