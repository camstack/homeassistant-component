"""Humidifiers built from the components CamStack pushes."""

from __future__ import annotations

from typing import Any

from homeassistant.components.humidifier import (
    HumidifierAction,
    HumidifierDeviceClass,
    HumidifierEntity,
    HumidifierEntityFeature,
)
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import CamStackConfigEntry
from .entity import CamStackPushEntity, as_bool, as_int

_ACTIONS: frozenset[str] = frozenset(action.value for action in HumidifierAction)
_DEVICE_CLASSES: frozenset[str] = frozenset(
    device_class.value for device_class in HumidifierDeviceClass
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CamStackConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Let the push hub create humidifiers as it learns about them."""
    entry.runtime_data.push.async_register_platform(
        Platform.HUMIDIFIER, CamStackHumidifier, async_add_entities
    )


class CamStackHumidifier(CamStackPushEntity, HumidifierEntity):
    """A pushed humidifier: on/off, a target, and what it measures."""

    @property
    def device_class(self) -> HumidifierDeviceClass | None:
        """Return whether this adds moisture or removes it."""
        raw = self._component.get("device_class")
        if not isinstance(raw, str) or raw not in _DEVICE_CLASSES:
            return None
        return HumidifierDeviceClass(raw)

    @property
    def supported_features(self) -> HumidifierEntityFeature:
        """Advertise the mode list only when the hub declared a route."""
        return (
            HumidifierEntityFeature.MODES
            if self._can_command("mode")
            else HumidifierEntityFeature(0)
        )

    @property
    def is_on(self) -> bool | None:
        """Return whether the unit is running."""
        return as_bool(self._value, self._component)

    @property
    def current_humidity(self) -> int | None:
        """Return the humidity the unit measures."""
        return as_int(self._control_value("current_humidity"))

    @property
    def target_humidity(self) -> int | None:
        """Return the humidity setpoint."""
        return as_int(self._control_value("target_humidity"))

    @property
    def min_humidity(self) -> int:
        """Return the lowest setpoint THIS unit accepts."""
        low = self._control("target_humidity").get("min")
        return int(low) if isinstance(low, (int, float)) else super().min_humidity

    @property
    def max_humidity(self) -> int:
        """Return the highest setpoint THIS unit accepts."""
        high = self._control("target_humidity").get("max")
        return int(high) if isinstance(high, (int, float)) else super().max_humidity

    @property
    def action(self) -> HumidifierAction | None:
        """Return what the unit is doing right now, if HA has the word."""
        value = self._control_value("action")
        if value is None or value not in _ACTIONS:
            return None
        return HumidifierAction(value)

    @property
    def mode(self) -> str | None:
        """Return the active mode."""
        return self._control_value("mode") or None

    @property
    def available_modes(self) -> list[str] | None:
        """Return the modes the device accepts, as it reported them."""
        raw = self._control("mode").get("options")
        if not isinstance(raw, list):
            return None
        options = [entry for entry in raw if isinstance(entry, str)]
        return options or None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Start the unit."""
        await self._async_command("true")

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Stop the unit."""
        await self._async_command("false")

    async def async_set_humidity(self, humidity: int) -> None:
        """Send the humidity setpoint."""
        await self._async_command_control("target_humidity", str(int(humidity)))

    async def async_set_mode(self, mode: str) -> None:
        """Send the mode."""
        await self._async_command_control("mode", mode)
