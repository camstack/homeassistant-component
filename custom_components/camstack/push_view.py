"""The HTTP view camstack pushes into.

Behind Home Assistant's own authentication: the hub POSTs with the
long-lived token the operator already gave it, so there is no second shared
secret to configure or rotate.

**One refusal is deliberate, and removing it breaks the integration in a way
nothing reports.** The hub dedups per topic and never re-sends an unchanged
value; it drops that cache only when the link comes back, which it detects as
a failed POST followed by a successful one. Home Assistant reloading this
integration produces no such edge — a view cannot be unregistered once
registered, so the endpoint keeps answering 200 through a reload while the
entities behind it are brand new and empty. They would then stay empty until
each value happened to change on its own.

So the first push after every setup is answered 503, once. That is the edge:
the hub drops its cache, and its next reconcile re-pushes every value. The
message carried by that one POST is not lost either — the reconcile that
follows contains it.
"""

from __future__ import annotations

import logging
from typing import Any

from aiohttp import web
from homeassistant.components.http import KEY_HASS, HomeAssistantView
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN, PUSH_VIEW_URL
from .push import CamStackPushHub

_LOGGER = logging.getLogger(__name__)

# Where the loaded hubs live, so the view can find one. The view outlives the
# config entry: Home Assistant has no way to unregister an HTTP view.
DATA_HUBS = "push_hubs"
DATA_VIEW = "push_view_registered"


@callback
def async_register_push_view(hass: HomeAssistant, hub: CamStackPushHub) -> None:
    """Publish a hub to the view, registering the view the first time."""
    data = hass.data.setdefault(DOMAIN, {})
    hubs: dict[str, CamStackPushHub] = data.setdefault(DATA_HUBS, {})
    if hubs and hub.entry.entry_id not in hubs:
        # There is nothing on the wire that tells two hubs apart: the path is
        # fixed and the payload carries no instance id. Saying so is better
        # than silently feeding one hub's entities from another's heartbeat.
        _LOGGER.error(
            "A second CamStack hub was configured. Only one can receive "
            "pushes on %s; this entry's entities will stay unavailable",
            PUSH_VIEW_URL,
        )
        return
    hubs[hub.entry.entry_id] = hub
    if not data.get(DATA_VIEW):
        hass.http.register_view(CamStackPushView())
        data[DATA_VIEW] = True


@callback
def async_unregister_push_hub(hass: HomeAssistant, entry_id: str) -> None:
    """Detach a hub. The view itself stays; Home Assistant keeps it forever."""
    hubs = hass.data.get(DOMAIN, {}).get(DATA_HUBS, {})
    hubs.pop(entry_id, None)


class CamStackPushView(HomeAssistantView):
    """Receives every state update, structure change and heartbeat."""

    url = PUSH_VIEW_URL
    name = "api:camstack:push"
    requires_auth = True

    async def post(self, request: web.Request) -> web.Response:
        """Apply one pushed message."""
        hass: HomeAssistant = request.app[KEY_HASS]
        hub = _first_hub(hass)
        if hub is None:
            # The integration is not loaded. Refusing is also the right
            # answer for the hub: it stops deduping against entities that do
            # not exist.
            return web.Response(status=503, text="camstack is not loaded")

        if hub.resync_pending:
            hub.resync_pending = False
            _LOGGER.debug(
                "Refusing the first push after setup so the hub drops its "
                "dedup cache and re-pushes every value"
            )
            return web.Response(status=503, text="resync")

        try:
            message: Any = await request.json()
        except ValueError:
            return web.Response(status=400, text="expected JSON")

        hub.async_handle_message(message)
        return web.Response(status=200)


def _first_hub(hass: HomeAssistant) -> CamStackPushHub | None:
    """Return the hub the pushes belong to, if the integration is loaded."""
    hubs: dict[str, CamStackPushHub] = hass.data.get(DOMAIN, {}).get(DATA_HUBS, {})
    return next(iter(hubs.values()), None)
