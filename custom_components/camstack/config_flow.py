"""Config flow for the CamStack integration."""

from __future__ import annotations

import base64
import json
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult

from .const import (
    CONF_CAMERAS_TO_IMPORT,
    CONF_MODE,
    CONF_PANEL_ICON,
    CONF_PANEL_TITLE,
    CONF_SCRYPTED_PASSWORD,
    CONF_SCRYPTED_URL,
    CONF_SCRYPTED_USERNAME,
    CONF_URL_BASE,
    DEFAULT_PANEL_ICON,
    DEFAULT_PANEL_TITLE,
    DOMAIN,
    MODE_EMBEDDED,
    MODE_PROXY_EXTERNAL,
    MODE_PROXY_SCRYPTED,
)


async def _validate_scrypted_url(hass: HomeAssistant, url: str) -> str | None:
    """Validate Scrypted URL is reachable."""
    if not url or not url.strip():
        return "url_required"
    url = url.strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        return "invalid_url"
    # Basic validation - could add actual HTTP check
    return None


async def _fetch_scrypted_cameras(
    hass: HomeAssistant,
    scrypted_url: str,
    username: str,
    password: str,
) -> list[dict[str, str]]:
    """Fetch list of cameras from Scrypted via Advanced Notifier EventsApp API."""
    url = f"{scrypted_url.rstrip('/')}/endpoint/@apocaliss92/scrypted-advanced-notifier/public/eventsApp"
    auth = base64.b64encode(f"{username}:{password}".encode()).decode() if username or password else None
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if auth:
        headers["Authorization"] = f"Basic {auth}"
    payload = {"apimethod": "GetCamerasList", "payload": {}}
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                raise ConnectionError(f"Scrypted API returned {resp.status}")
            data = await resp.json()
    cameras = data.get("cameras") or []
    return [c for c in cameras if isinstance(c.get("id"), str) and isinstance(c.get("name"), str)]


class CamStackConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for CamStack."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize config flow."""
        self._mode: str | None = None
        self._url_base: str | None = None
        self._scrypted_url: str | None = None
        self._scrypted_username: str | None = None
        self._scrypted_password: str | None = None
        self._cameras_to_import: list[str] = []

    async def async_step_user(self, user_input: dict | None = None) -> FlowResult:
        """Handle the initial step."""
        if user_input is not None:
            self._mode = user_input[CONF_MODE]
            if self._mode == MODE_EMBEDDED:
                return await self.async_step_embedded()
            if self._mode == MODE_PROXY_EXTERNAL:
                return await self.async_step_proxy_external()
            return await self.async_step_proxy_scrypted()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_MODE): vol.In(
                        {
                            MODE_EMBEDDED: "Embedded (CamStack served by addon)",
                            MODE_PROXY_EXTERNAL: "Proxy (external CamStack URL)",
                            MODE_PROXY_SCRYPTED: "Proxy Scrypted (CamStack from Scrypted instance)",
                        }
                    ),
                }
            ),
        )

    async def async_step_embedded(self, user_input: dict | None = None) -> FlowResult:
        """Configure embedded mode (addon ingress)."""
        if user_input is not None:
            self._url_base = user_input.get(CONF_URL_BASE, "").strip() or None
            return await self.async_step_panel_options()

        return self.async_show_form(
            step_id="embedded",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_URL_BASE,
                        default="/api/hassio_ingress/camstack",
                    ): str,
                }
            ),
        )

    async def async_step_proxy_external(self, user_input: dict | None = None) -> FlowResult:
        """Configure proxy external mode."""
        errors = {}
        if user_input is not None:
            url = user_input.get(CONF_URL_BASE, "").strip()
            if not url:
                errors[CONF_URL_BASE] = "url_required"
            elif not url.startswith(("http://", "https://")):
                errors[CONF_URL_BASE] = "invalid_url"
            else:
                self._url_base = url.rstrip("/")
                return await self.async_step_panel_options()

        return self.async_show_form(
            step_id="proxy_external",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_URL_BASE,
                        description="Full URL to CamStack (e.g. https://camstack.example.com)",
                    ): str,
                }
            ),
            errors=errors,
        )

    async def async_step_proxy_scrypted(self, user_input: dict | None = None) -> FlowResult:
        """Configure proxy Scrypted mode."""
        errors = {}
        if user_input is not None:
            url = user_input.get(CONF_SCRYPTED_URL, "").strip()
            err = await _validate_scrypted_url(self.hass, url)
            if err:
                errors[CONF_SCRYPTED_URL] = err
            else:
                self._scrypted_url = url.rstrip("/")
                self._scrypted_username = user_input.get(CONF_SCRYPTED_USERNAME) or ""
                self._scrypted_password = user_input.get(CONF_SCRYPTED_PASSWORD) or ""
                self._url_base = f"{self._scrypted_url}/endpoint/@apocaliss92/scrypted-advanced-notifier/public/camstack"
                return await self.async_step_import_cameras()

        return self.async_show_form(
            step_id="proxy_scrypted",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SCRYPTED_URL,
                        description="Scrypted instance URL (e.g. https://scrypted.local)",
                    ): str,
                    vol.Optional(CONF_SCRYPTED_USERNAME): str,
                    vol.Optional(CONF_SCRYPTED_PASSWORD): str,
                }
            ),
            errors=errors,
        )

    async def async_step_import_cameras(self, user_input: dict | None = None) -> FlowResult:
        """Step for selecting cameras to import from Scrypted."""
        if user_input is not None:
            self._cameras_to_import = user_input.get(CONF_CAMERAS_TO_IMPORT) or []
            return await self.async_step_panel_options()

        errors: dict[str, str] = {}
        try:
            cameras = await _fetch_scrypted_cameras(
                self.hass,
                self._scrypted_url or "",
                self._scrypted_username or "",
                self._scrypted_password or "",
            )
        except Exception as e:
            errors["base"] = "cannot_connect"
            return self.async_show_form(
                step_id="proxy_scrypted",
                data_schema=vol.Schema(
                    {
                        vol.Required(CONF_SCRYPTED_URL, default=self._scrypted_url): str,
                        vol.Optional(CONF_SCRYPTED_USERNAME, default=self._scrypted_username): str,
                        vol.Optional(CONF_SCRYPTED_PASSWORD, default=self._scrypted_password): str,
                    }
                ),
                errors=errors,
            )

        options = {c["id"]: f"{c['name']} ({c['id']})" for c in cameras}
        if not options:
            return await self.async_step_panel_options()

        return self.async_show_form(
            step_id="import_cameras",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_CAMERAS_TO_IMPORT,
                        default=[],
                    ): vol.MultiSelect(options),
                }
            ),
            errors=errors,
        )

    async def async_step_panel_options(self, user_input: dict | None = None) -> FlowResult:
        """Configure panel title and icon."""
        if user_input is not None:
            entry_data: dict[str, Any] = {
                CONF_MODE: self._mode,
                CONF_URL_BASE: self._url_base or "",
                CONF_SCRYPTED_URL: self._scrypted_url or "",
                CONF_SCRYPTED_USERNAME: self._scrypted_username or "",
                CONF_SCRYPTED_PASSWORD: self._scrypted_password or "",
            }
            if self._mode == MODE_PROXY_SCRYPTED:
                entry_data[CONF_CAMERAS_TO_IMPORT] = self._cameras_to_import
            return self.async_create_entry(
                title="CamStack",
                data=entry_data,
                options={
                    CONF_PANEL_TITLE: user_input.get(CONF_PANEL_TITLE, DEFAULT_PANEL_TITLE),
                    CONF_PANEL_ICON: user_input.get(CONF_PANEL_ICON, DEFAULT_PANEL_ICON),
                },
            )

        return self.async_show_form(
            step_id="panel_options",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_PANEL_TITLE, default=DEFAULT_PANEL_TITLE): str,
                    vol.Optional(CONF_PANEL_ICON, default=DEFAULT_PANEL_ICON): str,
                }
            ),
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> CamStackOptionsFlow:
        """Get the options flow for this handler."""
        return CamStackOptionsFlow(config_entry)


class CamStackOptionsFlow(config_entries.OptionsFlow):
    """Handle CamStack options."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(self, user_input: dict | None = None) -> FlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        opts = self.config_entry.options or {}
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_PANEL_TITLE,
                        default=opts.get(CONF_PANEL_TITLE, DEFAULT_PANEL_TITLE),
                    ): str,
                    vol.Optional(
                        CONF_PANEL_ICON,
                        default=opts.get(CONF_PANEL_ICON, DEFAULT_PANEL_ICON),
                    ): str,
                }
            ),
        )
