"""Thin async client for the CamStack hub tRPC API.

The hub speaks tRPC over HTTP:

* query    -> ``GET  /trpc/<path>?input=<urlencoded {"json": <input>}>``
* mutation -> ``POST /trpc/<path>`` with body ``{"json": <input>}``

Every response is wrapped as ``{"result": {"data": {"json": ...}}}`` or
``{"error": {...}}``. This module is the only place that knows that, so no
other file in the integration builds a URL or unwraps an envelope.

How the bearer token is obtained is NOT this module's business: a
:class:`CamStackAuth` supplies it. Two exist — :class:`PasswordAuth`, which
logs in with the credentials the operator typed, and ``OAuth2Auth`` in
``oauth.py``, which reads the token Home Assistant refreshes. The retry-once-
behind-a-fresh-token path is identical for both.
"""

from __future__ import annotations

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from typing import Any
from urllib.parse import quote

import aiohttp

_LOGGER = logging.getLogger(__name__)

# A tRPC error object carries a numeric JSON-RPC-ish code; -32001 is UNAUTHORIZED.
_TRPC_UNAUTHORIZED = -32001
# -32600 is BAD_REQUEST. On an input this component supplies from its own
# constants — an `addonId` pin, say — the hub refusing it can never be fixed by
# asking again, so it is a different failure from "the hub is busy".
_TRPC_BAD_REQUEST = -32600

_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)


class CamStackError(Exception):
    """Base error for every failure raised by this client."""


class CamStackConnectionError(CamStackError):
    """The hub could not be reached."""


class CamStackAuthError(CamStackError):
    """The hub rejected the credentials or the session token."""


class CamStackApiError(CamStackError):
    """The hub answered, and the answer was an error."""


class CamStackRequestError(CamStackApiError):
    """The hub REFUSED the request itself, and retrying cannot change that.

    The case this exists for: a capability pinned to an `addonId` the hub does
    not have. The hub answers with the valid ids enumerated rather than
    quietly serving the request from another provider — so the message is
    worth surfacing to the operator verbatim, and treating it as a transient
    connection problem would hide a broken integration behind an empty one.
    """


def base_url_for(host: str, port: int) -> str:
    """Return the hub's root URL. The hub is HTTPS-only."""
    return f"https://{host}:{port}"


class CamStackAuth(ABC):
    """Supplies the bearer token every hub call carries.

    Two implementations differ in ONE way that matters: a password can be
    replayed to mint a fresh token, an OAuth access token cannot — Home
    Assistant refreshes it or the link needs redoing. ``async_renew`` is
    therefore allowed to fail, and the client treats that as a real
    credential problem rather than retrying forever.
    """

    @abstractmethod
    async def async_token(self) -> str:
        """Return a token to send, obtaining one if there is none yet."""

    @abstractmethod
    async def async_renew(self) -> str:
        """Discard the current token and obtain another. May raise."""


class PasswordAuth(CamStackAuth):
    """Mints a hub session token from a username and a password."""

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
        """Store credentials. No I/O happens here."""
        self._session = session
        self._base_url = base_url_for(host, port)
        self._username = username
        self._password = password
        self._verify_ssl = verify_ssl
        self._token: str | None = None
        self._login_lock = asyncio.Lock()
        self._identity: dict[str, Any] = {}

    @property
    def identity(self) -> dict[str, Any]:
        """Return the user record the last login reported, if any."""
        return self._identity

    async def async_token(self) -> str:
        """Return the current token, logging in when there is none."""
        if self._token is None:
            return await self.async_renew()
        return self._token

    async def async_renew(self) -> str:
        """Log in to the hub and keep the token it hands back."""
        async with self._login_lock:
            result = await trpc_request(
                self._session,
                self._base_url,
                "POST",
                "auth.login",
                {"username": self._username, "password": self._password},
                token=None,
                verify_ssl=self._verify_ssl,
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
            self._identity = user if isinstance(user, dict) else {}
            return self._token


class CamStackClient:
    """Calls the CamStack hub and renews its token when the hub rejects it."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        port: int,
        auth: CamStackAuth,
        *,
        verify_ssl: bool = False,
    ) -> None:
        """Store connection parameters. No I/O happens here."""
        self._session = session
        self._host = host
        self._port = port
        self._auth = auth
        self._verify_ssl = verify_ssl

    @property
    def base_url(self) -> str:
        """Return the hub's root URL."""
        return base_url_for(self._host, self._port)

    @property
    def session(self) -> aiohttp.ClientSession:
        """Return the HTTP session, for callers fetching hub-minted URLs.

        An image entity needs it: the picture link the hub mints points at the
        hub itself, so it has to be fetched under the SAME TLS setting as
        every other call. Home Assistant's shared session verifies, and a hub
        with a self-signed certificate would then serve broken images only.
        """
        return self._session

    @property
    def verify_ssl(self) -> bool:
        """Return whether this entry verifies the hub's certificate."""
        return self._verify_ssl

    async def async_verify(self) -> dict[str, Any]:
        """Obtain a token and confirm the hub accepts it. Used by the flow."""
        await self._auth.async_token()
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

    async def async_post_addon_route(self, path: str, payload: Any) -> Any:
        """POST to an addon's own HTTP route.

        Not tRPC. Commands travel Home Assistant -> CamStack on the export
        addon's `addon-routes` surface, which is a plain authenticated
        endpoint answering `{"error": ...}` with a 4xx when it refuses a
        command rather than pretending to have applied it.
        """
        token = await self._auth.async_token()
        try:
            return await self._post_route(path, payload, token=token)
        except CamStackAuthError:
            token = await self._auth.async_renew()
            return await self._post_route(path, payload, token=token)

    async def _post_route(self, path: str, payload: Any, *, token: str) -> Any:
        """Perform one addon-route POST and raise on anything but success."""
        url = f"{self.base_url}{path}"
        try:
            async with self._session.post(
                url,
                headers={
                    "content-type": "application/json",
                    "authorization": f"Bearer {token}",
                },
                data=json.dumps(payload),
                ssl=self._verify_ssl,
                timeout=_REQUEST_TIMEOUT,
            ) as response:
                if response.status in (401, 403):
                    raise CamStackAuthError(f"{path}: hub rejected the token")
                status = response.status
                text = await response.text()
        except TimeoutError as err:
            raise CamStackConnectionError(f"{path}: timed out") from err
        except aiohttp.ClientError as err:
            raise CamStackConnectionError(f"{path}: {err}") from err

        if status >= 400:
            raise CamStackApiError(f"{path}: hub answered {status} {text.strip()}")
        try:
            return json.loads(text) if text else None
        except json.JSONDecodeError:
            return None

    async def _authenticated_request(
        self, method: str, path: str, payload: Any | None
    ) -> Any:
        """Call the hub, obtaining or renewing the token when required."""
        token = await self._auth.async_token()
        try:
            return await self._request(method, path, payload, token=token)
        except CamStackAuthError:
            # The token expired or was revoked. One retry behind a renewal;
            # a second failure is a real credential problem and must surface.
            token = await self._auth.async_renew()
            return await self._request(method, path, payload, token=token)

    async def _request(
        self,
        method: str,
        path: str,
        payload: Any | None,
        *,
        token: str | None,
    ) -> Any:
        """Perform one tRPC call and unwrap its envelope."""
        return await trpc_request(
            self._session,
            self.base_url,
            method,
            path,
            payload,
            token=token,
            verify_ssl=self._verify_ssl,
        )


async def trpc_request(
    session: aiohttp.ClientSession,
    base_url: str,
    method: str,
    path: str,
    payload: Any | None = None,
    *,
    token: str | None = None,
    verify_ssl: bool = False,
) -> Any:
    """Perform one tRPC call against `base_url` and unwrap its envelope.

    Module level, not a client method, because `PasswordAuth` needs it to log
    in and the client needs it for everything else. One place knows the wire
    format; a second copy is how the two drift.
    """
    url = f"{base_url}/trpc/{path}"
    headers = {"content-type": "application/json"}
    if token:
        headers["authorization"] = f"Bearer {token}"

    body: str | None = None
    if method == "POST":
        body = json.dumps({"json": payload if payload is not None else {}})
    elif payload is not None:
        url += f"?input={quote(json.dumps({'json': payload}))}"

    try:
        async with session.request(
            method,
            url,
            headers=headers,
            data=body,
            ssl=verify_ssl,
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
        if code == _TRPC_BAD_REQUEST:
            raise CamStackRequestError(f"{path}: {message}")
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
