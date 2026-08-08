"""Thin async client for the CamStack hub tRPC API.

The hub speaks tRPC over HTTP:

* query    -> ``GET  /trpc/<path>?input=<urlencoded {"json": <input>}>``
* mutation -> ``POST /trpc/<path>`` with body ``{"json": <input>}``
* subscription -> ``GET /trpc/<path>`` with ``Accept: text/event-stream``

Every response is wrapped as ``{"result": {"data": {"json": ...}}}`` or
``{"error": {...}}``. This module is the only place that knows that, so no
other file in the integration builds a URL or unwraps an envelope.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import quote

import aiohttp

_LOGGER = logging.getLogger(__name__)

# A tRPC error object carries a numeric JSON-RPC-ish code; -32001 is UNAUTHORIZED.
_TRPC_UNAUTHORIZED = -32001

_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)
# The stream is long-lived: only the connect and the socket-read matter.
_STREAM_TIMEOUT = aiohttp.ClientTimeout(total=None, connect=30, sock_read=300)


class CamStackError(Exception):
    """Base error for every failure raised by this client."""


class CamStackConnectionError(CamStackError):
    """The hub could not be reached."""


class CamStackAuthError(CamStackError):
    """The hub rejected the credentials or the session token."""


class CamStackApiError(CamStackError):
    """The hub answered, and the answer was an error."""


class CamStackClient:
    """Calls the CamStack hub and re-authenticates when its token expires."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        port: int,
        username: str,
        password: str,
        *,
        verify_ssl: bool = False,
    ) -> None:
        """Store connection parameters. No I/O happens here."""
        self._session = session
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._verify_ssl = verify_ssl
        self._token: str | None = None
        self._login_lock = asyncio.Lock()

    @property
    def base_url(self) -> str:
        """Return the hub's root URL."""
        return f"https://{self._host}:{self._port}"

    @property
    def token(self) -> str | None:
        """Return the current session token, if one has been minted."""
        return self._token

    async def async_login(self) -> dict[str, Any]:
        """Mint a session token and return the authenticated user record."""
        async with self._login_lock:
            result = await self._request(
                "POST",
                "auth.login",
                {"username": self._username, "password": self._password},
                authenticated=False,
            )
            if not isinstance(result, dict) or "token" not in result:
                raise CamStackAuthError("hub returned no token")
            if result.get("requiresTotp"):
                # A TOTP challenge cannot be answered from a config entry that
                # holds only a password, and pretending otherwise would store a
                # token that never worked.
                raise CamStackAuthError("account requires two-factor authentication")
            self._token = str(result["token"])
            user = result.get("user")
            return user if isinstance(user, dict) else {}

    async def async_verify(self) -> dict[str, Any]:
        """Log in and confirm the token is accepted. Used by the config flow."""
        await self.async_login()
        me = await self.query("auth.me")
        if not isinstance(me, dict):
            raise CamStackAuthError("hub accepted the login but returned no identity")
        return me

    async def query(self, path: str, payload: Any | None = None) -> Any:
        """Call a tRPC query."""
        return await self._authenticated_request("GET", path, payload)

    async def mutate(self, path: str, payload: Any | None = None) -> Any:
        """Call a tRPC mutation."""
        return await self._authenticated_request("POST", path, payload)

    async def _authenticated_request(
        self, method: str, path: str, payload: Any | None
    ) -> Any:
        """Call the hub, minting or refreshing the token when required."""
        if self._token is None:
            await self.async_login()
        try:
            return await self._request(method, path, payload)
        except CamStackAuthError:
            # The token expired or was revoked. One retry behind a fresh login;
            # a second failure is a real credential problem and must surface.
            await self.async_login()
            return await self._request(method, path, payload)

    async def _request(
        self,
        method: str,
        path: str,
        payload: Any | None,
        *,
        authenticated: bool = True,
    ) -> Any:
        """Perform one tRPC call and unwrap its envelope."""
        url = f"{self.base_url}/trpc/{path}"
        headers = {"content-type": "application/json"}
        if authenticated and self._token:
            headers["authorization"] = f"Bearer {self._token}"

        body: str | None = None
        if method == "POST":
            body = json.dumps({"json": payload if payload is not None else {}})
        elif payload is not None:
            url += f"?input={quote(json.dumps({'json': payload}))}"

        try:
            async with self._session.request(
                method,
                url,
                headers=headers,
                data=body,
                ssl=self._verify_ssl,
                timeout=_REQUEST_TIMEOUT,
            ) as response:
                if response.status in (401, 403):
                    raise CamStackAuthError(f"{path}: hub rejected the token")
                text = await response.text()
        except TimeoutError as err:
            raise CamStackConnectionError(f"{path}: timed out") from err
        except aiohttp.ClientError as err:
            raise CamStackConnectionError(f"{path}: {err}") from err

        return _unwrap(path, text)

    async def subscribe_events(
        self, category: str | None = None
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield live hub events from a tRPC SSE subscription.

        The caller owns the retry loop: this generator ends when the stream
        ends, and never reconnects behind the caller's back.
        """
        if self._token is None:
            await self.async_login()

        payload: dict[str, Any] = {"category": category} if category else {}
        url = (
            f"{self.base_url}/trpc/live.onEvent"
            f"?input={quote(json.dumps({'json': payload}))}"
        )
        headers = {
            "accept": "text/event-stream",
            "authorization": f"Bearer {self._token}",
            # MEASURED, not defensive. aiohttp advertises gzip by default and
            # the hub honours it on `text/event-stream`, which then only
            # reaches the client when the compressor's buffer fills. On a
            # busy unfiltered stream that looks like it works; on a filtered
            # one (~7 events/s) it delivered ZERO events in 8 seconds while
            # the same request under `identity` delivered 56. Motion would
            # have arrived in late bursts, or not at all.
            "accept-encoding": "identity",
        }

        try:
            async with self._session.get(
                url, headers=headers, ssl=self._verify_ssl, timeout=_STREAM_TIMEOUT
            ) as response:
                if response.status in (401, 403):
                    self._token = None
                    raise CamStackAuthError("event stream: hub rejected the token")
                if response.status != 200:
                    raise CamStackApiError(
                        f"event stream: hub answered {response.status}"
                    )
                async for raw_line in response.content:
                    event = _parse_sse_line(raw_line)
                    if event is not None:
                        yield event
        except TimeoutError as err:
            raise CamStackConnectionError("event stream: timed out") from err
        except aiohttp.ClientError as err:
            raise CamStackConnectionError(f"event stream: {err}") from err


def _parse_sse_line(raw_line: bytes) -> dict[str, Any] | None:
    """Return the event carried by one SSE line, or None for framing lines."""
    line = raw_line.decode("utf-8", errors="replace").strip()
    if not line.startswith("data:"):
        return None
    data = line[len("data:") :].strip()
    if not data:
        return None
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError:
        _LOGGER.debug("Discarding unparsable event frame: %s", data[:200])
        return None
    # The hub serialises through a superjson-style envelope; the payload is
    # under `json` and `meta` only describes revived types we do not need.
    event = parsed.get("json") if isinstance(parsed, dict) else None
    return event if isinstance(event, dict) else None


def _unwrap(path: str, text: str) -> Any:
    """Unwrap a tRPC response envelope, raising on the error branch."""
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as err:
        raise CamStackApiError(f"{path}: hub returned a non-JSON body") from err

    if not isinstance(parsed, dict):
        raise CamStackApiError(f"{path}: hub returned {type(parsed).__name__}")

    error = parsed.get("error")
    if error is not None:
        message, code = _describe_error(error)
        if code == _TRPC_UNAUTHORIZED:
            raise CamStackAuthError(f"{path}: {message}")
        raise CamStackApiError(f"{path}: {message}")

    result = parsed.get("result")
    if not isinstance(result, dict):
        raise CamStackApiError(f"{path}: hub returned no result")
    data = result.get("data")
    if not isinstance(data, dict):
        raise CamStackApiError(f"{path}: hub returned no data")
    return data.get("json")


def _describe_error(error: Any) -> tuple[str, int | None]:
    """Extract a message and a tRPC code from an error envelope."""
    if not isinstance(error, dict):
        return str(error), None
    payload = error.get("json") if isinstance(error.get("json"), dict) else error
    message = payload.get("message") if isinstance(payload, dict) else None
    code = payload.get("code") if isinstance(payload, dict) else None
    return (
        str(message) if message else "hub returned an error",
        code if isinstance(code, int) else None,
    )
