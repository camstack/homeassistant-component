"""Valves built from the components CamStack pushes.

The cover lifecycle without tilt. Home Assistant models a valve as its own
platform rather than as a cover because a water shut-off is not a window,
and a dashboard that offers "open the blinds" for the mains supply is one
mis-tap away from a flood.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.valve import (
    ValveEntity,
    ValveEntityFeature,
)
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import CamStackConfigEntry
from .entity import CamStackPushEntity, as_int

# `ValveStatusSchema` on the hub — the same five as a cover.
STATE_OPENING = "opening"
STATE_CLOSING = "closing"
STATE_CLOSED = "closed"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CamStackConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Let the push hub create valves as it learns about them."""
    entry.runtime_data.push.async_register_platform(
        Platform.VALVE, CamStackValve, async_add_entities
    )


class CamStackValve(CamStackPushEntity, ValveEntity):
    """A pushed valve, positionable when the hardware says so."""

    @property
    def reports_position(self) -> bool:
        """Whether this valve reports where it is between the two ends.

        Home Assistant asks this FIRST and treats the answer as a contract:
        a valve that reports a position and returns None for it renders as
        broken. So it is answered from the declaration — the hub sends the
        position control only for hardware that has one.
        """
        return self._control_topic("position", "state_topic") is not None

    @property
    def supported_features(self) -> ValveEntityFeature:
        """Advertise exactly what the hub declared a route for."""
        features = ValveEntityFeature(0)
        if self._has_verb("open"):
            features |= ValveEntityFeature.OPEN
        if self._has_verb("close"):
            features |= ValveEntityFeature.CLOSE
        if self._has_verb("stop"):
            features |= ValveEntityFeature.STOP
        if self._can_command("position"):
            features |= ValveEntityFeature.SET_POSITION
        return features

    @property
    def current_valve_position(self) -> int | None:
        """Return the position the hub pushed, 0 closed to 100 open."""
        return as_int(self._control_value("position"))

    @property
    def is_opening(self) -> bool | None:
        """Return whether the valve is travelling open."""
        return None if self._value is None else self._value == STATE_OPENING

    @property
    def is_closing(self) -> bool | None:
        """Return whether the valve is travelling closed."""
        return None if self._value is None else self._value == STATE_CLOSING

    @property
    def is_closed(self) -> bool | None:
        """Return whether the valve is shut."""
        position = self.current_valve_position
        if position is not None:
            return position == 0
        if self._value is None:
            return None
        return self._value == STATE_CLOSED

    async def async_open_valve(self, **kwargs: Any) -> None:
        """Ask the hub to open the valve."""
        await self._async_verb("open", "OPEN")

    async def async_close_valve(self, **kwargs: Any) -> None:
        """Ask the hub to close the valve."""
        await self._async_verb("close", "CLOSE")

    async def async_stop_valve(self, **kwargs: Any) -> None:
        """Ask the hub to stop the valve where it is."""
        await self._async_verb("stop", "STOP")

    async def async_set_valve_position(self, position: int) -> None:
        """Send the requested position to the hub."""
        await self._async_command_control("position", str(int(position)))
