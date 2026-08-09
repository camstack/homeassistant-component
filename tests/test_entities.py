"""The camera entity — the one thing the push transport does not carry."""

from __future__ import annotations

import copy
from typing import Any
from unittest.mock import AsyncMock

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.camstack.const import DOMAIN

from .conftest import (
    CAMERA_KEY,
    DEVICE_LIST,
    make_query_responder,
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
