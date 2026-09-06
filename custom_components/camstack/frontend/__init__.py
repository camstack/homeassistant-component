"""The CamStack sidebar panel and the Lovelace cards.

All of them are served by the integration itself, from the files next to this
module. None asks for an address: the panel and the cards resolve the hub from
the config entry that already holds `host` and `port`. Asking a second time is
how the surfaces end up pointing at different hubs, and nothing about that
looks broken until a camera is missing from one of them.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from aiohttp import ClientError, ClientTimeout, web
from homeassistant.components import frontend, panel_custom
from homeassistant.components.http import KEY_HASS, HomeAssistantView, StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.start import async_at_started
from homeassistant.loader import async_get_integration

from ..const import (
    CARD_FILENAMES,
    CONF_PANEL_ENABLED,
    CONF_PANEL_ICON,
    CONF_PANEL_TITLE,
    CONF_VERIFY_SSL,
    CONFIG_VIEW_URL,
    DEFAULT_PANEL_ENABLED,
    DEFAULT_PANEL_ICON,
    DEFAULT_PANEL_TITLE,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
    MOUNT_MARKER,
    MOUNT_PROBE_PREFIX,
    MOUNT_PROBE_READ_BYTES,
    MOUNT_PROBE_TIMEOUT,
    PANEL_COMPONENT_NAME,
    PANEL_FILENAME,
    PANEL_URL_PATH,
    STATIC_URL_PATH,
)
from ..embed_token import async_register_embed_token_view
from ..hub_url import async_resolve_base_url
from ..proxy import (
    async_issue_panel_grant,
    async_register_proxy_view,
    proxy_base_for,
)

_LOGGER = logging.getLogger(__name__)

_ASSET_DIR = Path(__file__).parent
_STATIC_REGISTERED = f"{DOMAIN}_static_registered"
_VIEW_REGISTERED = f"{DOMAIN}_view_registered"
# Read out of `hass.data` rather than imported, so that a Lovelace this
# component does not depend on cannot break its import.
LOVELACE_DATA_KEY = "lovelace"

PANEL_MODULE_URL = f"{STATIC_URL_PATH}/{PANEL_FILENAME}"
CARD_MODULE_URLS = tuple(f"{STATIC_URL_PATH}/{name}" for name in CARD_FILENAMES)
# The grid card, kept named because it is the one this component shipped first
# and every existing Lovelace resource points at it.
CARD_MODULE_URL = CARD_MODULE_URLS[0]


async def async_setup_frontend(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Serve the assets, register the panel, and offer the cards to Lovelace."""
    await _async_register_static_assets(hass)
    _async_register_config_view(hass)
    async_register_embed_token_view(hass)
    async_register_proxy_view(hass)
    await _async_register_card_resources(hass, entry)
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
        config={
            "url": url,
            "entry_id": entry.entry_id,
            **(await _async_panel_relay_config(hass, entry, url)),
        },
    )
    _LOGGER.debug("CamStack sidebar panel now points at %s", url)


async def _async_panel_relay_config(
    hass: HomeAssistant, entry: ConfigEntry, url: str
) -> dict[str, str]:
    """Return `{"proxy_base": …}` when the hub can be framed through the relay.

    Only a hub that answers its index under a forwarded prefix with the
    `camstack-mount` marker is relayed: an older hub's admin UI references
    `/assets/…` at the root and would be a page of 404s under the prefix, so
    it keeps being framed directly.
    """
    if not await async_probe_mount_support(hass, entry, url):
        return {}
    return {"proxy_base": proxy_base_for(async_issue_panel_grant(hass, entry.entry_id))}


async def async_probe_mount_support(
    hass: HomeAssistant, entry: ConfigEntry, url: str
) -> bool:
    """Ask the hub whether its admin UI honours `X-Forwarded-Prefix`."""
    verify_ssl = entry.data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)
    session = async_get_clientsession(hass, verify_ssl=verify_ssl)
    try:
        async with session.get(
            f"{url.rstrip('/')}/",
            headers={"X-Forwarded-Prefix": MOUNT_PROBE_PREFIX},
            timeout=ClientTimeout(total=MOUNT_PROBE_TIMEOUT.total_seconds()),
            allow_redirects=False,
        ) as response:
            if response.status != 200:
                return False
            head = await response.content.read(MOUNT_PROBE_READ_BYTES)
    except (ClientError, TimeoutError, OSError) as err:
        _LOGGER.debug("hub %s did not answer the mount probe: %s", url, err)
        return False
    return MOUNT_MARKER.encode() in head


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


async def _async_register_card_resources(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Add every card to Lovelace's resources when Lovelace stores them.

    Deferred to "Home Assistant has started" rather than retried on a timer.
    A timer that polls for a component that may never be installed is a loop
    with no end, and it outlives the entry that started it.

    In YAML mode the resource list is the operator's file and this component
    does not own it, so it is left alone and documented instead.

    Driven by `CARD_FILENAMES`, so shipping a card and registering it cannot
    drift: a card added to the directory and forgotten here would simply never
    appear in the dashboard picker, with nothing logged.
    """
    integration = await async_get_integration(hass, DOMAIN)
    version = str(integration.version or "0")
    wanted = {name: f"{STATIC_URL_PATH}/{name}?v={version}" for name in CARD_FILENAMES}

    async def _add(_: Any = None) -> None:
        lovelace = hass.data.get(LOVELACE_DATA_KEY)
        resources = getattr(lovelace, "resources", None)
        if resources is None:
            _LOGGER.info("Lovelace is not loaded; the cards were not registered")
            return
        mode = getattr(lovelace, "resource_mode", getattr(lovelace, "mode", "yaml"))
        if mode != "storage":
            _LOGGER.info("Lovelace is in YAML mode; not touching its resources")
            return
        # Loads the collection from storage as a side effect. Reading
        # `async_items()` first would see an empty list and create a duplicate.
        await resources.async_get_info()
        existing = list(resources.async_items())
        for filename, url in wanted.items():
            await _async_reconcile_resource(resources, existing, filename, url)

    entry.async_on_unload(async_at_started(hass, _add))


async def _async_reconcile_resource(
    resources: Any, existing: list[dict[str, Any]], filename: str, url: str
) -> None:
    """Create or re-point the single resource entry for one card file."""
    for item in existing:
        if filename not in str(item.get("url") or ""):
            continue
        if item.get("url") != url:
            # A stale version query pins every browser to the card that
            # shipped with the previous release.
            await resources.async_update_item(item["id"], {"url": url})
            _LOGGER.info("Re-pointed the %s resource to %s", filename, url)
        return
    await resources.async_create_item({"res_type": "module", "url": url})
    _LOGGER.info("Registered %s as a Lovelace resource at %s", filename, url)


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
        """Return one record per configured hub, with the cameras it exports.

        The camera list is here rather than derived in the browser because a
        card needs the HUB's numeric device ids, and a dashboard that has no
        camera entity for a device — a camera the operator never added — would
        otherwise have no way to name it.
        """
        hass: HomeAssistant = request.app[KEY_HASS]
        entries = []
        for entry in hass.config_entries.async_entries(DOMAIN):
            url = async_resolve_base_url(hass, entry)
            if url is None:
                continue
            entries.append(
                {
                    "entry_id": entry.entry_id,
                    "title": entry.title,
                    "url_base": url,
                    "cameras": _exported_cameras(entry),
                }
            )
        return self.json({"entries": entries})


def _exported_cameras(entry: ConfigEntry) -> list[dict[str, Any]]:
    """Return `{id, name}` per exported camera, or an empty list.

    Empty when the entry is not loaded yet. That is honest — unknown, not
    "this hub has no cameras" — and the cards say "waiting" rather than
    "none" while it is.
    """
    coordinator = getattr(getattr(entry, "runtime_data", None), "coordinator", None)
    data = getattr(coordinator, "data", None)
    if data is None:
        return []
    return [
        {"id": device.device_id, "name": device.name}
        for device in data.cameras()
        if not device.disabled
    ]
