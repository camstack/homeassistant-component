"""Mints the credential the Lovelace cards' iframes carry.

The integration holds the hub's OAuth access token and **never** hands it to a
browser: that credential is the whole account, it is refreshed on a timer, and
a dashboard is not a place to keep one. What the cards get instead is a hub
SHARE token (`csv_…`), minted per request through `auth.createShareToken` and
scoped to

* a view **kind** — `grid-view` or `events-view`, two disjoint perimeters on
  the hub's side (a grid token cannot read tracks, an events token cannot open
  WebRTC), and
* an explicit **list of device ids**, never the account.

Two guards live here rather than on the hub:

* a device id is only minted for if this entry actually **exports** it as a
  camera. Without that, any Home Assistant user could ask for a token against
  any device id the linked hub account can see, and the card would be a
  general-purpose minting endpoint for the whole hub.
* the token is **cached per (entry, kind, device ids)** until shortly before it
  expires. A card re-renders whenever `hass` updates — several times a second
  on a busy instance — and minting per render would write a row into the hub's
  `share_view_tokens` table each time, forever.

The tokens are deliberately short-lived. `ttlSec: "never"` exists on the hub
and is not used: a bearer credential that has left Home Assistant can only be
taken back by expiring or by revocation, and nothing here would ever revoke.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from aiohttp import web
from homeassistant.components.http import KEY_HASS, HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .api import CamStackError
from .const import (
    DOMAIN,
    EMBED_TOKEN_VIEW_URL,
    SHARE_SCOPE_KINDS,
    SHARE_SCOPE_MAX_DEVICES,
    SHARE_TOKEN_MUTATION,
    SHARE_TOKEN_RENEW_MARGIN,
    SHARE_TOKEN_TTL,
)
from .proxy import async_forget_entry_grants, async_issue_grant, proxy_base_for

_LOGGER = logging.getLogger(__name__)

_VIEW_REGISTERED = f"{DOMAIN}_embed_token_view_registered"
_CACHE = f"{DOMAIN}_embed_token_cache"


@dataclass(frozen=True, slots=True)
class MintedToken:
    """One share token, and the moment it stops being usable."""

    token: str
    #: Epoch seconds, or None when the hub issued a token that never expires.
    expires_at: float | None

    def is_usable(self, now: float) -> bool:
        """Return whether this token still has more than the margin left."""
        if self.expires_at is None:
            return True
        return self.expires_at - now > SHARE_TOKEN_RENEW_MARGIN.total_seconds()


def async_register_embed_token_view(hass: HomeAssistant) -> None:
    """Register the minting endpoint, once per Home Assistant."""
    if hass.data.get(_VIEW_REGISTERED):
        return
    hass.http.register_view(CamStackEmbedTokenView)
    hass.data[_VIEW_REGISTERED] = True


def _cache(hass: HomeAssistant) -> dict[tuple[str, str, tuple[int, ...]], MintedToken]:
    """Return the process-wide mint cache, creating it on first use."""
    return hass.data.setdefault(_CACHE, {})


def async_forget_entry_tokens(hass: HomeAssistant, entry_id: str) -> None:
    """Drop every token minted for an entry that is going away."""
    cache = _cache(hass)
    for key in [key for key in cache if key[0] == entry_id]:
        del cache[key]
    async_forget_entry_grants(hass, entry_id)


def parse_device_ids(raw: Any) -> list[int] | None:
    """Return a clean, ordered, de-duplicated id list, or None if unusable.

    Order is preserved because the grid renders its tiles in the order the
    devices are given: sorting here would silently rearrange an operator's
    wall.
    """
    if not isinstance(raw, list):
        return None
    seen: list[int] = []
    for entry in raw:
        # `True` is an `int` in Python and would become device 1.
        if not isinstance(entry, int) or isinstance(entry, bool) or entry < 0:
            return None
        if entry not in seen:
            seen.append(entry)
    if not seen or len(seen) > SHARE_SCOPE_MAX_DEVICES:
        return None
    return seen


def async_exported_camera_ids(hass: HomeAssistant, entry_id: str) -> set[int]:
    """Return the device ids this entry exports as cameras.

    Empty when the entry is not loaded — which is a refusal, not a pass: a
    token must never be minted against an entry whose export membership is
    unknown.
    """
    entry = hass.config_entries.async_get_entry(entry_id)
    runtime = getattr(entry, "runtime_data", None) if entry is not None else None
    coordinator = getattr(runtime, "coordinator", None)
    data = getattr(coordinator, "data", None)
    if data is None:
        return set()
    return {device.device_id for device in data.cameras()}


class CamStackEmbedTokenView(HomeAssistantView):
    """Hands a card a hub share token for the cameras it is configured with."""

    url = EMBED_TOKEN_VIEW_URL
    name = "api:camstack:embed_token"
    # Any authenticated Home Assistant user, deliberately: the sidebar panel is
    # `require_admin=False` and a dashboard card is the same surface. The scope
    # guard above — exported cameras only — is what keeps that from being a
    # minting endpoint for the whole hub.
    requires_auth = True

    async def post(self, request: web.Request) -> web.Response:
        """Mint (or reuse) a share token for one entry, kind and device set."""
        hass: HomeAssistant = request.app[KEY_HASS]
        try:
            body = await request.json()
        except ValueError:
            return self.json_message("invalid JSON body", 400)
        if not isinstance(body, dict):
            return self.json_message("expected an object", 400)

        kind = body.get("kind")
        if kind not in SHARE_SCOPE_KINDS:
            return self.json_message(
                f"kind must be one of {sorted(SHARE_SCOPE_KINDS)}", 400
            )

        device_ids = parse_device_ids(body.get("device_ids"))
        if device_ids is None:
            return self.json_message(
                "device_ids must be 1 to "
                f"{SHARE_SCOPE_MAX_DEVICES} non-negative integers",
                400,
            )

        entry_id = body.get("entry_id")
        if entry_id is None:
            entries = hass.config_entries.async_entries(DOMAIN)
            if not entries:
                return self.json_message("no CamStack entry is configured", 404)
            entry_id = entries[0].entry_id
        if not isinstance(entry_id, str):
            return self.json_message("entry_id must be a string", 400)

        exported = async_exported_camera_ids(hass, entry_id)
        if not exported:
            return self.json_message(
                "the CamStack entry is not loaded, or exports no camera", 503
            )
        unknown = [device_id for device_id in device_ids if device_id not in exported]
        if unknown:
            # Named, because a card configured against a camera the hub has
            # stopped exporting looks identical to a broken integration.
            return self.json_message(
                f"CamStack does not export these devices as cameras: {unknown}", 400
            )

        return await self._async_answer(hass, entry_id, kind, device_ids)

    async def _async_answer(
        self,
        hass: HomeAssistant,
        entry_id: str,
        kind: str,
        device_ids: list[int],
    ) -> web.Response:
        """Return a cached token, or mint one and cache it."""
        cache = _cache(hass)
        key = (entry_id, kind, tuple(device_ids))
        now = dt_util.utcnow().timestamp()
        cached = cache.get(key)
        if cached is not None and cached.is_usable(now):
            return self.json(_answer(hass, entry_id, key, cached))

        entry = hass.config_entries.async_get_entry(entry_id)
        client = getattr(getattr(entry, "runtime_data", None), "client", None)
        if client is None:
            return self.json_message("the CamStack entry is not loaded", 503)

        payload = {
            "scope": {"kind": kind, "deviceIds": device_ids},
            "ttlSec": int(SHARE_TOKEN_TTL.total_seconds()),
        }
        try:
            result = await client.mutate(SHARE_TOKEN_MUTATION, payload)
        except CamStackError as err:
            # The hub's own sentence, verbatim. "Could not mint a token" tells
            # an operator nothing; "no scope grants view on …" tells them
            # everything.
            _LOGGER.warning("CamStack refused to mint an embed token: %s", err)
            return self.json_message(str(err), 502)

        minted = _read_minted(result)
        if minted is None:
            return self.json_message("the hub returned no share token", 502)
        cache[key] = minted
        return self.json(_answer(hass, entry_id, key, minted))


def _answer(
    hass: HomeAssistant,
    entry_id: str,
    key: tuple[str, str, tuple[int, ...]],
    minted: MintedToken,
) -> dict[str, Any]:
    """Return the token, and the same-origin relay path bound to it.

    The relay path is the card's `serverUrl`; its grant is stable per scope, so
    a re-mint changes the token behind the URL and never the URL.
    """
    scope_key = (key[0], key[1], *map(str, key[2]))
    grant_id = async_issue_grant(
        hass, entry_id, scope_key, minted.token, minted.expires_at
    )
    return {
        "token": minted.token,
        "expires_at": minted.expires_at,
        "proxy_base": proxy_base_for(grant_id),
    }


def _read_minted(result: Any) -> MintedToken | None:
    """Read `{ token, expiresAt }` off the hub's answer."""
    if not isinstance(result, dict):
        return None
    token = result.get("token")
    if not isinstance(token, str) or not token:
        return None
    raw_expiry = result.get("expiresAt")
    expires_at: float | None = None
    if isinstance(raw_expiry, int | float) and not isinstance(raw_expiry, bool):
        # The hub reports epoch MILLISECONDS. A value read as seconds would sit
        # 55 000 years in the future and the token would never be re-minted.
        expires_at = float(raw_expiry) / 1000.0
    return MintedToken(token=token, expires_at=expires_at)
