"""Alarm panels built from the components CamStack pushes.

This one closes a hole rather than improving a projection. The hub has
been announcing `alarm_control_panel` components since the native export
shipped, and no released version of this integration built that platform —
so an exported alarm panel produced one warning line in the log and no
entity at all. Every installation that exported a panel has been missing
it.

`AlarmStateSchema` on the hub IS Home Assistant's vocabulary, verbatim, so
the state needs no mapping. What does need declaring is which arm modes
THIS panel accepts: a panel that cannot arm `vacation` must not offer the
button, and `availableModes` is the only thing that knows.
"""

from __future__ import annotations

from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
    CodeFormat,
)
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import CamStackConfigEntry
from .entity import CamStackPushEntity

_STATES: frozenset[str] = frozenset(state.value for state in AlarmControlPanelState)

# `AlarmArmModeSchema` on the hub → the feature Home Assistant gates the
# button on. The panel's own `availableModes` decides which of these are
# offered; a mode it does not list is a button that would be refused.
_MODE_FEATURES: dict[str, AlarmControlPanelEntityFeature] = {
    "home": AlarmControlPanelEntityFeature.ARM_HOME,
    "away": AlarmControlPanelEntityFeature.ARM_AWAY,
    "night": AlarmControlPanelEntityFeature.ARM_NIGHT,
    "vacation": AlarmControlPanelEntityFeature.ARM_VACATION,
    "custom_bypass": AlarmControlPanelEntityFeature.ARM_CUSTOM_BYPASS,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CamStackConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Let the push hub create alarm panels as it learns about them."""
    entry.runtime_data.push.async_register_platform(
        Platform.ALARM_CONTROL_PANEL, CamStackAlarmControlPanel, async_add_entities
    )


class CamStackAlarmControlPanel(CamStackPushEntity, AlarmControlPanelEntity):
    """A pushed alarm panel, offering the modes the panel accepts."""

    @property
    def _modes(self) -> list[str]:
        """Return the arm modes the hub declared for this panel."""
        raw = self._component.get("options")
        if not isinstance(raw, list):
            return []
        return [entry for entry in raw if isinstance(entry, str)]

    @property
    def supported_features(self) -> AlarmControlPanelEntityFeature:
        """Advertise one arm mode per mode the panel says it accepts."""
        features = AlarmControlPanelEntityFeature(0)
        if self._command_topic is None:
            return features
        for mode in self._modes:
            features |= _MODE_FEATURES.get(mode, AlarmControlPanelEntityFeature(0))
        if not self._modes:
            # A panel that declared nothing still arms: `away` is the mode
            # every alarm has, and offering none would make the entity
            # read-only on a hub that simply has not reported yet.
            features |= AlarmControlPanelEntityFeature.ARM_AWAY
        features |= AlarmControlPanelEntityFeature.TRIGGER
        return features

    @property
    def code_arm_required(self) -> bool:
        """Return whether the panel demands a PIN to arm."""
        return self._component.get("code_arm_required") is True

    @property
    def code_format(self) -> CodeFormat | None:
        """Return the shape of the PIN, when one is required."""
        return CodeFormat.NUMBER if self.code_arm_required else None

    @property
    def alarm_state(self) -> AlarmControlPanelState | None:
        """Return the panel's state, which is HA's own vocabulary."""
        value = self._value
        if value is None or value not in _STATES:
            return None
        return AlarmControlPanelState(value)

    async def async_alarm_disarm(self, code: str | None = None) -> None:
        """Disarm the panel."""
        await self._async_arm("DISARM")

    async def async_alarm_arm_home(self, code: str | None = None) -> None:
        """Arm for people staying in."""
        await self._async_arm("ARM_HOME")

    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        """Arm for an empty house."""
        await self._async_arm("ARM_AWAY")

    async def async_alarm_arm_night(self, code: str | None = None) -> None:
        """Arm for the night."""
        await self._async_arm("ARM_NIGHT")

    async def async_alarm_arm_vacation(self, code: str | None = None) -> None:
        """Arm for a long absence."""
        await self._async_arm("ARM_VACATION")

    async def async_alarm_arm_custom_bypass(self, code: str | None = None) -> None:
        """Arm with the panel's own bypass set."""
        await self._async_arm("ARM_CUSTOM_BYPASS")

    async def async_alarm_trigger(self, code: str | None = None) -> None:
        """Set the alarm off."""
        await self._async_arm("TRIGGER")

    async def _async_arm(self, action: str) -> None:
        """Send one panel instruction.

        Never optimistic, and this is the one where it matters most. The
        hub REFUSES an arm the panel turns down — a contact the mode covers
        is open — and showing `armed_away` over that refusal would leave
        Home Assistant reporting a secured house with an open door. The
        refusal raises; the entity does not move.

        The PIN is not forwarded. The capability takes an optional code and
        the transport has no field for one, so a code typed in Home
        Assistant is used by Home Assistant's own check and goes no
        further. A panel that needs a PIN at the hub is armed at the hub.
        """
        await self._async_command(action, optimistic=False)
