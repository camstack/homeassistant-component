"""Selects built from the components CamStack pushes."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import CamStackConfigEntry
from .entity import CamStackPushEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CamStackConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Let the push hub create selects as it learns about them."""
    entry.runtime_data.push.async_register_platform(
        Platform.SELECT, CamStackSelect, async_add_entities
    )


class CamStackSelect(CamStackPushEntity, SelectEntity):
    """A pushed choice — the snooze durations, or the PTZ presets."""

    @property
    def options(self) -> list[str]:
        """Return the options the hub declared, live.

        Read on every access rather than captured at construction: a camera
        that learns a new PTZ preset re-announces its component set, and an
        option list frozen at setup would never show it.
        """
        raw = self._component.get("options")
        if not isinstance(raw, list):
            return []
        return [option for option in raw if isinstance(option, str)]

    @property
    def current_option(self) -> str | None:
        """Return the pushed option, if it is still one of the choices.

        Some of these are commandable and never report back — the hub says so
        rather than inventing a value. Showing the last option Home Assistant
        sent is honest; showing one that is no longer offered is not.
        """
        value = self._value
        return value if value in self.options else None

    async def async_select_option(self, option: str) -> None:
        """Send the chosen option to the hub."""
        await self._hub.async_send_command(self._command_topic, option)
        topic = self._state_topic
        if topic is not None:
            self._hub.states[topic] = option
            self.async_write_ha_state()
