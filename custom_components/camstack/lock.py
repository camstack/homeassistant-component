"""Locks built from the components CamStack pushes.

The narrowest case for a native platform, and the strongest. CamStack's
lock state is `locked | unlocked | locking | unlocking | jammed`; a
`switch` can hold two of those five, and the way it gets the other three
wrong is not neutral — a JAMMED door reports as unlocked, which is the
answer that makes an "everything is secure" automation say yes.

So the hub sends both: this entity READS the enum and WRITES the switch
the route table already resolves.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.lock import LockEntity, LockEntityFeature
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import CamStackConfigEntry
from .entity import CamStackPushEntity

# `LockStateSchema` on the hub.
STATE_LOCKED = "locked"
STATE_UNLOCKED = "unlocked"
STATE_LOCKING = "locking"
STATE_UNLOCKING = "unlocking"
STATE_JAMMED = "jammed"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CamStackConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Let the push hub create locks as it learns about them."""
    entry.runtime_data.push.async_register_platform(
        Platform.LOCK, CamStackLock, async_add_entities
    )


class CamStackLock(CamStackPushEntity, LockEntity):
    """A pushed lock, with the three states a boolean cannot hold."""

    @property
    def supported_features(self) -> LockEntityFeature:
        """Advertise the latch only when the hub declared a route for it."""
        return (
            LockEntityFeature.OPEN
            if self._can_command("open")
            else LockEntityFeature(0)
        )

    @property
    def is_locked(self) -> bool | None:
        """Return whether the lock is engaged.

        `None` for anything that is not one of the two settled states — a
        lock mid-travel is not unlocked, and neither is a jammed one. Home
        Assistant renders that as `unknown`, which is the honest answer and
        the one an automation can test for.
        """
        if self._value is None:
            return None
        if self._value == STATE_LOCKED:
            return True
        if self._value == STATE_UNLOCKED:
            return False
        return None

    @property
    def is_locking(self) -> bool:
        """Return whether the bolt is being thrown."""
        return self._value == STATE_LOCKING

    @property
    def is_unlocking(self) -> bool:
        """Return whether the bolt is being withdrawn."""
        return self._value == STATE_UNLOCKING

    @property
    def is_jammed(self) -> bool:
        """Return whether the lock failed to move. The reason for all this."""
        return self._value == STATE_JAMMED

    async def async_lock(self, **kwargs: Any) -> None:
        """Ask the hub to lock.

        Optimistic to `locking` rather than to `locked`: the command has
        been accepted, the bolt has not moved yet, and the hub says when it
        has. Claiming `locked` here is exactly the report a jammed lock
        must never be able to produce.
        """
        await self._async_command("lock", optimistic=False)
        self._async_expect(STATE_LOCKING)

    async def async_unlock(self, **kwargs: Any) -> None:
        """Ask the hub to unlock."""
        await self._async_command("unlock", optimistic=False)
        self._async_expect(STATE_UNLOCKING)

    async def async_open(self, **kwargs: Any) -> None:
        """Ask the hub to withdraw the latch, on hardware that has one."""
        await self._async_command_control("open", "OPEN", optimistic=False)

    def _async_expect(self, state: str) -> None:
        """Show the transition the hub accepted, until it reports the end."""
        topic = self._state_topic
        if topic is not None:
            self._hub.states[topic] = state
            self.async_write_ha_state()
