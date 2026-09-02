"""Deleting a camstack device that the hub no longer exports.

Home Assistant draws no delete button for a device unless its integration
implements `async_remove_config_entry_device`. This one did not, so the
operator's only recourse was deleting the whole integration, and every camera
ever exported stayed in the registry for good — with its entities, its area and
its history — long after the hub stopped exporting it.

The three cases below are the whole contract: a stale device goes, a live one is
refused, and an unanswered coordinator refuses everything rather than reading an
absent listing as "the hub exports nothing".
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from homeassistant.helpers.device_registry import DeviceEntry

from custom_components.camstack import async_remove_config_entry_device
from custom_components.camstack.const import DOMAIN, SYNTHETIC_DEVICE_KEYS
from custom_components.camstack.coordinator import CamStackData, CamStackDevice


def _device(device_id: int, name: str) -> CamStackDevice:
    return CamStackDevice(
        device_id=device_id,
        name=name,
        device_type="camera",
        addon_id="reolink",
        is_camera=True,
        disabled=False,
    )


def _entry(devices: list[CamStackDevice] | None) -> Any:
    """Build a config entry whose coordinator answers with `devices`, or not at all."""
    entry = MagicMock()
    entry.runtime_data.coordinator.data = (
        None
        if devices is None
        else CamStackData(devices={d.device_id: d for d in devices})
    )
    return entry


def _registry_device(key: str) -> DeviceEntry:
    entry = MagicMock(spec=DeviceEntry)
    entry.identifiers = {(DOMAIN, key)}
    return entry


@pytest.mark.asyncio
async def test_a_device_the_hub_no_longer_exports_can_be_deleted() -> None:
    entry = _entry([_device(615, "Ingresso")])

    assert (
        await async_remove_config_entry_device(
            None, entry, _registry_device("camstack-590")
        )
        is True
    )


@pytest.mark.asyncio
async def test_a_device_the_hub_still_exports_is_refused() -> None:
    """Refuse a device the hub still exports.

    Deleting it would leave the hub pushing state at a registry entry that is
    no longer there. The membership is the authority; this hook is only the
    gesture.
    """
    entry = _entry([_device(615, "Ingresso")])

    assert (
        await async_remove_config_entry_device(
            None, entry, _registry_device("camstack-615")
        )
        is False
    )


@pytest.mark.asyncio
async def test_an_unanswered_coordinator_refuses_every_deletion() -> None:
    """Refuse everything while the listing is unknown.

    `data is None` means the listing has not been read, NOT that the hub
    exports nothing. Reading it as the latter would authorise deleting the
    operator's entire fleet off one transient failure — and this is a one-shot
    destructive action, so unknown is a refusal.
    """
    entry = _entry(None)

    assert (
        await async_remove_config_entry_device(
            None, entry, _registry_device("camstack-590")
        )
        is False
    )


@pytest.mark.asyncio
async def test_a_device_that_is_not_ours_is_refused() -> None:
    """Another integration's device reaching this hook is not ours to delete."""
    entry = _entry([_device(615, "Ingresso")])
    foreign = MagicMock(spec=DeviceEntry)
    foreign.identifiers = {("othervendor", "whatever-1")}

    assert await async_remove_config_entry_device(None, entry, foreign) is False


@pytest.mark.asyncio
@pytest.mark.parametrize("key", sorted(SYNTHETIC_DEVICE_KEYS))
async def test_a_live_synthetic_device_is_refused(key: str) -> None:
    """Refuse the notification centre and the server.

    They are pushed regardless of the export membership, so they never appear
    in the listing — "not exported" is not "not live" for them. Without this the
    hook offered to delete the two devices carrying most of the operator's
    entities, and the next push would simply build them again.
    """
    entry = _entry([_device(615, "Ingresso")])

    assert (
        await async_remove_config_entry_device(None, entry, _registry_device(key))
        is False
    )
