"""Config flow for CamStack."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import (
    CamStackAuthError,
    CamStackClient,
    CamStackConnectionError,
    CamStackError,
)
from .const import (
    CONF_PANEL_ENABLED,
    CONF_PANEL_ICON,
    CONF_PANEL_TITLE,
    CONF_PANEL_URL,
    CONF_VERIFY_SSL,
    CONFIG_ENTRY_VERSION,
    DEFAULT_PANEL_ENABLED,
    DEFAULT_PANEL_ICON,
    DEFAULT_PANEL_TITLE,
    DEFAULT_PORT,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
)
from .migration import entry_has_credentials

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Required(CONF_VERIFY_SSL, default=DEFAULT_VERIFY_SSL): bool,
    }
)


class CamStackConfigFlow(ConfigFlow, domain=DOMAIN):
    """Ask for a hub and prove it answers before creating the entry.

    The address is asked for **once**. The sidebar panel and the Lovelace card
    derive their URL from it rather than carrying a second copy that can drift.
    """

    VERSION = CONFIG_ENTRY_VERSION

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect connection details and validate them against the hub."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST]
            port = user_input[CONF_PORT]
            await self.async_set_unique_id(f"{host}:{port}")
            self._abort_if_unique_id_configured()

            try:
                identity = await _async_validate(self.hass, user_input)
            except CamStackAuthError:
                errors["base"] = "invalid_auth"
            except CamStackConnectionError:
                errors["base"] = "cannot_connect"
            except CamStackError:
                _LOGGER.exception("Unexpected error validating the CamStack hub")
                errors["base"] = "unknown"
            else:
                username = identity.get("username") or user_input[CONF_USERNAME]
                return self.async_create_entry(
                    title=f"CamStack ({host})",
                    data=user_input,
                    description_placeholders={"username": str(username)},
                )

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_SCHEMA, user_input
            ),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Start a re-authentication when the hub stops accepting the password."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for credentials and confirm the hub accepts them.

        An entry migrated from the panel-only component has never had any, and
        its host and port were *derived* from a URL that may well have been a
        reverse proxy. So when there are no credentials yet the connection
        fields are asked for too — otherwise the only way to correct a derived
        address would be to delete the entry and lose the panel with it.
        """
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        needs_connection = not entry_has_credentials(entry.data)

        if user_input is not None:
            candidate = {**entry.data, **user_input}
            try:
                await _async_validate(self.hass, candidate)
            except CamStackAuthError:
                errors["base"] = "invalid_auth"
            except CamStackConnectionError:
                errors["base"] = "cannot_connect"
            except CamStackError:
                _LOGGER.exception("Unexpected error re-validating the CamStack hub")
                errors["base"] = "unknown"
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    data=candidate,
                    unique_id=f"{candidate[CONF_HOST]}:{candidate[CONF_PORT]}",
                )

        fields: dict[Any, Any] = {}
        if needs_connection:
            fields[vol.Required(CONF_HOST, default=entry.data.get(CONF_HOST, ""))] = str
            fields[
                vol.Required(CONF_PORT, default=entry.data.get(CONF_PORT, DEFAULT_PORT))
            ] = int
        fields[
            vol.Required(CONF_USERNAME, default=entry.data.get(CONF_USERNAME, ""))
        ] = str
        fields[vol.Required(CONF_PASSWORD)] = str
        if needs_connection:
            fields[
                vol.Required(
                    CONF_VERIFY_SSL,
                    default=entry.data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
                )
            ] = bool

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(fields),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> CamStackOptionsFlow:
        """Return the options flow, which covers the panel and the card."""
        return CamStackOptionsFlow()


class CamStackOptionsFlow(OptionsFlow):
    """Settings for the sidebar panel. The hub address is not among them."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit the panel settings."""
        if user_input is not None:
            panel_url = str(user_input.get(CONF_PANEL_URL) or "").strip()
            return self.async_create_entry(
                data={**user_input, CONF_PANEL_URL: panel_url}
            )

        options = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_PANEL_ENABLED,
                        default=options.get(CONF_PANEL_ENABLED, DEFAULT_PANEL_ENABLED),
                    ): bool,
                    vol.Optional(
                        CONF_PANEL_TITLE,
                        default=options.get(CONF_PANEL_TITLE, DEFAULT_PANEL_TITLE),
                    ): str,
                    vol.Optional(
                        CONF_PANEL_ICON,
                        default=options.get(CONF_PANEL_ICON, DEFAULT_PANEL_ICON),
                    ): str,
                    vol.Optional(
                        CONF_PANEL_URL,
                        default=options.get(CONF_PANEL_URL, ""),
                    ): str,
                }
            ),
        )


async def _async_validate(
    hass: HomeAssistant, config: dict[str, Any]
) -> dict[str, Any]:
    """Log in to the hub and return the identity it reports back."""
    verify_ssl = config.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)
    session = async_create_clientsession(hass, verify_ssl=verify_ssl)
    client = CamStackClient(
        session,
        config[CONF_HOST],
        config[CONF_PORT],
        config[CONF_USERNAME],
        config[CONF_PASSWORD],
        verify_ssl=verify_ssl,
    )
    return await client.async_verify()
