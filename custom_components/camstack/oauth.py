"""OAuth against a CamStack hub.

The hub is its own authorization server — the same one it uses for Alexa
account linking. This integration is registered on it as a **public** client
(`integration=homeassistant`): the source you are reading is published, so
there is no client secret it could keep, and PKCE is what binds an issued
authorization code to the instance that asked for it.

Three things here are not Home Assistant's defaults, and each is deliberate:

* **TLS.** `LocalOAuth2Implementation` reaches for a session that verifies
  certificates. A CamStack hub ships a self-signed one, and `verify_ssl` is
  already the per-entry setting every other call in this integration honours.
  The token exchange must honour the same setting or linking fails on a stock
  hub while the rest of the integration works — the worst possible failure.
* **PKCE.** `code_challenge` on the authorize URL, `code_verifier` on the token
  request, no `client_secret` anywhere.
* **One implementation per hub.** The authorize and token URLs are the
  operator's own address, so the implementation cannot be a constant; it is
  built from the config entry and re-registered on every setup.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
from typing import Any, cast

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import CamStackAuth, CamStackConnectionError, base_url_for
from .const import (
    OAUTH_AUTHORIZE_PATH,
    OAUTH_CLIENT_ID,
    OAUTH_DISCOVERY_PATH,
    OAUTH_INTEGRATION_ID,
    OAUTH_TOKEN_PATH,
)

_LOGGER = logging.getLogger(__name__)

_PROBE_TIMEOUT = aiohttp.ClientTimeout(total=15)


def implementation_domain(host: str, port: int) -> str:
    """Return the implementation key for a hub.

    Home Assistant stores implementations in a dict keyed by this string and
    writes it into the entry as `auth_implementation`. Two hubs must therefore
    not collide, and the key must be reconstructible at setup time from the
    entry alone — so it is derived from the address, not generated.
    """
    return f"{host}:{port}"


async def async_probe_oauth(
    hass: HomeAssistant, host: str, port: int, *, verify_ssl: bool
) -> bool:
    """Return whether this hub can link a Home Assistant over OAuth.

    The hub-side support ships with the server, not with an addon, so a hub
    that predates it answers 404 here. Asking first is what lets this component
    offer the manual path with a reason instead of walking the operator into a
    browser round trip that ends on a 400.

    A hub that cannot be reached at all raises, because that is a different
    problem and silently offering the manual path would hide it.
    """
    session = async_get_clientsession(hass, verify_ssl=verify_ssl)
    url = f"{base_url_for(host, port)}{OAUTH_DISCOVERY_PATH}"
    try:
        async with session.get(url, ssl=verify_ssl, timeout=_PROBE_TIMEOUT) as response:
            if response.status == 404:
                return False
            if response.status != 200:
                _LOGGER.debug("OAuth discovery answered %s", response.status)
                return False
            payload = await response.json(content_type=None)
    except TimeoutError as err:
        raise CamStackConnectionError("oauth discovery: timed out") from err
    except aiohttp.ClientError as err:
        raise CamStackConnectionError(f"oauth discovery: {err}") from err
    except ValueError:
        # A reverse proxy answering HTML on an unknown path looks exactly like
        # an older hub, and is.
        return False

    integrations = payload.get("integrations") if isinstance(payload, dict) else None
    if not isinstance(integrations, list):
        return False
    return any(
        isinstance(entry, dict) and entry.get("integrationId") == OAUTH_INTEGRATION_ID
        for entry in integrations
    )


class CamStackOAuth2Implementation(config_entry_oauth2_flow.LocalOAuth2Implementation):
    """A public, PKCE-bound OAuth client of one specific CamStack hub."""

    def __init__(
        self, hass: HomeAssistant, host: str, port: int, *, verify_ssl: bool
    ) -> None:
        """Build the implementation for the hub at `host:port`."""
        base = base_url_for(host, port)
        super().__init__(
            hass,
            implementation_domain(host, port),
            OAUTH_CLIENT_ID,
            # Never sent. The hub does not read it, and a value here would be
            # a secret published on GitHub pretending to be one.
            "",
            f"{base}{OAUTH_AUTHORIZE_PATH}",
            f"{base}{OAUTH_TOKEN_PATH}",
        )
        self._verify_ssl = verify_ssl
        self._code_verifier: str | None = None

    @property
    def name(self) -> str:
        """Return the label shown when more than one hub is configured."""
        return f"CamStack ({self.domain})"

    @property
    def extra_authorize_data(self) -> dict[str, Any]:
        """Return the hub-specific authorize parameters.

        A fresh verifier per authorization: reusing one across attempts would
        let a code intercepted from an earlier attempt be redeemed with a
        verifier learned from a later one.
        """
        verifier = secrets.token_urlsafe(64)
        self._code_verifier = verifier
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
        return {
            "integration": OAUTH_INTEGRATION_ID,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }

    async def _token_request(self, data: dict[str, Any]) -> dict[str, Any]:
        """Exchange or refresh a token, honouring the entry's `verify_ssl`."""
        session = async_get_clientsession(self.hass, verify_ssl=self._verify_ssl)
        body = {**data, "client_id": self.client_id}
        if body.get("grant_type") == "authorization_code":
            if self._code_verifier is None:
                # The hub refuses a bare code, so failing here with a readable
                # message beats an opaque 400 from the exchange.
                raise ValueError(
                    "no PKCE verifier for this exchange — restart the flow"
                )
            body["code_verifier"] = self._code_verifier

        async with session.post(
            self.token_url, data=body, ssl=self._verify_ssl, timeout=_PROBE_TIMEOUT
        ) as response:
            response.raise_for_status()
            return cast(dict[str, Any], await response.json(content_type=None))


class OAuth2Auth(CamStackAuth):
    """Reads the access token Home Assistant keeps refreshed."""

    def __init__(self, session: config_entry_oauth2_flow.OAuth2Session) -> None:
        """Wrap the Home Assistant OAuth session for the config entry."""
        self._session = session

    async def async_token(self) -> str:
        """Return a valid access token, refreshing it if it has expired."""
        await self._session.async_ensure_token_valid()
        return cast(str, self._session.token["access_token"])

    async def async_renew(self) -> str:
        """Re-check the token after the hub rejected it.

        There is no password to replay. If the token has not expired by the
        clock this returns the same one and the retry fails again — which is
        exactly what a link REVOKED on the hub looks like, and it should
        surface as a failure rather than be papered over. Home Assistant then
        refreshes on schedule, the refresh is refused, and the entry goes to
        reauth: the operator relinks. Nothing here should try to be cleverer
        than that.
        """
        await self._session.async_ensure_token_valid()
        return cast(str, self._session.token["access_token"])
