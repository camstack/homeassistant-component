"""The camera entity — the one thing the push transport does not carry."""

from __future__ import annotations

import copy
from typing import Any
from unittest.mock import AsyncMock

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.camstack.const import DOMAIN

from .conftest import (
    CAMERA_KEY,
    DEVICE_LIST,
    ENTITY_CHANGE_CAMERA,
    ENTITY_CHANGE_CAMERA_STREAMS,
    HEARTBEAT,
    make_query_responder,
    push,
    setup_integration,
)

CAMERA = "camera.videocamera_ingresso"


async def test_a_camera_entity_is_created_for_each_exported_camera(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """615 is a camera and is exported; 585 and 291 are exported and are not."""
    await setup_integration(hass, config_entry)

    state = hass.states.get(CAMERA)
    assert state is not None
    assert state.attributes["camstack_device_id"] == 615
    assert hass.states.get("camera.sirena_ingresso") is None


async def test_the_camera_joins_the_device_the_hub_pushes_to(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """One Home Assistant device per camstack device, keyed on the hub's id.

    The camera comes from the hub API and its sensors come from the push. Two
    identity schemes would put the live view on a second device sitting next
    to the entities it belongs with, and no log line would say why.
    """
    await setup_integration(hass, config_entry)

    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, CAMERA_KEY)})
    assert device is not None
    assert hass.states.get(CAMERA).attributes["camstack_device_key"] == CAMERA_KEY


async def test_a_camera_the_hub_disabled_is_unavailable(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """A disabled camera must not keep serving its last frame."""
    listing = copy.deepcopy(DEVICE_LIST)
    for payload in listing:
        if payload["id"] == 615:
            payload["disabled"] = True

    async def respond(path: str, payload: Any | None = None) -> Any:
        if path == "deviceManager.listAll":
            return listing
        return await make_query_responder()(path, payload)

    mock_client.query = AsyncMock(side_effect=respond)
    await setup_integration(hass, config_entry)

    assert hass.states.get(CAMERA).state == "unavailable"


async def test_a_still_comes_from_the_snapshot_capability(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """Stills are fetched on demand, and never pushed."""
    from homeassistant.components.camera import async_get_image

    await setup_integration(hass, config_entry)

    image = await async_get_image(hass, CAMERA)

    assert image.content
    paths = [call.args[0] for call in mock_client.query.await_args_list]
    assert "snapshot.getSnapshot" in paths


async def test_a_device_without_a_stable_id_is_skipped(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """Numeric ids are reallocated by a re-sync; entities are not built on one.

    An entity keyed on a numeric id follows whichever camera later inherits
    the number, taking its automations with it. Skipping is the honest answer
    until the hub sends a stable id.
    """
    listing = copy.deepcopy(DEVICE_LIST)
    for payload in listing:
        if payload["id"] == 615:
            del payload["stableId"]

    async def respond(path: str, payload: Any | None = None) -> Any:
        if path == "deviceManager.listAll":
            return listing
        return await make_query_responder()(path, payload)

    mock_client.query = AsyncMock(side_effect=respond)
    runtime = await setup_integration(hass, config_entry)

    assert 615 not in runtime.coordinator.data.devices
    assert hass.states.get(CAMERA) is None


# ── One entity per STREAM ──────────────────────────────────────────────────
#
# Measured on the operator's instance on 2026-08-14: 165 camstack entities and
# TWO `camera` ones, while the hub had been announcing twelve camera
# components a week. They built nothing, because this platform read the export
# membership and never the components. These pin the other direction.


async def test_a_camera_entity_is_built_for_every_stream_the_hub_announces(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """Four announced streams plus the camera itself — the raw one off."""
    await setup_integration(hass, config_entry)
    await push(hass, config_entry, HEARTBEAT)
    await push(hass, config_entry, ENTITY_CHANGE_CAMERA_STREAMS)

    for entity_id in (
        "camera.videocamera_ingresso",
        "camera.videocamera_ingresso_stream_high_2160p",
        "camera.videocamera_ingresso_stream_mid_720p",
        "camera.videocamera_ingresso_stream_low_360p",
    ):
        assert hass.states.get(entity_id) is not None, entity_id

    registry = er.async_get(hass)
    raw = registry.async_get_entity_id(
        "camera", DOMAIN, f"{DOMAIN}_hikvision:615_stream_native_main"
    )
    assert raw is not None
    assert registry.async_get(raw).disabled_by is er.RegistryEntryDisabler.INTEGRATION


async def test_the_operators_existing_camera_survives_the_new_streams(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """The migration, and it is a non-event by construction.

    The adaptive stream IS this entity — it negotiates no target — so the hub
    announces no component for it. A component claiming
    `camstack_<stableId>_camera` a second time would make Home Assistant drop
    one of the two with a "does not generate unique IDs" error, and the one
    it dropped would be the operator's camera with its history on it.
    """
    await setup_integration(hass, config_entry)
    await push(hass, config_entry, HEARTBEAT)
    await push(hass, config_entry, ENTITY_CHANGE_CAMERA_STREAMS)

    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "camera", DOMAIN, f"{DOMAIN}_hikvision:615_camera"
    )
    assert entity_id == CAMERA
    assert type(_entity(hass, CAMERA)).__name__ == "CamStackCamera"


async def test_a_stream_entity_negotiates_the_target_the_hub_named(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """The whole content of a camera entity is its target.

    An entity that promised the low profile and negotiated whatever
    `handleOffer` felt like would show a different picture from its own name —
    which is the failure the RTSP url shape hid, one layer down.
    """
    from homeassistant.components.camera import async_get_stream_source  # noqa: F401

    await setup_integration(hass, config_entry)
    await push(hass, config_entry, HEARTBEAT)
    await push(hass, config_entry, ENTITY_CHANGE_CAMERA_STREAMS)

    mock_client.mutate.reset_mock()
    mock_client.mutate.return_value = {"sessionId": "hub-1", "sdpAnswer": "v=0"}
    entity = _entity(hass, "camera.videocamera_ingresso_stream_low_360p")
    await entity.async_handle_async_webrtc_offer("v=0", "ha-1", lambda _message: None)

    path, payload = mock_client.mutate.call_args[0]
    assert path == "webrtcSession.handleOffer"
    assert payload["deviceId"] == 615
    assert payload["target"] == {"kind": "profile", "profile": "low"}


async def test_a_hub_that_announces_no_stream_still_gets_its_camera(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """A hub older than the per-stream announce loses nothing.

    Its `entity_change` carries 51 components and not one `camera`, which is
    exactly what the shipped hub sent. The camera comes from the membership
    either way — the streams are additive.
    """
    await setup_integration(hass, config_entry)
    await push(hass, config_entry, HEARTBEAT)
    await push(hass, config_entry, ENTITY_CHANGE_CAMERA)

    assert hass.states.get(CAMERA).attributes["camstack_device_id"] == 615
    assert hass.states.get("camera.videocamera_ingresso_stream_high_2160p") is None


def _entity(hass: HomeAssistant, entity_id: str) -> Any:
    """Return the live entity object behind an entity id."""
    component = hass.data["entity_components"]["camera"]
    entity = component.get_entity(entity_id)
    assert entity is not None, entity_id
    return entity
