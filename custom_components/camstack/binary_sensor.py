"""Binary sensors built from the components CamStack pushes."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
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
    """Let the push hub create binary sensors as it learns about them."""
    entry.runtime_data.push.async_register_platform(
        Platform.BINARY_SENSOR, CamStackBinarySensor, async_add_entities
    )


class CamStackBinarySensor(CamStackPushEntity, BinarySensorEntity):
    """A pushed on/off value."""

    @property
    def device_class(self) -> BinarySensorDeviceClass | None:
        """Return the device class the hub asked for, if HA has it.

        An unknown class is dropped rather than raising: the hub adds entities
        without this component being released, so a class from a newer hub
        must cost the entity its icon, never its existence.
        """
        raw = self._component.get("device_class")
        if not isinstance(raw, str):
            return None
        try:
            return BinarySensorDeviceClass(raw)
        except ValueError:
            return None

    @property
    def is_on(self) -> bool | None:
        """Return the pushed value, or None while nothing has arrived."""
        return as_bool(self._value, self._component)
