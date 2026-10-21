"""Tests for the credential the Lovelace cards' iframes carry."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.camstack import token_lifecycle
from custom_components.camstack.api import (
    CamStackApiError,
    CamStackConnectionError,
    CamStackForbiddenError,
)
from custom_components.camstack.const import (
    CONF_TALK_ENABLED,
    EMBED_TOKEN_LIFETIME,
    EMBED_TOKEN_RENEW_BEFORE,
    EMBED_TOKEN_VIEW_URL,
    TOKEN_EXTEND_MUTATION,
    TOKEN_LIST_QUERY,
    TOKEN_MINT_MUTATION,
    TOKEN_REVOKE_MUTATION,
)
from custom_components.camstack.embed_token import parse_device_ids
from custom_components.camstack.proxy import async_issue_grant, proxy_base_for
from custom_components.camstack.token_lifecycle import MintedToken, TokenScope

from .test_entities import setup_integration

# 615 is the camera every fixture in this suite exports.
EXPORTED = 615
DAY = 86_400


class FakeHub:
    """The hub's four token procedures, with the state they keep.

    Stateful on purpose: a fake that answered every `listTokens` with the same
    list could not tell "found the token it already holds" from "minted a
    duplicate", which is the whole point of the label.
    """

    def __init__(self, base_query: Callable[..., Awaitable[Any]]) -> None:
        """Answer every other query with the suite's usual responder."""
        self._base_query = base_query
        self.rows: dict[str, dict[str, Any]] = {}
        self.secrets: dict[str, str] = {}
        self.calls: list[tuple[str, Any]] = []
        #: path -> exception raised for that call.
        self.fail: dict[str, Exception] = {}
        #: path -> coroutine run once, mid-call (to interleave another request).
        self.hooks: dict[str, Callable[[], Awaitable[None]]] = {}
        #: path -> canned answer replacing the real one.
        self.answers: dict[str, Any] = {}
        self._serial = 0

    @staticmethod
    def now_ms() -> int:
        """Return the hub's clock: epoch milliseconds."""
        return int(dt_util.utcnow().timestamp() * 1000)

    async def query(self, path: str, payload: Any | None = None) -> Any:
        """Answer `auth.listTokens` from the rows, everything else as before."""
        if path != TOKEN_LIST_QUERY:
            return await self._base_query(path, payload)
        self.calls.append((path, payload))
        await asyncio.sleep(0)  # a real hub call yields; races need it to
        if path in self.fail:
            raise self.fail[path]
        label = (payload or {}).get("label")
        return [
            dict(row)
            for row in self.rows.values()
            if label is None or row["label"] == label
        ]

    async def mutate(self, path: str, payload: Any | None = None) -> Any:
        """Mint, extend or revoke a row."""
        self.calls.append((path, payload))
        await asyncio.sleep(0)
        if path in self.hooks:
            await self.hooks.pop(path)()
        if path in self.fail:
            raise self.fail[path]
        if path == TOKEN_MINT_MUTATION:
            return self._mint(payload)
        if path in self.answers:
            return self.answers[path]
        if path == TOKEN_EXTEND_MUTATION:
            row = self.rows.get(payload["id"])
            if row is None or row["revokedAt"] is not None:
                raise CamStackApiError(f"{path}: Token not found: {payload['id']}")
            row["expiresAt"] = self.now_ms() + payload["expiresIn"] * 1000
            return dict(row)
        if path == TOKEN_REVOKE_MUTATION:
            row = self.rows.get(payload["id"])
            if row is None or row["revokedAt"] is not None:
                return {"success": False}
            row["revokedAt"] = self.now_ms()
            return {"success": True}
        return {}

    def _mint(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._serial += 1
        token_id = f"tok{self._serial}"
        row = {
            "id": token_id,
            "subjectId": "user-1",
            "label": payload["label"],
            "scopes": payload["scopes"],
            "createdAt": self.now_ms(),
            "expiresAt": self.now_ms() + payload["expiresIn"] * 1000,
            "lastUsedAt": None,
            "revokedAt": None,
        }
        self.rows[token_id] = row
        self.secrets[token_id] = f"jwt-secret-{self._serial}"
        return {"token": self.secrets[token_id], "record": dict(row)}

    def plant(self, label: str, *, expires_in: int = 80 * DAY) -> str:
        """Put a row on the hub this Home Assistant holds no secret for."""
        minted = self._mint({"label": label, "scopes": [], "expiresIn": expires_in})
        return minted["record"]["id"]

    def count(self, path: str) -> int:
        """Return how often `path` was called."""
        return sum(1 for called, _ in self.calls if called == path)

    def grants(self) -> list[dict[str, Any]]:
        """Every view grant the hub was asked to mint, in order."""
        return [
            payload["scopes"][0]["grant"]
            for called, payload in self.calls
            if called == TOKEN_MINT_MUTATION
        ]

    def revoked(self) -> list[str]:
        """Return every id the hub was asked to revoke, in order."""
        return [
            payload["id"]
            for called, payload in self.calls
            if called == TOKEN_REVOKE_MUTATION
        ]

    def live(self) -> list[str]:
        """Return the ids of the rows not revoked."""
        return [i for i, row in self.rows.items() if row["revokedAt"] is None]


@pytest.fixture
async def hub(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> FakeHub:
    """Load the entry and point its client at a stateful fake hub."""
    await setup_integration(hass, config_entry)
    fake = FakeHub(mock_client.query.side_effect)
    mock_client.query.side_effect = fake.query
    mock_client.mutate.reset_mock()
    mock_client.mutate.side_effect = fake.mutate
    return fake


async def post(client, body: dict[str, object]):
    """POST a mint request as an authenticated Home Assistant user."""
    return await client.post(EMBED_TOKEN_VIEW_URL, json=body)


def allow_talk(hass: HomeAssistant, entry: MockConfigEntry, value: bool) -> None:
    """Set the entry option that is the ONLY authority for talk-back."""
    hass.config_entries.async_update_entry(
        entry, options={**dict(entry.options), CONF_TALK_ENABLED: value}
    )


def restart(hass: HomeAssistant) -> None:
    """Drop the keeper as a restart does: memory goes, the Store stays."""
    hass.data.pop(token_lifecycle._KEEPER, None)


def grid(*device_ids: int, **extra: object) -> dict[str, object]:
    return {"kind": "grid-view", "device_ids": list(device_ids), **extra}


# --- the mint -----------------------------------------------------------------


async def test_a_card_is_given_a_scoped_view_token_never_the_hub_credential(
    hub: FakeHub, hass_client: ClientSessionGenerator
) -> None:
    """The OAuth token stays in Home Assistant; the hub mints a view token.

    `direct`: this is the card that frames the hub itself, with no relay in
    front of it — the only shape that still gets the credential.
    """
    client = await hass_client()

    response = await post(client, grid(EXPORTED, direct=True))

    assert response.status == 200
    payload = await response.json()
    assert payload["token"] == "jwt-secret-1"
    mint = next(p for called, p in hub.calls if called == TOKEN_MINT_MUTATION)
    assert mint["scopes"] == [
        {"type": "share-view", "grant": {"kind": "grid-view", "deviceIds": [EXPORTED]}}
    ]
    assert mint["expiresIn"] == int(EMBED_TOKEN_LIFETIME.total_seconds())
    # Epoch MILLISECONDS on the wire, seconds here. Read as seconds the token
    # would look 55 000 years fresh and never be renewed.
    expected = hub.rows["tok1"]["expiresAt"] / 1000
    assert payload["expires_at"] == pytest.approx(expected)


def test_the_lifetime_is_the_hubs_ninety_day_maximum() -> None:
    """Longer is refused by the hub; `never` is not asked for."""
    assert EMBED_TOKEN_LIFETIME.total_seconds() == 90 * DAY
    assert EMBED_TOKEN_RENEW_BEFORE.total_seconds() == 7 * DAY


def test_the_label_is_readable_unique_per_scope_and_bounded() -> None:
    """The label is how the token is found again, so it names the scope."""
    four = TokenScope("entry-a", "grid-view", (1, 2, 3, 4), False)
    label = four.label
    assert label.startswith("Home Assistant · grid · 4 cameras · #")
    assert len(label) <= 80
    # Stable: the same scope always yields the same label.
    assert TokenScope("entry-a", "grid-view", (1, 2, 3, 4), False).label == label
    # Different for every component of the scope.
    others = {
        TokenScope("entry-b", "grid-view", (1, 2, 3, 4), False).label,
        TokenScope("entry-a", "events-view", (1, 2, 3, 4), False).label,
        TokenScope("entry-a", "grid-view", (4, 3, 2, 1), False).label,
        TokenScope("entry-a", "grid-view", (1, 2, 3, 4), True).label,
    }
    assert label not in others
    assert len(others) == 4
    one = TokenScope("e", "events-view", (615,), False).label
    assert one.startswith("Home Assistant · events · 1 camera · #")


async def test_the_same_scope_is_minted_once_and_then_reused(
    hub: FakeHub, hass_client: ClientSessionGenerator
) -> None:
    """A card re-renders many times a second; none of that reaches the hub."""
    client = await hass_client()

    for _ in range(3):
        assert (await post(client, grid(EXPORTED))).status == 200

    assert hub.count(TOKEN_MINT_MUTATION) == 1
    # Looked up by its label once, before the first mint — never per render.
    assert hub.count(TOKEN_LIST_QUERY) == 1


async def test_the_two_view_kinds_do_not_share_a_token(
    hub: FakeHub, hass_client: ClientSessionGenerator
) -> None:
    """`grid-view` and `events-view` are disjoint perimeters on the hub."""
    client = await hass_client()

    await post(client, grid(EXPORTED))
    await post(client, {"kind": "events-view", "device_ids": [EXPORTED]})

    assert [g["kind"] for g in hub.grants()] == ["grid-view", "events-view"]


# --- restart: find, never duplicate -------------------------------------------


async def test_a_restart_finds_the_token_it_already_holds(
    hass: HomeAssistant, hub: FakeHub, hass_client: ClientSessionGenerator
) -> None:
    """The secret is in the Store, the row on the hub: reuse, never re-mint."""
    client = await hass_client()
    first = await (await post(client, grid(EXPORTED, direct=True))).json()

    restart(hass)
    second = await (await post(client, grid(EXPORTED, direct=True))).json()

    assert second["token"] == first["token"]
    assert hub.count(TOKEN_MINT_MUTATION) == 1
    assert hub.revoked() == []
    # It ASKED the hub, by label, rather than trusting the disk blindly.
    assert hub.count(TOKEN_LIST_QUERY) == 2


async def test_a_lost_secret_revokes_the_found_row_and_mints_anew(
    hass: HomeAssistant, hub: FakeHub, hass_client: ClientSessionGenerator
) -> None:
    """A row nobody holds the secret of is dead weight that still grants access."""
    scope = TokenScope(
        hass.config_entries.async_entries("camstack")[0].entry_id,
        "grid-view",
        (EXPORTED,),
        False,
    )
    orphan = hub.plant(scope.label)
    client = await hass_client()

    response = await post(client, grid(EXPORTED, direct=True))

    assert response.status == 200
    assert hub.revoked() == [orphan]
    assert hub.count(TOKEN_MINT_MUTATION) == 1
    assert hub.live() == ["tok2"]
    assert (await response.json())["token"] == hub.secrets["tok2"]


async def test_a_stored_token_the_hub_no_longer_lists_is_replaced(
    hass: HomeAssistant, hub: FakeHub, hass_client: ClientSessionGenerator
) -> None:
    """Revoked from the hub's UI, or the entry now points at another hub."""
    client = await hass_client()
    await post(client, grid(EXPORTED))
    hub.rows["tok1"]["revokedAt"] = hub.now_ms()

    restart(hass)
    response = await post(client, grid(EXPORTED, direct=True))

    assert (await response.json())["token"] == hub.secrets["tok2"]
    assert hub.live() == ["tok2"]


async def test_a_hub_that_cannot_list_does_not_take_the_wall_down(
    hass: HomeAssistant, hub: FakeHub, hass_client: ClientSessionGenerator
) -> None:
    """Unverified is not wrong: serve the stored token and ask again next time."""
    client = await hass_client()
    await post(client, grid(EXPORTED))
    restart(hass)
    hub.fail[TOKEN_LIST_QUERY] = CamStackConnectionError("auth.listTokens: down")

    response = await post(client, grid(EXPORTED, direct=True))

    assert (await response.json())["token"] == hub.secrets["tok1"]
    assert hub.count(TOKEN_MINT_MUTATION) == 1
    del hub.fail[TOKEN_LIST_QUERY]
    await post(client, grid(EXPORTED))
    assert hub.count(TOKEN_LIST_QUERY) == 3


async def test_an_unload_keeps_the_tokens_and_reverifies_them(
    hass: HomeAssistant,
    hub: FakeHub,
    config_entry: MockConfigEntry,
    hass_client: ClientSessionGenerator,
) -> None:
    """Every options change reloads the entry; that must not cost a token."""
    client = await hass_client()
    await post(client, grid(EXPORTED))

    assert await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()
    response = await post(client, grid(EXPORTED, direct=True))

    assert (await response.json())["token"] == hub.secrets["tok1"]
    assert hub.count(TOKEN_MINT_MUTATION) == 1
    assert hub.revoked() == []
    assert hub.count(TOKEN_LIST_QUERY) == 2


# --- renewal -------------------------------------------------------------------


async def test_a_token_near_expiry_is_extended_in_place(
    hass: HomeAssistant, hub: FakeHub, hass_client: ClientSessionGenerator
) -> None:
    """Under a week left: the SAME secret gets a fresh 90 days."""
    client = await hass_client()
    first = await (await post(client, grid(EXPORTED, direct=True))).json()
    hub.rows["tok1"]["expiresAt"] = hub.now_ms() + 3 * DAY * 1000
    restart(hass)  # re-reads the row's expiry from the hub

    second = await (await post(client, grid(EXPORTED, direct=True))).json()

    assert second["token"] == first["token"]
    assert hub.count(TOKEN_EXTEND_MUTATION) == 1
    assert hub.count(TOKEN_MINT_MUTATION) == 1
    now = dt_util.utcnow().timestamp()
    assert second["expires_at"] == pytest.approx(now + 90 * DAY, abs=5)


async def test_a_token_with_weeks_left_is_not_extended(
    hass: HomeAssistant, hub: FakeHub, hass_client: ClientSessionGenerator
) -> None:
    client = await hass_client()
    await post(client, grid(EXPORTED))
    hub.rows["tok1"]["expiresAt"] = hub.now_ms() + 30 * DAY * 1000
    restart(hass)

    await post(client, grid(EXPORTED))

    assert hub.count(TOKEN_EXTEND_MUTATION) == 0


async def test_a_refused_extension_mints_a_replacement(
    hass: HomeAssistant, hub: FakeHub, hass_client: ClientSessionGenerator
) -> None:
    """The hub ANSWERED that the row is gone: replace it."""
    client = await hass_client()
    await post(client, grid(EXPORTED))
    keeper = token_lifecycle.async_get_keeper(hass)
    key = next(iter(keeper._tokens))
    keeper._tokens[key] = MintedToken(
        "jwt-secret-1", "tok1", dt_util.utcnow().timestamp() + DAY
    )
    # Revoked on the hub: the fake answers NOT_FOUND, and the list agrees.
    hub.rows["tok1"]["revokedAt"] = hub.now_ms()

    response = await post(client, grid(EXPORTED, direct=True))

    assert (await response.json())["token"] == hub.secrets["tok2"]
    assert hub.live() == ["tok2"]


async def test_an_unreachable_hub_keeps_serving_a_token_due_for_renewal(
    hass: HomeAssistant, hub: FakeHub, hass_client: ClientSessionGenerator
) -> None:
    client = await hass_client()
    await post(client, grid(EXPORTED))
    keeper = token_lifecycle.async_get_keeper(hass)
    key = next(iter(keeper._tokens))
    keeper._tokens[key] = MintedToken(
        "jwt-secret-1", "tok1", dt_util.utcnow().timestamp() + DAY
    )
    hub.fail[TOKEN_EXTEND_MUTATION] = CamStackConnectionError("timed out")

    response = await post(client, grid(EXPORTED, direct=True))

    assert (await response.json())["token"] == "jwt-secret-1"
    assert hub.count(TOKEN_MINT_MUTATION) == 1


async def test_an_expired_token_is_minted_again(
    hass: HomeAssistant, hub: FakeHub, hass_client: ClientSessionGenerator
) -> None:
    """A cached credential past its expiry is never handed out."""
    client = await hass_client()
    await post(client, grid(EXPORTED))
    keeper = token_lifecycle.async_get_keeper(hass)
    key = next(iter(keeper._tokens))
    keeper._tokens[key] = MintedToken("jwt-secret-1", "tok1", 1000.0)

    await post(client, grid(EXPORTED))

    assert hub.count(TOKEN_MINT_MUTATION) == 2


def due_for_renewal(hass: HomeAssistant) -> None:
    """Move the one stored token to a day before its expiry."""
    keeper = token_lifecycle.async_get_keeper(hass)
    key = next(iter(keeper._tokens))
    keeper._tokens[key] = MintedToken(
        "jwt-secret-1", "tok1", dt_util.utcnow().timestamp() + DAY
    )


async def test_a_hub_error_on_extend_keeps_a_row_the_hub_still_lists(
    hass: HomeAssistant, hub: FakeHub, hass_client: ClientSessionGenerator
) -> None:
    """A 5xx from a proxy in front of the hub is not "the row is gone"."""
    client = await hass_client()
    await post(client, grid(EXPORTED))
    due_for_renewal(hass)
    hub.fail[TOKEN_EXTEND_MUTATION] = CamStackApiError("auth.extendToken: HTTP 502")

    response = await post(client, grid(EXPORTED, direct=True))

    assert (await response.json())["token"] == "jwt-secret-1"
    assert hub.count(TOKEN_MINT_MUTATION) == 1
    assert hub.revoked() == []


async def test_an_extend_answer_without_an_expiry_is_not_never(
    hass: HomeAssistant, hub: FakeHub, hass_client: ClientSessionGenerator
) -> None:
    """Read as "never", it would stop the renewals and die on the hub."""
    client = await hass_client()
    await post(client, grid(EXPORTED))
    due_for_renewal(hass)
    hub.answers[TOKEN_EXTEND_MUTATION] = {"id": "tok1"}

    await post(client, grid(EXPORTED))

    keeper = token_lifecycle.async_get_keeper(hass)
    assert next(iter(keeper._tokens.values())).expires_at is not None


async def test_concurrent_first_requests_mint_once_per_scope(
    hass: HomeAssistant, hub: FakeHub, hass_client: ClientSessionGenerator
) -> None:
    """A dashboard renders every card at once, on a cold keeper."""
    restart(hass)
    client = await hass_client()

    answers = await asyncio.gather(
        post(client, grid(EXPORTED)),
        post(client, grid(EXPORTED)),
        post(client, {"kind": "events-view", "device_ids": [EXPORTED]}),
        post(client, {"kind": "events-view", "device_ids": [EXPORTED]}),
    )

    assert [a.status for a in answers] == [200] * 4
    assert hub.count(TOKEN_MINT_MUTATION) == 2
    keeper = token_lifecycle.async_get_keeper(hass)
    assert len(keeper._tokens) == 2


async def test_an_entry_removed_mid_mint_leaves_no_live_token(
    hass: HomeAssistant,
    hub: FakeHub,
    config_entry: MockConfigEntry,
    hass_client: ClientSessionGenerator,
) -> None:
    """The mint lands after the purge: it must be revoked, not stored."""
    client = await hass_client()
    keeper = token_lifecycle.async_get_keeper(hass)

    async def remove_now() -> None:
        await keeper.async_revoke_entry(None, config_entry.entry_id)

    hub.hooks[TOKEN_MINT_MUTATION] = remove_now

    response = await post(client, grid(EXPORTED))

    assert response.status == 502
    assert hub.live() == []
    assert keeper._tokens == {}


# --- revocation ----------------------------------------------------------------


async def test_a_card_that_changes_its_device_set_revokes_the_old_token(
    hass: HomeAssistant,
    hub: FakeHub,
    config_entry: MockConfigEntry,
    hass_client: ClientSessionGenerator,
) -> None:
    """The old scope's token must not stay alive for 90 days.

    The card held a two-camera wall and now shows one camera; it names the old
    set in `replaces`. Seeded by hand because the fixtures export one camera.
    """
    old = TokenScope(config_entry.entry_id, "grid-view", (EXPORTED, 590), False)
    old_id = hub.plant(old.label)
    keeper = token_lifecycle.async_get_keeper(hass)
    keeper._tokens[old.key] = MintedToken(hub.secrets[old_id], old_id, None)
    old_grant = async_issue_grant(
        hass, old.entry_id, old.grant_key, hub.secrets[old_id], None
    )
    client = await hass_client()

    new = await post(client, grid(EXPORTED, replaces={"device_ids": [EXPORTED, 590]}))

    assert new.status == 200, await new.text()
    assert hub.revoked() == [old_id]
    assert hub.live() == ["tok2"]
    assert old.key not in keeper._tokens
    # The old grant is gone too: a page still framing it sees a stale grant
    # (404) and asks again, instead of relaying a revoked token.
    relayed = await client.get(f"{proxy_base_for(old_grant)}/viewer/x")
    assert relayed.status == 404


async def test_replaces_naming_the_same_scope_revokes_nothing(
    hub: FakeHub, hass_client: ClientSessionGenerator
) -> None:
    client = await hass_client()
    await post(client, grid(EXPORTED))

    await post(client, grid(EXPORTED, replaces={"device_ids": [EXPORTED]}))

    assert hub.revoked() == []


async def test_a_malformed_replaces_is_refused(
    hub: FakeHub, hass_client: ClientSessionGenerator
) -> None:
    client = await hass_client()

    for bad in ("615", {"device_ids": "615"}, {"device_ids": [615], "talk": "y"}):
        response = await post(client, grid(EXPORTED, replaces=bad))
        assert response.status == 400

    assert hub.count(TOKEN_MINT_MUTATION) == 0


async def test_removing_the_entry_revokes_its_tokens(
    hass: HomeAssistant,
    hub: FakeHub,
    config_entry: MockConfigEntry,
    hass_client: ClientSessionGenerator,
) -> None:
    """A removed entry leaves no live credential on the hub."""
    client = await hass_client()
    await post(client, grid(EXPORTED))
    await post(client, {"kind": "events-view", "device_ids": [EXPORTED]})

    assert await hass.config_entries.async_remove(config_entry.entry_id)
    await hass.async_block_till_done()

    assert sorted(hub.revoked()) == ["tok1", "tok2"]
    assert hub.live() == []
    assert token_lifecycle.async_get_keeper(hass)._tokens == {}


def test_minted_token_store_round_trip() -> None:
    """Store serialization keeps secret + id + expiry, and rejects junk."""
    original = MintedToken(token="jwt-abc", id="tok1", expires_at=1_700_000_000.0)
    assert MintedToken.from_storage(original.to_storage()) == original
    assert MintedToken.from_storage({"token": ""}) is None
    # A row without an id can be neither renewed nor revoked: not a token.
    assert MintedToken.from_storage({"token": "jwt", "expires_at": 1.0}) is None
    assert MintedToken.from_storage("nope") is None


# --- talk-back -------------------------------------------------------------
#
# A `share-view` grant carries an opt-in `talk` flag: absent means off, and a
# `grid-view` token minted WITH it may talk back on the deviceIds it already
# carries. On THIS side the question is who may ask, and the answer is the
# entry's option and nothing else. A Lovelace config is editable by anyone who
# can edit a dashboard, and this endpoint is open to every authenticated user,
# so a tick box on a card alone would mean that editing a dashboard grants you
# the microphone. The card may only decline.


async def test_talk_back_is_off_until_the_entry_says_otherwise(
    hub: FakeHub, hass_client: ClientSessionGenerator
) -> None:
    """A card cannot grant itself the microphone — but the wall still mints."""
    client = await hass_client()

    response = await post(client, grid(EXPORTED, talk=True))

    assert response.status == 200
    assert (await response.json())["talk"] is False
    assert hub.grants() == [{"kind": "grid-view", "deviceIds": [EXPORTED]}]


async def test_the_entry_option_is_what_grants_talk_back(
    hass: HomeAssistant,
    hub: FakeHub,
    config_entry: MockConfigEntry,
    hass_client: ClientSessionGenerator,
) -> None:
    allow_talk(hass, config_entry, True)
    await hass.async_block_till_done()
    client = await hass_client()

    response = await post(client, grid(EXPORTED, talk=True))

    assert (await response.json())["talk"] is True
    assert hub.grants()[-1] == {
        "kind": "grid-view",
        "deviceIds": [EXPORTED],
        "talk": True,
    }


async def test_a_card_may_decline_talk_back_the_option_allows(
    hass: HomeAssistant,
    hub: FakeHub,
    config_entry: MockConfigEntry,
    hass_client: ClientSessionGenerator,
) -> None:
    """`talk` is OMITTED when off, never sent as `false`."""
    allow_talk(hass, config_entry, True)
    await hass.async_block_till_done()
    client = await hass_client()

    response = await post(client, grid(EXPORTED))

    assert (await response.json())["talk"] is False
    assert hub.grants()[-1] == {"kind": "grid-view", "deviceIds": [EXPORTED]}


async def test_a_talking_token_is_never_served_to_a_request_that_did_not_ask(
    hass: HomeAssistant,
    hub: FakeHub,
    config_entry: MockConfigEntry,
    hass_client: ClientSessionGenerator,
) -> None:
    """`talk` is part of the scope: two credentials, two labels, two grants."""
    allow_talk(hass, config_entry, True)
    await hass.async_block_till_done()
    client = await hass_client()

    talking = await post(client, grid(EXPORTED, talk=True))
    silent = await post(client, grid(EXPORTED))

    assert (await talking.json())["talk"] is True
    assert (await silent.json())["talk"] is False
    assert hub.grants() == [
        {"kind": "grid-view", "deviceIds": [EXPORTED], "talk": True},
        {"kind": "grid-view", "deviceIds": [EXPORTED]},
    ]
    labels = {row["label"] for row in hub.rows.values()}
    assert len(labels) == 2
    assert (await talking.json())["proxy_base"] != (await silent.json())["proxy_base"]


async def test_a_talking_scope_is_reused_like_any_other(
    hass: HomeAssistant,
    hub: FakeHub,
    config_entry: MockConfigEntry,
    hass_client: ClientSessionGenerator,
) -> None:
    allow_talk(hass, config_entry, True)
    await hass.async_block_till_done()
    client = await hass_client()

    await post(client, grid(EXPORTED, talk=True))
    await post(client, grid(EXPORTED, talk=True))

    assert hub.count(TOKEN_MINT_MUTATION) == 1


async def test_an_events_token_never_carries_talk_back(
    hass: HomeAssistant,
    hub: FakeHub,
    config_entry: MockConfigEntry,
    hass_client: ClientSessionGenerator,
) -> None:
    """The events perimeter has no camera to speak through."""
    allow_talk(hass, config_entry, True)
    await hass.async_block_till_done()
    client = await hass_client()

    response = await post(
        client, {"kind": "events-view", "device_ids": [EXPORTED], "talk": True}
    )

    assert (await response.json())["talk"] is False
    assert hub.grants() == [{"kind": "events-view", "deviceIds": [EXPORTED]}]


async def test_talk_must_be_a_boolean(
    hub: FakeHub, hass_client: ClientSessionGenerator
) -> None:
    client = await hass_client()

    response = await post(client, grid(EXPORTED, talk="yes"))

    assert response.status == 400
    assert hub.calls == []


# --- the guards ----------------------------------------------------------------


async def test_a_device_this_entry_does_not_export_is_refused(
    hub: FakeHub, hass_client: ClientSessionGenerator
) -> None:
    """Otherwise the card endpoint mints for any device the hub account sees."""
    client = await hass_client()

    response = await post(client, grid(999999))

    assert response.status == 400
    assert "999999" in (await response.json())["message"]
    assert hub.calls == []


async def test_an_unknown_kind_is_refused_before_the_hub_is_called(
    hub: FakeHub, hass_client: ClientSessionGenerator
) -> None:
    client = await hass_client()

    response = await post(client, {"kind": "everything", "device_ids": [EXPORTED]})

    assert response.status == 400
    assert hub.calls == []


async def test_the_hubs_own_refusal_reaches_the_dashboard(
    hub: FakeHub, hass_client: ClientSessionGenerator
) -> None:
    """The hub's own sentence reaches the dashboard, not a generic failure."""
    hub.fail[TOKEN_MINT_MUTATION] = CamStackForbiddenError(
        "auth.mintToken: A token cannot carry more than you hold: "
        "a share-view grant names a device you cannot view"
    )
    client = await hass_client()

    response = await post(client, grid(EXPORTED))

    assert response.status == 502
    assert "cannot view" in (await response.json())["message"]


async def test_an_unauthenticated_caller_gets_no_credential(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    config_entry: MockConfigEntry,
    hass_client_no_auth: ClientSessionGenerator,
) -> None:
    """A view token is a bearer credential for the hub."""
    await setup_integration(hass, config_entry)
    client = await hass_client_no_auth()

    response = await post(client, grid(EXPORTED))

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


# --- where the token goes ------------------------------------------------------


async def test_the_relayed_mint_keeps_the_token_inside_home_assistant(
    hub: FakeHub, hass_client: ClientSessionGenerator
) -> None:
    """The default answer carries the grant, and NOT the credential.

    A card without `url_base` reaches the hub through `proxy.py`, which strips
    the browser's `Authorization` and injects the token itself. A copy of it in
    the page authenticates nothing there and would sit in browser history and
    screenshots, usable against the hub from outside Home Assistant.
    """
    client = await hass_client()

    response = await post(client, grid(EXPORTED))

    assert response.status == 200
    payload = await response.json()
    assert "token" not in payload
    assert "jwt-secret-1" not in await response.text()
    assert payload["proxy_base"]
    assert payload["expires_at"] == pytest.approx(hub.rows["tok1"]["expiresAt"] / 1000)
    assert hub.count(TOKEN_MINT_MUTATION) == 1


async def test_an_events_card_is_relayed_by_default_too(
    hub: FakeHub, hass_client: ClientSessionGenerator
) -> None:
    """The reel was the worst offender: its token rode the iframe URL."""
    client = await hass_client()

    response = await post(client, {"kind": "events-view", "device_ids": [EXPORTED]})

    assert response.status == 200
    assert "token" not in await response.json()


async def test_the_cached_answer_withholds_the_token_as_well(
    hub: FakeHub, hass_client: ClientSessionGenerator
) -> None:
    """The keeper serves one token to both paths; only the answer differs."""
    client = await hass_client()

    direct = await post(client, grid(EXPORTED, direct=True))
    relayed = await post(client, grid(EXPORTED))

    assert (await direct.json())["token"] == "jwt-secret-1"
    assert "token" not in await relayed.json()
    assert hub.count(TOKEN_MINT_MUTATION) == 1


async def test_direct_must_be_a_boolean(
    hub: FakeHub, hass_client: ClientSessionGenerator
) -> None:
    """A truthy string must not talk its way into a credential."""
    client = await hass_client()

    response = await post(client, grid(EXPORTED, direct="yes"))

    assert response.status == 400
