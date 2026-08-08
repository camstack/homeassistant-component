"""Binary sensors derived from CamStack capability slices."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CAP_DEVICE_STATUS, CAP_MOTION
from .coordinator import CamStackConfigEntry, CamStackCoordinator, CamStackDevice
from .entity import CamStackEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CamStackConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create a motion sensor for every device that carries a motion slice."""
    coordinator = entry.runtime_data
    data = coordinator.data
    if data is None:
        return

    entities: list[BinarySensorEntity] = []
    for device in data.devices.values():
        # The slice is the allowlist. A device type says almost nothing about
        # what a device can do; the capability it actually carries does.
        if data.slice_for(device.device_id, CAP_MOTION) is not None:
            entities.append(CamStackMotionSensor(coordinator, device))
        if data.slice_for(device.device_id, CAP_DEVICE_STATUS) is not None:
            entities.append(CamStackConnectivitySensor(coordinator, device))
    async_add_entities(entities)


class CamStackMotionSensor(CamStackEntity, BinarySensorEntity):
    """Motion, as reported by the hub's `motion` capability."""

    _attr_device_class = BinarySensorDeviceClass.MOTION
    _attr_translation_key = "motion"

    def __init__(
        self, coordinator: CamStackCoordinator, device: CamStackDevice
    ) -> None:
        """Bind the sensor to its device."""
        super().__init__(coordinator, device, "motion")

    @property
    def is_on(self) -> bool | None:
        """Return whether motion is currently detected."""
        motion = self.slice_for(CAP_MOTION)
        if motion is None:
            return None
        detected = motion.get("detected")
        return bool(detected) if isinstance(detected, bool) else None


class CamStackConnectivitySensor(CamStackEntity, BinarySensorEntity):
    """Whether the hub can currently reach the device."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_registry_enabled_default = False
    _attr_translation_key = "reachable"

    def __init__(
        self, coordinator: CamStackCoordinator, device: CamStackDevice
    ) -> None:
        """Bind the sensor to its device."""
        super().__init__(coordinator, device, "reachable")

    @property
    def available(self) -> bool:
        """Stay available while the hub is.

        A reachability sensor that goes unavailable when the device is
        unreachable can never report the thing it exists to report.
        """
        return self.coordinator.last_update_success and self.device is not None

    @property
    def is_on(self) -> bool | None:
        """Return whether the device is online."""
        status = self.slice_for(CAP_DEVICE_STATUS)
        if status is None:
            return None
        online = status.get("online")
        return bool(online) if isinstance(online, bool) else None
