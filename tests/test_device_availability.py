"""A component may declare the device's own liveness; the hub link still gates.

The heartbeat says camstack is alive. A device whose provider lost its session
(a gateway that went down) is a different fact, and the hub declares it per
component with `availability_topic` plus the two payloads. Unknown is never
available, and a component that declares nothing behaves as it always did.
"""

from __future__ import annotations

import copy
from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock

from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.camstack.const import HEARTBEAT_TIMEOUT

from .conftest import (
    ENTITY_CHANGE_SENSOR,
    HEARTBEAT,
    SENSOR_KEY,
    push,
    setup_integration,
)

LEVEL_TOPIC = f"camstack/{SENSOR_KEY}/level"
ONLINE_TOPIC = f"camstack/{SENSOR_KEY}/online"
LEVEL = "sensor.indoor_temperature_level"


def _announcement(declare: bool = True) -> dict[str, Any]:
    message = copy.deepcopy(ENTITY_CHANGE_SENSOR)
    component: dict[str, Any] = {
        "platform": "sensor",
        "unique_id": "291_level",
        "name": "Level",
        "state_topic": LEVEL_TOPIC,
    }
    if declare:
        component |= {
            "availability_topic": ONLINE_TOPIC,
            "payload_available": "true",
            "payload_not_available": "false",
        }
    message["cmps"]["sensor-level"] = component
    return message


def _update(topic: str, value: str) -> dict[str, Any]:
    return {"type": "state_update", "topic": topic, "value": value}


async def _live(
    hass: HomeAssistant, entry: MockConfigEntry, *, declare: bool = True
) -> None:
    await setup_integration(hass, entry)
    await push(hass, entry, _announcement(declare))
    await push(hass, entry, HEARTBEAT)
    await push(hass, entry, _update(LEVEL_TOPIC, "42"))


async def test_a_declared_topic_saying_false_is_unavailable(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """The provider lost its session: the entity stops showing the last value."""
    await _live(hass, config_entry)
    await push(hass, config_entry, _update(ONLINE_TOPIC, "false"))

    assert hass.states.get(LEVEL).state == STATE_UNAVAILABLE


async def test_a_declared_topic_saying_true_is_available(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """The value written by the device shows, and a change updates the state."""
    await _live(hass, config_entry)
    await push(hass, config_entry, _update(ONLINE_TOPIC, "false"))
    await push(hass, config_entry, _update(ONLINE_TOPIC, "true"))

    assert hass.states.get(LEVEL).state == "42"


async def test_a_declared_topic_with_no_value_yet_is_unavailable(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """Unknown is never available."""
    await _live(hass, config_entry)

    assert hass.states.get(LEVEL).state == STATE_UNAVAILABLE


async def test_no_declared_topic_behaves_as_before(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """Without `availability_topic` the heartbeat alone decides."""
    await _live(hass, config_entry, declare=False)

    assert hass.states.get(LEVEL).state == "42"


async def test_a_dead_link_is_unavailable_whatever_the_device_says(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    mock_client: AsyncMock,
    config_entry: MockConfigEntry,
) -> None:
    """The device's own `online` never revives an entity under a dead hub."""
    await _live(hass, config_entry)
    await push(hass, config_entry, _update(ONLINE_TOPIC, "true"))
    assert hass.states.get(LEVEL).state == "42"

    freezer.tick(HEARTBEAT_TIMEOUT + timedelta(seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert hass.states.get(LEVEL).state == STATE_UNAVAILABLE
