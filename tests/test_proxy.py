"""The same-origin relay the cards' iframes go through.

A fake hub (plain aiohttp) stands in for CamStack: the tests assert what
reaches it — path, query, the injected share token, no Home Assistant cookie —
and what the browser gets back, over HTTP and over a WebSocket.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.camstack.const import (
    CONF_HOST,
    CONF_PORT,
    EMBED_TOKEN_VIEW_URL,
    PROXY_GRANT_GRACE,
    PROXY_VIEW_URL,
)
from custom_components.camstack.proxy import (
    ProxyGrant,
    async_forget_entry_grants,
    async_issue_grant,
    is_relayed_path,
)


EXPORTED = 615


def minted(expires_at_ms: int | None = 4_000_000_000_000) -> dict[str, object]:
    return {"id": "tok1", "token": "csv_deadbeef", "expiresAt": expires_at_ms}


class FakeHub:
    """Records every request; answers a few of the routes the embed uses."""

    def __init__(self) -> None:
        """Build the routes the embed uses, plus one it must never reach."""
        self.requests: list[dict[str, Any]] = []
        self.app = web.Application()
        self.app.router.add_route("*", "/viewer/{tail:.*}", self._static)
        self.app.router.add_get("/trpc", self._trpc)
        self.app.router.add_post("/trpc/{tail:.*}", self._trpc)
        self.app.router.add_get("/addon/snapshot/media/{tail:.*}", self._static)
        self.app.router.add_get("/secret/{tail:.*}", self._static)
        self.server = TestServer(self.app)

    async def __aenter__(self) -> FakeHub:
        """Start listening."""
        await self.server.start_server()
        return self

    async def __aexit__(self, *exc: object) -> None:
        """Stop listening."""
        await self.server.close()

    @property
    def base(self) -> str:
        """Return the hub's own address."""
        return str(self.server.make_url("")).rstrip("/")

    def _record(self, request: web.Request) -> None:
        self.requests.append(
            {
                "path": request.path,
                "query": dict(request.query),
                "authorization": request.headers.get("Authorization"),
                "cookie": request.headers.get("Cookie"),
                "method": request.method,
            }
        )

    async def _static(self, request: web.Request) -> web.Response:
        self._record(request)
        return web.Response(
            text=f"hub says {request.path}",
            content_type="text/html",
            headers={"X-Hub": "yes", "Set-Cookie": "hub=1"},
        )

    async def _trpc(self, request: web.Request) -> web.StreamResponse:
        self._record(request)
        if request.headers.get("Upgrade", "").lower() == "websocket":
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    await ws.send_str(
                        f"echo:{msg.data}:{request.headers.get('Authorization')}"
                    )
            return ws
        body = await request.text()
        return web.json_response(
            {"got": body, "auth": request.headers.get("Authorization")}
        )


@pytest.fixture
async def hub(hass: HomeAssistant, config_entry: MockConfigEntry):
    """Point the config entry at a fake hub for the life of one test."""
    async with FakeHub() as fake:
        # Added here, once: an entry can be re-pointed only once it is known,
        # and adding it twice is refused. `_setup` below does the rest.
        config_entry.add_to_hass(hass)
        hass.config_entries.async_update_entry(
            config_entry,
            data={
                **config_entry.data,
                CONF_HOST: fake.server.host,
                CONF_PORT: fake.server.port,
            },
        )
        yield fake


async def _setup(hass: HomeAssistant, config_entry: MockConfigEntry) -> None:
    """Load an entry the `hub` fixture already added."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()


def _grant_for(
    hass: HomeAssistant, entry_id: str, expires_at: float | None = None
) -> str:
    return async_issue_grant(
        hass, entry_id, (entry_id, "grid-view", "615"), "csv_x", expires_at
    )


async def test_the_relay_forwards_the_embed_page_with_the_grant_token_and_no_ha_cookie(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    config_entry: MockConfigEntry,
    hub: FakeHub,
    hass_client_no_auth: ClientSessionGenerator,
) -> None:
    """Unauthenticated on purpose: an iframe cannot send HA's auth header."""
    await _setup(hass, config_entry)
    grant = _grant_for(hass, config_entry.entry_id)
    client = await hass_client_no_auth()

    response = await client.get(
        f"{PROXY_VIEW_URL}/{grant}/viewer/camstack/embed/index.html?mode=grid",
        headers={"Cookie": "ha_session=secret"},
    )

    assert response.status == 200
    assert await response.text() == "hub says /viewer/camstack/embed/index.html"
    assert response.headers["X-Hub"] == "yes"
    # The hub's cookie never lands on Home Assistant's origin.
    assert "Set-Cookie" not in response.headers
    assert hub.requests == [
        {
            "path": "/viewer/camstack/embed/index.html",
            "query": {"mode": "grid"},
            "authorization": "Bearer csv_x",
            "cookie": None,
            "method": "GET",
        }
    ]


async def test_a_trpc_post_is_relayed_with_its_body(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    config_entry: MockConfigEntry,
    hub: FakeHub,
    hass_client_no_auth: ClientSessionGenerator,
) -> None:
    await _setup(hass, config_entry)
    grant = _grant_for(hass, config_entry.entry_id)
    client = await hass_client_no_auth()

    response = await client.post(
        f"{PROXY_VIEW_URL}/{grant}/trpc/deviceManager.listAll",
        data='{"json":null}',
        headers={"Content-Type": "application/json"},
    )

    assert response.status == 200
    assert await response.json() == {"got": '{"json":null}', "auth": "Bearer csv_x"}


async def test_a_websocket_is_relayed_both_ways(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    config_entry: MockConfigEntry,
    hub: FakeHub,
    hass_client_no_auth: ClientSessionGenerator,
) -> None:
    """TRPC's live link: what the browser sends reaches the hub, and back."""
    await _setup(hass, config_entry)
    grant = _grant_for(hass, config_entry.entry_id)
    client = await hass_client_no_auth()

    async with client.ws_connect(f"{PROXY_VIEW_URL}/{grant}/trpc") as ws:
        await ws.send_str("hello")
        msg = await ws.receive(timeout=5)

    assert msg.type == aiohttp.WSMsgType.TEXT
    assert msg.data == "echo:hello:Bearer csv_x"


async def test_an_unknown_and_an_expired_grant_look_the_same(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    config_entry: MockConfigEntry,
    hub: FakeHub,
    hass_client_no_auth: ClientSessionGenerator,
) -> None:
    await _setup(hass, config_entry)
    long_gone = dt_util.utcnow().timestamp() - PROXY_GRANT_GRACE.total_seconds() - 1
    expired = _grant_for(hass, config_entry.entry_id, expires_at=long_gone)
    client = await hass_client_no_auth()

    unknown = await client.get(f"{PROXY_VIEW_URL}/nope/viewer/x")
    stale = await client.get(f"{PROXY_VIEW_URL}/{expired}/viewer/x")

    assert unknown.status == 404
    assert stale.status == 404
    assert hub.requests == []


async def test_only_the_embeds_own_routes_are_relayed(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    config_entry: MockConfigEntry,
    hub: FakeHub,
    hass_client_no_auth: ClientSessionGenerator,
) -> None:
    """Home Assistant is a door to the embed, not a relay to the whole hub."""
    await _setup(hass, config_entry)
    grant = _grant_for(hass, config_entry.entry_id)
    client = await hass_client_no_auth()

    response = await client.get(f"{PROXY_VIEW_URL}/{grant}/secret/admin")

    assert response.status == 404
    assert hub.requests == []


def test_the_relayed_path_set_is_the_embeds_call_graph() -> None:
    assert is_relayed_path("trpc")
    assert is_relayed_path("trpc/health")
    assert is_relayed_path("viewer/camstack/embed/index.html")
    assert is_relayed_path("addon/snapshot/media/615.jpg")
    assert is_relayed_path("addon/pipeline-analytics/event-media/x.jpg")
    assert not is_relayed_path("")
    assert not is_relayed_path("trpcx")
    assert not is_relayed_path("addon/snapshot/other")


async def test_a_grant_is_stable_per_scope_and_forgotten_with_its_entry(
    hass: HomeAssistant,
) -> None:
    """A re-mint must not move the iframe.

    The URL is the grant; only the token behind it changes.
    """
    first = async_issue_grant(hass, "e1", ("e1", "grid-view", "615"), "csv_a", None)
    second = async_issue_grant(hass, "e1", ("e1", "grid-view", "615"), "csv_b", None)
    other = async_issue_grant(hass, "e1", ("e1", "events-view", "615"), "csv_c", None)

    assert first == second
    assert other != first
    async_forget_entry_grants(hass, "e1")
    assert (
        async_issue_grant(hass, "e1", ("e1", "grid-view", "615"), "csv_d", None)
        != first
    )


def test_a_grant_outlives_its_token_by_the_grace_only() -> None:
    grant = ProxyGrant("e1", "csv_x", expires_at=1000.0)
    grace = PROXY_GRANT_GRACE.total_seconds()
    assert grant.is_live(1000.0 + grace)
    assert not grant.is_live(1000.0 + grace + 1)
    assert ProxyGrant("e1", "csv_x", expires_at=None).is_live(1e12)


async def test_the_mint_answer_carries_the_relay_path_bound_to_the_token(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    config_entry: MockConfigEntry,
    hub: FakeHub,
    hass_client: ClientSessionGenerator,
    hass_client_no_auth: ClientSessionGenerator,
) -> None:
    """Mint as a user, then relay unauthenticated under the path, end to end."""
    await _setup(hass, config_entry)
    mock_client.mutate.reset_mock()
    mock_client.mutate.return_value = minted()
    user = await hass_client()

    response = await user.post(
        EMBED_TOKEN_VIEW_URL, json={"kind": "grid-view", "device_ids": [EXPORTED]}
    )
    assert response.status == 200
    payload = await response.json()
    assert payload["proxy_base"].startswith(f"{PROXY_VIEW_URL}/")

    again = await user.post(
        EMBED_TOKEN_VIEW_URL, json={"kind": "grid-view", "device_ids": [EXPORTED]}
    )
    assert (await again.json())["proxy_base"] == payload["proxy_base"]

    browser = await hass_client_no_auth()
    relayed = await browser.get(
        f"{payload['proxy_base']}/viewer/camstack/embed/index.html"
    )
    assert relayed.status == 200
    assert hub.requests[-1]["authorization"] == "Bearer csv_deadbeef"
