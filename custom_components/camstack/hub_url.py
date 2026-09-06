"""The hub address an entry points at.

ONE derivation for the panel, the cards and the proxy.

Derived from the connection the entry already holds. The `panel_url` OPTION
overrides it and is never asked for during setup: the migration writes it so
that upgrading does not silently move an operator's panel, and an operator
whose browser reaches the hub at a different address than Home Assistant does
can set it deliberately.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant, callback

from .const import CONF_PANEL_URL


@callback
def async_resolve_base_url(hass: HomeAssistant, entry: ConfigEntry) -> str | None:
    """Return the hub URL the panel, the cards and the proxy should point at."""
    legacy = str(entry.options.get(CONF_PANEL_URL) or "").strip().rstrip("/")
    if legacy:
        if legacy.startswith("/"):
            # A path-only URL was resolved against Home Assistant's own address
            # by the component this one replaces. Keep doing that.
            base = hass.config.internal_url or hass.config.external_url
            return f"{base.rstrip('/')}{legacy}" if base else None
        return legacy

    host = str(entry.data.get(CONF_HOST) or "").strip()
    port = entry.data.get(CONF_PORT)
    if not host or not isinstance(port, int):
        return None
    return f"https://{host}:{port}"
