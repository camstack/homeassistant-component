"""Switches built from the components CamStack pushes.

Every switch here is a MIRROR. The hub renders it from the authority that
already owns the function and applies it back through the same one, so this
component never writes a setting itself: a toggle in Home Assistant that wrote
its own store would be a second knob able to disagree with the admin UI, and
two knobs that disagree are worse than one.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import CamStackConfigEntry
from .entity import CamStackPushEntity, as_bool


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CamStackConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Let the push hub create switches as it learns about them."""
    entry.runtime_data.push.async_register_platform(
        Platform.SWITCH, CamStackSwitch, async_add_entities
    )


class CamStackSwitch(CamStackPushEntity, SwitchEntity):
    """A pushed on/off control, commanded back over the hub's command route."""

    @property
    def is_on(self) -> bool | None:
        """Return the pushed value, or None while nothing has arrived."""
        return as_bool(self._value, self._component)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Ask the hub to switch the function on."""
        await self._async_command(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Ask the hub to switch the function off."""
        await self._async_command(False)

    async def _async_command(self, on: bool) -> None:
        """Send the command, then show the result until the hub confirms it.

        The camera switches report back on the hub's reconcile rather than on
        an event, so waiting for the push would leave the toggle sitting on
        its old value for minutes. The optimistic write happens only AFTER the
        hub accepted the command — a refusal raises and the entity does not
        move, which is the whole reason the hub refuses rather than
        approximating.
        """
        component = self._component
        payload = str(
            component.get("payload_on" if on else "payload_off", str(on).lower())
        )
        await self._hub.async_send_command(self._command_topic, payload)
        topic = self._state_topic
        if topic is not None:
            self._hub.states[topic] = payload
            self.async_write_ha_state()
