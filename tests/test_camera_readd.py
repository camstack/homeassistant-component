"""Membership cameras are rebuilt when the registry row outlives the process.

A Home Assistant restart restores the entity registry and nothing else. The
membership camera used to be created only when that registry had no row, so
after a restart the row existed, nothing in this process was running it, and
the entity stayed without ``camstack_device_id``. The cards keep only entities
that carry that attribute, so the wall went empty.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.camstack.camera import CamStackCamera
from custom_components.camstack.const import DOMAIN
from custom_components.camstack.coordinator import CamStackDevice

from .conftest import DEVICE_LIST, EXPOSED_DEVICES, make_query_responder

SALON_ID = 590
SALON_UNIQUE_ID = f"{DOMAIN}_{SALON_ID}_camera"

SALON: dict[str, Any] = {
    "id": SALON_ID,
    "stableId": "hikvision:590",
    "addonId": "provider-hikvision",
    "type": "camera",
    "name": "Videocamera salone",
    "disabled": False,
    "isCamera": True,
}


def _export_salon(mock_client: AsyncMock) -> None:
    """Answer membership with the salone camera included."""
    listing = [*DEVICE_LIST, SALON]
    exposed = [
        *EXPOSED_DEVICES,
        {"deviceId": str(SALON_ID), "exposedAs": "Home Assistant"},
    ]
    base = make_query_responder(exposed)

    async def respond(path: str, payload: Any | None = None) -> Any:
        if path == "deviceManager.listAll":
            return listing
        return await base(path, payload)

    mock_client.query = AsyncMock(side_effect=respond)


def _remember_camera(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    *,
    disabled: bool,
) -> str:
    """Leave a registry row and no running entity, as a restart does."""
    config_entry.add_to_hass(hass)
    entry = er.async_get(hass).async_get_or_create(
        "camera",
        DOMAIN,
        SALON_UNIQUE_ID,
        config_entry=config_entry,
        disabled_by=er.RegistryEntryDisabler.USER if disabled else None,
        suggested_object_id="videocamera_salone",
    )
    return entry.entity_id


async def _start(hass: HomeAssistant, config_entry: MockConfigEntry) -> Any:
    """Load the integration. The entry is already in Home Assistant."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry.runtime_data


@contextmanager
def _counting_cameras() -> Iterator[list[int]]:
    """Record the device id of every membership camera this process builds."""
    built: list[int] = []
    original = CamStackCamera.__init__

    def _init(
        self: CamStackCamera,
        coordinator: Any,
        device: CamStackDevice,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        built.append(device.device_id)
        original(self, coordinator, device, *args, **kwargs)

    with patch.object(CamStackCamera, "__init__", _init):
        yield built


async def test_a_remembered_camera_is_added_again_after_a_restart(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """The registry already holds ``camstack_590_camera`` and nothing is live."""
    _export_salon(mock_client)
    entity_id = _remember_camera(hass, config_entry, disabled=False)
    assert hass.states.get(entity_id) is None

    with _counting_cameras() as built:
        await _start(hass, config_entry)

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.attributes["camstack_device_id"] == SALON_ID
    assert built.count(SALON_ID) == 1


async def test_a_camera_the_operator_disabled_is_not_readded(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """A disabled row is a choice. Rebuilding it would undo that choice."""
    _export_salon(mock_client)
    entity_id = _remember_camera(hass, config_entry, disabled=True)

    with _counting_cameras() as built:
        await _start(hass, config_entry)

    assert hass.states.get(entity_id) is None
    assert built.count(SALON_ID) == 0
    entry = er.async_get(hass).async_get(entity_id)
    assert entry is not None
    assert entry.disabled_by is er.RegistryEntryDisabler.USER


async def test_a_later_refresh_does_not_add_a_live_camera_twice(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """The camera added for the remembered row stays the only one."""
    _export_salon(mock_client)
    entity_id = _remember_camera(hass, config_entry, disabled=False)

    with _counting_cameras() as built:
        runtime = await _start(hass, config_entry)
        await runtime.coordinator.async_refresh()
        await hass.async_block_till_done()

    assert built.count(SALON_ID) == 1
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.attributes["camstack_device_id"] == SALON_ID
