"""Camera platform for CamStack (Scrypted import)."""

from __future__ import annotations

import base64
from typing import Any

import aiohttp

from homeassistant.components.camera import Camera
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_CAMERAS_TO_IMPORT,
    CONF_SCRYPTED_PASSWORD,
    CONF_SCRYPTED_URL,
    CONF_SCRYPTED_USERNAME,
    DOMAIN,
)


async def _fetch_snapshot(
    scrypted_url: str,
    username: str,
    password: str,
    device_id: str,
) -> bytes | None:
    """Fetch snapshot from Scrypted via GetCameraSnapshot API."""
    url = f"{scrypted_url.rstrip('/')}/endpoint/@apocaliss92/scrypted-advanced-notifier/public/eventsApp"
    auth = base64.b64encode(f"{username}:{password}".encode()).decode() if username or password else None
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if auth:
        headers["Authorization"] = f"Basic {auth}"
    payload = {"apimethod": "GetCameraSnapshot", "payload": {"deviceId": device_id}}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                b64 = data.get("image")
                if not b64:
                    return None
                return base64.b64decode(b64)
    except Exception:
        return None


class CamStackScryptedCamera(Camera):
    """Camera entity for a Scrypted camera imported via CamStack."""

    _attr_has_entity_name = True

    def __init__(
        self,
        device_id: str,
        name: str,
        scrypted_url: str,
        username: str,
        password: str,
    ) -> None:
        """Initialize the camera."""
        super().__init__()
        self._device_id = device_id
        self._attr_name = name
        self._scrypted_url = scrypted_url
        self._username = username
        self._password = password
        self._attr_unique_id = f"camstack_scrypted_{device_id}"

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return bytes of camera image."""
        return await _fetch_snapshot(
            self._scrypted_url,
            self._username,
            self._password,
            self._device_id,
        )


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up CamStack cameras from a config entry (proxy_scrypted mode)."""
    cameras_to_import: list[str] = config_entry.data.get(CONF_CAMERAS_TO_IMPORT) or []
    if not cameras_to_import:
        async_add_entities([])
        return

    scrypted_url = config_entry.data.get(CONF_SCRYPTED_URL) or ""
    username = config_entry.data.get(CONF_SCRYPTED_USERNAME) or ""
    password = config_entry.data.get(CONF_SCRYPTED_PASSWORD) or ""

    # Fetch camera names from Scrypted (we only have IDs stored)
    names: dict[str, str] = {}
    url = f"{scrypted_url.rstrip('/')}/endpoint/@apocaliss92/scrypted-advanced-notifier/public/eventsApp"
    auth = base64.b64encode(f"{username}:{password}".encode()).decode() if username or password else None
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if auth:
        headers["Authorization"] = f"Basic {auth}"
    payload = {"apimethod": "GetCamerasList", "payload": {}}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    for c in data.get("cameras") or []:
                        if isinstance(c.get("id"), str):
                            names[c["id"]] = c.get("name") or c["id"]
    except Exception:
        pass

    entities = [
        CamStackScryptedCamera(
            device_id=dev_id,
            name=names.get(dev_id, dev_id),
            scrypted_url=scrypted_url,
            username=username,
            password=password,
        )
        for dev_id in cameras_to_import
    ]
    async_add_entities(entities)
