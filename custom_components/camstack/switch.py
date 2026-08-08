"""Per-camera function switches.

These mirror the hub's own switch group, which stores nothing of its own: each
switch is a view onto the authority that already owned that function. So a
switch here writes exactly what the CamStack UI writes, and a second surface
cannot drift from the first.

Two consequences are load-bearing and are honoured below:

* a switch the hub reports as **unavailable** is rendered unavailable, never
  as "off" — "this camera cannot do that" and "an operator turned that off"
  are different facts and an automation must be able to tell them apart;
* `privacy-mask` is the one row whose ON means the picture is *obscured*, not
  that the function is working. The polarity is the hub's, and mirroring it
  verbatim is what keeps this toggle agreeing with every other client's.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import CamStackError
from .coordinator import CamStackConfigEntry, CamStackCoordinator, CamStackDevice
from .entity import CamStackEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CamStackConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create one entity per switch the hub offers for each camera."""
    coordinator = entry.runtime_data
    data = coordinator.data
    if data is None:
        return

    entities: list[SwitchEntity] = []
    for device in data.cameras():
        for entry_payload in data.switches.get(device.device_id, []):
            switch_id = entry_payload.get("id")
            if isinstance(switch_id, str):
                entities.append(CamStackCameraSwitch(coordinator, device, switch_id))
    async_add_entities(entities)


class CamStackCameraSwitch(CamStackEntity, SwitchEntity):
    """One function switch on one camera."""

    def __init__(
        self,
        coordinator: CamStackCoordinator,
        device: CamStackDevice,
        switch_id: str,
    ) -> None:
        """Bind the entity to a (camera, switch) pair."""
        super().__init__(coordinator, device, f"switch_{switch_id}")
        self._switch_id = switch_id

    @property
    def _record(self) -> dict[str, Any] | None:
        """Return the hub's current record for this switch."""
        if self.coordinator.data is None:
            return None
        for payload in self.coordinator.data.switches.get(self._device_id, []):
            if payload.get("id") == self._switch_id:
                return payload
        return None

    @property
    def name(self) -> str | None:
        """Use the hub's own label so both UIs name the function identically."""
        record = self._record
        label = record.get("label") if record else None
        return str(label) if isinstance(label, str) else self._switch_id

    @property
    def available(self) -> bool:
        """Report the hub's availability verdict for this specific switch."""
        if not super().available:
            return False
        record = self._record
        return record is not None and bool(record.get("available"))

    @property
    def is_on(self) -> bool | None:
        """Return the switch state exactly as the hub reports it."""
        record = self._record
        if record is None:
            return None
        enabled = record.get("enabled")
        return bool(enabled) if isinstance(enabled, bool) else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose what turning this off costs, and why it may be unavailable."""
        record = self._record or {}
        attributes: dict[str, Any] = {
            "camstack_device_id": self._device_id,
            "switch_id": self._switch_id,
        }
        if isinstance(record.get("costWhenOff"), str):
            attributes["cost_when_off"] = record["costWhenOff"]
        if isinstance(record.get("unavailableReason"), str):
            attributes["unavailable_reason"] = record["unavailableReason"]
        authority = record.get("authority")
        if isinstance(authority, dict) and isinstance(authority.get("kind"), str):
            attributes["authority"] = authority["kind"]
        return attributes

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the function on."""
        await self._async_write(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the function off."""
        await self._async_write(False)

    async def _async_write(self, enabled: bool) -> None:
        """Write the switch and publish what the hub confirmed.

        The hub answers with the whole recomposed group, so the entity never
        has to guess: a call that throws updates nothing, and Home Assistant's
        optimistic value is corrected by the next reconcile rather than being
        silently confirmed.
        """
        try:
            result = await self.coordinator.client.mutate(
                "pipelineOrchestrator.setCameraSwitch",
                {
                    "deviceId": self._device_id,
                    "switchId": self._switch_id,
                    "enabled": enabled,
                },
            )
        except CamStackError as err:
            raise HomeAssistantError(
                f"CamStack refused to set {self._switch_id} on device "
                f"{self._device_id}: {err}"
            ) from err

        group = result.get("switches") if isinstance(result, dict) else None
        if not isinstance(group, list) or self.coordinator.data is None:
            await self.coordinator.async_refresh_switches(self._device_id)
            return
        self.coordinator.data.switches[self._device_id] = [
            item for item in group if isinstance(item, dict)
        ]
        self.coordinator.async_set_updated_data(self.coordinator.data)
