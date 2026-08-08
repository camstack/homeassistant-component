"""Tests for the sidebar panel, the card asset and the address they derive."""

from __future__ import annotations

from unittest.mock import AsyncMock

from homeassistant.components.frontend import DATA_PANELS
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.camstack.const import (
    CONF_PANEL_ENABLED,
    CONF_PANEL_ICON,
    CONF_PANEL_TITLE,
    CONF_PANEL_URL,
    CONFIG_VIEW_URL,
    PANEL_URL_PATH,
)
from custom_components.camstack.frontend import CARD_MODULE_URL, PANEL_MODULE_URL

from .test_entities import setup_integration


def panel(hass: HomeAssistant):
    """Return the registered CamStack panel, or None."""
    return hass.data.get(DATA_PANELS, {}).get(PANEL_URL_PATH)


async def test_the_panel_url_is_derived_from_the_hub_the_entry_already_knows(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """The address is asked for once and reused, never asked for again."""
    await setup_integration(hass, config_entry)

    registered = panel(hass)
    assert registered is not None
    assert registered.config["url"] == "https://192.168.1.9:4443"
    assert registered.config["_panel_custom"]["module_url"] == PANEL_MODULE_URL
    # Nothing about the panel is stored: strip the entry's connection and the
    # URL cannot survive.
    assert CONF_PANEL_URL not in config_entry.data


async def test_the_panel_carries_the_configured_title_and_icon(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        config_entry,
        options={CONF_PANEL_TITLE: "Telecamere", CONF_PANEL_ICON: "mdi:video"},
    )
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    registered = panel(hass)
    assert registered.sidebar_title == "Telecamere"
    assert registered.sidebar_icon == "mdi:video"


async def test_the_panel_can_be_switched_off_without_losing_the_entities(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """An operator who does not want the sidebar keeps the cameras."""
    await setup_integration(hass, config_entry)
    assert panel(hass) is not None

    hass.config_entries.async_update_entry(
        config_entry, options={CONF_PANEL_ENABLED: False}
    )
    await hass.async_block_till_done()

    assert panel(hass) is None
    assert config_entry.state is ConfigEntryState.LOADED
    assert hass.states.get("camera.videocamera_ingresso") is not None


async def test_an_override_wins_over_the_derived_address(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """A browser may reach the hub at an address Home Assistant does not."""
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        config_entry, options={CONF_PANEL_URL: "https://cam.example.com/"}
    )
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert panel(hass).config["url"] == "https://cam.example.com"


async def test_the_options_flow_renames_the_sidebar_and_never_asks_for_an_address(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """The hub address is asked for once, in the config flow, and only there."""
    await setup_integration(hass, config_entry)

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    schema = set(result["data_schema"].schema)
    assert CONF_HOST not in schema
    assert CONF_PORT not in schema

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_PANEL_ENABLED: True,
            CONF_PANEL_TITLE: "Videosorveglianza",
            CONF_PANEL_ICON: "mdi:cctv",
            CONF_PANEL_URL: "",
        },
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert panel(hass).sidebar_title == "Videosorveglianza"
    # An empty override still derives, rather than blanking the panel.
    assert panel(hass).config["url"] == "https://192.168.1.9:4443"


async def test_unloading_the_entry_takes_the_panel_down(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    await setup_integration(hass, config_entry)
    assert panel(hass) is not None

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert panel(hass) is None


async def test_the_panel_and_the_card_are_served_by_the_integration(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    config_entry: MockConfigEntry,
    hass_client: ClientSessionGenerator,
) -> None:
    """The assets ship with the component; nothing has to be copied to www/."""
    await setup_integration(hass, config_entry)
    client = await hass_client()

    for url, marker in (
        (PANEL_MODULE_URL, "camstack-panel"),
        (CARD_MODULE_URL, "camstack-grid-card"),
    ):
        response = await client.get(url)
        assert response.status == 200
        assert marker in await response.text()


async def test_the_card_endpoint_reports_the_hub_so_the_card_need_not_ask(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    config_entry: MockConfigEntry,
    hass_client: ClientSessionGenerator,
) -> None:
    await setup_integration(hass, config_entry)
    client = await hass_client()

    response = await client.get(CONFIG_VIEW_URL)
    assert response.status == 200
    payload = await response.json()
    assert payload["entries"] == [
        {
            "entry_id": config_entry.entry_id,
            "title": "CamStack (192.168.1.9)",
            "url_base": "https://192.168.1.9:4443",
        }
    ]


async def test_the_card_endpoint_refuses_an_unauthenticated_caller(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    config_entry: MockConfigEntry,
    hass_client_no_auth: ClientSessionGenerator,
) -> None:
    """The hub address is not public information."""
    await setup_integration(hass, config_entry)
    client = await hass_client_no_auth()

    response = await client.get(CONFIG_VIEW_URL)
    assert response.status == 401
