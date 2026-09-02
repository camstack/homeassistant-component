"""Fixtures for the CamStack tests.

Every payload below is a **verbatim shape taken from a live CamStack hub**, not
a shape invented to match this code. A fake that supplies what production
forgot to fetch produces a green suite over a broken integration, so the
fixtures are recordings and the assertions are about behaviour.

The `entity_change` fixtures in `fixtures/` go one better: they were generated
by running the hub's OWN `entity-catalog.ts` — 51 components for a camera with
one zone and PTZ, 3 for a derived sensor. Nothing in them was typed by hand,
so a component key or a payload that this integration gets wrong here gets it
wrong against the hub too.

One edit has been applied to those recordings since: on 2026-09-02 the export
moved every identity from `stableId` to the hub's NUMERIC device id, aligning
Home Assistant with the Alexa, HomeKit and Google exports. The fixtures were
rekeyed mechanically — `camstack-hikvision:615` → `camstack-615`,
`hikvision:615_online` → `615_online` — because that substitution is exactly
what the hub change does to its own output and nothing else in the shape moved.
The `stableId` still present on the `deviceManager.listAll` payloads below is
NOT a leftover: the hub still has one, and these tests prove this integration
no longer reads it.
"""

from __future__ import annotations

import json
from collections.abc import Generator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.camstack.api import CamStackRequestError
from custom_components.camstack.const import (
    CONF_VERIFY_SSL,
    CONFIG_ENTRY_VERSION,
    DOMAIN,
    LEGACY_CONF_URL_BASE,
)

pytest_plugins = "pytest_homeassistant_custom_component"

_FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict[str, Any]:
    """Return one recorded push message."""
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


ENTITY_CHANGE_CAMERA: dict[str, Any] = load_fixture("entity_change_camera.json")
"""The same camera from a hub that announces its STREAMS.

Generated the same way — by running the hub's `entity-catalog.ts` — for a
device whose `webrtcSession.listStreams` returns adaptive, three profiles and
one raw substream. It is a second fixture rather than a replacement because
the first one records what a hub SHIPPED, and the fallback path has to keep
working against it.
"""
ENTITY_CHANGE_CAMERA_STREAMS: dict[str, Any] = load_fixture(
    "entity_change_camera_streams.json"
)
ENTITY_CHANGE_SENSOR: dict[str, Any] = load_fixture("entity_change_sensor.json")

CAMERA_KEY: str = ENTITY_CHANGE_CAMERA["device_id"]
SENSOR_KEY: str = ENTITY_CHANGE_SENSOR["device_id"]


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Let Home Assistant load the integration from custom_components."""
    return


# --- Recorded hub payloads -------------------------------------------------

DEVICE_CAMERA: dict[str, Any] = {
    "id": 615,
    "stableId": "hikvision:615",
    "addonId": "provider-hikvision",
    "type": "camera",
    "name": "Videocamera ingresso",
    "location": None,
    "disabled": False,
    "parentDeviceId": None,
    "role": None,
    "online": True,
    "isCamera": True,
    "config": {},
    "metadata": {},
    "sourceInfo": {"id": "hikvision:615", "system": "hikvision"},
}

DEVICE_SIREN: dict[str, Any] = {
    "id": 585,
    "stableId": "hikvision:585",
    "addonId": "provider-hikvision",
    "type": "siren",
    "name": "Sirena ingresso",
    "parentDeviceId": 615,
    "disabled": False,
    "isCamera": False,
    "sourceInfo": {"id": "hikvision:585", "system": "hikvision"},
}

DEVICE_HUB: dict[str, Any] = {
    "id": 289,
    "stableId": "ecowitt:1",
    "addonId": "provider-ecowitt",
    "type": "hub",
    "name": "Ecowitt Gateway",
    "parentDeviceId": None,
    "disabled": False,
    "isCamera": False,
    "sourceInfo": {"id": "ecowitt:1", "system": "ecowitt-gateway"},
}

DEVICE_STATION: dict[str, Any] = {
    "id": 290,
    "stableId": "ecowitt:1:gateway",
    "addonId": "provider-ecowitt",
    "type": "container",
    "name": "Ecowitt Gateway - gateway",
    "parentDeviceId": 289,
    "disabled": False,
    "isCamera": False,
    "sourceInfo": {"id": "ecowitt:1:gateway", "system": "ecowitt"},
}

DEVICE_SENSOR: dict[str, Any] = {
    "id": 291,
    "stableId": "ecowitt:1:tempinf",
    "addonId": "provider-ecowitt",
    "type": "sensor",
    "name": "Indoor Temperature",
    "parentDeviceId": 290,
    "disabled": False,
    "isCamera": False,
    "sourceInfo": {"id": "ecowitt:1:tempinf", "system": "ecowitt"},
}

# Came FROM Home Assistant; exporting it back would mirror it across the bridge.
DEVICE_ECHO: dict[str, Any] = {
    "id": 1027,
    "stableId": "ha:person.someone",
    "addonId": "provider-homeassistant",
    "type": "presence",
    "name": "Someone",
    "parentDeviceId": None,
    "disabled": False,
    "isCamera": False,
    "sourceInfo": {"id": "ha:person.someone", "system": "homeassistant"},
}

DEVICE_LIST: list[dict[str, Any]] = [
    DEVICE_HUB,
    DEVICE_STATION,
    DEVICE_SENSOR,
    DEVICE_CAMERA,
    DEVICE_SIREN,
    DEVICE_ECHO,
]

# `deviceExport.listExposedDevices`, verbatim from the live hub — note that
# `deviceId` is a STRING here while `deviceManager.listAll` reports an int.
# Comparing the two without converting matches nothing, and "nothing matched"
# is indistinguishable from "nothing is exported", so the fixture keeps the
# real type rather than the convenient one.
#
# 289 (the Ecowitt gateway) and 290 (its container) are deliberately ABSENT:
# 291 is exported and its parents are not, which is the ordinary case.
EXPOSED_DEVICES: list[dict[str, Any]] = [
    {"deviceId": "615", "exposedAs": "Home Assistant"},
    {"deviceId": "585", "exposedAs": "Home Assistant"},
    {"deviceId": "291", "exposedAs": "Home Assistant"},
]

# The hub's refusal of an unresolvable `addonId`, recorded verbatim on
# 2026-08-09. It enumerates the providers that DO exist, which is the whole
# reason it must reach the operator instead of being flattened into "no
# devices were exported".
UNRESOLVABLE_ADDON_ERROR: dict[str, Any] = {
    "json": {
        "message": (
            'Capability "device-export" has no provider with addonId '
            '"export-ha-mqtt". Valid addonId(s): export-alexa, '
            "homeassistant-export, export-hap. An unresolvable addonId is "
            "refused rather than answered by another provider."
        ),
        "code": -32600,
        "data": {
            "code": "BAD_REQUEST",
            "httpStatus": 400,
            "path": "deviceExport.listExposedDevices",
        },
    }
}

ME: dict[str, Any] = {
    "id": "1476a68a",
    "username": "operator",
    "isAdmin": True,
    "permissions": {"isAdmin": True, "allowedProviders": "*", "allowedDevices": {}},
    "isApiKey": False,
    "agentId": None,
}


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """Return a config entry for the hub."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="CamStack (192.168.1.9)",
        unique_id="192.168.1.9:4443",
        version=CONFIG_ENTRY_VERSION,
        data={
            CONF_HOST: "192.168.1.9",
            CONF_PORT: 4443,
            CONF_USERNAME: "operator",
            CONF_PASSWORD: "secret",
            CONF_VERIFY_SSL: False,
        },
    )


@pytest.fixture
def legacy_panel_entry() -> MockConfigEntry:
    """Return an entry exactly as the panel-only component wrote it.

    Version 1, one URL, no credentials, and the panel settings in `options`.
    This is what is sitting in a real operator's `.storage` today.
    """
    return MockConfigEntry(
        domain=DOMAIN,
        title="CamStack",
        version=1,
        # Deliberately plain http: the derived address is https, so a
        # migration that REBUILDS the URL instead of carrying it forward
        # produces a different one and the tests can tell.
        data={LEGACY_CONF_URL_BASE: "http://camstack.example.com:8443"},
        options={"panel_title": "Telecamere", "panel_icon": "mdi:cctv"},
    )


@pytest.fixture
def legacy_entity_entry() -> MockConfigEntry:
    """Return a version 1 entry written by the entity-only component."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="CamStack (192.168.1.9)",
        unique_id="192.168.1.9:4443",
        version=1,
        data={
            CONF_HOST: "192.168.1.9",
            CONF_PORT: 4443,
            CONF_USERNAME: "operator",
            CONF_PASSWORD: "secret",
            CONF_VERIFY_SSL: False,
        },
    )


def make_query_responder(
    exposed: list[dict[str, Any]] | None = None,
) -> Any:
    """Return an async responder that answers like the hub does.

    Three behaviours are reproduced because getting any of them wrong makes a
    broken filter look healthy:

    * **pinned** to an exporter, `listExposedDevices` returns that exporter's
      list alone;
    * **unpinned** it merges every exporter into one array, which is what
      makes an unpinned read import somebody else's selection;
    * **pinned to an id the hub does not have** it REFUSES, naming the valid
      ids, instead of answering from another provider.

    A fake that answered the same with and without the pin would let the
    defect this file exists to catch pass green.
    """
    per_provider = EXPOSED_DEVICES if exposed is None else exposed

    async def respond(path: str, payload: Any | None = None) -> Any:
        if path == "deviceExport.listExposedDevices":
            addon_id = (payload or {}).get("addonId")
            if addon_id is None:
                return per_provider + AGGREGATE_EXPOSED
            if addon_id == "homeassistant-export":
                return per_provider
            if addon_id in ("export-alexa", "export-hap"):
                return AGGREGATE_EXPOSED
            raise CamStackRequestError(
                f"{path}: {UNRESOLVABLE_ADDON_ERROR['json']['message']}"
            )
        if path == "deviceManager.listAll":
            return DEVICE_LIST
        if path == "auth.me":
            return ME
        if path == "snapshot.getSnapshot":
            return {"base64": "/9j/4AAQ", "contentType": "image/jpeg"}
        raise AssertionError(f"unexpected query: {path}")

    return respond


# What the OTHER exporters have. On the live cluster the Alexa export and the
# HomeKit export each hold three cameras while the Home Assistant provider
# holds none, so reading the merged collection imports somebody else's
# selection and looks entirely healthy doing it (D12).
AGGREGATE_EXPOSED: list[dict[str, Any]] = [
    {"deviceId": "615", "exposedAs": "Videocamera ingresso ACBD"},
    {"deviceId": "590", "exposedAs": "Videocamera salone 822D"},
    {"deviceId": "587", "exposedAs": "Videocamera cucina DAF2"},
]


async def setup_integration(hass: Any, config_entry: MockConfigEntry) -> Any:
    """Load the integration and return its runtime data."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry.runtime_data


async def push(hass: Any, config_entry: MockConfigEntry, message: Any) -> None:
    """Deliver one message as the hub's POST would, and settle."""
    config_entry.runtime_data.push.async_handle_message(message)
    await hass.async_block_till_done()


HEARTBEAT: dict[str, Any] = {"type": "heartbeat", "ts": 1786142400000}


@pytest.fixture
def mock_client() -> Generator[AsyncMock]:
    """Patch the API client used by both the integration and the config flow."""
    with (
        patch(
            "custom_components.camstack.CamStackClient", autospec=True
        ) as integration_client,
        patch(
            "custom_components.camstack.config_flow.CamStackClient", autospec=True
        ) as flow_client,
    ):
        client = integration_client.return_value
        client.query = AsyncMock(side_effect=make_query_responder())
        client.mutate = AsyncMock(return_value={})
        client.async_post_addon_route = AsyncMock(return_value=None)
        client.async_login = AsyncMock(return_value=ME)
        client.async_verify = AsyncMock(return_value=ME)
        flow_client.return_value = client
        yield client
