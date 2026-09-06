"""The cards are offered to Lovelace, and re-pointed on every release.

The resource entry carries `?v=<version>`; a stale query would pin every
browser to the card that shipped with the previous release.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.camstack.const import CARD_FILENAMES, DOMAIN, STATIC_URL_PATH
from custom_components.camstack.frontend import LOVELACE_DATA_KEY

from .test_entities import setup_integration

RESOURCES_STORAGE_KEY = "lovelace_resources"


def _card_resource_urls(hass: HomeAssistant) -> list[str]:
    resources = hass.data[LOVELACE_DATA_KEY].resources
    return sorted(
        str(item["url"])
        for item in resources.async_items()
        if STATIC_URL_PATH in str(item["url"])
    )


async def _expected_urls(hass: HomeAssistant) -> list[str]:
    version = (await async_get_integration(hass, DOMAIN)).version
    return sorted(f"{STATIC_URL_PATH}/{name}?v={version}" for name in CARD_FILENAMES)


async def test_every_card_is_registered_as_a_lovelace_resource(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    assert await async_setup_component(hass, "lovelace", {})
    await setup_integration(hass, config_entry)

    assert _card_resource_urls(hass) == await _expected_urls(hass)


async def test_a_resource_left_by_an_older_release_is_re_pointed(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    mock_client: AsyncMock,
    config_entry: MockConfigEntry,
) -> None:
    """One entry per card, at THIS release's version — never a duplicate."""
    stale = f"{STATIC_URL_PATH}/{CARD_FILENAMES[0]}?v=0.0.1"
    hass_storage[RESOURCES_STORAGE_KEY] = {
        "version": 1,
        "minor_version": 1,
        "key": RESOURCES_STORAGE_KEY,
        "data": {"items": [{"id": "stale", "type": "module", "url": stale}]},
    }
    assert await async_setup_component(hass, "lovelace", {})
    await setup_integration(hass, config_entry)

    assert _card_resource_urls(hass) == await _expected_urls(hass)
