"""Thermostats built from the components CamStack pushes.

A thermostat is the device that suffers most from a flat projection: mode,
setpoint, measured temperature, fan mode and preset are one machine, and
Home Assistant's thermostat card is the surface an operator actually uses.
Through the older platforms they arrived as five unrelated entities that
no card would draw.

The mode, fan-mode and preset VOCABULARIES come from the device, on the
slice: a thermostat that cannot do `heat_cool` never offers it, and a
vendor preset nobody could have hard-coded arrives with the entity.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import (
    ATTR_TARGET_TEMP_HIGH,
    ATTR_TARGET_TEMP_LOW,
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, Platform, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import CamStackConfigEntry
from .entity import CamStackPushEntity, as_float, as_int

# `HvacModeSchema` on the hub is Home Assistant's own vocabulary, verbatim.
# Checked rather than trusted: a mode from a newer hub must cost the entity
# its mode list, never its existence.
_HVAC_MODES: frozenset[str] = frozenset(mode.value for mode in HVACMode)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CamStackConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Let the push hub create thermostats as it learns about them."""
    entry.runtime_data.push.async_register_platform(
        Platform.CLIMATE, CamStackClimate, async_add_entities
    )


class CamStackClimate(CamStackPushEntity, ClimateEntity):
    """A pushed thermostat: a mode, its setpoints and what it measures."""

    @property
    def temperature_unit(self) -> str:
        """Return the unit every temperature topic carries.

        Declared by the hub rather than assumed. Every camstack setpoint is
        Celsius by the capability's own doc comment, and a component that
        guessed would be one firmware away from reporting 21 °F.
        """
        unit = self._component.get("temperature_unit")
        return (
            UnitOfTemperature.FAHRENHEIT if unit == "°F" else UnitOfTemperature.CELSIUS
        )

    @property
    def hvac_modes(self) -> list[HVACMode]:
        """Return the modes THIS unit accepts, as the device reported them."""
        raw = self._component.get("options")
        declared = (
            [entry for entry in raw if isinstance(entry, str)]
            if isinstance(raw, list)
            else []
        )
        modes = [HVACMode(entry) for entry in declared if entry in _HVAC_MODES]
        if modes:
            return modes
        # Nothing declared: offer the one it is in, never a guessed list. A
        # mode an operator can select and the unit rejects is worse than a
        # thermostat that only reports.
        current = self.hvac_mode
        return [current] if current is not None else []

    @property
    def hvac_mode(self) -> HVACMode | None:
        """Return the mode the hub pushed, if Home Assistant has it."""
        value = self._value
        if value is None:
            return None
        return HVACMode(value) if value in _HVAC_MODES else None

    @property
    def supported_features(self) -> ClimateEntityFeature:
        """Advertise exactly what the hub declared a route for."""
        features = ClimateEntityFeature(0)
        if self._can_command("target"):
            features |= ClimateEntityFeature.TARGET_TEMPERATURE
        if self._can_command("target_low") and self._can_command("target_high"):
            features |= ClimateEntityFeature.TARGET_TEMPERATURE_RANGE
        if self._can_command("target_humidity"):
            features |= ClimateEntityFeature.TARGET_HUMIDITY
        if self._can_command("fan_mode"):
            features |= ClimateEntityFeature.FAN_MODE
        if self._can_command("preset"):
            features |= ClimateEntityFeature.PRESET_MODE
        if self._command_topic is not None and HVACMode.OFF in self.hvac_modes:
            # Home Assistant's own turn_on/turn_off, which is what a voice
            # assistant and a script call. Both are `set_hvac_mode` here.
            features |= ClimateEntityFeature.TURN_ON | ClimateEntityFeature.TURN_OFF
        return features

    @property
    def current_temperature(self) -> float | None:
        """Return the temperature the unit measures."""
        return as_float(self._control_value("current_temperature"))

    @property
    def target_temperature(self) -> float | None:
        """Return the single setpoint."""
        return as_float(self._control_value("target"))

    @property
    def target_temperature_low(self) -> float | None:
        """Return the lower bound of a dual setpoint."""
        return as_float(self._control_value("target_low"))

    @property
    def target_temperature_high(self) -> float | None:
        """Return the upper bound of a dual setpoint."""
        return as_float(self._control_value("target_high"))

    @property
    def target_temperature_step(self) -> float | None:
        """Return the granularity the hub declared for the setpoint."""
        step = self._control("target").get("step")
        return float(step) if isinstance(step, (int, float)) else None

    @property
    def min_temp(self) -> float:
        """Return the lowest setpoint the hub will accept."""
        low = self._control("target").get("min")
        return float(low) if isinstance(low, (int, float)) else super().min_temp

    @property
    def max_temp(self) -> float:
        """Return the highest setpoint the hub will accept."""
        high = self._control("target").get("max")
        return float(high) if isinstance(high, (int, float)) else super().max_temp

    @property
    def current_humidity(self) -> int | None:
        """Return the humidity the unit measures."""
        return as_int(self._control_value("current_humidity"))

    @property
    def target_humidity(self) -> int | None:
        """Return the humidity setpoint."""
        return as_int(self._control_value("target_humidity"))

    @property
    def fan_mode(self) -> str | None:
        """Return the active fan mode."""
        return self._control_value("fan_mode") or None

    @property
    def fan_modes(self) -> list[str] | None:
        """Return the fan modes the device accepts."""
        return self._control_options("fan_mode")

    @property
    def preset_mode(self) -> str | None:
        """Return the active preset."""
        return self._control_value("preset") or None

    @property
    def preset_modes(self) -> list[str] | None:
        """Return the presets the device accepts."""
        return self._control_options("preset")

    def _control_options(self, name: str) -> list[str] | None:
        """Return one control's declared vocabulary, or None if it has none."""
        raw = self._control(name).get("options")
        if not isinstance(raw, list):
            return None
        options = [entry for entry in raw if isinstance(entry, str)]
        return options or None

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Ask the hub for a mode."""
        await self._async_command(str(hvac_mode))

    async def async_turn_on(self) -> None:
        """Leave `off` for the mode the unit was last in, or heat/cool."""
        for mode in self.hvac_modes:
            if mode is not HVACMode.OFF:
                await self.async_set_hvac_mode(mode)
                return

    async def async_turn_off(self) -> None:
        """Switch the unit off through its own mode vocabulary."""
        await self.async_set_hvac_mode(HVACMode.OFF)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Send whichever setpoints Home Assistant asked to change.

        A dual-setpoint call carries both bounds and no single value; a
        single-setpoint call carries only `temperature`. Sending the one
        that is absent would write `None` over a bound the operator did not
        touch.
        """
        single = kwargs.get(ATTR_TEMPERATURE)
        if single is not None:
            await self._async_command_control("target", _number(single))
        low = kwargs.get(ATTR_TARGET_TEMP_LOW)
        if low is not None:
            await self._async_command_control("target_low", _number(low))
        high = kwargs.get(ATTR_TARGET_TEMP_HIGH)
        if high is not None:
            await self._async_command_control("target_high", _number(high))

    async def async_set_humidity(self, humidity: int) -> None:
        """Send the humidity setpoint."""
        await self._async_command_control("target_humidity", str(int(humidity)))

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Send the fan mode."""
        await self._async_command_control("fan_mode", fan_mode)

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Send the preset."""
        await self._async_command_control("preset", preset_mode)


def _number(value: float) -> str:
    """Render a setpoint without turning 21 into 21.0 on the wire."""
    return str(int(value)) if float(value).is_integer() else str(value)
