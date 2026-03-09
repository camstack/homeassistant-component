"""Config flow for the CamStack integration."""

from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .const import (
    CONF_PANEL_ICON,
    CONF_PANEL_TITLE,
    CONF_URL_BASE,
    DEFAULT_PANEL_ICON,
    DEFAULT_PANEL_TITLE,
    DOMAIN,
)


class CamStackConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for CamStack."""

    VERSION = 1

    async def async_step_user(self, user_input: dict | None = None) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            url = user_input.get(CONF_URL_BASE, "").strip()
            if not url:
                errors[CONF_URL_BASE] = "url_required"
            elif not url.startswith(("http://", "https://")):
                errors[CONF_URL_BASE] = "invalid_url"
            else:
                return self.async_create_entry(
                    title="CamStack",
                    data={CONF_URL_BASE: url.rstrip("/")},
                    options={
                        CONF_PANEL_TITLE: user_input.get(CONF_PANEL_TITLE, DEFAULT_PANEL_TITLE),
                        CONF_PANEL_ICON: user_input.get(CONF_PANEL_ICON, DEFAULT_PANEL_ICON),
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_URL_BASE,
                        description="Full URL to CamStack (e.g. https://camstack.example.com)",
                    ): str,
                    vol.Optional(CONF_PANEL_TITLE, default=DEFAULT_PANEL_TITLE): str,
                    vol.Optional(CONF_PANEL_ICON, default=DEFAULT_PANEL_ICON): str,
                }
            ),
            errors=errors,
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
