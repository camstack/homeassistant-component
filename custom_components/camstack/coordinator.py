"""Export membership — the one thing this integration still asks the hub for.

Every VALUE arrives by push. What cannot arrive by push is the live camera:
the hub's component set carries no `camera` entity, and a WebRTC session has
to be opened against the hub's numeric device id, which the push never sends.
So one read remains, and it answers exactly two questions: which devices the
hub exports to Home Assistant, and which of those are cameras.

**Membership is the HUB's authority, and this component only reads it.** A
second opt-in list kept here could disagree with the hub's, and two switches
that disagree are worse than one. It is read PINNED to the export addon:
`device-export` is a collection capability, and an unpinned read merges every
export provider — Alexa's devices and HomeKit's would arrive as ours. Cap
names are shared across vendors, so the aggregate is never the answer to
"what did the operator export to Home Assistant".

An unresolvable pin is refused by the hub with the valid ids enumerated, and
that refusal is raised as a setup error rather than swallowed. The failure
this guards against is the one that costs the most time: a broken integration
that looks exactly like an empty one.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    CamStackAuthError,
    CamStackClient,
    CamStackError,
    CamStackForbiddenError,
    CamStackRequestError,
)
from .const import (
    DEVICE_KEY_PREFIX,
    DOMAIN,
    EXPORT_PANEL_HINT,
    HA_EXPORT_ADDON_ID,
    MEMBERSHIP_INTERVAL,
)
from .push import CamStackPushHub

_LOGGER = logging.getLogger(__name__)

type CamStackConfigEntry = ConfigEntry[CamStackRuntimeData]

# Devices that came FROM Home Assistant are never exported back to it; doing so
# creates a device that mirrors itself across the bridge. The hub refuses them
# too — this is the second half of a guard that must hold on both sides.
ECHO_SOURCE_ADDON = "provider-homeassistant"


@dataclass(slots=True)
class CamStackRuntimeData:
    """Everything one loaded config entry owns."""

    client: CamStackClient
    push: CamStackPushHub
    coordinator: CamStackCoordinator


@dataclass(frozen=True, slots=True)
class CamStackDevice:
    """One exported device, as `deviceManager.listAll` describes it."""

    device_id: int
    name: str
    device_type: str
    addon_id: str
    is_camera: bool
    disabled: bool

    @property
    def device_key(self) -> str:
        """Return the Home Assistant device this device's entities live on.

        **The HUB owns this rule**, in `ha-export/topics.ts::deviceKeyFor`.
        This is the one place the component composes a key instead of taking
        one: every pushed entity gets its key verbatim off the wire
        (`entity_change.device_id`, which is `dev.ids[0]`), but the camera
        entity is built from the export MEMBERSHIP, and a listing entry
        carries no `dev` block to take it from.

        Two independent compositions agree only by convention, and a
        divergence is silent — the camera lands on a SECOND Home Assistant
        device beside the pushed one. So both sides assert the same literal
        for device 615: `test_the_device_key_matches_the_hub_rule` here, and
        "the cross-side identity contract" in the hub's
        `__tests__/entity-catalog.spec.ts`. Change one and the other is red.
        """
        return f"{DEVICE_KEY_PREFIX}{self.device_id}"

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> CamStackDevice | None:
        """Build a device from a listing entry, or None if it is unusable.

        The numeric `id` is the ONLY identity used, and a device without one
        is skipped rather than keyed on something else — every identity in
        this integration is derived from it, so an entry that lacks it has no
        entity that can be built.

        `stableId` is deliberately not read at all. It is the hub's
        addon-facing identity (`(addonId, stableId)` in the device manager),
        this integration was the only export that ever leaked it, and keeping
        a field for it would read as a second identity that still matters.
        """
        device_id = payload.get("id")
        if not isinstance(device_id, int):
            return None
        return cls(
            device_id=device_id,
            name=str(payload.get("name") or f"Device {device_id}"),
            device_type=str(payload.get("type") or "unknown"),
            addon_id=str(payload.get("addonId") or ""),
            is_camera=bool(payload.get("isCamera")),
            disabled=bool(payload.get("disabled")),
        )


@dataclass(slots=True)
class CamStackData:
    """The exported devices, and nothing else."""

    devices: dict[int, CamStackDevice] = field(default_factory=dict)

    def cameras(self) -> list[CamStackDevice]:
        """Return every exported camera."""
        return [device for device in self.devices.values() if device.is_camera]


class CamStackCoordinator(DataUpdateCoordinator[CamStackData]):
    """Reads which devices the hub exports to Home Assistant."""

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
            # Membership is structure, not value. Nothing here is a reading,
            # so this is a slow reconcile rather than a poll.
            update_interval=MEMBERSHIP_INTERVAL,
        )
        self.client = client

    async def _async_update_data(self) -> CamStackData:
        """Read the exported set and resolve it against the device list."""
        try:
            exported = await self._async_fetch_exported_ids()
            listing = await self.client.query("deviceManager.listAll", {})
        except CamStackForbiddenError as err:
            # A 403 is the hub ACCEPTING the credential and refusing the CALL.
            # `ConfigEntryAuthFailed` here would send the operator to reauth,
            # which replays the same credential — the loop that produced three
            # live sessions on 2026-08-09. So: a permanent setup error.
            #
            # WHY the hub refused is hub-side and not observable from here.
            # This component once shipped a fixed diagnosis ("re-linking mints
            # the same grant, update CamStack first") and it was wrong: the
            # refusal came from a scope-enforcement regression on the hub, and
            # the sentence sent the operator re-linking for nothing. Report the
            # hub's own message verbatim and add nothing causal to it.
            raise ConfigEntryError(
                f"{err}. The hub refused the request with the message above. "
                "Check the hub's version and the link's grants on the hub side"
            ) from err
        except CamStackRequestError as err:
            # The hub refused the request itself. Asking again cannot change
            # the answer, and reporting it as "no devices" would hide it.
            raise ConfigEntryError(str(err)) from err
        except CamStackAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except CamStackError as err:
            raise UpdateFailed(str(err)) from err

        data = CamStackData()
        for payload in listing if isinstance(listing, list) else []:
            if not isinstance(payload, dict):
                continue
            device = CamStackDevice.from_payload(payload)
            if device is None or device.device_id not in exported:
                continue
            if device.addon_id == ECHO_SOURCE_ADDON:
                continue
            data.devices[device.device_id] = device

        missing = len(exported) - len(data.devices)
        if exported and not data.devices:
            # Every exported id failed to match a live device. Silence here
            # reads as "the hub exports nothing", which is a different problem
            # with a different fix.
            _LOGGER.warning(
                "The hub exports %d device(s) to Home Assistant, but none of "
                "them appear in its device list. Exported ids: %s",
                len(exported),
                sorted(exported),
            )
        elif missing:
            _LOGGER.debug("%d exported id(s) matched no live device", missing)
        return data

    async def _async_fetch_exported_ids(self) -> set[int]:
        """Return the device ids the hub exports to Home Assistant.

        An empty list is a legitimate answer meaning "the operator has
        exported nothing yet", and it must NOT widen into importing
        everything — widening it is the defect this read exists to fix. A
        malformed answer, by contrast, is not an answer and fails the update
        so the coordinator retries instead of tearing every entity down.
        """
        result = await self.client.query(
            "deviceExport.listExposedDevices", {"addonId": HA_EXPORT_ADDON_ID}
        )
        if not isinstance(result, list):
            raise UpdateFailed(
                "deviceExport.listExposedDevices did not return a list; "
                f"got {type(result).__name__}"
            )

        exported: set[int] = set()
        for entry in result:
            if not isinstance(entry, dict):
                continue
            # The hub sends `deviceId` as a STRING on this capability while
            # `deviceManager.listAll` sends an int. Comparing the two without
            # converting silently matches nothing, which looks exactly like
            # "nothing is exported" and passes every test that does not use
            # the hub's real shapes.
            raw = entry.get("deviceId")
            try:
                exported.add(int(raw))
            except (TypeError, ValueError):
                _LOGGER.debug("Ignoring unparsable exported device id: %r", raw)

        if not exported:
            _LOGGER.warning(
                "No devices are exported to Home Assistant, so nothing will be "
                "imported. Choose them in %s. Devices exposed to Alexa or "
                "HomeKit are deliberately not included",
                EXPORT_PANEL_HINT,
            )
        return exported
