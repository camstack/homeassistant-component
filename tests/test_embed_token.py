"""Tests for the credential the Lovelace cards' iframes carry."""

from __future__ import annotations

from unittest.mock import AsyncMock

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.camstack.api import CamStackForbiddenError
from custom_components.camstack.const import (
    CONF_TALK_ENABLED,
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


def allow_talk(hass: HomeAssistant, entry: MockConfigEntry, value: bool) -> None:
    """Set the entry option that is the ONLY authority for talk-back."""
    hass.config_entries.async_update_entry(
        entry, options={**dict(entry.options), CONF_TALK_ENABLED: value}
    )


def scopes(mock_client: AsyncMock) -> list[dict[str, object]]:
    """Every `scope` the hub was asked to mint, in order."""
    return [call.args[1]["scope"] for call in mock_client.mutate.await_args_list]


# --- talk-back --------------------------------------------------------------
#
# The hub carries an opt-in `talk` flag on `ShareTokenScopeSchema`: absent means
# off, and a `grid-view` token minted WITH it may call three named methods
# (`intercom.startTalkSession` / `pushTalkAudio` / `endTalkSession`) for the
# deviceIds it already carries. The flag is asked for at mint time and is never
# granted retroactively — a share link handed to somebody last week must not
# acquire the microphone of the house because a feature shipped.
#
# On THIS side the question is who may ask, and the answer is the entry's
# option and nothing else. A Lovelace config is editable by anyone who can edit
# a dashboard, and this endpoint is open to every authenticated user, so a tick
# box on a card alone would mean that editing a dashboard grants you the
# microphone. The card may only decline.


async def test_talk_back_is_off_until_the_entry_says_otherwise(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    config_entry: MockConfigEntry,
    hass_client: ClientSessionGenerator,
) -> None:
    """A card cannot grant itself the microphone.

    The default is OFF and no migration is needed to say so: the key is read
    out of `options` with a default, so an entry created before it existed
    answers `False` on its own.
    """
    await setup_integration(hass, config_entry)
    mock_client.mutate.reset_mock()
    mock_client.mutate.return_value = minted()
    client = await hass_client()

    response = await post(
        client, {"kind": "grid-view", "device_ids": [EXPORTED], "talk": True}
    )

    assert response.status == 200
    # Refused — but the WALL is still minted for. Taking the video down to
    # withhold a microphone would be a worse answer than saying no to the
    # microphone.
    assert (await response.json())["talk"] is False
    assert scopes(mock_client) == [{"kind": "grid-view", "deviceIds": [EXPORTED]}]


async def test_the_entry_option_is_what_grants_talk_back(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    config_entry: MockConfigEntry,
    hass_client: ClientSessionGenerator,
) -> None:
    """With the option on, an asking card gets `talk: true` on its scope."""
    await setup_integration(hass, config_entry)
    allow_talk(hass, config_entry, True)
    await hass.async_block_till_done()
    mock_client.mutate.reset_mock()
    mock_client.mutate.return_value = minted()
    client = await hass_client()

    response = await post(
        client, {"kind": "grid-view", "device_ids": [EXPORTED], "talk": True}
    )

    assert response.status == 200
    assert (await response.json())["talk"] is True
    assert scopes(mock_client) == [
        {"kind": "grid-view", "deviceIds": [EXPORTED], "talk": True}
    ]


async def test_a_card_may_decline_talk_back_the_option_allows(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    config_entry: MockConfigEntry,
    hass_client: ClientSessionGenerator,
) -> None:
    """The card can only RESTRICT. A card that does not ask gets a silent token.

    And the scope it produces is byte-identical to the one a card sent before
    talk-back existed — `talk` is OMITTED, never sent as `false`, because the
    hub's schema says absent means off.
    """
    await setup_integration(hass, config_entry)
    allow_talk(hass, config_entry, True)
    await hass.async_block_till_done()
    mock_client.mutate.reset_mock()
    mock_client.mutate.return_value = minted()
    client = await hass_client()

    response = await post(client, {"kind": "grid-view", "device_ids": [EXPORTED]})

    assert (await response.json())["talk"] is False
    assert scopes(mock_client) == [{"kind": "grid-view", "deviceIds": [EXPORTED]}]


async def test_a_talking_token_is_never_served_to_a_request_that_did_not_ask(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    config_entry: MockConfigEntry,
    hass_client: ClientSessionGenerator,
) -> None:
    """`talk` is part of the CACHE KEY, and this is the defect that proves it.

    The cache is keyed per (entry, kind, device ids) so a card re-rendering
    several times a second does not write a row into the hub's token table each
    time. Two tokens for the same cameras are nevertheless DIFFERENT credentials
    when one of them can speak into the house: without `talk` in the key, the
    first mint of a scope is handed to every later request for it — a card that
    never asked would be given a talking token, and a card that did would be
    given a silent one and look broken.
    """
    await setup_integration(hass, config_entry)
    allow_talk(hass, config_entry, True)
    await hass.async_block_till_done()
    mock_client.mutate.reset_mock()
    mock_client.mutate.return_value = minted()
    client = await hass_client()

    talking = await post(
        client, {"kind": "grid-view", "device_ids": [EXPORTED], "talk": True}
    )
    silent = await post(client, {"kind": "grid-view", "device_ids": [EXPORTED]})

    assert (await talking.json())["talk"] is True
    assert (await silent.json())["talk"] is False
    # TWO mints, not one: they are not the same credential.
    assert mock_client.mutate.await_count == 2
    assert scopes(mock_client) == [
        {"kind": "grid-view", "deviceIds": [EXPORTED], "talk": True},
        {"kind": "grid-view", "deviceIds": [EXPORTED]},
    ]
    # …and two GRANTS, or the relay would inject whichever token was issued
    # last behind a single same-origin path.
    assert (await talking.json())["proxy_base"] != (await silent.json())["proxy_base"]


async def test_a_talking_scope_is_reused_like_any_other(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    config_entry: MockConfigEntry,
    hass_client: ClientSessionGenerator,
) -> None:
    """Splitting the key must not cost the cache: the same ask still mints once."""
    await setup_integration(hass, config_entry)
    allow_talk(hass, config_entry, True)
    await hass.async_block_till_done()
    mock_client.mutate.reset_mock()
    mock_client.mutate.return_value = minted()
    client = await hass_client()

    body = {"kind": "grid-view", "device_ids": [EXPORTED], "talk": True}
    await post(client, body)
    await post(client, body)

    assert mock_client.mutate.await_count == 1


async def test_an_events_token_never_carries_talk_back(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    config_entry: MockConfigEntry,
    hass_client: ClientSessionGenerator,
) -> None:
    """The events perimeter has no camera to speak through.

    Confined here rather than left to the hub to ignore: a flag that travels on
    a scope it means nothing for is a flag somebody will eventually honour.
    """
    await setup_integration(hass, config_entry)
    allow_talk(hass, config_entry, True)
    await hass.async_block_till_done()
    mock_client.mutate.reset_mock()
    mock_client.mutate.return_value = minted()
    client = await hass_client()

    response = await post(
        client, {"kind": "events-view", "device_ids": [EXPORTED], "talk": True}
    )

    assert (await response.json())["talk"] is False
    assert scopes(mock_client) == [{"kind": "events-view", "deviceIds": [EXPORTED]}]


async def test_talk_must_be_a_boolean(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    config_entry: MockConfigEntry,
    hass_client: ClientSessionGenerator,
) -> None:
    """Refused before the hub is called, like `direct`."""
    await setup_integration(hass, config_entry)
    mock_client.mutate.reset_mock()
    client = await hass_client()

    response = await post(
        client, {"kind": "grid-view", "device_ids": [EXPORTED], "talk": "yes"}
    )

    assert response.status == 400
    mock_client.mutate.assert_not_awaited()


async def test_a_card_is_given_a_scoped_share_token_never_the_hub_credential(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    config_entry: MockConfigEntry,
    hass_client: ClientSessionGenerator,
) -> None:
    """The OAuth token stays in Home Assistant; the browser gets `csv_…`.

    `direct`: this is the card that frames the hub itself, with no relay in
    front of it. It is the only shape that still needs the credential in the
    browser — see the relayed test below.
    """
    await setup_integration(hass, config_entry)
    mock_client.mutate.reset_mock()
    mock_client.mutate.return_value = minted()
    client = await hass_client()

    response = await post(
        client, {"kind": "grid-view", "device_ids": [EXPORTED], "direct": True}
    )

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


async def test_the_relayed_mint_keeps_the_token_inside_home_assistant(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    config_entry: MockConfigEntry,
    hass_client: ClientSessionGenerator,
) -> None:
    """The default answer carries the grant, and NOT the credential.

    A card without `url_base` reaches the hub through `proxy.py`, which strips
    the browser's `Authorization` and injects the share token itself. A copy of
    that token in the page authenticates nothing there, and it used to sit in
    the events iframe's `#t=` fragment — in browser history, in screenshots,
    usable against the hub from outside Home Assistant for the rest of its hour.
    """
    await setup_integration(hass, config_entry)
    mock_client.mutate.reset_mock()
    mock_client.mutate.return_value = minted()
    client = await hass_client()

    response = await post(client, {"kind": "grid-view", "device_ids": [EXPORTED]})

    assert response.status == 200
    payload = await response.json()
    assert "token" not in payload
    assert "csv_deadbeef" not in await response.text()
    # The grant IS handed over: it is what authorises the frame, and unlike the
    # token it only works from a Home Assistant session and dies with it.
    assert payload["proxy_base"]
    # And the expiry, because the card has to know when to come back for a
    # fresh grant. It is not a secret.
    assert payload["expires_at"] == 4_000_000_000.0
    # The hub was still asked for a real, scoped token — the credential exists,
    # it simply stays here.
    mock_client.mutate.assert_awaited_once()


async def test_an_events_card_is_relayed_by_default_too(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    config_entry: MockConfigEntry,
    hass_client: ClientSessionGenerator,
) -> None:
    """The reel was the worst offender: its token rode the iframe URL."""
    await setup_integration(hass, config_entry)
    mock_client.mutate.reset_mock()
    mock_client.mutate.return_value = minted()
    client = await hass_client()

    response = await post(client, {"kind": "events-view", "device_ids": [EXPORTED]})

    assert response.status == 200
    assert "token" not in await response.json()


async def test_the_cached_answer_withholds_the_token_as_well(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    config_entry: MockConfigEntry,
    hass_client: ClientSessionGenerator,
) -> None:
    """The cache serves the same MintedToken to both paths; only the answer differs.

    Without this the second render of a relayed card — served from the cache,
    not from a fresh mint — would leak what the first one withheld.
    """
    await setup_integration(hass, config_entry)
    mock_client.mutate.reset_mock()
    mock_client.mutate.return_value = minted()
    client = await hass_client()

    direct = await post(
        client, {"kind": "grid-view", "device_ids": [EXPORTED], "direct": True}
    )
    relayed = await post(client, {"kind": "grid-view", "device_ids": [EXPORTED]})

    assert (await direct.json())["token"] == "csv_deadbeef"
    assert "token" not in await relayed.json()
    assert mock_client.mutate.await_count == 1


async def test_direct_must_be_a_boolean(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    config_entry: MockConfigEntry,
    hass_client: ClientSessionGenerator,
) -> None:
    """A truthy string must not talk its way into a credential."""
    await setup_integration(hass, config_entry)
    mock_client.mutate.return_value = minted()
    client = await hass_client()

    response = await post(
        client, {"kind": "grid-view", "device_ids": [EXPORTED], "direct": "yes"}
    )

    assert response.status == 400
