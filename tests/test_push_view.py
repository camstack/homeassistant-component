"""The endpoint camstack pushes into, and what survives a restart."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

from homeassistant.const import STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.camstack.const import (
    PUSH_VIEW_URL,
    STORAGE_SAVE_DELAY,
)

from .conftest import (
    CAMERA_KEY,
    ENTITY_CHANGE_CAMERA,
    HEARTBEAT,
    setup_integration,
)
from .test_push import TRIGGERED, state_update, topic


async def test_the_view_refuses_an_unauthenticated_push(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    config_entry: MockConfigEntry,
    hass_client_no_auth: Any,
) -> None:
    """The view sits behind Home Assistant's own authentication.

    That is what lets the hub authenticate with the long-lived token the
    operator already gave it, instead of a second shared secret to configure
    and rotate.
    """
    await setup_integration(hass, config_entry)
    client = await hass_client_no_auth()

    response = await client.post(PUSH_VIEW_URL, json=HEARTBEAT)

    assert response.status == 401


async def test_the_first_push_after_setup_is_refused_on_purpose(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    config_entry: MockConfigEntry,
    hass_client: Any,
) -> None:
    """One deliberate 503, and the integration is broken without it.

    The hub dedups per topic and re-sends nothing unchanged. It drops that
    cache when the link returns, which it detects as a failed POST followed by
    a successful one — and reloading this integration produces no such edge,
    because a Home Assistant view cannot be unregistered and keeps answering
    200 while the entities behind it are new and empty. They would then stay
    empty until each value happened to change on its own.

    So the first push is refused, once, to manufacture the edge.
    """
    await setup_integration(hass, config_entry)
    client = await hass_client()

    first = await client.post(PUSH_VIEW_URL, json=HEARTBEAT)
    assert first.status == 503

    second = await client.post(PUSH_VIEW_URL, json=HEARTBEAT)
    assert second.status == 200


async def test_a_pushed_message_reaches_the_entities_through_the_view(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    config_entry: MockConfigEntry,
    hass_client: Any,
) -> None:
    """End to end over HTTP, not just through the handler."""
    await setup_integration(hass, config_entry)
    client = await hass_client()
    await client.post(PUSH_VIEW_URL, json=HEARTBEAT)  # the deliberate 503

    for message in (
        ENTITY_CHANGE_CAMERA,
        HEARTBEAT,
        state_update(topic("triggered"), "true"),
    ):
        assert (await client.post(PUSH_VIEW_URL, json=message)).status == 200
    await hass.async_block_till_done()

    assert hass.states.get(TRIGGERED).state == STATE_ON


async def test_a_body_that_is_not_json_is_refused(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    config_entry: MockConfigEntry,
    hass_client: Any,
) -> None:
    """A malformed body is refused rather than half-applied."""
    await setup_integration(hass, config_entry)
    client = await hass_client()
    await client.post(PUSH_VIEW_URL, json=HEARTBEAT)  # the deliberate 503

    response = await client.post(PUSH_VIEW_URL, data="not json")

    assert response.status == 400


async def test_structure_and_values_survive_a_restart(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    config_entry: MockConfigEntry,
    hass_storage: dict[str, Any],
    freezer: Any,
) -> None:
    """Entities come back before camstack says anything.

    The hub re-announces on its reconcile, which is up to five minutes away.
    Without this an operator would watch every entity sit blank after each
    restart and reasonably conclude the integration was broken.
    """
    runtime = await setup_integration(hass, config_entry)
    runtime.push.async_handle_message(ENTITY_CHANGE_CAMERA)
    runtime.push.async_handle_message(HEARTBEAT)
    runtime.push.async_handle_message(state_update(topic("triggered"), "true"))
    await hass.async_block_till_done()
    assert hass.states.get(TRIGGERED).state == STATE_ON

    # The store coalesces its writes; this is the flush.
    freezer.tick(STORAGE_SAVE_DELAY + 1)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()

    # Rebuilt from disk — and unavailable, because nothing has proved camstack
    # is alive since the reload.
    assert hass.states.get(TRIGGERED).state == STATE_UNAVAILABLE
    config_entry.runtime_data.push.async_handle_message(HEARTBEAT)
    await hass.async_block_till_done()
    assert hass.states.get(TRIGGERED).state == STATE_ON
    assert CAMERA_KEY in config_entry.runtime_data.push.components
