"""The same-origin door to the hub for the Lovelace cards' iframes.

The hub answers over HTTPS with a certificate signed by its own local root. A
browser that does not trust it blocks an iframe to the hub silently, and the
Home Assistant apps offer no exception to click through — so a card pointed
straight at the hub is a white rectangle on every phone. The Scrypted
integration solved the same problem years ago by RELAYING: Home Assistant
serves the embed under its own origin and forwards every request, HTTP and
WebSocket, to the hub itself. This module is that relay for CamStack.

## The grant

`/api/camstack/p/{grant}/{path}` is `requires_auth = False` because an iframe
cannot send Home Assistant's auth header. What guards it is `{grant}`: an
unguessable id the embed-token endpoint hands out TOGETHER with a share token,
to an authenticated Home Assistant user only. The relay injects that share
token — device-scoped, one hour — into every forwarded request. The grant
dies with the token (plus a short grace) and knows only what the token knows;
the integration's own hub credential never travels this path. Scrypted's relay
puts a full login token in the URL for the life of the process; this one does
not.

The grant id is STABLE across re-mints of the same scope, so the iframe's
URL never changes when the token rotates: rebuilding the frame restarts every
WebRTC session on the wall.

## The panel's grant

The sidebar panel frames the WHOLE admin UI, which has its own login, so its
grant injects nothing and relays every path: reaching it is reaching the
hub's login page, no more. It is stable per entry, never expires, and is
handed out only inside the panel's config — which the frontend delivers to
signed-in users. The hub learns where it is mounted from
`X-Forwarded-Prefix`, sent on every relayed request, and answers its index
with a `<base>` under that prefix (the contract the admin UI implements).

## What the relay does not carry

Video. Live, recorded playback and scrub are one WebRTC session between the
browser and the hub; only the signaling (tRPC over WebSocket) crosses here.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import aiohttp
from aiohttp import ClientTimeout, hdrs, web
from homeassistant.components.http import KEY_HASS, HomeAssistantView
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util

from .const import (
    CONF_VERIFY_SSL,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
    PROXY_ALLOWED_PREFIXES,
    PROXY_GRANT_GRACE,
    PROXY_VIEW_URL,
)
from .hub_url import async_resolve_base_url

_LOGGER = logging.getLogger(__name__)

_VIEW_REGISTERED = f"{DOMAIN}_proxy_view_registered"
_GRANTS = f"{DOMAIN}_proxy_grants"
_GRANT_IDS = f"{DOMAIN}_proxy_grant_ids"

# Bodies up to this size are answered in one piece; larger ones are streamed.
_INLINE_BODY_MAX = 4_194_000
_WS_MAX_MSG = 4_194_304 * 4
# Hop-by-hop and Home Assistant-only headers that must not reach the hub.
_REQUEST_HEADERS_DROPPED = frozenset(
    {
        hdrs.CONTENT_LENGTH,
        hdrs.CONTENT_ENCODING,
        hdrs.TRANSFER_ENCODING,
        hdrs.CONNECTION,
        hdrs.SEC_WEBSOCKET_EXTENSIONS,
        hdrs.SEC_WEBSOCKET_PROTOCOL,
        hdrs.SEC_WEBSOCKET_VERSION,
        hdrs.SEC_WEBSOCKET_KEY,
        hdrs.HOST,
        hdrs.COOKIE,
        hdrs.AUTHORIZATION,
    }
)
# What the relay asks the hub to compress with. NOT the browser's list: this
# process decodes the body before re-sending it, and Home Assistant's aiohttp
# cannot decode zstd without `backports.zstd`. Forwarding the browser's
# `Accept-Encoding` verbatim made the hub answer in zstd and every relayed
# request fail with "Can not decode content-encoding: zstandard (zstd)" — a
# 502 painted into the dashboard, while the hub was perfectly healthy and
# curl (which does not ask for zstd) saw nothing wrong.
_ACCEPT_ENCODING = "gzip, deflate"

_RESPONSE_HEADERS_DROPPED = frozenset(
    {
        hdrs.TRANSFER_ENCODING,
        hdrs.CONTENT_LENGTH,
        hdrs.CONTENT_TYPE,
        hdrs.CONTENT_ENCODING,
        hdrs.SET_COOKIE,
        "Access-Control-Allow-Origin",
        "Access-Control-Allow-Credentials",
        "Access-Control-Allow-Methods",
        "Access-Control-Allow-Headers",
        "Access-Control-Expose-Headers",
        "Access-Control-Max-Age",
    }
)


# A grant for a card's embed: the embed's own routes, the share token injected.
GRANT_EMBED = "embed"
# A grant for the sidebar panel: every route, nothing injected — the admin UI
# signs in on its own.
GRANT_PANEL = "panel"


@dataclass(frozen=True, slots=True)
class ProxyGrant:
    """What one grant id stands for: an entry, and the token relayed under it."""

    entry_id: str
    #: The share token injected on every relayed request; None for the panel.
    token: str | None
    #: Epoch seconds, or None for a grant that never expires.
    expires_at: float | None
    kind: str = GRANT_EMBED

    def is_live(self, now: float) -> bool:
        """Return whether requests under this grant are still relayed."""
        if self.expires_at is None:
            return True
        return now <= self.expires_at + PROXY_GRANT_GRACE.total_seconds()


def _grants(hass: HomeAssistant) -> dict[str, ProxyGrant]:
    return hass.data.setdefault(_GRANTS, {})


def _grant_ids(hass: HomeAssistant) -> dict[tuple[str, ...], str]:
    return hass.data.setdefault(_GRANT_IDS, {})


@callback
def async_issue_grant(
    hass: HomeAssistant,
    entry_id: str,
    scope_key: tuple[str, ...],
    token: str,
    expires_at: float | None,
) -> str:
    """Bind `token` to the grant for `scope_key`, creating the grant once.

    The id is stable per scope: a re-mint refreshes the token behind the same
    URL, and the card's iframe is not rebuilt.
    """
    ids = _grant_ids(hass)
    grant_id = ids.get(scope_key)
    if grant_id is None:
        grant_id = secrets.token_urlsafe(32)
        ids[scope_key] = grant_id
    _grants(hass)[grant_id] = ProxyGrant(entry_id, token, expires_at)
    return grant_id


@callback
def async_issue_panel_grant(hass: HomeAssistant, entry_id: str) -> str:
    """Return the entry's one panel grant, creating it on first use."""
    ids = _grant_ids(hass)
    scope_key = (entry_id, GRANT_PANEL)
    grant_id = ids.get(scope_key)
    if grant_id is None:
        grant_id = secrets.token_urlsafe(32)
        ids[scope_key] = grant_id
    _grants(hass)[grant_id] = ProxyGrant(entry_id, None, None, GRANT_PANEL)
    return grant_id


@callback
def async_forget_entry_grants(hass: HomeAssistant, entry_id: str) -> None:
    """Drop every grant issued for an entry that is going away."""
    grants = _grants(hass)
    gone = [
        grant_id for grant_id, grant in grants.items() if grant.entry_id == entry_id
    ]
    for grant_id in gone:
        del grants[grant_id]
    ids = _grant_ids(hass)
    for key in [key for key, grant_id in ids.items() if grant_id in gone]:
        del ids[key]


@callback
def proxy_base_for(grant_id: str) -> str:
    """Return the path prefix a card uses as its `serverUrl`, relative to HA."""
    return f"{PROXY_VIEW_URL}/{grant_id}"


def is_relayed_path(path: str) -> bool:
    """Return whether the relay forwards `path` at all."""
    # The WebSocket link is `/trpc` itself; every HTTP procedure is under it.
    if path == "trpc":
        return True
    return any(path.startswith(prefix) for prefix in PROXY_ALLOWED_PREFIXES)


def async_register_proxy_view(hass: HomeAssistant) -> None:
    """Register the relay, once per Home Assistant."""
    if hass.data.get(_VIEW_REGISTERED):
        return
    hass.http.register_view(CamStackProxyView)
    hass.data[_VIEW_REGISTERED] = True


class CamStackProxyView(HomeAssistantView):
    """Relay one grant's requests to its hub."""

    url = f"{PROXY_VIEW_URL}/{{grant}}/{{path:.*}}"
    name = "api:camstack:proxy"
    # The grant in the path is the credential; see the module docstring.
    requires_auth = False

    async def _handle(
        self, request: web.Request, grant: str, path: str
    ) -> web.Response | web.StreamResponse | web.WebSocketResponse:
        hass: HomeAssistant = request.app[KEY_HASS]
        now = dt_util.utcnow().timestamp()
        record = _grants(hass).get(grant)
        if record is None or not record.is_live(now):
            # Unknown and expired look the same on purpose: a guess must not
            # learn whether an id once existed.
            return self.json_message("unknown grant", 404)
        if record.kind == GRANT_EMBED and not is_relayed_path(path):
            return self.json_message("not relayed", 404)

        entry = hass.config_entries.async_get_entry(record.entry_id)
        base = async_resolve_base_url(hass, entry) if entry is not None else None
        if entry is None or base is None:
            return self.json_message("the CamStack entry is gone", 503)
        verify_ssl = entry.data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)
        session = async_get_clientsession(hass, verify_ssl=verify_ssl)
        url = f"{base.rstrip('/')}/{path}"
        if request.query_string:
            url = f"{url}?{request.query_string}"
        headers = _forward_headers(request, record.token, proxy_base_for(grant))

        try:
            if _is_websocket(request):
                return await _relay_websocket(request, session, url, headers)
            return await _relay_request(request, session, url, headers)
        except (aiohttp.ClientError, TimeoutError, OSError) as err:
            # Warning, not debug: this 502 is painted INTO a dashboard card,
            # and an operator who sees it deserves a line naming the hub and
            # the reason. A hub restart produces a burst of these.
            _LOGGER.warning(
                "CamStack relay could not reach %s: %s", url, err or type(err).__name__
            )
        return self.json_message("the hub did not answer", 502)

    get = _handle
    post = _handle
    put = _handle
    delete = _handle
    patch = _handle


def _forward_headers(
    request: web.Request, token: str | None, prefix: str
) -> dict[str, str]:
    """Return the request headers the hub gets.

    The browser's, minus hop-by-hop and Home Assistant's own, plus the
    grant's credential.
    """
    headers = {
        name: value
        for name, value in request.headers.items()
        if name not in _REQUEST_HEADERS_DROPPED
    }
    if token is not None:
        headers[hdrs.AUTHORIZATION] = f"Bearer {token}"
    # Never the browser's list — see `_ACCEPT_ENCODING`.
    headers[hdrs.ACCEPT_ENCODING] = _ACCEPT_ENCODING
    # Where the hub is mounted, seen from the browser: the admin UI's index
    # answers with a `<base>` under it (the panel's contract).
    headers["X-Forwarded-Prefix"] = prefix
    headers[hdrs.X_FORWARDED_HOST] = request.headers.get(
        hdrs.X_FORWARDED_HOST, request.host
    )
    headers[hdrs.X_FORWARDED_PROTO] = request.headers.get(
        hdrs.X_FORWARDED_PROTO, request.url.scheme
    )
    return headers


def _response_headers(response: aiohttp.ClientResponse) -> dict[str, str]:
    return {
        name: value
        for name, value in response.headers.items()
        if name not in _RESPONSE_HEADERS_DROPPED
    }


def _is_websocket(request: web.Request) -> bool:
    headers = request.headers
    return (
        "upgrade" in headers.get(hdrs.CONNECTION, "").lower()
        and headers.get(hdrs.UPGRADE, "").lower() == "websocket"
    )


async def _relay_request(
    request: web.Request,
    session: aiohttp.ClientSession,
    url: str,
    headers: dict[str, str],
) -> web.Response | web.StreamResponse:
    async with session.request(
        request.method,
        url,
        headers=headers,
        allow_redirects=False,
        data=request.content,
        timeout=ClientTimeout(total=None),
        skip_auto_headers={hdrs.CONTENT_TYPE},
    ) as result:
        out_headers = _response_headers(result)
        declared = result.headers.get(hdrs.CONTENT_LENGTH)
        if (
            declared is not None and int(declared) < _INLINE_BODY_MAX
        ) or result.status in (
            204,
            304,
        ):
            return web.Response(
                headers=out_headers,
                status=result.status,
                content_type=result.content_type,
                body=await result.read(),
            )

        response = web.StreamResponse(status=result.status, headers=out_headers)
        response.content_type = result.content_type
        try:
            await response.prepare(request)
            async for chunk in result.content.iter_chunked(4096):
                await response.write(chunk)
        except (aiohttp.ClientError, ConnectionResetError) as err:
            _LOGGER.debug("relay stream ended early for %s: %s", url, err)
        return response


async def _relay_websocket(
    request: web.Request,
    session: aiohttp.ClientSession,
    url: str,
    headers: dict[str, str],
) -> web.WebSocketResponse:
    protocols: Iterable[str] = ()
    if hdrs.SEC_WEBSOCKET_PROTOCOL in request.headers:
        protocols = [
            proto.strip()
            for proto in request.headers[hdrs.SEC_WEBSOCKET_PROTOCOL].split(",")
        ]
    ws_server = web.WebSocketResponse(
        protocols=protocols, autoclose=False, autoping=False, max_msg_size=_WS_MAX_MSG
    )
    await ws_server.prepare(request)

    async with session.ws_connect(
        url,
        headers=headers,
        protocols=protocols,
        autoclose=False,
        autoping=False,
        max_msg_size=_WS_MAX_MSG,
    ) as ws_client:
        await asyncio.wait(
            [
                asyncio.create_task(_pump(ws_server, ws_client)),
                asyncio.create_task(_pump(ws_client, ws_server)),
            ],
            return_when=asyncio.FIRST_COMPLETED,
        )
    return ws_server


async def _pump(ws_from: Any, ws_to: Any) -> None:
    """Copy frames one way until either side closes."""
    try:
        async for msg in ws_from:
            if msg.type == aiohttp.WSMsgType.TEXT:
                await ws_to.send_str(msg.data)
            elif msg.type == aiohttp.WSMsgType.BINARY:
                await ws_to.send_bytes(msg.data)
            elif msg.type == aiohttp.WSMsgType.PING:
                await ws_to.ping()
            elif msg.type == aiohttp.WSMsgType.PONG:
                await ws_to.pong()
            elif ws_to.closed:
                await ws_to.close(code=ws_to.close_code, message=msg.extra)
    except (RuntimeError, ConnectionResetError) as err:
        _LOGGER.debug("relay websocket ended: %s", err)
