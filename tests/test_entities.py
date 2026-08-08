"""Behavioural tests for the entities the integration creates."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.camstack.const import DOMAIN
from custom_components.camstack.coordinator import CamStackCoordinator

from .conftest import SWITCH_GROUP


async def setup_integration(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> CamStackCoordinator:
    """Load the integration and return its coordinator."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry.runtime_data


async def test_camera_entity_is_created_for_each_camera(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    await setup_integration(hass, config_entry)
    state = hass.states.get("camera.videocamera_ingresso")
    assert state is not None
    assert state.attributes["camstack_device_id"] == 615


async def test_motion_sensor_reflects_the_slice(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    await setup_integration(hass, config_entry)
    assert hass.states.get("binary_sensor.videocamera_ingresso_motion").state == (
        STATE_OFF
    )


async def test_a_motion_event_reaches_the_entity_without_a_poll(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """Prove motion arrives by push, not by poll.

    A motion pulse can auto-clear in 3s; the event stream is the only feed
    fast enough to see it, so this must not depend on the reconcile.
    """
    coordinator = await setup_integration(hass, config_entry)
    mock_client.query.reset_mock()

    coordinator._apply_event(
        {
            "category": "device.state-changed",
            "data": {
                "deviceId": 615,
                "capName": "motion",
                "slice": {"detected": True, "autoClearAfterMs": 3000},
            },
        }
    )
    await hass.async_block_till_done()

    assert hass.states.get("binary_sensor.videocamera_ingresso_motion").state == (
        STATE_ON
    )
    assert mock_client.query.call_count == 0


async def test_a_high_rate_slice_never_wakes_the_entities(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """Ignore a slice nothing renders.

    `audio-metrics` arrives several times a second per camera and nothing
    renders it; applying it would be pure cost.
    """
    coordinator = await setup_integration(hass, config_entry)
    before = coordinator.data.slices[615]["audio-metrics"]["peakDbfs"]

    coordinator._apply_event(
        {
            "category": "device.state-changed",
            "data": {
                "deviceId": 615,
                "capName": "audio-metrics",
                "slice": {"peakDbfs": -1.0},
            },
        }
    )
    assert coordinator.data.slices[615]["audio-metrics"]["peakDbfs"] == before


async def test_devices_that_came_from_home_assistant_are_not_exported_back(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    coordinator = await setup_integration(hass, config_entry)
    assert 1027 not in coordinator.data.devices
    assert hass.states.get("binary_sensor.someone_reachable") is None


async def test_one_device_tree_becomes_one_home_assistant_device(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """Group a device tree onto one Home Assistant device.

    A siren under a camera is a control on the camera's card, but a station
    under a hub stays its own device.
    """
    coordinator = await setup_integration(hass, config_entry)
    assert coordinator.data.root_device_id(585) == 615
    # 290 sits under the hub 289, so the chain stops at 290 rather than
    # collapsing every sub-station into the gateway.
    assert coordinator.data.root_device_id(291) == 290
    assert coordinator.data.root_device_id(290) == 290


async def test_sensor_unit_comes_from_the_live_slice(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    await setup_integration(hass, config_entry)
    state = hass.states.get("sensor.ecowitt_gateway_gateway_indoor_temperature")
    assert state.state == "23.7"
    assert state.attributes["unit_of_measurement"] == "°C"


async def test_a_fahrenheit_slice_is_not_relabelled_as_celsius(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """The canonical unit must never override what the feed actually reports.

    Home Assistant displays temperatures in the user's own unit system, so the
    proof is not the displayed label — it is that 74.7 read as Fahrenheit
    CONVERTS to roughly 23.7 °C. Had the canonical °C been assumed, the state
    would still read 74.7 and nothing would look broken.
    """
    coordinator = await setup_integration(hass, config_entry)
    coordinator.data.slices[291]["temperature-sensor"] = {
        "celsius": 74.7,
        "unit": "F",
    }
    coordinator.async_set_updated_data(coordinator.data)
    await hass.async_block_till_done()

    state = hass.states.get("sensor.ecowitt_gateway_gateway_indoor_temperature")
    assert state.attributes["unit_of_measurement"] == "°C"
    assert float(state.state) == pytest.approx(23.7, abs=0.1)


async def test_switch_entities_mirror_the_hub_group(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    await setup_integration(hass, config_entry)
    assert hass.states.get("switch.videocamera_ingresso_camera").state == STATE_ON
    assert (
        hass.states.get("switch.videocamera_ingresso_object_detection_tracking").state
        == STATE_ON
    )


async def test_an_unavailable_switch_is_unavailable_not_off(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """Report an unavailable switch as unavailable.

    "this camera cannot do that" and "an operator turned it off" are
    different facts and an automation has to tell them apart.
    """
    await setup_integration(hass, config_entry)
    state = hass.states.get("switch.videocamera_ingresso_privacy_mask")
    assert state.state == STATE_UNAVAILABLE


async def test_turning_a_switch_off_writes_the_hub_and_echoes_its_answer(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    await setup_integration(hass, config_entry)
    confirmed = {
        **SWITCH_GROUP,
        "switches": [
            {**item, "enabled": False} if item["id"] == "object-detection" else item
            for item in SWITCH_GROUP["switches"]
        ],
    }
    mock_client.mutate.return_value = confirmed

    await hass.services.async_call(
        "switch",
        "turn_off",
        {"entity_id": "switch.videocamera_ingresso_object_detection_tracking"},
        blocking=True,
    )

    mock_client.mutate.assert_awaited_once_with(
        "pipelineOrchestrator.setCameraSwitch",
        {"deviceId": 615, "switchId": "object-detection", "enabled": False},
    )
    assert (
        hass.states.get("switch.videocamera_ingresso_object_detection_tracking").state
        == STATE_OFF
    )


async def test_a_refused_write_raises_and_does_not_confirm_the_value(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """An optimistic update that the hub never accepted is a lie."""
    from custom_components.camstack.api import CamStackApiError

    await setup_integration(hass, config_entry)
    mock_client.mutate.side_effect = CamStackApiError("FORBIDDEN")

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "switch",
            "turn_off",
            {"entity_id": "switch.videocamera_ingresso_object_detection_tracking"},
            blocking=True,
        )

    assert (
        hass.states.get("switch.videocamera_ingresso_object_detection_tracking").state
        == STATE_ON
    )


async def test_an_offline_device_does_not_keep_serving_its_last_value(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    reachable_id = "binary_sensor.ecowitt_gateway_gateway_indoor_temperature_reachable"
    coordinator = await setup_integration(hass, config_entry)

    # Reachability is diagnostic and off by default — with hundreds of devices
    # it would otherwise double the entity count — so enable it explicitly.
    registry = er.async_get(hass)
    registry.async_update_entity(reachable_id, disabled_by=None)
    await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()

    coordinator = config_entry.runtime_data
    coordinator.data.slices[291]["device-status"] = {"online": False}
    coordinator.async_set_updated_data(coordinator.data)
    await hass.async_block_till_done()

    assert (
        hass.states.get("sensor.ecowitt_gateway_gateway_indoor_temperature").state
        == STATE_UNAVAILABLE
    )
    # The reachability sensor is the one entity that must survive, or it can
    # never report the thing it exists to report.
    assert hass.states.get(reachable_id).state == STATE_OFF


async def test_device_registry_groups_the_camera_and_its_siren(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    await setup_integration(hass, config_entry)
    registry = dr.async_get(hass)
    camera_device = registry.async_get_device(identifiers={(DOMAIN, "615")})
    assert camera_device is not None
    assert camera_device.name == "Videocamera ingresso"
