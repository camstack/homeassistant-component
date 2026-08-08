"""Fixtures for the CamStack tests.

Every payload below is a **verbatim shape taken from a live CamStack hub**, not
a shape invented to match this code. A fake that supplies what production
forgot to fetch produces a green suite over a broken integration, so the
fixtures are recordings and the assertions are about behaviour.
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.camstack.const import (
    CONF_VERIFY_SSL,
    CONFIG_ENTRY_VERSION,
    DOMAIN,
    LEGACY_CONF_URL_BASE,
)

pytest_plugins = "pytest_homeassistant_custom_component"


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

SNAPSHOTS: dict[str, dict[str, dict[str, Any]]] = {
    "615": {
        "device-status": {"online": True, "lastChangedAt": 1786141769118},
        "motion": {
            "detected": False,
            "lastDetectedAt": 1786142120749,
            "autoClearAfterMs": 3000,
        },
        "zone-analytics": {
            "ts": 1786142087470,
            "frame": {"totalObjects": 0, "byClass": {}},
            "zones": [],
        },
        "audio-metrics": {"ts": 1786142213797, "peakDbfs": -50.3},
    },
    "291": {
        "device-status": {"online": True, "lastChangedAt": 1786035411846},
        "temperature-sensor": {
            "celsius": 23.7,
            "lastFetchedAt": 1786142299890,
            "unit": "C",
        },
        "battery": {
            "percentage": 100,
            "charging": "none",
            "sleeping": False,
            "lastUpdated": 1786035411846,
        },
    },
    "1027": {"device-status": {"online": True, "lastChangedAt": 1}},
}

SWITCH_GROUP: dict[str, Any] = {
    "deviceId": 615,
    "fetchedAt": 1786142400000,
    "switches": [
        {
            "id": "stream-broker",
            "label": "Camera",
            "costWhenOff": "Off: the whole camera stops.",
            "available": True,
            "enabled": True,
            "authority": {"kind": "device-disabled"},
        },
        {
            "id": "object-detection",
            "label": "Object detection & tracking",
            "costWhenOff": "Off: nothing is detected or tracked on this camera.",
            "available": True,
            "enabled": True,
            "authority": {"kind": "wrapper-binding", "capName": "detection-pipeline"},
        },
        {
            "id": "privacy-mask",
            "label": "Privacy mask",
            "costWhenOff": "On: the zones you drew are blacked out by the camera.",
            "available": False,
            "unavailableReason": "not-configured",
            "enabled": False,
            "authority": {"kind": "camera-mask", "capName": "privacy-mask"},
        },
    ],
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


def make_query_responder() -> Any:
    """Return an async responder mapping tRPC paths to recorded payloads."""

    async def respond(path: str, payload: Any | None = None) -> Any:
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


@pytest.fixture
def mock_client() -> Generator[AsyncMock]:
    """Patch the API client used by both the integration and the config flow."""

    async def never_yields(*_args: Any, **_kwargs: Any) -> Any:
        # The event stream must be inert in tests: a real one would make every
        # assertion depend on timing.
        if False:  # pragma: no cover
            yield {}

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
        client.mutate = AsyncMock(return_value=SWITCH_GROUP)
        client.async_login = AsyncMock(return_value=ME)
        client.async_verify = AsyncMock(return_value=ME)
        client.subscribe_events = never_yields
        flow_client.return_value = client
        yield client
