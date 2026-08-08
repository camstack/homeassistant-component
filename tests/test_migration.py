"""Tests for the config entry migration.

The failure this guards against is specific: an operator upgrades, and the
CamStack that was in their sidebar is gone. Every test below is about the entry
they already have, not about one this component created.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.camstack.const import (
    CONF_PANEL_URL,
    CONF_VERIFY_SSL,
    CONFIG_ENTRY_VERSION,
    DEFAULT_PORT,
    DOMAIN,
    LEGACY_CONF_URL_BASE,
)
from custom_components.camstack.migration import _split_legacy_url

from .test_panel import panel


async def test_a_legacy_panel_entry_keeps_serving_its_panel(
    hass: HomeAssistant, mock_client: AsyncMock, legacy_panel_entry: MockConfigEntry
) -> None:
    """The sidebar must survive the upgrade even though credentials do not."""
    legacy_panel_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(legacy_panel_entry.entry_id)
    await hass.async_block_till_done()

    registered = panel(hass)
    assert registered is not None
    # Verbatim: the panel points where it pointed, not at a URL rebuilt from a
    # host and port this migration merely guessed.
    assert registered.config["url"] == "http://camstack.example.com:8443"
    assert registered.sidebar_title == "Telecamere"


async def test_a_legacy_panel_entry_asks_for_credentials_rather_than_failing_silently(
    hass: HomeAssistant, mock_client: AsyncMock, legacy_panel_entry: MockConfigEntry
) -> None:
    legacy_panel_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(legacy_panel_entry.entry_id)
    await hass.async_block_till_done()

    assert legacy_panel_entry.version == CONFIG_ENTRY_VERSION
    assert legacy_panel_entry.state is ConfigEntryState.SETUP_ERROR
    assert legacy_panel_entry.data[CONF_HOST] == "camstack.example.com"
    assert legacy_panel_entry.data[CONF_PORT] == 8443
    assert LEGACY_CONF_URL_BASE not in legacy_panel_entry.data
    assert (
        legacy_panel_entry.options[CONF_PANEL_URL] == "http://camstack.example.com:8443"
    )

    flows = [
        flow
        for flow in hass.config_entries.flow.async_progress_by_handler(DOMAIN)
        if flow["context"]["source"] == SOURCE_REAUTH
    ]
    assert len(flows) == 1


async def test_reusing_a_migrated_entry_creates_the_entities(
    hass: HomeAssistant, mock_client: AsyncMock, legacy_panel_entry: MockConfigEntry
) -> None:
    """The first reuse asks for credentials, and then everything appears."""
    legacy_panel_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(legacy_panel_entry.entry_id)
    await hass.async_block_till_done()

    flow = hass.config_entries.flow.async_progress_by_handler(DOMAIN)[0]
    result = await hass.config_entries.flow.async_configure(
        flow["flow_id"],
        {
            # The derived host was a guess; the form lets it be corrected,
            # which is the only reason a wrong guess is not a dead end.
            CONF_HOST: "192.168.1.9",
            CONF_PORT: 4443,
            CONF_USERNAME: "operator",
            CONF_PASSWORD: "secret",
            CONF_VERIFY_SSL: False,
        },
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert legacy_panel_entry.state is ConfigEntryState.LOADED
    assert legacy_panel_entry.unique_id == "192.168.1.9:4443"
    assert hass.states.get("camera.videocamera_ingresso") is not None
    # The panel still points at the address the operator was using; correcting
    # the API host does not silently move their browser somewhere else.
    assert panel(hass).config["url"] == "http://camstack.example.com:8443"


async def test_the_reauth_form_of_a_working_entry_asks_only_for_credentials(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """A rotated password must not make an operator retype their hub address."""
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reauth_flow(hass)

    assert result["step_id"] == "reauth_confirm"
    assert set(result["data_schema"].schema) == {CONF_USERNAME, CONF_PASSWORD}


async def test_an_entity_only_v1_entry_migrates_without_touching_its_credentials(
    hass: HomeAssistant, mock_client: AsyncMock, legacy_entity_entry: MockConfigEntry
) -> None:
    """The other component also wrote version 1 entries. They still work."""
    legacy_entity_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(legacy_entity_entry.entry_id)
    await hass.async_block_till_done()

    assert legacy_entity_entry.version == CONFIG_ENTRY_VERSION
    assert legacy_entity_entry.state is ConfigEntryState.LOADED
    assert legacy_entity_entry.data[CONF_PASSWORD] == "secret"
    assert CONF_PANEL_URL not in legacy_entity_entry.options
    assert panel(hass).config["url"] == "https://192.168.1.9:4443"


async def test_an_entry_from_a_newer_component_is_refused_not_rewritten(
    hass: HomeAssistant, mock_client: AsyncMock
) -> None:
    """Downgrading must not let this code mangle a shape it has never seen."""
    entry = MockConfigEntry(
        domain=DOMAIN, version=CONFIG_ENTRY_VERSION + 1, data={"whatever": True}
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.MIGRATION_ERROR
    assert entry.data == {"whatever": True}


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://camstack.example.com:8443", ("camstack.example.com", 8443)),
        ("https://camstack.example.com", ("camstack.example.com", 443)),
        ("http://192.168.1.100:8080", ("192.168.1.100", 8080)),
        # The old component accepted a path and resolved it against Home
        # Assistant's own address. There is no hub address in that at all.
        ("/local/camstack", ("", DEFAULT_PORT)),
        ("", ("", DEFAULT_PORT)),
        ("not a url", ("", DEFAULT_PORT)),
    ],
)
def test_the_legacy_url_yields_a_host_and_port_guess(
    url: str, expected: tuple[str, int]
) -> None:
    assert _split_legacy_url(url) == expected
