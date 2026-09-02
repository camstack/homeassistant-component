"""Remove Home Assistant devices the hub has stopped exporting.

## Why this exists at all

The push protocol has four messages — `state_update`, `entity_change`,
`heartbeat`, `batch` — and none of them is a removal. The hub CANNOT tell Home
Assistant that a device is gone; it can only stop talking about it, and a device
nobody talks about just sits in the registry looking unavailable. Every camera
ever exported stayed for good, with its entities, its area and its history.

Home Assistant owns its registry, so the cleanup has to live here. The hub's
export membership is the authority; this module is the hand.

## The two-read gate, which is the whole design

A destructive action never gates on a single fallible read. A refresh that
SUCCEEDS can still answer with less than the truth — the hub's export addon
restarting, a partial listing, a membership not yet loaded — and an empty answer
is indistinguishable from "the operator removed everything". Acting on one would
delete the operator's fleet and its recorder history, unprompted.

So a device is removed only when **two consecutive successful refreshes agree**
that it is not live. One anomalous answer arms nothing and is forgotten on the
next pass; a real removal converges one cycle later. A failed refresh clears the
armed set entirely rather than leaving half a decision behind — the coordinator
raises on failure, so this module only ever sees successful reads, and
{@link StaleDevicePruner.async_forget} exists for the caller that knows better.

## What is never pruned

The synthetic devices (`SYNTHETIC_DEVICE_KEYS`) are pushed regardless of the
membership, so they are never in the listing — "not exported" is not "not live"
for them. And a device carrying no `camstack-` identifier is not ours to touch.
"""

from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr

from .const import DEVICE_KEY_PREFIX, DOMAIN, SYNTHETIC_DEVICE_KEYS
from .coordinator import CamStackConfigEntry, CamStackData

_LOGGER = logging.getLogger(__name__)


@callback
def camstack_keys(device: dr.DeviceEntry) -> set[str]:
    """Return the camstack device keys this registry entry carries."""
    return {
        identifier
        for domain, identifier in device.identifiers
        if domain == DOMAIN and identifier.startswith(DEVICE_KEY_PREFIX)
    }


@callback
def live_keys(data: CamStackData) -> set[str]:
    """Return every key the hub is currently speaking for."""
    return {device.device_key for device in data.devices.values()} | set(
        SYNTHETIC_DEVICE_KEYS
    )


class StaleDevicePruner:
    """Removes devices two consecutive successful refreshes call stale."""

    def __init__(self, hass: HomeAssistant, entry: CamStackConfigEntry) -> None:
        """Arm nothing: the first refresh can only ever be the first witness."""
        self._hass = hass
        self._entry = entry
        self._armed: set[str] = set()

    @callback
    def async_forget(self) -> None:
        """Drop the armed set, so the next refresh starts the count again."""
        self._armed = set()

    @callback
    def async_apply(self, data: CamStackData) -> list[str]:
        """Prune what this refresh AND the previous one both call stale.

        Returns the device ids removed, for the caller's log and the tests.
        """
        registry = dr.async_get(self._hass)
        live = live_keys(data)
        stale_now: set[str] = set()
        removable: list[tuple[str, set[str]]] = []

        for device in dr.async_entries_for_config_entry(
            registry, self._entry.entry_id
        ):
            ours = camstack_keys(device)
            if not ours or not ours.isdisjoint(live):
                continue
            stale_now |= ours
            if ours <= self._armed:
                removable.append((device.id, ours))

        removed: list[str] = []
        for device_id, keys in removable:
            registry.async_update_device(
                device_id, remove_config_entry_id=self._entry.entry_id
            )
            removed.append(device_id)
            _LOGGER.info(
                "Removed %s: the hub stopped exporting it, confirmed by two "
                "consecutive membership reads",
                ", ".join(sorted(keys)),
            )

        # Armed for the NEXT pass: what is stale now and was not just removed.
        self._armed = stale_now - {key for _, keys in removable for key in keys}
        return removed
