"""CamStack integration for Home Assistant."""

import os

from homeassistant.components import frontend, panel_custom
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, Platform
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN, PANEL_FILENAME, PANEL_NAME, PANEL_URL
from .frontend import async_register_card

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS: list[Platform] = [Platform.CAMERA]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the CamStack component."""
    async def _register_card(_=None) -> None:
        await async_register_card(hass)

    if hass.state == CoreState.running:
        await _register_card()
    else:
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _register_card)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up CamStack from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    config = {**entry.data, **entry.options}
    hass.data[DOMAIN][entry.entry_id] = {"config": config, "options": entry.options}

    await _register_frontend(hass, entry)

    if entry.data.get("mode") == "proxy_scrypted":
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if entry.data.get("mode") == "proxy_scrypted":
        await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    frontend.async_remove_panel(hass, "camstack")

    if DOMAIN in hass.data and entry.entry_id in hass.data[DOMAIN]:
        del hass.data[DOMAIN][entry.entry_id]

    return True


def get_camstack_url(hass: HomeAssistant, entry_id: str) -> str | None:
    """Get the CamStack URL for the given config entry."""
    if DOMAIN not in hass.data or entry_id not in hass.data[DOMAIN]:
        return None
    data = hass.data[DOMAIN][entry_id]
    config = data.get("config", {})
    mode = config.get("mode")

    if mode == "embedded":
        return config.get("url_base") or f"/api/hassio_ingress/{config.get('addon_slug', 'camstack')}"
    if mode in ("proxy_external", "proxy_scrypted"):
        url_base = config.get("url_base", "").rstrip("/")
        if mode == "proxy_scrypted":
            if not url_base.endswith("/public/camstack"):
                url_base = f"{url_base.rstrip('/')}/endpoint/@apocaliss92/scrypted-advanced-notifier/public/camstack"
        return url_base or None
    return None


def _build_iframe_url(hass: HomeAssistant, config: dict) -> str | None:
    """Build the iframe URL from config."""
    mode = config.get("mode")
    url_base = config.get("url_base", "").rstrip("/")

    if mode == "embedded":
        iframe_url = url_base or f"/api/hassio_ingress/{config.get('addon_slug', 'camstack')}"
    elif mode == "proxy_scrypted":
        if not url_base.endswith("/public/camstack"):
            iframe_url = f"{url_base}/endpoint/@apocaliss92/scrypted-advanced-notifier/public/camstack"
        else:
            iframe_url = url_base
    else:
        iframe_url = url_base

    if not iframe_url:
        return None

    if iframe_url.startswith("/"):
        base = hass.config.internal_url or "http://homeassistant.local:8123"
        iframe_url = f"{base.rstrip('/')}{iframe_url}"
    return iframe_url


async def _register_frontend(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Register the CamStack panel."""
    config = {**entry.data, **entry.options}
    iframe_url = _build_iframe_url(hass, config)
    if not iframe_url:
        return

    root = os.path.join(hass.config.path("custom_components"), DOMAIN)
    panel_path = os.path.join(root, "frontend", PANEL_FILENAME)

    if os.path.isfile(panel_path):
        await hass.http.async_register_static_paths(
            [StaticPathConfig(PANEL_URL, panel_path, cache_headers=False)]
        )

    await panel_custom.async_register_panel(
        hass,
        "camstack",
        PANEL_NAME,
        sidebar_title=config.get("panel_title", "CamStack"),
        sidebar_icon=config.get("panel_icon", "mdi:cctv"),
        module_url=PANEL_URL,
        embed_iframe=True,
        config={"url": iframe_url},
    )
