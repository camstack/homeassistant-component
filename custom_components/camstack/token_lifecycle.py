"""The lifecycle of the hub tokens the Lovelace cards' iframes carry.

The hub has ONE token format (D892, D895), minted by `auth.mintToken`; what a
token may do is its `scopes`. A card's token carries exactly one: a
`share-view` grant for one view kind over an explicit list of cameras, the one
grant an account link — this integration's OAuth token — may mint by itself.

One token per **scope** — (entry, kind, ordered device ids, talk-back) — for as
long as a dashboard asks for it:

* **mint** once, for `EMBED_TOKEN_LIFETIME` (the hub's 90-day maximum), under a
  label that carries a stable digest of the scope;
* **find** it again after a Home Assistant restart: the secret is persisted in
  a `Store` (the hub returns it only at mint), and the row is looked up by its
  label through `auth.listTokens` — once per scope per process — so the
  integration never mints a duplicate of a token it already holds. A stored
  secret whose row the hub no longer lists is dropped; a listed row whose
  secret Home Assistant lost is REVOKED and replaced, since nothing can use it;
* **renew in place** with `auth.extendToken` when a card asks with less than
  `EMBED_TOKEN_RENEW_BEFORE` left — the same secret keeps working, so the
  relay's grant and the card's iframe are untouched;
* **revoke** with `auth.revokeToken` when a card replaces its device set, and
  every token of an entry when the entry is removed.

A hub that does not answer never takes a wall down: a cached token the hub
would still accept is served while listing or renewing fails, and the work is
retried on the next request.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import Any, Protocol

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .api import CamStackApiError, CamStackError
from .const import (
    DOMAIN,
    EMBED_TOKEN_LABEL_MAX,
    EMBED_TOKEN_LABEL_PREFIX,
    EMBED_TOKEN_LIFETIME,
    EMBED_TOKEN_RENEW_BEFORE,
    EMBED_TOKEN_STORAGE_KEY,
    EMBED_TOKEN_STORAGE_VERSION,
    TOKEN_EXTEND_MUTATION,
    TOKEN_LIST_QUERY,
    TOKEN_MINT_MUTATION,
    TOKEN_REVOKE_MUTATION,
    VIEW_GRANT_GRID,
    VIEW_GRANT_SCOPE_TYPE,
)

_LOGGER = logging.getLogger(__name__)

_KEEPER = f"{DOMAIN}_embed_token_keeper"
_DIGEST_HEX = 12
_MS = 1000.0


class HubClient(Protocol):
    """The two calls this module makes on `CamStackClient`."""

    async def query(self, path: str, payload: Any | None = None) -> Any:
        """Run a tRPC query."""

    async def mutate(self, path: str, payload: Any | None = None) -> Any:
        """Run a tRPC mutation."""


@dataclass(frozen=True, slots=True)
class TokenScope:
    """What one card token is for. Two scopes are two credentials."""

    entry_id: str
    kind: str
    #: Ordered: the grid renders its tiles in this order.
    device_ids: tuple[int, ...]
    talk: bool

    @property
    def key(self) -> str:
        """Stable Store / memory key."""
        ids = ",".join(str(device_id) for device_id in self.device_ids)
        return f"{self.entry_id}|{self.kind}|{ids}|{'1' if self.talk else '0'}"

    @property
    def grant_key(self) -> tuple[str, ...]:
        """The relay's grant key for this scope.

        The talk flag is in it for the same reason it is in `key`: the grant id
        is what the browser carries, and two scopes that differ only by
        talk-back sharing one id would mean the relay injecting whichever of the
        two tokens was issued last.
        """
        ids = map(str, self.device_ids)
        return (self.entry_id, self.kind, *ids, "talk" if self.talk else "no-talk")

    @property
    def label(self) -> str:
        """The hub label: readable, and unique per scope through its digest.

        The digest is the idempotency key `auth.listTokens({label})` finds the
        token by after a restart. It covers the entry id, so two Home Assistant
        entries linked to the same hub account never adopt each other's token.
        """
        digest = hashlib.sha256(self.key.encode()).hexdigest()[:_DIGEST_HEX]
        count = len(self.device_ids)
        what = "grid" if self.kind == VIEW_GRANT_GRID else "events"
        cameras = f"{count} camera" if count == 1 else f"{count} cameras"
        talk = " · talk" if self.talk else ""
        label = f"{EMBED_TOKEN_LABEL_PREFIX} · {what} · {cameras}{talk} · #{digest}"
        return label[:EMBED_TOKEN_LABEL_MAX]

    def hub_scopes(self) -> list[dict[str, Any]]:
        """Return the mint's `scopes`: one view grant, for exactly these cameras."""
        grant = {
            "kind": self.kind,
            "deviceIds": list(self.device_ids),
            # OMITTED when off, never `false`: on the hub absent means off.
            **({"talk": True} if self.talk else {}),
        }
        return [{"type": VIEW_GRANT_SCOPE_TYPE, "grant": grant}]


@dataclass(frozen=True, slots=True)
class MintedToken:
    """One hub token: its secret, its row id, and when it stops working."""

    token: str
    id: str
    #: Epoch seconds, or None for a token that never expires.
    expires_at: float | None

    def is_valid(self, now: float) -> bool:
        """Return whether the hub would still accept this token."""
        return self.expires_at is None or self.expires_at > now

    def needs_renewal(self, now: float) -> bool:
        """Return whether it is time to extend this token in place."""
        if self.expires_at is None:
            return False
        return self.expires_at - now < EMBED_TOKEN_RENEW_BEFORE.total_seconds()

    def to_storage(self) -> dict[str, Any]:
        """Serialize for the Store."""
        return {"token": self.token, "id": self.id, "expires_at": self.expires_at}

    @staticmethod
    def from_storage(raw: Any) -> MintedToken | None:
        """Read a Store row, or None when it is not a usable token.

        A row without an id cannot be renewed or revoked, so it is not one.
        """
        if not isinstance(raw, dict):
            return None
        token = raw.get("token")
        token_id = raw.get("id")
        if not _is_text(token) or not _is_text(token_id):
            return None
        expires_raw = raw.get("expires_at")
        if expires_raw is None:
            return MintedToken(token=token, id=token_id, expires_at=None)
        if not _is_number(expires_raw):
            return None
        return MintedToken(token=token, id=token_id, expires_at=float(expires_raw))


@dataclass(frozen=True, slots=True)
class HubTokenRow:
    """One row of `auth.listTokens` — never a secret."""

    id: str
    expires_at: float | None


def _is_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


def _is_number(value: Any) -> bool:
    # `True` is an `int` in Python.
    return isinstance(value, int | float) and not isinstance(value, bool)


def _expiry_seconds(raw: Any) -> float | None:
    """Epoch milliseconds on the wire to seconds; None stays never-expires.

    Read as seconds, a hub expiry would sit 55 000 years in the future and the
    token would never be renewed.
    """
    return float(raw) / _MS if _is_number(raw) else None


def read_minted(result: Any) -> MintedToken | None:
    """Read `{token, record: {id, expiresAt}}` off the hub's mint answer."""
    if not isinstance(result, dict):
        return None
    token = result.get("token")
    record = result.get("record")
    if not _is_text(token) or not isinstance(record, dict):
        return None
    token_id = record.get("id")
    if not _is_text(token_id):
        return None
    expiry = record.get("expiresAt")
    if expiry is not None and not _is_number(expiry):
        return None
    return MintedToken(token=token, id=token_id, expires_at=_expiry_seconds(expiry))


def read_live_rows(result: Any, now: float) -> list[HubTokenRow]:
    """Every listed row that still works: not revoked, not expired."""
    if not isinstance(result, list):
        return []
    rows: list[HubTokenRow] = []
    for raw in result:
        if not isinstance(raw, dict) or not _is_text(raw.get("id")):
            continue
        if raw.get("revokedAt") is not None:
            continue
        expires_at = _expiry_seconds(raw.get("expiresAt"))
        if expires_at is not None and expires_at <= now:
            continue
        rows.append(HubTokenRow(id=raw["id"], expires_at=expires_at))
    return rows


def _now() -> float:
    return dt_util.utcnow().timestamp()


class EmbedTokenKeeper:
    """Holds every card token of this Home Assistant, in memory and on disk."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Bind to `hass`; nothing is read until the first request."""
        self._store: Store[dict[str, Any]] = Store(
            hass, EMBED_TOKEN_STORAGE_VERSION, EMBED_TOKEN_STORAGE_KEY
        )
        self._tokens: dict[str, MintedToken] = {}
        #: Scope keys whose row has been matched against the hub this process.
        self._verified: set[str] = set()
        self._locks: dict[str, asyncio.Lock] = {}
        #: One load for every scope: the per-scope locks do not cover it.
        self._load_lock = asyncio.Lock()
        self._loaded = False
        #: Entries removed this process: a mint in flight for one is revoked.
        self._removed: set[str] = set()

    # --- the one entry point --------------------------------------------

    async def async_token_for(
        self, client: HubClient, scope: TokenScope
    ) -> MintedToken:
        """Return a live token for `scope`: cached, found, renewed or minted.

        Serialized per scope: a dashboard renders all its cards at once, and
        two concurrent first requests must not mint two tokens.

        Raises `CamStackError` only when no usable token can be produced.
        """
        async with self._lock(scope.key):
            await self._async_ensure_loaded()
            cached = self._tokens.get(scope.key)
            if scope.key not in self._verified:
                cached = await self._async_reconcile(client, scope, cached)
            now = _now()
            if cached is None or not cached.is_valid(now):
                stale = [] if cached is None else [cached.id]
                return await self._async_mint(client, scope, stale)
            if cached.needs_renewal(now):
                return await self._async_renew(client, scope, cached)
            return cached

    # --- revocation -------------------------------------------------------

    async def async_revoke_scope(self, client: HubClient, scope: TokenScope) -> bool:
        """Revoke the token of a scope a card no longer uses.

        Revokes the stored row and any row listed under the scope's label (a
        token minted before a restart that lost its secret). Returns whether
        anything was revoked.
        """
        async with self._lock(scope.key):
            await self._async_ensure_loaded()
            ids: set[str] = set()
            cached = self._tokens.pop(scope.key, None)
            if cached is not None:
                ids.add(cached.id)
            try:
                listed = await client.query(TOKEN_LIST_QUERY, {"label": scope.label})
            except CamStackError as err:
                _LOGGER.debug("Could not list tokens for %s: %s", scope.label, err)
            else:
                ids.update(row.id for row in read_live_rows(listed, _now()))
            self._verified.discard(scope.key)
            await self._async_persist()
        return await _async_revoke(client, ids) > 0

    async def async_revoke_entry(self, client: HubClient | None, entry_id: str) -> None:
        """Revoke and forget every token minted for an entry being removed.

        `client` is None when the entry has no credentials left; the secrets
        are dropped anyway, and the hub expires the rows on its own.
        """
        self._removed.add(entry_id)
        await self._async_ensure_loaded()
        prefix = f"{entry_id}|"
        keys = [key for key in self._tokens if key.startswith(prefix)]
        ids = [self._tokens.pop(key).id for key in keys]
        self._verified.difference_update(keys)
        # The hub first: a disk error must not leave the rows alive.
        if client is not None:
            await _async_revoke(client, ids)
        await self._async_persist()

    def async_forget_entry(self, entry_id: str) -> None:
        """Forget what was VERIFIED for an entry that unloads; keep its secrets.

        An unload is a reload far more often than a removal (every options
        change is one), and the reloaded entry may point at another hub — so
        every scope is matched against the hub again before it is served, and
        nothing is minted or revoked here.
        """
        prefix = f"{entry_id}|"
        self._verified = {key for key in self._verified if not key.startswith(prefix)}

    # --- internals --------------------------------------------------------

    def _lock(self, key: str) -> asyncio.Lock:
        return self._locks.setdefault(key, asyncio.Lock())

    async def _async_reconcile(
        self, client: HubClient, scope: TokenScope, cached: MintedToken | None
    ) -> MintedToken | None:
        """Match the stored secret against the hub's rows under the label."""
        try:
            listed = await client.query(TOKEN_LIST_QUERY, {"label": scope.label})
        except CamStackError as err:
            # Unverified, not wrong: serve what we hold and ask again next time.
            _LOGGER.info("Could not list CamStack tokens for %s: %s", scope.label, err)
            return cached
        rows = read_live_rows(listed, _now())
        self._verified.add(scope.key)
        ours = (
            None
            if cached is None
            else next((row for row in rows if row.id == cached.id), None)
        )
        if cached is not None and ours is not None:
            # A duplicate under our label can only be a leftover of a race or a
            # lost write; nothing holds its secret.
            await _async_revoke(client, [row.id for row in rows if row.id != ours.id])
            adopted = replace(cached, expires_at=ours.expires_at)
            if adopted != cached:
                self._tokens[scope.key] = adopted
                await self._async_persist()
            return adopted
        if cached is not None:
            # The hub no longer lists our row: revoked from the hub's UI, expired,
            # or this entry now points at another hub.
            _LOGGER.info("Stored CamStack token %s is gone from the hub", cached.id)
            self._tokens.pop(scope.key, None)
            await self._async_persist()
        if rows:
            # Rows whose secret Home Assistant lost: nothing can use them.
            _LOGGER.info(
                "Revoking %d CamStack token(s) for %s whose secret was lost",
                len(rows),
                scope.label,
            )
            await _async_revoke(client, [row.id for row in rows])
        return None

    async def _async_mint(
        self, client: HubClient, scope: TokenScope, stale_ids: Iterable[str]
    ) -> MintedToken:
        result = await client.mutate(
            TOKEN_MINT_MUTATION,
            {
                "scopes": scope.hub_scopes(),
                "expiresIn": int(EMBED_TOKEN_LIFETIME.total_seconds()),
                "label": scope.label,
            },
        )
        minted = read_minted(result)
        if minted is None:
            raise CamStackApiError(f"{TOKEN_MINT_MUTATION}: hub returned no token")
        if scope.entry_id in self._removed:
            # The entry was removed while this mint was in flight.
            await _async_revoke(client, [minted.id])
            raise CamStackApiError("the CamStack entry was removed")
        # Not marked verified here: only a successful reconcile is. A mint after
        # a failed listing is checked against the hub on the next request.
        self._tokens[scope.key] = minted
        await self._async_persist()
        await _async_revoke(client, [i for i in stale_ids if i != minted.id])
        return minted

    async def _async_renew(
        self, client: HubClient, scope: TokenScope, cached: MintedToken
    ) -> MintedToken:
        try:
            result = await client.mutate(
                TOKEN_EXTEND_MUTATION,
                {
                    "id": cached.id,
                    "expiresIn": int(EMBED_TOKEN_LIFETIME.total_seconds()),
                },
            )
        except CamStackApiError as err:
            # An answer that is an error — a refusal OR a 5xx from anything in
            # front of the hub. Only the hub's own list can say the row is gone.
            _LOGGER.info("CamStack did not extend token %s: %s", cached.id, err)
            return await self._async_replace_if_gone(client, scope, cached)
        except CamStackError as err:
            # Unreachable: the token still works, try again on the next request.
            _LOGGER.info("Could not extend CamStack token %s: %s", cached.id, err)
            return cached
        expiry = result.get("expiresAt") if isinstance(result, dict) else None
        if not _is_number(expiry):
            # A renewed token always has an expiry; anything else is not an
            # answer, and reading it as "never" would stop the renewals.
            _LOGGER.info("CamStack's extend of %s carried no expiry", cached.id)
            return cached
        renewed = replace(cached, expires_at=_expiry_seconds(expiry))
        self._tokens[scope.key] = renewed
        await self._async_persist()
        return renewed

    async def _async_replace_if_gone(
        self, client: HubClient, scope: TokenScope, cached: MintedToken
    ) -> MintedToken:
        """Re-mint only when the hub no longer LISTS the row it refused to extend."""
        try:
            listed = await client.query(TOKEN_LIST_QUERY, {"label": scope.label})
        except CamStackError as err:
            _LOGGER.info("Could not list CamStack tokens for %s: %s", scope.label, err)
            return cached
        if any(row.id == cached.id for row in read_live_rows(listed, _now())):
            return cached
        try:
            return await self._async_mint(client, scope, [cached.id])
        except CamStackError:
            if cached.is_valid(_now()):
                return cached
            raise

    async def _async_ensure_loaded(self) -> None:
        if self._loaded:
            return
        async with self._load_lock:
            if self._loaded:
                return
            stored = await self._store.async_load()
            rows = stored.get("tokens") if isinstance(stored, dict) else None
            now = _now()
            for key, raw in (rows if isinstance(rows, dict) else {}).items():
                minted = MintedToken.from_storage(raw)
                if isinstance(key, str) and minted is not None and minted.is_valid(now):
                    self._tokens[key] = minted
            self._loaded = True
        _LOGGER.debug("Restored %d embed token(s) from Store", len(self._tokens))

    async def _async_persist(self) -> None:
        now = _now()
        payload = {
            "tokens": {
                key: minted.to_storage()
                for key, minted in self._tokens.items()
                if minted.is_valid(now)
            }
        }
        await self._store.async_save(payload)


async def _async_revoke(client: HubClient, ids: Iterable[str]) -> int:
    """Revoke rows, best effort; return how many calls succeeded.

    A row left behind still expires on its own.
    """
    revoked = 0
    for token_id in ids:
        try:
            await client.mutate(TOKEN_REVOKE_MUTATION, {"id": token_id})
        except CamStackError as err:
            _LOGGER.info("Could not revoke CamStack token %s: %s", token_id, err)
        else:
            revoked += 1
    return revoked


def async_get_keeper(hass: HomeAssistant) -> EmbedTokenKeeper:
    """Return this Home Assistant's keeper, creating it on first use."""
    keeper = hass.data.get(_KEEPER)
    if keeper is None:
        keeper = EmbedTokenKeeper(hass)
        hass.data[_KEEPER] = keeper
    return keeper
