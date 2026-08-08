"""Shared base for every CamStack entity."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.typing import UndefinedType
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CAP_DEVICE_STATUS, DOMAIN, MANUFACTURER
from .coordinator import CamStackCoordinator, CamStackDevice


class CamStackEntity(CoordinatorEntity[CamStackCoordinator]):
    """An entity backed by one capability slice on one CamStack device."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: CamStackCoordinator,
        device: CamStackDevice,
        entity_key: str,
    ) -> None:
        """Bind the entity to a device and give it a stable unique id."""
        super().__init__(coordinator)
        self._device_id = device.device_id
        self._entity_key = entity_key
        self._attr_unique_id = f"{DOMAIN}_{device.device_id}_{entity_key}"

    @property
    def device(self) -> CamStackDevice | None:
        """Return the current record for this entity's device."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.devices.get(self._device_id)

    @property
    def device_info(self) -> DeviceInfo:
        """Attach the entity to the root of its CamStack device tree."""
        data = self.coordinator.data
        device = self.device
        if data is None or device is None:
            return DeviceInfo(
                identifiers={(DOMAIN, str(self._device_id))},
                manufacturer=MANUFACTURER,
            )
        root_id = data.root_device_id(self._device_id)
        # The root is very often NOT exported itself, so it is looked up in the
        # topology rather than the exported set.
        root = data.topology.get(root_id, device)
        return DeviceInfo(
            identifiers={(DOMAIN, str(root_id))},
            name=root.name,
            manufacturer=MANUFACTURER,
            model=root.model or root.device_type,
        )

    @property
    def _sub_device_name(self) -> str | None:
        """Return this device's own name when it is not the root of its tree.

        Several CamStack devices collapse into one Home Assistant device, so
        without this every sensor under a weather station would be named after
        the station and they would collide with each other.
        """
        data = self.coordinator.data
        device = self.device
        if data is None or device is None:
            return None
        if data.root_device_id(self._device_id) == self._device_id:
            return None
        return device.name

    @property
    def name(self) -> str | UndefinedType | None:
        """Qualify the platform's name with the sub-device it belongs to."""
        base = super().name
        sub = self._sub_device_name
        if sub is None:
            return base
        if base is None or isinstance(base, UndefinedType):
            return sub
        return f"{sub} {base}"

    @property
    def use_device_name(self) -> bool:
        """Never let a sub-device entity be named after the whole tree.

        Home Assistant builds the entity_id from the device name alone when an
        entity claims to BE the device. For a sensor that is one of several
        under a shared parent that is wrong twice: it hides which sub-device
        the reading came from, and two such sensors collide into `_2`.
        """
        if self._sub_device_name is not None:
            return False
        return super().use_device_name

    def slice_for(self, cap_name: str) -> dict[str, Any] | None:
        """Return one of this device's capability slices."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.slice_for(self._device_id, cap_name)

    @property
    def available(self) -> bool:
        """Report availability from the hub connection AND the device itself.

        A device the hub says is offline must not keep serving its last value:
        a stale reading that looks healthy is a worse failure than a gap.
        """
        if not super().available:
            return False
        device = self.device
        if device is None or device.disabled:
            return False
        status = self.slice_for(CAP_DEVICE_STATUS)
        if status is None:
            return True
        return bool(status.get("online", True))
