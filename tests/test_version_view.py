"""The handshake: what this integration tells the hub it can build.

The hub and this integration ship on different trains, so the hub asks
before it decides how to shape a device. Everything asserted here is a
WIRE FORMAT — a path, two field names and the platform spellings — and the
cost of getting one wrong is that every native entity on the installation
silently degrades back to a fan-out of sensors.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.camstack import PLATFORMS
from custom_components.camstack.const import VERSION_VIEW_URL

from .conftest import setup_integration


async def test_the_hub_is_told_the_platforms_this_version_builds(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    config_entry: MockConfigEntry,
    hass_client: ClientSessionGenerator,
) -> None:
    """The list is READ from `PLATFORMS`, so it cannot promise a stranger.

    A hand-kept second list would be one release away from telling the hub
    to send a platform nobody wired up, and that promise costs entities.
    """
    await setup_integration(hass, config_entry)
    client = await hass_client()

    response = await client.get(VERSION_VIEW_URL)

    assert response.status == 200
    payload = await response.json()
    assert payload["integration"] == "camstack"
    assert payload["platforms"] == sorted(platform.value for platform in PLATFORMS)


async def test_every_native_platform_is_announced(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    config_entry: MockConfigEntry,
    hass_client: ClientSessionGenerator,
) -> None:
    """The hub gates each native platform on finding its name in this list.

    Spelled out rather than derived: these ten strings are matched verbatim
    against the hub's `HaPlatform`, and a rename on either side is a
    migration rather than an edit.
    """
    await setup_integration(hass, config_entry)
    client = await hass_client()

    platforms = (await (await client.get(VERSION_VIEW_URL)).json())["platforms"]

    for platform in (
        "alarm_control_panel",
        "climate",
        "cover",
        "fan",
        "humidifier",
        "lock",
        "media_player",
        "vacuum",
        "valve",
        "water_heater",
    ):
        assert platform in platforms


async def test_the_version_travels_for_the_hubs_log_line(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    config_entry: MockConfigEntry,
    hass_client: ClientSessionGenerator,
) -> None:
    """Read from the manifest, never from a constant that could drift."""
    await setup_integration(hass, config_entry)
    client = await hass_client()

    payload = await (await client.get(VERSION_VIEW_URL)).json()

    assert payload["version"] not in (None, "", "0")


async def test_the_probe_answers_before_the_credentials_do(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    legacy_panel_entry: MockConfigEntry,
    hass_client: ClientSessionGenerator,
) -> None:
    """An entry waiting for credentials still answers the hub honestly.

    A 404 is how the hub tells an OLD integration from an unreachable one,
    so answering one here would put a fully-capable installation on the
    degraded projection until the operator finished re-authenticating.
    """
    legacy_panel_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(legacy_panel_entry.entry_id)
    await hass.async_block_till_done()
    client = await hass_client()

    response = await client.get(VERSION_VIEW_URL)

    assert response.status == 200
