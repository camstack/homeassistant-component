"""Tests for the credential the Lovelace cards' iframes carry."""

from __future__ import annotations

from unittest.mock import AsyncMock

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.camstack.api import CamStackForbiddenError
from custom_components.camstack.const import (
    EMBED_TOKEN_VIEW_URL,
    SHARE_TOKEN_MUTATION,
    SHARE_TOKEN_TTL,
)
from custom_components.camstack.embed_token import parse_device_ids

from .test_entities import setup_integration

# 615 is the camera every fixture in this suite exports.
EXPORTED = 615


def minted(expires_at_ms: int | None = 4_000_000_000_000) -> dict[str, object]:
    """Return a hub `auth.createShareToken` answer."""
    return {"id": "tok1", "token": "csv_deadbeef", "expiresAt": expires_at_ms}


async def post(client, body: dict[str, object]):
    """POST a mint request as an authenticated Home Assistant user."""
    return await client.post(EMBED_TOKEN_VIEW_URL, json=body)


async def test_a_card_is_given_a_scoped_share_token_never_the_hub_credential(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    config_entry: MockConfigEntry,
    hass_client: ClientSessionGenerator,
) -> None:
    """The OAuth token stays in Home Assistant; the browser gets `csv_…`."""
    await setup_integration(hass, config_entry)
    mock_client.mutate.reset_mock()
    mock_client.mutate.return_value = minted()
    client = await hass_client()

    response = await post(client, {"kind": "grid-view", "device_ids": [EXPORTED]})

    assert response.status == 200
    payload = await response.json()
    assert payload["token"] == "csv_deadbeef"
    # Epoch MILLISECONDS on the wire, seconds here. Read as seconds the token
    # would look 55 000 years fresh and never be re-minted.
    assert payload["expires_at"] == 4_000_000_000.0
    mock_client.mutate.assert_awaited_once_with(
        SHARE_TOKEN_MUTATION,
        {
            "scope": {"kind": "grid-view", "deviceIds": [EXPORTED]},
            "ttlSec": int(SHARE_TOKEN_TTL.total_seconds()),
        },
    )


async def test_the_same_scope_is_minted_once_and_then_reused(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    config_entry: MockConfigEntry,
    hass_client: ClientSessionGenerator,
) -> None:
    """A card re-renders many times a second; each one must not write a row."""
    await setup_integration(hass, config_entry)
    mock_client.mutate.reset_mock()
    mock_client.mutate.return_value = minted()
    client = await hass_client()

    for _ in range(3):
        assert (
            await post(client, {"kind": "grid-view", "device_ids": [EXPORTED]})
        ).status == 200

    assert mock_client.mutate.await_count == 1


async def test_a_token_about_to_expire_is_minted_again(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    config_entry: MockConfigEntry,
    hass_client: ClientSessionGenerator,
) -> None:
    """A cached credential inside the renewal margin is not handed out."""
    await setup_integration(hass, config_entry)
    mock_client.mutate.reset_mock()
    # Already expired: the cache must not serve it.
    mock_client.mutate.return_value = minted(expires_at_ms=1000)
    client = await hass_client()

    await post(client, {"kind": "grid-view", "device_ids": [EXPORTED]})
    await post(client, {"kind": "grid-view", "device_ids": [EXPORTED]})

    assert mock_client.mutate.await_count == 2


async def test_the_two_view_kinds_do_not_share_a_token(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    config_entry: MockConfigEntry,
    hass_client: ClientSessionGenerator,
) -> None:
    """`grid-view` and `events-view` are disjoint perimeters on the hub."""
    await setup_integration(hass, config_entry)
    mock_client.mutate.reset_mock()
    mock_client.mutate.return_value = minted()
    client = await hass_client()

    await post(client, {"kind": "grid-view", "device_ids": [EXPORTED]})
    await post(client, {"kind": "events-view", "device_ids": [EXPORTED]})

    assert mock_client.mutate.await_count == 2
    kinds = [
        call.args[1]["scope"]["kind"] for call in mock_client.mutate.await_args_list
    ]
    assert kinds == ["grid-view", "events-view"]


async def test_a_device_this_entry_does_not_export_is_refused(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    config_entry: MockConfigEntry,
    hass_client: ClientSessionGenerator,
) -> None:
    """Otherwise the card endpoint mints for any device the hub account sees."""
    await setup_integration(hass, config_entry)
    mock_client.mutate.reset_mock()
    client = await hass_client()

    response = await post(client, {"kind": "grid-view", "device_ids": [999999]})

    assert response.status == 400
    assert "999999" in (await response.json())["message"]
    mock_client.mutate.assert_not_awaited()


async def test_an_unknown_kind_is_refused_before_the_hub_is_called(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    config_entry: MockConfigEntry,
    hass_client: ClientSessionGenerator,
) -> None:
    await setup_integration(hass, config_entry)
    mock_client.mutate.reset_mock()
    client = await hass_client()

    response = await post(client, {"kind": "everything", "device_ids": [EXPORTED]})

    assert response.status == 400
    mock_client.mutate.assert_not_awaited()


async def test_the_hubs_own_refusal_reaches_the_dashboard(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    config_entry: MockConfigEntry,
    hass_client: ClientSessionGenerator,
) -> None:
    """The hub's own sentence reaches the dashboard, not a generic failure.

    "Could not mint a token" tells an operator nothing; "no scope grants view
    on 'device-export'" tells them everything.
    """
    await setup_integration(hass, config_entry)
    mock_client.mutate.reset_mock()
    mock_client.mutate.side_effect = CamStackForbiddenError(
        "auth.createShareToken: no scope grants view on 'device-export'"
    )
    client = await hass_client()

    response = await post(client, {"kind": "grid-view", "device_ids": [EXPORTED]})

    assert response.status == 502
    assert "device-export" in (await response.json())["message"]


async def test_an_unauthenticated_caller_gets_no_credential(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    config_entry: MockConfigEntry,
    hass_client_no_auth: ClientSessionGenerator,
) -> None:
    """A share token is a bearer credential for the hub."""
    await setup_integration(hass, config_entry)
    client = await hass_client_no_auth()

    response = await post(client, {"kind": "grid-view", "device_ids": [EXPORTED]})

    assert response.status == 401


def test_the_device_id_list_is_cleaned_without_being_reordered() -> None:
    """The grid renders in the given order; sorting would rearrange a wall."""
    assert parse_device_ids([617, 615, 617]) == [617, 615]
    assert parse_device_ids([]) is None
    assert parse_device_ids("615") is None
    assert parse_device_ids([-1]) is None
    # `True` is an `int` in Python and would silently become device 1.
    assert parse_device_ids([True]) is None
    assert parse_device_ids(list(range(65))) is None
