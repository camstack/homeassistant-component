"""Config entry migration.

Two components used to claim the `camstack` domain, and both wrote a version 1
entry:

* the panel-only one stored a single `url_base` and no credentials;
* the entity one stored `host`, `port`, `username`, `password`, `verify_ssl`.

Version 2 is the merged shape. A migrated panel-only entry keeps serving the
exact address it was serving — the URL is carried forward verbatim as
`panel_url` rather than rebuilt — and gains a host/port guess that the operator
confirms the first time the entry is reused. Nothing about an operator's
sidebar changes on upgrade; the entities simply do not exist until credentials
are supplied.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .const import (
    CONF_PANEL_URL,
    CONF_TOKEN_BUNDLE,
    CONF_VERIFY_SSL,
    CONFIG_ENTRY_VERSION,
    DEFAULT_PORT,
    DEFAULT_VERIFY_SSL,
    LEGACY_CONF_URL_BASE,
)

_LOGGER = logging.getLogger(__name__)

_SCHEME_PORTS: dict[str, int] = {"http": 80, "https": 443}


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Bring a config entry up to the merged shape."""
    if entry.version > CONFIG_ENTRY_VERSION:
        # Written by a newer component than the one now installed. Refusing is
        # the only safe answer: guessing would rewrite data this code has never
        # seen the shape of.
        return False
    if entry.version == CONFIG_ENTRY_VERSION:
        return True

    data = dict(entry.data)
    options = dict(entry.options)
    unique_id = entry.unique_id

    if CONF_HOST in data:
        # An entry from the entity-only component. The shape is already right.
        data.setdefault(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)
    else:
        legacy = str(data.pop(LEGACY_CONF_URL_BASE, "") or "").strip().rstrip("/")
        host, port = _split_legacy_url(legacy)
        data = {
            CONF_HOST: host,
            CONF_PORT: port,
            CONF_VERIFY_SSL: DEFAULT_VERIFY_SSL,
        }
        # Carried verbatim, so the panel keeps loading exactly what it loaded
        # before even when the address is a reverse proxy that host and port
        # could never reconstruct. It stays editable in the options.
        options[CONF_PANEL_URL] = legacy
        if host:
            unique_id = f"{host}:{port}"
        _LOGGER.info(
            "Migrated the CamStack panel entry: the sidebar keeps pointing at %s, "
            "and %s:%s was derived for the API. Credentials are needed before any "
            "entity appears",
            legacy or "(nothing)",
            host or "(unknown)",
            port,
        )

    hass.config_entries.async_update_entry(
        entry,
        data=data,
        options=options,
        unique_id=unique_id,
        version=CONFIG_ENTRY_VERSION,
    )
    return True


def _split_legacy_url(url: str) -> tuple[str, int]:
    """Derive a host and a port from the panel-only component's URL.

    A path-only value (the old component accepted one and resolved it against
    Home Assistant's own address) carries no hub address at all, so it yields
    an empty host and the operator is asked.
    """
    if not url or url.startswith("/"):
        return "", DEFAULT_PORT
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.hostname:
        return "", DEFAULT_PORT
    port = parsed.port or _SCHEME_PORTS.get(parsed.scheme, DEFAULT_PORT)
    return parsed.hostname, port


def entry_is_oauth(data: Mapping[str, Any]) -> bool:
    """Return whether this entry authenticates with an OAuth token bundle."""
    return isinstance(data.get(CONF_TOKEN_BUNDLE), Mapping)


def entry_has_credentials(data: Mapping[str, Any]) -> bool:
    """Return whether an entry can authenticate against a hub at all.

    Two shapes qualify, and a migrated panel-only entry has neither — which is
    exactly why this is a question and not an assumption.
    """
    if entry_is_oauth(data):
        return True
    return bool(data.get(CONF_USERNAME)) and bool(data.get(CONF_PASSWORD))
