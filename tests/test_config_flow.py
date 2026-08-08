"""Tests for the CamStack config flow."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.camstack.api import CamStackAuthError, CamStackConnectionError
from custom_components.camstack.const import CONF_VERIFY_SSL, DOMAIN

USER_INPUT = {
    CONF_HOST: "192.168.1.9",
    CONF_PORT: 4443,
    CONF_USERNAME: "operator",
    CONF_PASSWORD: "secret",
    CONF_VERIFY_SSL: False,
}


async def test_user_flow_creates_an_entry(
    hass: HomeAssistant, mock_client: AsyncMock
) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "CamStack (192.168.1.9)"
    assert result["data"] == USER_INPUT
    assert result["result"].unique_id == "192.168.1.9:4443"


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (CamStackAuthError("nope"), "invalid_auth"),
        (CamStackConnectionError("unreachable"), "cannot_connect"),
    ],
)
async def test_user_flow_surfaces_failures_and_recovers(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    error: Exception,
    expected: str,
) -> None:
    mock_client.async_verify.side_effect = error
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": expected}

    # The form must remain usable: a wrong password is not a dead end.
    mock_client.async_verify.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_the_same_hub_cannot_be_added_twice(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_a_totp_protected_account_is_refused_not_stored(
    hass: HomeAssistant, mock_client: AsyncMock
) -> None:
    # Storing a password that only ever yields a TOTP challenge would create an
    # entry that can never load.
    mock_client.async_verify.side_effect = CamStackAuthError(
        "account requires two-factor authentication"
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_reauth_updates_the_stored_password(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_USERNAME: "operator", CONF_PASSWORD: "rotated"}
    )
    # The abort reloads the entry; letting that finish here keeps the reload
    # out of teardown, where it would race Home Assistant's shutdown.
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data[CONF_PASSWORD] == "rotated"
