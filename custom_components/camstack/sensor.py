"""Sensors built from the components CamStack pushes."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import CamStackConfigEntry
from .entity import CamStackPushEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CamStackConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Let the push hub create sensors as it learns about them."""
    entry.runtime_data.push.async_register_platform(
        Platform.SENSOR, CamStackSensor, async_add_entities
    )


class CamStackSensor(CamStackPushEntity, SensorEntity):
    """A pushed reading: a timestamp, a number or a label."""

    @property
    def device_class(self) -> SensorDeviceClass | None:
        """Return the device class the hub asked for, if HA has it."""
        raw = self._component.get("device_class")
        if not isinstance(raw, str):
            return None
        try:
            return SensorDeviceClass(raw)
        except ValueError:
            return None

    @property
    def state_class(self) -> SensorStateClass | None:
        """Return the state class the hub asked for, if HA has it."""
        raw = self._component.get("state_class")
        if not isinstance(raw, str):
            return None
        try:
            return SensorStateClass(raw)
        except ValueError:
            return None

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Return the unit the hub sent."""
        unit = self._component.get("unit_of_measurement")
        return unit if isinstance(unit, str) else None

    @property
    def native_value(self) -> datetime | float | str | None:
        """Return the pushed value in the type its device class requires.

        Everything arrives as a string, so the conversion happens here. A
        value that will not convert is dropped to `None` rather than passed
        through: Home Assistant raises on a timestamp sensor holding text, and
        it swallows that inside the state write — the entity then silently
        never updates again.
        """
        raw = self._value
        if raw is None:
            return None
        device_class = self.device_class
        if device_class is SensorDeviceClass.TIMESTAMP:
            return dt_util.parse_datetime(raw)
        if device_class is not None or self.native_unit_of_measurement is not None:
            try:
                return float(raw)
            except ValueError:
                return None
        return raw
