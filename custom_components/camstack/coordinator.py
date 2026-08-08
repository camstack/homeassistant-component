"""Coordinator holding one view of the CamStack hub.

Two feeds, and they are not equivalent:

* the **event stream** (`live.onEvent`) is the live path — a motion pulse can
  auto-clear after 3 seconds, so a poll interval slow enough to be polite is
  slow enough to miss it entirely;
* the **reconcile** (`deviceManager.listAll` + `deviceState.getAllSnapshots`)
  is the correctness backstop. Hub events are telemetry and may be dropped, so
  a pump alone is not a contract. The reconcile also runs on every stream
  (re)connect, which is when a drop is most likely to have happened.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import CamStackAuthError, CamStackClient, CamStackError
from .const import (
    CAP_BATTERY,
    CAP_DEVICE_STATUS,
    CAP_DOORBELL,
    CAP_MOTION,
    CAP_ZONE_ANALYTICS,
    DOMAIN,
    EVENT_DEVICE_STATE_CHANGED,
    RECONCILE_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

type CamStackConfigEntry = ConfigEntry[CamStackCoordinator]

# Slices this integration renders. An event for anything else is discarded
# rather than applied: `audio-metrics` alone arrives several times a second per
# camera, and waking every entity for a slice nothing displays is pure cost.
TRACKED_CAPS: frozenset[str] = frozenset(
    {CAP_DEVICE_STATUS, CAP_MOTION, CAP_DOORBELL, CAP_BATTERY, CAP_ZONE_ANALYTICS}
)

# Devices that came FROM Home Assistant are never exported back to it; doing so
# creates a device that mirrors itself across the bridge.
ECHO_SOURCE_ADDON = "provider-homeassistant"

_STREAM_BACKOFF_START = 5
_STREAM_BACKOFF_MAX = 300


@dataclass(frozen=True, slots=True)
class CamStackDevice:
    """One device as the hub describes it."""

    device_id: int
    name: str
    device_type: str
    addon_id: str
    is_camera: bool
    parent_device_id: int | None
    disabled: bool
    model: str | None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> CamStackDevice | None:
        """Build a device from a `deviceManager.listAll` entry, or None."""
        device_id = payload.get("id")
        if not isinstance(device_id, int):
            return None
        source = payload.get("sourceInfo")
        model = source.get("system") if isinstance(source, dict) else None
        parent = payload.get("parentDeviceId")
        return cls(
            device_id=device_id,
            name=str(payload.get("name") or f"Device {device_id}"),
            device_type=str(payload.get("type") or "unknown"),
            addon_id=str(payload.get("addonId") or ""),
            is_camera=bool(payload.get("isCamera")),
            parent_device_id=parent if isinstance(parent, int) else None,
            disabled=bool(payload.get("disabled")),
            model=str(model) if isinstance(model, str) else None,
        )


@dataclass(slots=True)
class CamStackData:
    """The whole hub view a platform reads from."""

    devices: dict[int, CamStackDevice] = field(default_factory=dict)
    slices: dict[int, dict[str, dict[str, Any]]] = field(default_factory=dict)
    switches: dict[int, list[dict[str, Any]]] = field(default_factory=dict)

    def slice_for(self, device_id: int, cap_name: str) -> dict[str, Any] | None:
        """Return one capability slice, or None when the device does not carry it."""
        return self.slices.get(device_id, {}).get(cap_name)

    def cameras(self) -> list[CamStackDevice]:
        """Return every exportable camera."""
        return [device for device in self.devices.values() if device.is_camera]

    def root_device_id(self, device_id: int) -> int:
        """Return the root of the parent chain, stopping before a hub ancestor.

        One CamStack device tree becomes one Home Assistant device. The chain
        stops before a `hub`-type ancestor so that, for example, eight weather
        sub-stations stay eight devices instead of collapsing into their gateway.
        """
        seen: set[int] = set()
        current = device_id
        while current not in seen:
            seen.add(current)
            device = self.devices.get(current)
            if device is None or device.parent_device_id is None:
                return current
            parent = self.devices.get(device.parent_device_id)
            if parent is None or parent.device_type == "hub":
                return current
            current = parent.device_id
        return current


class CamStackCoordinator(DataUpdateCoordinator[CamStackData]):
    """Reconciles hub state on a timer and patches it from the event stream."""

    config_entry: CamStackConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: CamStackConfigEntry,
        client: CamStackClient,
    ) -> None:
        """Set the coordinator up without performing any I/O."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=RECONCILE_INTERVAL,
        )
        self.client = client
        self._stream_task: asyncio.Task[None] | None = None

    async def _async_update_data(self) -> CamStackData:
        """Perform a full reconcile against the hub."""
        try:
            listing = await self.client.query("deviceManager.listAll", {})
            snapshots = await self.client.query("deviceState.getAllSnapshots", {})
        except CamStackAuthError as err:
            # Surfacing this as UpdateFailed would retry forever against a
            # credential the hub has already refused.
            raise ConfigEntryAuthFailed(str(err)) from err
        except CamStackError as err:
            raise UpdateFailed(str(err)) from err

        data = CamStackData()
        for payload in listing if isinstance(listing, list) else []:
            if not isinstance(payload, dict):
                continue
            device = CamStackDevice.from_payload(payload)
            if device is None or device.addon_id == ECHO_SOURCE_ADDON:
                continue
            data.devices[device.device_id] = device

        data.slices = _normalise_snapshots(snapshots, data.devices.keys())
        data.switches = await self._async_fetch_switches(data.cameras())
        return data

    async def _async_fetch_switches(
        self, cameras: list[CamStackDevice]
    ) -> dict[int, list[dict[str, Any]]]:
        """Read the per-camera function switches for every camera."""
        results = await asyncio.gather(
            *(
                self.client.query(
                    "pipelineOrchestrator.getCameraSwitches",
                    {"deviceId": camera.device_id},
                )
                for camera in cameras
            ),
            return_exceptions=True,
        )
        switches: dict[int, list[dict[str, Any]]] = {}
        for camera, result in zip(cameras, results, strict=True):
            if isinstance(result, BaseException):
                # One camera refusing must not blank the other fifteen.
                _LOGGER.debug(
                    "Camera switches unavailable for device %s: %s",
                    camera.device_id,
                    result,
                )
                continue
            group = result.get("switches") if isinstance(result, dict) else None
            if isinstance(group, list):
                switches[camera.device_id] = [
                    item for item in group if isinstance(item, dict)
                ]
        return switches

    async def async_refresh_switches(self, device_id: int) -> None:
        """Re-read one camera's switches and publish the result.

        Called after a write so the entity reflects what the hub confirmed
        rather than what the user asked for.
        """
        if self.data is None:
            return
        try:
            result = await self.client.query(
                "pipelineOrchestrator.getCameraSwitches", {"deviceId": device_id}
            )
        except CamStackError as err:
            _LOGGER.debug("Could not refresh switches for %s: %s", device_id, err)
            return
        group = result.get("switches") if isinstance(result, dict) else None
        if not isinstance(group, list):
            return
        self.data.switches[device_id] = [
            item for item in group if isinstance(item, dict)
        ]
        self.async_set_updated_data(self.data)

    def start_event_stream(self) -> None:
        """Start the background subscription to the hub's event stream."""
        if self._stream_task is not None:
            return
        self._stream_task = self.config_entry.async_create_background_task(
            self.hass, self._run_event_stream(), name=f"{DOMAIN}-events"
        )

    async def _run_event_stream(self) -> None:
        """Consume hub events forever, reconnecting with backoff."""
        backoff = _STREAM_BACKOFF_START
        while True:
            try:
                # Filter at the hub. Measured on a live cluster: 1 044 events
                # arrived in 12 seconds and exactly ONE of them would have
                # changed an entity. Without the category filter this stream
                # ships pipeline traces and broker metrics across the network
                # for the integration to throw away.
                async for event in self.client.subscribe_events(
                    EVENT_DEVICE_STATE_CHANGED
                ):
                    backoff = _STREAM_BACKOFF_START
                    self._apply_event(event)
            except asyncio.CancelledError:
                raise
            except CamStackError as err:
                _LOGGER.debug("CamStack event stream dropped: %s", err)
            except Exception:
                _LOGGER.exception("Unexpected failure in the CamStack event stream")

            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _STREAM_BACKOFF_MAX)
            # A reconnect is exactly when a dropped event is most likely, so the
            # stream never resumes without a reconcile behind it.
            await self.async_request_refresh()

    def _apply_event(self, event: dict[str, Any]) -> None:
        """Patch the cached slice map from one hub event."""
        if event.get("category") != EVENT_DEVICE_STATE_CHANGED:
            return
        payload = event.get("data")
        if not isinstance(payload, dict):
            return
        device_id = payload.get("deviceId")
        cap_name = payload.get("capName")
        new_slice = payload.get("slice")
        if (
            not isinstance(device_id, int)
            or not isinstance(cap_name, str)
            or cap_name not in TRACKED_CAPS
            or not isinstance(new_slice, dict)
        ):
            return
        data = self.data
        if data is None or device_id not in data.devices:
            return
        data.slices.setdefault(device_id, {})[cap_name] = new_slice
        self.async_set_updated_data(data)


def _normalise_snapshots(
    snapshots: Any, known_ids: Any
) -> dict[int, dict[str, dict[str, Any]]]:
    """Turn the hub's string-keyed snapshot map into an int-keyed one."""
    known = set(known_ids)
    normalised: dict[int, dict[str, dict[str, Any]]] = {}
    if not isinstance(snapshots, dict):
        return normalised
    for raw_id, caps in snapshots.items():
        try:
            device_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if device_id not in known or not isinstance(caps, dict):
            continue
        normalised[device_id] = {
            name: value for name, value in caps.items() if isinstance(value, dict)
        }
    return normalised
