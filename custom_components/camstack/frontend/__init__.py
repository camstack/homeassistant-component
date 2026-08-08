"""The CamStack sidebar panel and Lovelace card.

Both are served by the integration itself, from the files next to this module.
Neither asks for an address: the panel and the card resolve the hub from the
config entry that already holds `host` and `port`. Asking a second time is how
the two surfaces end up pointing at different hubs, and nothing about that
looks broken until a camera is missing from one of them.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from aiohttp import web
from homeassistant.components import frontend, panel_custom
from homeassistant.components.http import KEY_HASS, HomeAssistantView, StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.helpers.start import async_at_started
from homeassistant.loader import async_get_integration

from ..const import (
    CARD_FILENAME,
    CONF_PANEL_ENABLED,
    CONF_PANEL_ICON,
    CONF_PANEL_TITLE,
    CONF_PANEL_URL,
    CONFIG_VIEW_URL,
    DEFAULT_PANEL_ENABLED,
    DEFAULT_PANEL_ICON,
    DEFAULT_PANEL_TITLE,
    DOMAIN,
    PANEL_COMPONENT_NAME,
    PANEL_FILENAME,
    PANEL_URL_PATH,
    STATIC_URL_PATH,
)

_LOGGER = logging.getLogger(__name__)

_ASSET_DIR = Path(__file__).parent
_STATIC_REGISTERED = f"{DOMAIN}_static_registered"
_VIEW_REGISTERED = f"{DOMAIN}_view_registered"
# Read out of `hass.data` rather than imported, so that a Lovelace this
# component does not depend on cannot break its import.
LOVELACE_DATA_KEY = "lovelace"

PANEL_MODULE_URL = f"{STATIC_URL_PATH}/{PANEL_FILENAME}"
CARD_MODULE_URL = f"{STATIC_URL_PATH}/{CARD_FILENAME}"


def async_resolve_base_url(hass: HomeAssistant, entry: ConfigEntry) -> str | None:
    """Return the hub URL the panel and the card should point at.

    Derived from the connection the entry already holds. The `panel_url`
    OPTION overrides it and is never asked for during setup: the migration
    writes it so that upgrading does not silently move an operator's panel, and
    an operator whose browser reaches the hub at a different address than Home
    Assistant does can set it deliberately.
    """
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


async def async_setup_frontend(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Serve the assets, register the panel, and offer the card to Lovelace."""
    await _async_register_static_assets(hass)
    _async_register_config_view(hass)
    await _async_register_card_resource(hass, entry)
    await _async_register_panel(hass, entry)


async def _async_register_static_assets(hass: HomeAssistant) -> None:
    """Expose this directory so the browser can fetch the panel and the card."""
    if hass.data.get(_STATIC_REGISTERED):
        return
    try:
        await hass.http.async_register_static_paths(
            [StaticPathConfig(STATIC_URL_PATH, str(_ASSET_DIR), cache_headers=False)]
        )
    except (RuntimeError, ValueError) as err:
        # Home Assistant refuses a duplicate route, and refuses any route at
        # all once the server is running in some versions. Neither is fatal:
        # an already-served path is the state we wanted.
        _LOGGER.debug("CamStack assets already served at %s: %s", STATIC_URL_PATH, err)
    hass.data[_STATIC_REGISTERED] = True


def _async_register_config_view(hass: HomeAssistant) -> None:
    """Register the endpoint the card reads its hub address from."""
    if hass.data.get(_VIEW_REGISTERED):
        return
    hass.http.register_view(CamStackConfigView)
    hass.data[_VIEW_REGISTERED] = True


async def _async_register_panel(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Register (or replace) the sidebar panel for this entry."""
    options = {**entry.data, **entry.options}
    if not options.get(CONF_PANEL_ENABLED, DEFAULT_PANEL_ENABLED):
        async_remove_panel(hass)
        return

    url = async_resolve_base_url(hass, entry)
    if url is None:
        _LOGGER.warning(
            "CamStack entry %s has no resolvable hub address; the sidebar panel "
            "was not registered",
            entry.entry_id,
        )
        return

    # The sidebar is a single surface. Replacing rather than adding keeps two
    # entries from fighting over it and raising on the second registration.
    async_remove_panel(hass)
    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=PANEL_URL_PATH,
        webcomponent_name=PANEL_COMPONENT_NAME,
        sidebar_title=str(options.get(CONF_PANEL_TITLE) or DEFAULT_PANEL_TITLE),
        sidebar_icon=str(options.get(CONF_PANEL_ICON) or DEFAULT_PANEL_ICON),
        module_url=PANEL_MODULE_URL,
        embed_iframe=True,
        require_admin=False,
        config={"url": url, "entry_id": entry.entry_id},
    )
    _LOGGER.debug("CamStack sidebar panel now points at %s", url)


def async_remove_panel(hass: HomeAssistant, entry_id: str | None = None) -> None:
    """Take the sidebar panel down, if one is up.

    With an `entry_id`, only a panel that entry registered is removed. Two
    entries share one sidebar, and unloading the second must not blank the
    panel the first is still serving.
    """
    if entry_id is not None:
        registered = hass.data.get(frontend.DATA_PANELS, {}).get(PANEL_URL_PATH)
        config = getattr(registered, "config", None) or {}
        if config.get("entry_id") != entry_id:
            return
    frontend.async_remove_panel(hass, PANEL_URL_PATH, warn_if_unknown=False)


async def _async_register_card_resource(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Add the grid card to Lovelace's resources when Lovelace stores them.

    Deferred to "Home Assistant has started" rather than retried on a timer.
    A timer that polls for a component that may never be installed is a loop
    with no end, and it outlives the entry that started it.

    In YAML mode the resource list is the operator's file and this component
    does not own it, so it is left alone and documented instead.
    """
    integration = await async_get_integration(hass, DOMAIN)
    version = str(integration.version or "0")
    url = f"{CARD_MODULE_URL}?v={version}"

    async def _add(_: Any = None) -> None:
        lovelace = hass.data.get(LOVELACE_DATA_KEY)
        resources = getattr(lovelace, "resources", None)
        if resources is None:
            _LOGGER.debug("Lovelace is not loaded; the grid card was not registered")
            return
        mode = getattr(lovelace, "resource_mode", getattr(lovelace, "mode", "yaml"))
        if mode != "storage":
            _LOGGER.debug("Lovelace is in YAML mode; not touching its resources")
            return
        # Loads the collection from storage as a side effect. Reading
        # `async_items()` first would see an empty list and create a duplicate.
        await resources.async_get_info()
        for item in resources.async_items():
            if CARD_FILENAME not in str(item.get("url") or ""):
                continue
            if item.get("url") != url:
                # A stale version query pins every browser to the card that
                # shipped with the previous release.
                await resources.async_update_item(item["id"], {"url": url})
            return
        await resources.async_create_item({"res_type": "module", "url": url})
        _LOGGER.debug("Registered the CamStack grid card as a Lovelace resource")

    entry.async_on_unload(async_at_started(hass, _add))


class CamStackConfigView(HomeAssistantView):
    """Tells the Lovelace card which hub the integration is configured against.

    Without this the card would have to ask for the address again, and a card
    pointed at a different hub than the panel is a bug no error message would
    ever report.
    """

    url = CONFIG_VIEW_URL
    name = "api:camstack:config"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        """Return one record per configured hub."""
        hass: HomeAssistant = request.app[KEY_HASS]
        entries = []
        for entry in hass.config_entries.async_entries(DOMAIN):
            url = async_resolve_base_url(hass, entry)
            if url is None:
                continue
            entries.append(
                {"entry_id": entry.entry_id, "title": entry.title, "url_base": url}
            )
        return self.json({"entries": entries})
