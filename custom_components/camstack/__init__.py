"""The CamStack integration.

One component, two surfaces, one config entry:

* the **sidebar panel and the Lovelace card**, which need only the hub's
  address and are set up first;
* the **entities**, which need credentials.

They are deliberately not all-or-nothing. An entry created by the panel-only
component this one replaces has an address but no credentials, and it must keep
serving the panel it was serving while Home Assistant asks for the rest.
"""

from __future__ import annotations

from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_USERNAME,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import CamStackClient
from .const import CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL
from .coordinator import CamStackConfigEntry, CamStackCoordinator
from .frontend import async_remove_panel, async_setup_frontend
from .migration import async_migrate_entry, entry_has_credentials

__all__ = ["async_migrate_entry"]

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.CAMERA,
    Platform.SENSOR,
    Platform.SWITCH,
]


async def async_setup_entry(hass: HomeAssistant, entry: CamStackConfigEntry) -> bool:
    """Set up CamStack from a config entry."""
    # Before the credential check, and deliberately so: a migrated entry has an
    # address but no password, and an operator whose sidebar disappeared during
    # an upgrade has no way to tell an upgrade from a breakage.
    await async_setup_frontend(hass, entry)
    entry.async_on_unload(entry.add_update_listener(_async_entry_updated))

    if not entry_has_credentials(entry.data):
        raise ConfigEntryAuthFailed("credentials_required")

    verify_ssl = entry.data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)
    session = async_create_clientsession(hass, verify_ssl=verify_ssl)
    client = CamStackClient(
        session,
        entry.data[CONF_HOST],
        entry.data[CONF_PORT],
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        verify_ssl=verify_ssl,
    )

    coordinator = CamStackCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # Started only after the platforms exist, so the first event has entities
    # to wake rather than landing on an empty registry.
    coordinator.start_event_stream()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: CamStackConfigEntry) -> bool:
    """Unload a config entry."""
    async_remove_panel(hass, entry.entry_id)
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: CamStackConfigEntry) -> None:
    """Take the panel down when the entry is deleted.

    Home Assistant only calls `async_unload_entry` for an entry that actually
    loaded. An entry that never got past the credential check still put a panel
    in the sidebar, and deleting it must remove that panel too.
    """
    async_remove_panel(hass, entry.entry_id)


async def _async_entry_updated(hass: HomeAssistant, entry: CamStackConfigEntry) -> None:
    """Reload after an options change so the panel is rebuilt from them."""
    await hass.config_entries.async_reload(entry.entry_id)
