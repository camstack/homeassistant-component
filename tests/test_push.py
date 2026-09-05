"""The push consumer: what camstack sends, and what Home Assistant does with it.

CamStack pushes and Home Assistant never polls it for a value, so everything
an operator sees depends on this file being right about four message types and
one availability rule.
"""

from __future__ import annotations

import copy
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.camstack.const import (
    COMMAND_ROUTE_PATH,
    DOMAIN,
    HEARTBEAT_TIMEOUT,
    LINK_CHECK_INTERVAL,
)
from custom_components.camstack.push import CamStackPushHub
from custom_components.camstack.sensor import CamStackSensor

from .conftest import (
    CAMERA_KEY,
    ENTITY_CHANGE_CAMERA,
    ENTITY_CHANGE_SENSOR,
    HEARTBEAT,
    push,
    setup_integration,
)

TRIGGERED = "binary_sensor.videocamera_ingresso_triggered"
PERSON = "binary_sensor.videocamera_ingresso_person_detected"
CAMERA_SWITCH = "switch.videocamera_ingresso_camera"
PTZ_PRESET = "select.videocamera_ingresso_ptz_preset"
REBOOT = "button.videocamera_ingresso_reboot"


def state_update(topic: str, value: str) -> dict[str, Any]:
    """Return one state update as the hub sends it — the value is a STRING."""
    return {"type": "state_update", "topic": topic, "value": value}


def topic(entity: str) -> str:
    """Return the state topic the hub builds for one entity of the camera."""
    return f"camstack/{CAMERA_KEY}/{entity}"


async def alive(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Bring the link up, which is what makes every entity available."""
    await push(hass, entry, HEARTBEAT)


# --- structure -------------------------------------------------------------


async def test_an_entity_change_builds_the_whole_component_set(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """A device arrives as one message and becomes its entities.

    The component is generic: it knows nothing about cameras, zones or
    detection classes, so the count is whatever the hub sent.
    """
    await setup_integration(hass, config_entry)
    await push(hass, config_entry, ENTITY_CHANGE_CAMERA)

    registry = er.async_get(hass)
    entries = [
        entry
        for entry in registry.entities.values()
        if entry.config_entry_id == config_entry.entry_id and entry.domain != "camera"
    ]
    assert len(entries) == len(ENTITY_CHANGE_CAMERA["cmps"])


async def test_the_hubs_pressure_valve_is_honoured(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """~73 entities per camera only works because most arrive switched off.

    The hub marks everything an operator would not automate on
    `enabled_by_default: false`. Overriding that here would put all of them in
    the state machine, which is the cost the flag exists to avoid.
    """
    await setup_integration(hass, config_entry)
    await push(hass, config_entry, ENTITY_CHANGE_CAMERA)

    registry = er.async_get(hass)
    person_image = registry.async_get("image.videocamera_ingresso_person_last_image")
    assert person_image is not None
    assert person_image.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    # `*_detected` is what people automate on, so it arrives enabled.
    assert registry.async_get(PERSON) is not None
    assert registry.async_get(PERSON).disabled_by is None


async def test_entities_land_on_the_device_the_hub_named(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """The device identity is the hub's `device_id`, not the numeric one.

    The camera entity comes from the hub API and the sensors come from the
    push; keying both on `dev.ids[0]` is what keeps them on ONE Home Assistant
    device instead of two that look like duplicates.
    """
    await setup_integration(hass, config_entry)
    await push(hass, config_entry, ENTITY_CHANGE_CAMERA)

    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, CAMERA_KEY)})
    assert device is not None
    assert device.name == "Videocamera ingresso"
    assert device.manufacturer == "Hikvision"
    entity_ids = {
        entry.entity_id
        for entry in er.async_entries_for_device(er.async_get(hass), device.id)
    }
    assert TRIGGERED in entity_ids
    assert "camera.videocamera_ingresso" in entity_ids


async def test_entity_change_is_a_snapshot_and_never_accumulates(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """A component the hub stopped sending stops existing.

    `entity_change` carries the COMPLETE set every time, precisely so a
    dropped message repairs itself. Merging instead would make a removed
    entity permanent — a PTZ camera that lost its cap would keep six buttons
    that command nothing.
    """
    await setup_integration(hass, config_entry)
    await push(hass, config_entry, ENTITY_CHANGE_CAMERA)
    assert er.async_get(hass).async_get(REBOOT) is not None

    without_ptz = copy.deepcopy(ENTITY_CHANGE_CAMERA)
    for key in list(without_ptz["cmps"]):
        if key.startswith("button-ptz"):
            del without_ptz["cmps"][key]
    await push(hass, config_entry, without_ptz)

    registry = er.async_get(hass)
    assert registry.async_get("button.videocamera_ingresso_ptz_up") is None
    # Everything else survives: this is a diff, not a rebuild.
    assert registry.async_get(REBOOT) is not None
    assert registry.async_get(TRIGGERED) is not None


async def test_a_platform_this_version_cannot_build_is_named(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    config_entry: MockConfigEntry,
    caplog: Any,
) -> None:
    """An unsupported platform costs one entity, and says so.

    The hub adds entities without this component being released. Silence would
    read as "camstack exported nothing", which is a different problem with a
    different fix.
    """
    await setup_integration(hass, config_entry)
    message = copy.deepcopy(ENTITY_CHANGE_CAMERA)
    message["cmps"]["alarm_control_panel-alarm"] = {
        "platform": "alarm_control_panel",
        "unique_id": "615_alarm",
        "name": "Alarm",
        "state_topic": topic("alarm"),
    }
    await push(hass, config_entry, message)

    assert "alarm_control_panel" in caplog.text
    assert er.async_get(hass).async_get(TRIGGERED) is not None


# --- values ----------------------------------------------------------------


async def test_a_batch_of_values_reaches_the_entities(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """State updates arrive batched, and every value on the wire is a string."""
    await setup_integration(hass, config_entry)
    await push(hass, config_entry, ENTITY_CHANGE_CAMERA)
    await alive(hass, config_entry)

    await push(
        hass,
        config_entry,
        {
            "type": "batch",
            "items": [
                state_update(topic("triggered"), "true"),
                state_update(topic("person_detected"), "true"),
                state_update(topic("last_detection"), "2026-08-09T01:02:03.000Z"),
            ],
        },
    )

    assert hass.states.get(TRIGGERED).state == STATE_ON
    assert hass.states.get(PERSON).state == STATE_ON


async def test_a_timestamp_sensor_converts_the_string_it_is_sent(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """Everything on the wire is a string; a timestamp sensor needs a datetime.

    Home Assistant raises when a `timestamp` sensor holds text and it swallows
    that inside the state write — the entity then silently never updates
    again. Every `*_last_detection` is one of these, and every one of them
    arrives disabled by default, so this is asserted directly rather than
    through the state machine.
    """
    config_entry.add_to_hass(hass)
    hub = CamStackPushHub(hass, config_entry, mock_client)
    hub.async_handle_message(ENTITY_CHANGE_CAMERA)
    entity = CamStackSensor(hub, CAMERA_KEY, "sensor-last-detection")

    hub.states[topic("last_detection")] = "2026-08-09T01:02:03.000Z"
    assert entity.native_value == datetime(2026, 8, 9, 1, 2, 3, tzinfo=UTC)

    # A value that will not convert is dropped rather than passed through.
    hub.states[topic("last_detection")] = "never"
    assert entity.native_value is None


async def test_a_binary_value_is_matched_case_insensitively(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """The hub sends `true`/`false`; the component lowercases verbatim."""
    await setup_integration(hass, config_entry)
    await push(hass, config_entry, ENTITY_CHANGE_CAMERA)
    await alive(hass, config_entry)

    await push(hass, config_entry, state_update(topic("triggered"), "TRUE"))
    assert hass.states.get(TRIGGERED).state == STATE_ON
    await push(hass, config_entry, state_update(topic("triggered"), "false"))
    assert hass.states.get(TRIGGERED).state == STATE_OFF


async def test_a_select_offers_the_options_the_hub_sent(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """The PTZ preset names are the hub's, not this component's.

    Snooze left the export on 2026-09-05 (hub D358); the preset select is
    the camera's remaining select.
    """
    await setup_integration(hass, config_entry)
    await push(hass, config_entry, ENTITY_CHANGE_CAMERA)
    await alive(hass, config_entry)

    options = hass.states.get(PTZ_PRESET).attributes["options"]
    assert options == ENTITY_CHANGE_CAMERA["cmps"]["select-ptz-preset"]["options"]


# --- availability ----------------------------------------------------------


async def test_entities_are_unavailable_until_the_first_heartbeat(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """Structure without liveness is not a working integration.

    Entities are rebuilt from disk after a restart, so they exist before
    camstack has said anything. Showing their restored values as live would be
    the exact lie availability exists to prevent.
    """
    await setup_integration(hass, config_entry)
    await push(hass, config_entry, ENTITY_CHANGE_CAMERA)

    assert hass.states.get(TRIGGERED).state == STATE_UNAVAILABLE
    await alive(hass, config_entry)
    assert hass.states.get(TRIGGERED).state != STATE_UNAVAILABLE


async def test_a_stopped_heartbeat_marks_every_entity_unavailable(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    config_entry: MockConfigEntry,
    freezer: Any,
) -> None:
    """The heartbeat is the ONE source of truth for "camstack is alive".

    When it stops, an entity holding its last value would look healthy while
    its feed is dead — which is worse than a gap, because an automation would
    keep trusting it.
    """
    await setup_integration(hass, config_entry)
    await push(hass, config_entry, ENTITY_CHANGE_CAMERA)
    await alive(hass, config_entry)
    await push(hass, config_entry, state_update(topic("triggered"), "true"))
    assert hass.states.get(TRIGGERED).state == STATE_ON

    freezer.tick(HEARTBEAT_TIMEOUT + LINK_CHECK_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert hass.states.get(TRIGGERED).state == STATE_UNAVAILABLE

    await alive(hass, config_entry)
    assert hass.states.get(TRIGGERED).state == STATE_ON


# --- commands --------------------------------------------------------------


async def test_a_switch_commands_back_over_the_addon_route(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """A toggle becomes `{topic, value}` on the export addon's own route.

    Never a write to the underlying setting: the hub renders the switch from
    the authority that owns the function and applies it back through the same
    one, so a Home Assistant toggle cannot become a second knob that disagrees
    with the admin UI.
    """
    await setup_integration(hass, config_entry)
    await push(hass, config_entry, ENTITY_CHANGE_CAMERA)
    await alive(hass, config_entry)

    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": CAMERA_SWITCH}, blocking=True
    )

    mock_client.async_post_addon_route.assert_awaited_once_with(
        COMMAND_ROUTE_PATH,
        {"topic": f"camstack/{CAMERA_KEY}/stream_broker/set", "value": "false"},
    )
    # The camera switches report back on the hub's reconcile rather than on an
    # event, so the accepted command shows immediately and the next push
    # corrects it if the hub disagreed.
    assert hass.states.get(CAMERA_SWITCH).state == STATE_OFF


async def test_a_button_sends_the_press_payload_the_hub_declared(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """A button has no state; it only has a command topic."""
    await setup_integration(hass, config_entry)
    await push(hass, config_entry, ENTITY_CHANGE_CAMERA)
    await alive(hass, config_entry)

    await hass.services.async_call(
        "button", "press", {"entity_id": REBOOT}, blocking=True
    )

    mock_client.async_post_addon_route.assert_awaited_once_with(
        COMMAND_ROUTE_PATH,
        {"topic": f"camstack/{CAMERA_KEY}/reboot/set", "value": "PRESS"},
    )


# --- more than one device --------------------------------------------------


async def test_a_derived_device_builds_from_its_capabilities(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """A sensor is not a camera, and the component does not know the difference.

    Its entities are derived hub-side from the capabilities it declares. That
    they arrive here through exactly the same code path as a camera's is the
    property that lets the hub add a device kind without releasing this
    component.
    """
    await setup_integration(hass, config_entry)
    await push(hass, config_entry, ENTITY_CHANGE_SENSOR)
    await alive(hass, config_entry)

    registry = er.async_get(hass)
    temperature = registry.async_get("sensor.indoor_temperature_temperature_sensor")
    assert temperature is not None
    assert temperature.unit_of_measurement == "°C"
