"""Hands the Lovelace cards' iframes the credential they carry.

The integration holds the hub's OAuth access token and **never** hands it to a
browser: that credential is the whole account, it is refreshed on a timer, and
a dashboard is not a place to keep one. What the cards get instead is a hub
token minted by `auth.mintToken` whose only scope is a view grant on

* a view **kind** — `grid-view` or `events-view`, two disjoint perimeters on
  the hub's side (a grid token cannot read tracks, an events token cannot open
  WebRTC), and
* an explicit **list of device ids**, never the account.

Two guards live here rather than on the hub:

* a device id is only minted for if this entry actually **exports** it as a
  camera. Without that, any Home Assistant user could ask for a token against
  any device id the linked hub account can see, and the card would be a
  general-purpose minting endpoint for the whole hub.
* one token per **scope** (entry, kind, device ids, talk), kept for 90 days,
  renewed in place, found again after a restart and revoked when a card
  replaces it — see `token_lifecycle.py`. A card re-renders whenever `hass`
  updates, several times a second on a busy instance; none of that reaches
  the hub.

## Where the token goes

To the browser only when the card frames the hub DIRECTLY (`url_base` set on
the card). The default path is the relay in `proxy.py`, which strips the
incoming `Authorization` and injects the token server-side on every forwarded
request — HTTP and the WebSocket upgrade alike. A second copy of that
credential in the page authenticates nothing there and costs plenty: it used to
sit in the events iframe's `#t=` fragment, i.e. in browser history and in every
screenshot of the dashboard, and anyone who could read it could use it against
the hub from outside Home Assistant for the rest of its life. What the browser
gets instead is the grant id — same-origin, revocable, dead when the token
dies.
"""

from __future__ import annotations

import logging
from typing import Any, Final

from aiohttp import web
from homeassistant.components.http import KEY_HASS, HomeAssistantView
from homeassistant.core import HomeAssistant

from .api import CamStackError
from .const import (
    CONF_TALK_ENABLED,
    DEFAULT_TALK_ENABLED,
    DOMAIN,
    EMBED_TOKEN_VIEW_URL,
    VIEW_GRANT_GRID,
    VIEW_GRANT_KINDS,
    VIEW_GRANT_MAX_DEVICES,
)
from .proxy import (
    async_forget_entry_grants,
    async_forget_scope_grant,
    async_issue_grant,
    proxy_base_for,
)
from .token_lifecycle import MintedToken, TokenScope, async_get_keeper

_LOGGER = logging.getLogger(__name__)

_VIEW_REGISTERED = f"{DOMAIN}_embed_token_view_registered"

#: `_parse_replaced`'s answer for a malformed `replaces`.
_INVALID: Final = object()


def async_register_embed_token_view(hass: HomeAssistant) -> None:
    """Register the minting endpoint, once per Home Assistant."""
    if hass.data.get(_VIEW_REGISTERED):
        return
    hass.http.register_view(CamStackEmbedTokenView)
    hass.data[_VIEW_REGISTERED] = True


def async_forget_entry_tokens(hass: HomeAssistant, entry_id: str) -> None:
    """Forget an UNLOADING entry's grants and verifications; keep its tokens.

    The relay must not keep serving a credential of an entry that is gone, and
    a reloaded entry — possibly now pointing at a different hub — must match
    every stored token against its hub before serving it. The tokens
    themselves survive: a reload is not a removal.
    """
    async_get_keeper(hass).async_forget_entry(entry_id)
    async_forget_entry_grants(hass, entry_id)


async def async_revoke_entry_tokens(
    hass: HomeAssistant, entry_id: str, client: Any | None
) -> None:
    """Revoke every token of a REMOVED entry on its hub, and forget them."""
    await async_get_keeper(hass).async_revoke_entry(client, entry_id)
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
    if not seen or len(seen) > VIEW_GRANT_MAX_DEVICES:
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


def async_talk_allowed(hass: HomeAssistant, entry_id: str) -> bool:
    """Report whether THIS entry allows talk-back through a card's view token.

    The one authority (D62's rule applied to an integration-wide function: the
    switch stores nothing of its own, it reads the thing that already owns the
    function). A card may decline talk-back; nothing on a dashboard can grant
    it, because a Lovelace config is editable by anyone who can edit a
    dashboard and this endpoint is open to every authenticated user.

    Read from `options` with a default, so an entry created before the key
    existed answers `False` without a migration — and an operator's change takes
    effect through the update listener that already reloads the entry.
    """
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None:
        return False
    return bool(entry.options.get(CONF_TALK_ENABLED, DEFAULT_TALK_ENABLED))


class CamStackEmbedTokenView(HomeAssistantView):
    """Hands a card a hub view token for the cameras it is configured with."""

    url = EMBED_TOKEN_VIEW_URL
    name = "api:camstack:embed_token"
    # Any authenticated Home Assistant user, deliberately: the sidebar panel is
    # `require_admin=False` and a dashboard card is the same surface. The scope
    # guard above — exported cameras only — is what keeps that from being a
    # minting endpoint for the whole hub.
    requires_auth = True

    async def post(self, request: web.Request) -> web.Response:
        """Find, renew or mint the token for one entry, kind and device set."""
        hass: HomeAssistant = request.app[KEY_HASS]
        try:
            body = await request.json()
        except ValueError:
            return self.json_message("invalid JSON body", 400)
        if not isinstance(body, dict):
            return self.json_message("expected an object", 400)

        kind = body.get("kind")
        if kind not in VIEW_GRANT_KINDS:
            return self.json_message(
                f"kind must be one of {sorted(VIEW_GRANT_KINDS)}", 400
            )

        device_ids = parse_device_ids(body.get("device_ids"))
        if device_ids is None:
            return self.json_message(
                "device_ids must be 1 to "
                f"{VIEW_GRANT_MAX_DEVICES} non-negative integers",
                400,
            )

        # Does this card frame the hub DIRECTLY, or through the relay?
        # The relayed path strips whatever `Authorization` the browser sends and
        # injects the view token itself (see `proxy.py`), so the copy in the
        # page authenticates nothing and only adds exposure: it used to ride the
        # events iframe's `#t=` fragment into browser history, readable in any
        # screenshot of the dashboard and usable against the hub from OUTSIDE
        # Home Assistant for the rest of its life. A card with an explicit
        # `url_base` has no relay in front of it and still needs the credential,
        # so it asks for it here — the only case that gets one.
        direct = body.get("direct")
        if direct is not None and not isinstance(direct, bool):
            return self.json_message("direct must be a boolean", 400)

        # Talk-back. The card ASKS; the entry's option decides. A card that asks
        # while the option is off is not an error — it is a card whose operator
        # has not turned talk-back on, and refusing the whole mint over it would
        # take the WALL down (no video at all) to withhold a microphone. So the
        # token is minted without talk and the answer SAYS so, which is what
        # lets the card draw its talk control disabled with the right reason
        # instead of a button that fails at the hub.
        talk = body.get("talk")
        if talk is not None and not isinstance(talk, bool):
            return self.json_message("talk must be a boolean", 400)

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

        # AND, never OR: the option is the grant and the card can only narrow
        # it. Talk is also meaningless on an events token — that perimeter has
        # no camera to speak through — so it is confined to the grid kind here
        # rather than relying on the hub to ignore it.
        granted = (
            talk is True
            and kind == VIEW_GRANT_GRID
            and async_talk_allowed(hass, entry_id)
        )
        scope = TokenScope(entry_id, kind, tuple(device_ids), granted)

        # The scope this card held before its device set (or talk-back) changed.
        # Revoked AFTER the new token exists, so a failed mint never leaves the
        # card with nothing; a card sharing the old scope sees its grant go
        # stale and asks again.
        replaced = _parse_replaced(body.get("replaces"), entry_id, kind)
        if replaced is _INVALID:
            return self.json_message(
                "replaces must be an object with device_ids and an optional talk",
                400,
            )
        return await self._async_answer(
            hass, scope, direct is True, None if replaced == scope else replaced
        )

    async def _async_answer(
        self,
        hass: HomeAssistant,
        scope: TokenScope,
        direct: bool,
        replaced: TokenScope | None,
    ) -> web.Response:
        """Return the scope's token (found, renewed or minted), then retire the old."""
        entry = hass.config_entries.async_get_entry(scope.entry_id)
        client = getattr(getattr(entry, "runtime_data", None), "client", None)
        if client is None:
            return self.json_message("the CamStack entry is not loaded", 503)

        keeper = async_get_keeper(hass)
        try:
            minted = await keeper.async_token_for(client, scope)
        except CamStackError as err:
            # The hub's own sentence, verbatim. "Could not mint a token" tells
            # an operator nothing; "a share-view grant names a device you cannot
            # view" tells them everything.
            _LOGGER.warning("CamStack refused to mint an embed token: %s", err)
            return self.json_message(str(err), 502)

        if replaced is not None and await keeper.async_revoke_scope(client, replaced):
            async_forget_scope_grant(hass, replaced.grant_key)
        return self.json(_answer(hass, scope, minted, direct))


def _parse_replaced(raw: Any, entry_id: str, kind: str) -> TokenScope | object | None:
    """Read the card's previous scope, or None when it sent none.

    Same entry and same kind as the request: a card never changes either. The
    talk flag is what the card was GRANTED, so it is taken as sent — the option
    may have changed since, and the old token is what is being named.
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        return _INVALID
    device_ids = parse_device_ids(raw.get("device_ids"))
    talk = raw.get("talk")
    if device_ids is None or (talk is not None and not isinstance(talk, bool)):
        return _INVALID
    granted = talk is True and kind == VIEW_GRANT_GRID
    return TokenScope(entry_id, kind, tuple(device_ids), granted)


def _answer(
    hass: HomeAssistant, scope: TokenScope, minted: MintedToken, direct: bool
) -> dict[str, Any]:
    """Return the same-origin relay path, and the token only if it is needed.

    The relay path is the card's `serverUrl`; its grant is stable per scope, so
    a re-mint changes the token behind the URL and never the URL.

    The token itself is a bearer credential and it stays INSIDE Home Assistant
    unless the caller says it frames the hub directly. On the relayed path the
    grant is what authorises the browser: it only works from a Home Assistant
    session, it is revocable, and it dies with the token. `expires_at` is
    reported on both paths regardless — it is not a secret, and the card needs
    it to know when to come back for a fresh grant.
    """
    grant_id = async_issue_grant(
        hass, scope.entry_id, scope.grant_key, minted.token, minted.expires_at
    )
    return {
        **({"token": minted.token} if direct else {}),
        "expires_at": minted.expires_at,
        "proxy_base": proxy_base_for(grant_id),
        # What the card GOT, not what it asked for. A card that asked and was
        # refused must be able to say WHY — "the integration has talk-back off"
        # is an answer an operator can act on; a talk button that fails at the
        # hub is not.
        "talk": scope.talk,
    }
