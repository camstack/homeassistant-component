"""Covers built from the components CamStack pushes.

The first of the native platforms, and the clearest case for them. Through
the platforms this integration used to build, a shutter arrived as a state
`sensor`, a position `number`, a tilt `number` and three `button`s: five
entities that automate correctly and are not a cover. No
`cover.open_cover`, no position in the more-info dialog, no
`device_class: garage`, and every dashboard card that expects a cover
refuses it.

Everything here is DECLARED by the hub. Which sub-controls arrived decides
which features this entity advertises — a shutter with no tilt motor never
gets a tilt slider, and a hub whose route table cannot move a cover
produces a cover that reports and does not pretend to move.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.cover import (
    ATTR_POSITION,
    ATTR_TILT_POSITION,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import CamStackConfigEntry
from .entity import CamStackPushEntity, as_int

# `CoverStatusSchema` on the hub. Its own doc comment calls it "HA's
# canonical lifecycle", and it is: these five are matched verbatim.
STATE_OPEN = "open"
STATE_OPENING = "opening"
STATE_CLOSING = "closing"
STATE_CLOSED = "closed"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CamStackConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Let the push hub create covers as it learns about them."""
    entry.runtime_data.push.async_register_platform(
        Platform.COVER, CamStackCover, async_add_entities
    )


class CamStackCover(CamStackPushEntity, CoverEntity):
    """A pushed cover: a state, an optional position, an optional tilt."""

    @property
    def supported_features(self) -> CoverEntityFeature:
        """Advertise exactly what the hub declared a route for.

        A feature Home Assistant advertises is a service call it accepts,
        and a service call that reaches a hub with no route for it is a
        refusal the operator sees as a broken integration. So the flags are
        derived from the declaration rather than from the platform.
        """
        features = CoverEntityFeature(0)
        if self._has_verb("open"):
            features |= CoverEntityFeature.OPEN
        if self._has_verb("close"):
            features |= CoverEntityFeature.CLOSE
        if self._has_verb("stop"):
            features |= CoverEntityFeature.STOP
        if self._can_command("position"):
            features |= CoverEntityFeature.SET_POSITION
        if self._can_command("tilt"):
            features |= (
                CoverEntityFeature.SET_TILT_POSITION
                | CoverEntityFeature.OPEN_TILT
                | CoverEntityFeature.CLOSE_TILT
            )
        return features

    @property
    def current_cover_position(self) -> int | None:
        """Return the position the hub pushed, 0 closed to 100 open."""
        return as_int(self._control_value("position"))

    @property
    def current_cover_tilt_position(self) -> int | None:
        """Return the slat tilt, 0 closed to 100 open."""
        return as_int(self._control_value("tilt"))

    @property
    def is_opening(self) -> bool | None:
        """Return whether the cover is travelling open."""
        return None if self._value is None else self._value == STATE_OPENING

    @property
    def is_closing(self) -> bool | None:
        """Return whether the cover is travelling closed."""
        return None if self._value is None else self._value == STATE_CLOSING

    @property
    def is_closed(self) -> bool | None:
        """Return whether the cover is shut.

        The POSITION wins when the device reports one. `stopped` is a
        lifecycle state and says nothing about where the shutter stopped:
        answering it from the state alone would call a half-open blind
        open, and every "close the blinds if they are not already" automation
        would skip it.
        """
        position = self.current_cover_position
        if position is not None:
            return position == 0
        if self._value is None:
            return None
        return self._value == STATE_CLOSED

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Ask the hub to open the cover."""
        await self._async_verb("open", "OPEN")

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Ask the hub to close the cover."""
        await self._async_verb("close", "CLOSE")

    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Ask the hub to stop the cover where it is."""
        await self._async_verb("stop", "STOP")

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        """Send the requested position to the hub."""
        position = kwargs.get(ATTR_POSITION)
        if position is None:
            return
        await self._async_command_control("position", str(int(position)))

    async def async_set_cover_tilt_position(self, **kwargs: Any) -> None:
        """Send the requested tilt to the hub."""
        tilt = kwargs.get(ATTR_TILT_POSITION)
        if tilt is None:
            return
        await self._async_command_control("tilt", str(int(tilt)))

    async def async_open_cover_tilt(self, **kwargs: Any) -> None:
        """Open the slats fully.

        The capability has one tilt surface — a position — so the two tilt
        verbs are its ends rather than separate methods. Home Assistant
        offers them as buttons on the more-info dialog and they are what an
        operator reaches for.
        """
        await self._async_command_control("tilt", "100")

    async def async_close_cover_tilt(self, **kwargs: Any) -> None:
        """Close the slats fully."""
        await self._async_command_control("tilt", "0")
