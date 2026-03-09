"""Frontend registration for CamStack."""

import json
import logging
from pathlib import Path

from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_call_later

_LOGGER = logging.getLogger(__name__)

MANIFEST_PATH = Path(__file__).parent.parent / "manifest.json"
with open(MANIFEST_PATH, encoding="utf-8") as f:
    INTEGRATION_VERSION = json.load(f).get("version", "0.1.0")

URL_BASE = "/camstack"
CARD_FILENAME = "camstack-grid-card.js"


async def async_register_card(hass: HomeAssistant) -> None:
    """Register the CamStack grid card with Lovelace."""
    root = Path(__file__).parent
    card_path = root / CARD_FILENAME

    if not card_path.is_file():
        _LOGGER.warning("CamStack card file not found: %s", card_path)
        return

    try:
        await hass.http.async_register_static_paths(
            [StaticPathConfig(f"{URL_BASE}/{CARD_FILENAME}", str(card_path), cache_headers=False)]
        )
    except RuntimeError:
        _LOGGER.debug("CamStack card path already registered")

    lovelace = hass.data.get("lovelace")
    if lovelace is None:
        return

    mode = getattr(lovelace, "mode", getattr(lovelace, "resource_mode", "yaml"))
    if mode != "storage":
        _LOGGER.debug("Lovelace not in storage mode, skipping resource registration")
        return

    async def _add_resource(_=None) -> None:
        if not hasattr(lovelace, "resources") or lovelace.resources is None:
            async_call_later(hass, 5, _add_resource)
            return
        if not getattr(lovelace.resources, "loaded", True):
            async_call_later(hass, 5, _add_resource)
            return
        try:
            items = lovelace.resources.async_items()
            url = f"{URL_BASE}/{CARD_FILENAME}?v={INTEGRATION_VERSION}"
            existing = [r for r in items if CARD_FILENAME in (r.get("url") or "")]
            if not existing:
                await lovelace.resources.async_create_item(
                    {"res_type": "module", "url": url}
                )
                _LOGGER.info("Registered CamStack grid card")
        except Exception as e:
            _LOGGER.warning("Could not register CamStack card: %s", e)

    await _add_resource()
