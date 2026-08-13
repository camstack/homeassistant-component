"""Water heaters built from the components CamStack pushes.

Home Assistant's water heater IS its operation mode — the entity state is
`eco` / `performance` / `off`, not a temperature. The capability's primary
entity is the MEASURED temperature, so the hub binds the entity's state to
the mode and hands the measurement over as a sub-control. Getting that
round the wrong way produces a water heater whose state is "48.5".
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.water_heater import (
    WaterHeaterEntity,
    WaterHeaterEntityFeature,
)
from homeassistant.const import ATTR_TEMPERATURE, Platform, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import CamStackConfigEntry
from .entity import CamStackPushEntity, as_bool, as_float


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CamStackConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Let the push hub create water heaters as it learns about them."""
    entry.runtime_data.push.async_register_platform(
        Platform.WATER_HEATER, CamStackWaterHeater, async_add_entities
    )


class CamStackWaterHeater(CamStackPushEntity, WaterHeaterEntity):
    """A pushed water heater: a mode, a setpoint and a measurement."""

    @property
    def temperature_unit(self) -> str:
        """Return the unit every temperature topic carries."""
        unit = self._component.get("temperature_unit")
        return (
            UnitOfTemperature.FAHRENHEIT if unit == "°F" else UnitOfTemperature.CELSIUS
        )

    @property
    def supported_features(self) -> WaterHeaterEntityFeature:
        """Advertise exactly what the hub declared a route for."""
        features = WaterHeaterEntityFeature(0)
        if self._can_command("target"):
            features |= WaterHeaterEntityFeature.TARGET_TEMPERATURE
        if self._command_topic is not None:
            features |= WaterHeaterEntityFeature.OPERATION_MODE
        if self._can_command("away"):
            features |= WaterHeaterEntityFeature.AWAY_MODE
        return features

    @property
    def current_operation(self) -> str | None:
        """Return the operation mode the unit is in."""
        return self._value or None

    @property
    def operation_list(self) -> list[str] | None:
        """Return the modes THIS unit accepts, as it reported them."""
        raw = self._component.get("options")
        if not isinstance(raw, list):
            return None
        options = [entry for entry in raw if isinstance(entry, str)]
        return options or None

    @property
    def current_temperature(self) -> float | None:
        """Return the temperature the unit measures."""
        return as_float(self._control_value("current_temperature"))

    @property
    def target_temperature(self) -> float | None:
        """Return the setpoint."""
        return as_float(self._control_value("target"))

    @property
    def min_temp(self) -> float:
        """Return the lowest setpoint THIS unit accepts."""
        low = self._control("target").get("min")
        return float(low) if isinstance(low, (int, float)) else super().min_temp

    @property
    def max_temp(self) -> float:
        """Return the highest setpoint THIS unit accepts."""
        high = self._control("target").get("max")
        return float(high) if isinstance(high, (int, float)) else super().max_temp

    @property
    def is_away_mode_on(self) -> bool | None:
        """Return whether the holiday setting is engaged."""
        return as_bool(self._control_value("away"), self._component)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Send the setpoint."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        value = float(temperature)
        await self._async_command_control(
            "target", str(int(value)) if value.is_integer() else str(value)
        )

    async def async_set_operation_mode(self, operation_mode: str) -> None:
        """Send the operation mode."""
        await self._async_command(operation_mode)

    async def async_turn_away_mode_on(self) -> None:
        """Engage the holiday setting."""
        await self._async_command_control("away", "true")

    async def async_turn_away_mode_off(self) -> None:
        """Leave the holiday setting."""
        await self._async_command_control("away", "false")
