"""The exporter realigns by itself after any gap in what Home Assistant holds.

Production, 2026-10-19: one smaller announce removed most entities of several
cameras. The hub went on announcing the full set, but the integration only
built components NEW against its stored set, so nothing was ever rebuilt and a
reload did not help either. These tests pin the reconciliation against what
EXISTS in the entity registry, not against the last announcement.
"""

from __future__ import annotations

import copy
from unittest.mock import AsyncMock

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import ENTITY_CHANGE_CAMERA, push, setup_integration

REBOOT = "button.videocamera_ingresso_reboot"
TRIGGERED = "binary_sensor.videocamera_ingresso_triggered"
CAMERA = "camera.videocamera_ingresso"


async def test_a_component_missing_from_the_registry_is_recreated(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """The next announce rebuilds what the registry lost, set unchanged."""
    await setup_integration(hass, config_entry)
    await push(hass, config_entry, ENTITY_CHANGE_CAMERA)
    registry = er.async_get(hass)
    assert registry.async_get(REBOOT) is not None

    registry.async_remove(REBOOT)
    registry.async_remove(TRIGGERED)
    await hass.async_block_till_done()
    assert registry.async_get(REBOOT) is None

    # Identical to the stored set: nothing is "new" against it.
    await push(hass, config_entry, copy.deepcopy(ENTITY_CHANGE_CAMERA))

    assert registry.async_get(REBOOT) is not None
    assert registry.async_get(TRIGGERED) is not None
    assert hass.states.get(REBOOT) is not None


async def test_a_reload_restores_missing_entities(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """A reload alone rebuilds every announced component from the store."""
    await setup_integration(hass, config_entry)
    await push(hass, config_entry, ENTITY_CHANGE_CAMERA)
    registry = er.async_get(hass)
    registry.async_remove(REBOOT)
    await hass.async_block_till_done()

    assert await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()

    assert registry.async_get(REBOOT) is not None
    assert hass.states.get(REBOOT) is not None


async def test_the_primary_camera_is_recreated_when_it_is_missing(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """The API-built camera entity heals on the next membership read too."""
    runtime = await setup_integration(hass, config_entry)
    await push(hass, config_entry, ENTITY_CHANGE_CAMERA)
    registry = er.async_get(hass)
    assert registry.async_get(CAMERA) is not None

    registry.async_remove(CAMERA)
    await hass.async_block_till_done()
    await runtime.coordinator.async_refresh()
    await hass.async_block_till_done()

    assert registry.async_get(CAMERA) is not None


async def test_an_entity_the_user_disabled_stays_disabled(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """Self-healing rebuilds deleted entities, never undoes a user's choice."""
    await setup_integration(hass, config_entry)
    await push(hass, config_entry, ENTITY_CHANGE_CAMERA)
    registry = er.async_get(hass)
    registry.async_update_entity(REBOOT, disabled_by=er.RegistryEntryDisabler.USER)
    await hass.async_block_till_done()

    await push(hass, config_entry, copy.deepcopy(ENTITY_CHANGE_CAMERA))
    assert hass.states.get(REBOOT) is None
    assert registry.async_get(REBOOT).disabled_by is er.RegistryEntryDisabler.USER

    assert await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(REBOOT) is None
    assert registry.async_get(REBOOT).disabled_by is er.RegistryEntryDisabler.USER


async def test_a_partial_announce_never_removes(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """`partial: true` says nothing about absence, so nothing is removed."""
    await setup_integration(hass, config_entry)
    await push(hass, config_entry, ENTITY_CHANGE_CAMERA)

    partial = copy.deepcopy(ENTITY_CHANGE_CAMERA)
    partial["cmps"] = {
        key: value for key, value in partial["cmps"].items() if key != "button-reboot"
    }
    partial["partial"] = True
    await push(hass, config_entry, partial)

    assert er.async_get(hass).async_get(REBOOT) is not None


async def test_a_full_announce_removes_what_it_omits(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """`full: true` is authoritative: absent components are removed."""
    await setup_integration(hass, config_entry)
    await push(hass, config_entry, ENTITY_CHANGE_CAMERA)

    full = copy.deepcopy(ENTITY_CHANGE_CAMERA)
    full["cmps"] = {
        key: value for key, value in full["cmps"].items() if key != "button-reboot"
    }
    full["full"] = True
    await push(hass, config_entry, full)

    assert er.async_get(hass).async_get(REBOOT) is None
