"""Sensors derived from CamStack capability slices.

Each descriptor names the capability it reads, the field inside that slice, and
where its unit comes from. Units are read from the **live slice** wherever the
capability carries one, and fall back to the canonical unit only when it does
not — a Fahrenheit feed rendered as °C because the exporter hardcoded the
canonical unit is a wrong value, and a wrong value is worse than a missing one:
nothing about it looks broken.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfPressure,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CAP_BATTERY, CAP_ZONE_ANALYTICS
from .coordinator import CamStackConfigEntry, CamStackCoordinator, CamStackDevice
from .entity import CamStackEntity


@dataclass(frozen=True, kw_only=True)
class CamStackSensorDescription(SensorEntityDescription):
    """Describes one sensor and the slice field behind it."""

    cap_name: str
    value_fn: Callable[[dict[str, Any]], float | int | str | None]
    unit_fn: Callable[[dict[str, Any]], str | None] | None = None
    # True for the reading a device exists to provide, which then carries the
    # device's own name. A secondary reading (a battery level) must keep its
    # own label, or two sensors on one device collide into a `_2` suffix.
    primary: bool = False


def _number(field: str) -> Callable[[dict[str, Any]], float | int | None]:
    """Return a reader for a numeric slice field."""

    def read(slice_: dict[str, Any]) -> float | int | None:
        value = slice_.get(field)
        return value if isinstance(value, (int, float)) else None

    return read


def _text(field: str) -> Callable[[dict[str, Any]], str | None]:
    """Return a reader for a string slice field."""

    def read(slice_: dict[str, Any]) -> str | None:
        value = slice_.get(field)
        return value if isinstance(value, str) else None

    return read


def _temperature_unit(slice_: dict[str, Any]) -> str:
    """Return the unit the temperature slice reports, defaulting to Celsius."""
    unit = slice_.get("unit")
    if isinstance(unit, str) and unit.upper().startswith("F"):
        return UnitOfTemperature.FAHRENHEIT
    return UnitOfTemperature.CELSIUS


SENSOR_DESCRIPTIONS: tuple[CamStackSensorDescription, ...] = (
    CamStackSensorDescription(
        key="battery",
        cap_name=CAP_BATTERY,
        value_fn=_number("percentage"),
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    CamStackSensorDescription(
        key="temperature",
        primary=True,
        cap_name="temperature-sensor",
        value_fn=_number("celsius"),
        unit_fn=_temperature_unit,
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    CamStackSensorDescription(
        key="humidity",
        primary=True,
        cap_name="humidity-sensor",
        value_fn=_number("percent"),
        device_class=SensorDeviceClass.HUMIDITY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    CamStackSensorDescription(
        key="pressure",
        primary=True,
        cap_name="pressure-sensor",
        value_fn=_number("hpa"),
        device_class=SensorDeviceClass.PRESSURE,
        native_unit_of_measurement=UnitOfPressure.HPA,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    CamStackSensorDescription(
        key="numeric",
        primary=True,
        cap_name="numeric-sensor",
        value_fn=_number("value"),
        unit_fn=_text("unit"),
        state_class=SensorStateClass.MEASUREMENT,
    ),
    CamStackSensorDescription(
        key="enum",
        primary=True,
        cap_name="enum-sensor",
        value_fn=_text("value"),
    ),
    CamStackSensorDescription(
        key="objects",
        translation_key="objects",
        cap_name=CAP_ZONE_ANALYTICS,
        # A track is a time-bounded observation, not a thing with a current
        # state, so it is not an entity. The per-frame COUNT is — it is exactly
        # "a thing with a current state", and it graphs.
        value_fn=lambda slice_: (
            frame.get("totalObjects")
            if isinstance(frame := slice_.get("frame"), dict)
            and isinstance(frame.get("totalObjects"), int)
            else None
        ),
        state_class=SensorStateClass.MEASUREMENT,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CamStackConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create a sensor for every slice a device actually carries."""
    coordinator = entry.runtime_data
    data = coordinator.data
    if data is None:
        return

    async_add_entities(
        CamStackSensor(coordinator, device, description)
        for device in data.devices.values()
        for description in SENSOR_DESCRIPTIONS
        if data.slice_for(device.device_id, description.cap_name) is not None
    )


class CamStackSensor(CamStackEntity, SensorEntity):
    """A sensor reading one field of one capability slice."""

    entity_description: CamStackSensorDescription

    def __init__(
        self,
        coordinator: CamStackCoordinator,
        device: CamStackDevice,
        description: CamStackSensorDescription,
    ) -> None:
        """Bind the sensor to a (device, slice-field) pair."""
        super().__init__(coordinator, device, description.key)
        self.entity_description = description
        if description.primary:
            # A station's temperature reads better as the station's own name
            # than as a generic label bolted onto it. Only the primary reading
            # may claim it; everything else keeps its own label.
            self._attr_name = None

    @property
    def native_value(self) -> float | int | str | None:
        """Return the current value from the slice."""
        slice_ = self.slice_for(self.entity_description.cap_name)
        if slice_ is None:
            return None
        return self.entity_description.value_fn(slice_)

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Return the unit the slice reports, falling back to the canonical one."""
        unit_fn = self.entity_description.unit_fn
        if unit_fn is not None:
            slice_ = self.slice_for(self.entity_description.cap_name)
            if slice_ is not None and (unit := unit_fn(slice_)) is not None:
                return unit
        return self.entity_description.native_unit_of_measurement
