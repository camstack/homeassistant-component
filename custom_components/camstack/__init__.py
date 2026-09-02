"""The CamStack integration.

One component, three surfaces, one config entry:

* the **sidebar panel and the Lovelace card**, which need only the hub's
  address and are set up first;
* the **pushed entities**, which the hub builds and this component renders;
* the **camera entities**, the one thing the push cannot carry.

They are deliberately not all-or-nothing. An entry created by the panel-only
component this one replaces has an address but no credentials, and it must keep
serving the panel it was serving while Home Assistant asks for the rest.
"""

from __future__ import annotations

from typing import Any

from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_USERNAME,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.device_registry import DeviceEntry

from .api import CamStackAuth, CamStackClient, PasswordAuth
from .const import (
    CONF_VERIFY_SSL,
    DEFAULT_VERIFY_SSL,
    DEVICE_KEY_PREFIX,
    DOMAIN,
    SYNTHETIC_DEVICE_KEYS,
)
from .coordinator import (
    CamStackConfigEntry,
    CamStackCoordinator,
    CamStackRuntimeData,
)
from .frontend import async_remove_panel, async_setup_frontend
from .migration import async_migrate_entry, entry_has_credentials, entry_is_oauth
from .oauth import CamStackOAuth2Implementation, OAuth2Auth
from .push import CamStackPushHub
from .push_view import async_register_push_view, async_unregister_push_hub
from .version_view import async_register_version_view

__all__ = ["async_migrate_entry"]

# Every platform this integration builds — and the answer the hub reads at
# `/api/camstack/version` to decide what to send. Adding one here is what
# makes the hub start using it; there is deliberately no second list.
PLATFORMS: list[Platform] = [
    Platform.ALARM_CONTROL_PANEL,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CAMERA,
    Platform.CLIMATE,
    Platform.COVER,
    Platform.FAN,
    Platform.HUMIDIFIER,
    Platform.IMAGE,
    Platform.LOCK,
    Platform.MEDIA_PLAYER,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.VACUUM,
    Platform.VALVE,
    Platform.WATER_HEATER,
]


async def async_setup_entry(hass: HomeAssistant, entry: CamStackConfigEntry) -> bool:
    """Set up CamStack from a config entry."""
    # Before the credential check, and deliberately so: a migrated entry has an
    # address but no password, and an operator whose sidebar disappeared during
    # an upgrade has no way to tell an upgrade from a breakage.
    await async_setup_frontend(hass, entry)
    # Before the credential check too: the hub asks what this integration
    # builds in order to decide what to SEND, and an entry waiting for
    # credentials still has to answer honestly rather than 404 into the
    # hub's "pre-native component" branch.
    async_register_version_view(hass)
    entry.async_on_unload(entry.add_update_listener(_async_entry_updated))

    if not entry_has_credentials(entry.data):
        raise ConfigEntryAuthFailed("credentials_required")

    verify_ssl = entry.data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)
    session = async_create_clientsession(hass, verify_ssl=verify_ssl)
    client = CamStackClient(
        session,
        entry.data[CONF_HOST],
        entry.data[CONF_PORT],
        _build_auth(hass, entry, session, verify_ssl=verify_ssl),
        verify_ssl=verify_ssl,
    )

    push = CamStackPushHub(hass, entry, client)
    # The structure and the last values the hub pushed, from disk. Without
    # them every entity would sit blank between a restart and the hub's next
    # reconcile, which is up to five minutes away.
    await push.async_load()

    coordinator = CamStackCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = CamStackRuntimeData(
        client=client, push=push, coordinator=coordinator
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # Only now: a push arriving before the platforms exist has nowhere to put
    # an entity, and the hub would have deduped the value it carried.
    push.async_start()
    async_register_push_view(hass, push)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: CamStackConfigEntry) -> bool:
    """Unload a config entry."""
    async_remove_panel(hass, entry.entry_id)
    async_unregister_push_hub(hass, entry.entry_id)
    runtime = getattr(entry, "runtime_data", None)
    if isinstance(runtime, CamStackRuntimeData):
        runtime.push.async_stop()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_config_entry_device(
    hass: HomeAssistant,
    entry: CamStackConfigEntry,
    device_entry: DeviceEntry,
) -> bool:
    """Allow deleting a camstack device that the hub no longer exports.

    Without this hook Home Assistant draws no delete button at all: a device
    belonging to a config entry can only be removed if its integration says so.
    The operator's only recourse was deleting the whole integration, and every
    camera ever exported stayed in the registry for good — with its entities,
    its area and its history — long after the hub stopped exporting it.

    **Only a device the hub does NOT currently export may go.** The membership
    is the authority; this hook is the gesture. A device that is still exported
    is refused, because deleting it would leave the hub pushing state at a
    registry entry that no longer exists.

    **An unanswered coordinator refuses.** `data is None` means the listing has
    not been read yet, not that the hub exports nothing — and an empty listing
    read once, transiently, would otherwise authorise deleting the operator's
    entire fleet. This is a one-shot destructive action, so it never gates on a
    read that can fail: unknown is a refusal, and the operator can try again a
    moment later.
    """
    coordinator = entry.runtime_data.coordinator
    data = coordinator.data
    if data is None:
        return False
    # The synthetic devices (notification centre, server) are pushed regardless
    # of the membership, so they are never in this listing — "not exported" is
    # not "not live" for them, and deleting one would only have the next push
    # build it again. They are live by construction.
    exported_keys = {device.device_key for device in data.devices.values()} | set(
        SYNTHETIC_DEVICE_KEYS
    )
    ours = {
        identifier
        for domain, identifier in device_entry.identifiers
        if domain == DOMAIN and identifier.startswith(DEVICE_KEY_PREFIX)
    }
    if not ours:
        return False
    return ours.isdisjoint(exported_keys)


async def async_remove_entry(hass: HomeAssistant, entry: CamStackConfigEntry) -> None:
    """Take the panel down when the entry is deleted.

    Home Assistant only calls `async_unload_entry` for an entry that actually
    loaded. An entry that never got past the credential check still put a panel
    in the sidebar, and deleting it must remove that panel too.
    """
    async_remove_panel(hass, entry.entry_id)


def _build_auth(
    hass: HomeAssistant,
    entry: CamStackConfigEntry,
    session: Any,
    *,
    verify_ssl: bool,
) -> CamStackAuth:
    """Return the way THIS entry authenticates.

    An OAuth entry's implementation is rebuilt here rather than looked up:
    Home Assistant's registry of implementations is in-memory, so it is empty
    after a restart, and the entry holds everything needed to reconstruct it.
    """
    if entry_is_oauth(entry.data):
        implementation = CamStackOAuth2Implementation(
            hass,
            entry.data[CONF_HOST],
            entry.data[CONF_PORT],
            verify_ssl=verify_ssl,
        )
        config_entry_oauth2_flow.async_register_implementation(
            hass, DOMAIN, implementation
        )
        return OAuth2Auth(
            config_entry_oauth2_flow.OAuth2Session(hass, entry, implementation)
        )
    return PasswordAuth(
        session,
        entry.data[CONF_HOST],
        entry.data[CONF_PORT],
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        verify_ssl=verify_ssl,
    )


async def _async_entry_updated(hass: HomeAssistant, entry: CamStackConfigEntry) -> None:
    """Reload after an options change so the panel is rebuilt from them."""
    await hass.config_entries.async_reload(entry.entry_id)
