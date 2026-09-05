"""The entry reloads on an OPTIONS change, never on a token refresh.

Home Assistant refreshes the OAuth access token every hour by writing the
new token into ``entry.data`` — and ``add_update_listener`` fires on EVERY
entry update. The listener reloaded the entry, so every hour, to the second
(HH:10:36 on the live hub, 2026-09-05): the old coordinator's in-flight query
died with ``RuntimeError: Session is closed`` (the entry-scoped aiohttp
session goes with the unload), the fresh setup armed ``resync_pending`` and
answered the hub's next push ``503 resync``, and the hub dropped 18-20
messages and re-announced everything. Twenty occurrences in the system log.
"""

from __future__ import annotations



from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import setup_integration


async def test_a_token_refresh_does_not_reload_the_entry(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """Rewriting ``entry.data`` with a new token keeps the running setup."""
    runtime = await setup_integration(hass, config_entry)
    hass.config_entries.async_update_entry(
        config_entry,
        data={**config_entry.data, "token": {"access_token": "renewed", "expires_at": 1.0}},
    )
    await hass.async_block_till_done()
    assert config_entry.runtime_data is runtime


async def test_an_options_change_still_reloads_the_entry(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """The reason the listener exists: the panel is rebuilt from options."""
    runtime = await setup_integration(hass, config_entry)
    hass.config_entries.async_update_entry(
        config_entry, options={**config_entry.options, "changed": True}
    )
    await hass.async_block_till_done()
    assert config_entry.runtime_data is not runtime
