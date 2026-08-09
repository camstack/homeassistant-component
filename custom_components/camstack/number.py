"""Numbers built from the components CamStack pushes."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity
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
    """Let the push hub create numbers as it learns about them."""
    entry.runtime_data.push.async_register_platform(
        Platform.NUMBER, CamStackNumber, async_add_entities
    )


class CamStackNumber(CamStackPushEntity, NumberEntity):
    """A pushed writable number, such as a light's brightness.

    The wire format carries no range, so Home Assistant's own default (0-100,
    step 1) applies. That is stated rather than guessed at: the moment the hub
    exports a number whose range is not a percentage, the range has to travel
    with it.
    """

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Return the unit the hub sent."""
        unit = self._component.get("unit_of_measurement")
        return unit if isinstance(unit, str) else None

    @property
    def native_value(self) -> float | None:
        """Return the pushed value, or None when it will not convert."""
        raw = self._value
        if raw is None:
            return None
        try:
            return float(raw)
        except ValueError:
            return None

    async def async_set_native_value(self, value: float) -> None:
        """Send the new value to the hub."""
        payload = str(int(value)) if value.is_integer() else str(value)
        await self._hub.async_send_command(self._command_topic, payload)
        topic = self._state_topic
        if topic is not None:
            self._hub.states[topic] = payload
            self.async_write_ha_state()
