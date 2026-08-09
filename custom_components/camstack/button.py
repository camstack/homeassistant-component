"""Buttons built from the components CamStack pushes."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
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
    """Let the push hub create buttons as it learns about them."""
    entry.runtime_data.push.async_register_platform(
        Platform.BUTTON, CamStackButton, async_add_entities
    )


class CamStackButton(CamStackPushEntity, ButtonEntity):
    """A pushed command with no state — a reboot, or one PTZ step."""

    async def async_press(self) -> None:
        """Send the press payload the hub declared."""
        payload = str(self._component.get("payload_press", "PRESS"))
        await self._hub.async_send_command(self._command_topic, payload)
