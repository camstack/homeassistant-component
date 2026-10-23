"""Static attributes and enum sensors, as the hub announces them.

The hub's consumables export announces one entity per consumable with an
`attributes` object (form, kind, label) on the component entry, and a status
sensor whose value is one of a closed set. Both are carried by the component
set the hub sends — nothing here knows what a consumable is.
"""

from __future__ import annotations

import copy
from typing import Any
from unittest.mock import AsyncMock

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.camstack.button import CamStackButton
from custom_components.camstack.push import CamStackPushHub
from custom_components.camstack.sensor import CamStackSensor

from .conftest import (
    ENTITY_CHANGE_SENSOR,
    HEARTBEAT,
    SENSOR_KEY,
    push,
    setup_integration,
)

STATUS_OPTIONS = ["ok", "low", "empty", "full", "replace"]
FILTER_ATTRIBUTES = {"form": "filter", "kind": "replaceable", "label": "Main brush"}

LEVEL = "sensor.indoor_temperature_main_brush_level"
STATUS = "sensor.indoor_temperature_main_brush_status"
RESET = "button.indoor_temperature_main_brush_reset"


def _topic(entity: str) -> str:
    return f"camstack/{SENSOR_KEY}/{entity}"


def _announcement(attributes: Any = FILTER_ATTRIBUTES) -> dict[str, Any]:
    """Return the sensor device plus one consumable's three entities."""
    message = copy.deepcopy(ENTITY_CHANGE_SENSOR)
    message["cmps"]["sensor-main-brush-level"] = {
        "platform": "sensor",
        "unique_id": "291_main_brush_level",
        "name": "Main brush level",
        "state_topic": _topic("main_brush_level"),
        "unit_of_measurement": "%",
        "icon": "mdi:brush",
        "attributes": attributes,
    }
    message["cmps"]["sensor-main-brush-status"] = {
        "platform": "sensor",
        "unique_id": "291_main_brush_status",
        "name": "Main brush status",
        "state_topic": _topic("main_brush_status"),
        "device_class": "enum",
        "options": STATUS_OPTIONS,
        "attributes": attributes,
    }
    message["cmps"]["button-main-brush-reset"] = {
        "platform": "button",
        "unique_id": "291_main_brush_reset",
        "name": "Main brush reset",
        "command_topic": _topic("main_brush_reset/set"),
        "attributes": attributes,
    }
    return message


def _state_update(topic: str, value: str) -> dict[str, Any]:
    return {"type": "state_update", "topic": topic, "value": value}


async def _live(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await setup_integration(hass, entry)
    await push(hass, entry, _announcement())
    await push(hass, entry, HEARTBEAT)
    await push(hass, entry, _state_update(_topic("main_brush_level"), "42"))
    await push(hass, entry, _state_update(_topic("main_brush_status"), "low"))


# --- attributes ------------------------------------------------------------


async def test_the_announced_attributes_reach_sensor_and_button(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """Every platform built on the base class carries the hub's attributes."""
    await _live(hass, config_entry)

    for entity_id in (LEVEL, STATUS, RESET):
        attributes = hass.states.get(entity_id).attributes
        for key, value in FILTER_ATTRIBUTES.items():
            assert attributes[key] == value, entity_id
    assert hass.states.get(LEVEL).attributes["icon"] == "mdi:brush"


async def test_a_reannouncement_replaces_the_attributes(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """A relabelled consumable shows its new label without a reload."""
    await _live(hass, config_entry)

    relabelled = {**FILTER_ATTRIBUTES, "label": "Side brush"}
    await push(hass, config_entry, _announcement(relabelled))

    assert hass.states.get(LEVEL).attributes["label"] == "Side brush"
    assert hass.states.get(RESET).attributes["label"] == "Side brush"


async def test_attributes_that_are_not_an_object_are_ignored(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """A malformed `attributes` costs the attributes, never the entity."""
    config_entry.add_to_hass(hass)
    hub = CamStackPushHub(hass, config_entry, mock_client)
    hub.async_handle_message(_announcement(["form", "filter"]))

    assert (
        CamStackSensor(
            hub, SENSOR_KEY, "sensor-main-brush-level"
        ).extra_state_attributes
        is None
    )
    assert (
        CamStackButton(
            hub, SENSOR_KEY, "button-main-brush-reset"
        ).extra_state_attributes
        is None
    )


async def test_only_scalar_attribute_values_are_kept(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """Attributes are JSON scalars; a nested value is dropped, not stringified."""
    config_entry.add_to_hass(hass)
    hub = CamStackPushHub(hass, config_entry, mock_client)
    hub.async_handle_message(
        _announcement({"form": "filter", "hours": 120, "ok": True, "nested": {"a": 1}})
    )
    entity = CamStackSensor(hub, SENSOR_KEY, "sensor-main-brush-level")

    assert entity.extra_state_attributes == {"form": "filter", "hours": 120, "ok": True}


# --- enum ------------------------------------------------------------------


async def test_an_enum_sensor_keeps_its_text_and_offers_its_options(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """`device_class: enum` + `options` is a real enum, not a float."""
    await _live(hass, config_entry)

    state = hass.states.get(STATUS)
    assert state.state == "low"
    assert state.attributes["device_class"] == "enum"
    assert state.attributes["options"] == STATUS_OPTIONS


async def test_a_numeric_sensor_still_converts_beside_an_enum(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """The enum support must not make a unit-bearing sensor text."""
    await _live(hass, config_entry)

    assert float(hass.states.get(LEVEL).state) == 42.0
    assert "options" not in hass.states.get(LEVEL).attributes


async def test_an_enum_value_outside_its_options_is_unknown(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """HA raises on a value outside `options` and swallows it in the write.

    The entity would then never update again, so an unknown value is `None`.
    """
    config_entry.add_to_hass(hass)
    hub = CamStackPushHub(hass, config_entry, mock_client)
    hub.async_handle_message(_announcement())
    entity = CamStackSensor(hub, SENSOR_KEY, "sensor-main-brush-status")

    hub.states[_topic("main_brush_status")] = "replace"
    assert entity.native_value == "replace"
    hub.states[_topic("main_brush_status")] = "exploded"
    assert entity.native_value is None


async def test_an_enum_without_options_is_plain_text(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """`options` without a valid list would make HA raise on every write."""
    message = _announcement()
    del message["cmps"]["sensor-main-brush-status"]["options"]
    config_entry.add_to_hass(hass)
    hub = CamStackPushHub(hass, config_entry, mock_client)
    hub.async_handle_message(message)
    entity = CamStackSensor(hub, SENSOR_KEY, "sensor-main-brush-status")

    hub.states[_topic("main_brush_status")] = "low"
    assert entity.device_class is None
    assert entity.options is None
    assert entity.native_value == "low"
