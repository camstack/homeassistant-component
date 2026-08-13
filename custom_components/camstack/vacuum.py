"""Vacuums built from the components CamStack pushes.

Battery, cleaning progress and the vendor error string are deliberately
NOT part of this entity: Home Assistant moved the vacuum battery to a
separate `sensor` and never had anywhere to show the other two. The hub
keeps announcing them as their own sensors, which is why the operator does
not lose them by gaining a vacuum.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.vacuum import (
    StateVacuumEntity,
    VacuumActivity,
    VacuumEntityFeature,
)
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import CamStackConfigEntry
from .entity import CamStackPushEntity

# `VacuumStateSchema` on the hub → what Home Assistant has.
#
# `drying` is the one that does not map. A mop dock running its dryer is
# still working, so it folds into CLEANING rather than being dropped: an
# unmapped state would leave the entity on its LAST one, which is the
# reading an automation would act on for as long as the dry cycle lasts.
_ACTIVITIES: dict[str, VacuumActivity] = {
    "idle": VacuumActivity.IDLE,
    "cleaning": VacuumActivity.CLEANING,
    "paused": VacuumActivity.PAUSED,
    "returning": VacuumActivity.RETURNING,
    "docked": VacuumActivity.DOCKED,
    "drying": VacuumActivity.CLEANING,
    "error": VacuumActivity.ERROR,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CamStackConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Let the push hub create vacuums as it learns about them."""
    entry.runtime_data.push.async_register_platform(
        Platform.VACUUM, CamStackVacuum, async_add_entities
    )


class CamStackVacuum(CamStackPushEntity, StateVacuumEntity):
    """A pushed robot: what it is doing, and the verbs it accepts."""

    @property
    def supported_features(self) -> VacuumEntityFeature:
        """Advertise exactly what the hub declared a route for."""
        features = VacuumEntityFeature.STATE
        if self._has_verb("start"):
            features |= VacuumEntityFeature.START
        if self._has_verb("pause"):
            features |= VacuumEntityFeature.PAUSE
        if self._has_verb("stop"):
            features |= VacuumEntityFeature.STOP
        if self._has_verb("return_to_base"):
            features |= VacuumEntityFeature.RETURN_HOME
        if self._has_verb("locate"):
            features |= VacuumEntityFeature.LOCATE
        if self._can_command("fan_speed"):
            features |= VacuumEntityFeature.FAN_SPEED
        return features

    @property
    def activity(self) -> VacuumActivity | None:
        """Return what the robot is doing."""
        value = self._value
        return None if value is None else _ACTIVITIES.get(value)

    @property
    def fan_speed(self) -> str | None:
        """Return the active suction setting."""
        return self._control_value("fan_speed") or None

    @property
    def fan_speed_list(self) -> list[str]:
        """Return the suction settings THIS robot accepts."""
        raw = self._control("fan_speed").get("options")
        if not isinstance(raw, list):
            return []
        return [entry for entry in raw if isinstance(entry, str)]

    async def async_start(self, **kwargs: Any) -> None:
        """Start or resume the clean."""
        await self._async_verb("start", "START")

    async def async_pause(self, **kwargs: Any) -> None:
        """Hold where it is."""
        await self._async_verb("pause", "PAUSE")

    async def async_stop(self, **kwargs: Any) -> None:
        """End the clean."""
        await self._async_verb("stop", "STOP")

    async def async_return_to_base(self, **kwargs: Any) -> None:
        """Send it home."""
        await self._async_verb("return_to_base", "RETURN_TO_BASE")

    async def async_locate(self, **kwargs: Any) -> None:
        """Make it announce where it is."""
        await self._async_verb("locate", "LOCATE")

    async def async_set_fan_speed(self, fan_speed: str, **kwargs: Any) -> None:
        """Send the suction setting."""
        await self._async_command_control("fan_speed", fan_speed)
