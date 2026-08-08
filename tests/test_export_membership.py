"""Export membership decides what the integration imports.

The integration used to call `deviceManager.listAll` and build entities for
everything it returned. On the operator's cluster that is ~300 devices, of
which a handful were meant to reach Home Assistant. Membership belongs to the
`device-export` capability — the same interface the Alexa and HomeKit exporters
implement — and this file pins that it is read and honoured.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.camstack.const import HA_EXPORT_ADDON_ID

from .conftest import DEVICE_LIST, ME, SNAPSHOTS, SWITCH_GROUP
from .test_entities import setup_integration


def _responder(exposed: list[dict[str, Any]]) -> Any:
    """Return a hub responder whose export list is `exposed`."""

    async def respond(path: str, payload: Any | None = None) -> Any:
        if path == "deviceExport.listExposedDevices":
            return exposed
        if path == "deviceManager.listAll":
            return DEVICE_LIST
        if path == "deviceState.getAllSnapshots":
            return SNAPSHOTS
        if path == "pipelineOrchestrator.getCameraSwitches":
            return SWITCH_GROUP
        if path == "auth.me":
            return ME
        if path == "snapshot.getSnapshot":
            return {"base64": "/9j/4AAQ", "contentType": "image/jpeg"}
        raise AssertionError(f"unexpected query: {path}")

    return respond


async def test_membership_is_read_from_the_export_capability(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """The hub is asked for the export list, scoped to the HA export addon."""
    await setup_integration(hass, config_entry)

    calls = [call.args for call in mock_client.query.await_args_list]
    exports = [args for args in calls if args[0] == "deviceExport.listExposedDevices"]
    assert exports, "the integration never asked the hub what is exported"
    # Scoped to the addon that owns HA export. Unscoped, a `device-export`
    # collection call merges every exporter's list, so the HomeKit and Alexa
    # selections would silently become Home Assistant's too.
    assert exports[0][1] == {"addonId": HA_EXPORT_ADDON_ID}


async def test_only_exported_devices_become_entities(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """A device the hub returns but does not export creates nothing.

    289 and 290 are in `deviceManager.listAll` and NOT in the export list.
    """
    coordinator = await setup_integration(hass, config_entry)

    assert set(coordinator.data.devices) == {615, 585, 291}
    # The unexported parents are still known — grouping needs the chain.
    assert {289, 290} <= set(coordinator.data.topology)
    assert hass.states.get("sensor.ecowitt_gateway_gateway_indoor_temperature")
    assert hass.states.get("binary_sensor.ecowitt_gateway_reachable") is None


async def test_an_empty_export_list_creates_no_entities(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """Exporting nothing means importing nothing — never everything.

    This is the regression that matters: the failure mode being fixed is a
    fallback that treats "no selection" as "take them all".
    """
    mock_client.query = AsyncMock(side_effect=_responder([]))

    coordinator = await setup_integration(hass, config_entry)

    assert coordinator.data.devices == {}
    assert hass.states.get("camera.videocamera_ingresso") is None
    assert hass.states.get("binary_sensor.videocamera_ingresso_motion") is None


async def test_exporting_one_camera_imports_only_that_camera(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """Narrowing the hub's selection narrows what Home Assistant receives."""
    mock_client.query = AsyncMock(side_effect=_responder([{"deviceId": "615"}]))

    coordinator = await setup_integration(hass, config_entry)

    assert set(coordinator.data.devices) == {615}
    assert hass.states.get("camera.videocamera_ingresso") is not None
    # 291 is a live hub device carrying a temperature slice. It is absent only
    # because it is not exported.
    assert hass.states.get("sensor.ecowitt_gateway_gateway_indoor_temperature") is None


async def test_a_string_device_id_still_matches_an_int_one(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """The two hub endpoints disagree on the type of a device id.

    `deviceExport.listExposedDevices` sends `"615"`; `deviceManager.listAll`
    sends `615`. Comparing them unconverted matches nothing, which looks
    exactly like an empty selection — a bug that would pass every other test
    in this file while importing nothing on a real hub.
    """
    mock_client.query = AsyncMock(side_effect=_responder([{"deviceId": "615"}]))

    coordinator = await setup_integration(hass, config_entry)

    assert 615 in coordinator.data.devices


async def test_a_failed_export_read_does_not_silently_import_everything(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """A malformed answer is not an answer, and must not open the gate."""

    async def respond(path: str, payload: Any | None = None) -> Any:
        if path == "deviceExport.listExposedDevices":
            return {"unexpected": "shape"}
        return await _responder([])(path, payload)

    mock_client.query = AsyncMock(side_effect=respond)
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("camera.videocamera_ingresso") is None


async def test_an_exported_device_from_home_assistant_is_still_refused(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """The echo guard outranks the export list.

    Exporting an HA-sourced device back to HA mirrors it across the bridge, so
    a selection that asks for it is refused rather than honoured.
    """
    mock_client.query = AsyncMock(side_effect=_responder([{"deviceId": "1027"}]))

    coordinator = await setup_integration(hass, config_entry)

    assert 1027 not in coordinator.data.devices
