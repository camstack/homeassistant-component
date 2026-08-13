"""What this integration can BUILD, for the hub to read.

The hub and this integration ship on different trains: the hub is deployed
when the operator deploys it, and this is updated through HACS when the
operator decides to. So the hub cannot assume the platforms it knows about
exist here — a `cover` announced to a version that does not build one
produces a warning line and NO entity, which from the operator's side is
indistinguishable from "camstack exported nothing".

This endpoint is the answer. It reports the PLATFORM LIST rather than
leaving the hub to infer it from a version number: a table on the hub
mapping versions to platforms would be a second copy of a fact that lives
here, and it would rot on the first release that adds one. The version
travels too, for the hub's log line.

Absent (404) on every released version before native platforms, and that
is the point: it tells an OLD integration from an unreachable one. The hub
treats the 404 as information and the timeout as none.
"""

from __future__ import annotations

from aiohttp import web
from homeassistant.components.http import KEY_HASS, HomeAssistantView
from homeassistant.core import HomeAssistant, callback
from homeassistant.loader import async_get_integration

from .const import DOMAIN, VERSION_VIEW_URL

_VIEW_REGISTERED = f"{DOMAIN}_version_view_registered"


@callback
def async_register_version_view(hass: HomeAssistant) -> None:
    """Register the endpoint once, whatever the entry count.

    Home Assistant has no way to unregister an HTTP view, so a second entry
    must not try to add a second copy of this one.
    """
    data = hass.data.setdefault(DOMAIN, {})
    if data.get(_VIEW_REGISTERED):
        return
    hass.http.register_view(CamStackVersionView())
    data[_VIEW_REGISTERED] = True


def async_supported_platforms(hass: HomeAssistant) -> list[str]:
    """Return the platforms this integration forwards its entries to.

    Read from `PLATFORMS` itself, imported here rather than duplicated: a
    hand-kept list would be one release away from promising the hub a
    platform nobody wired up, and that promise costs entities.
    """
    from . import PLATFORMS

    return sorted(platform.value for platform in PLATFORMS)


class CamStackVersionView(HomeAssistantView):
    """Answers the hub's platform-support probe."""

    url = VERSION_VIEW_URL
    name = "api:camstack:version"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        """Return this integration's version and the platforms it builds."""
        hass: HomeAssistant = request.app[KEY_HASS]
        integration = await async_get_integration(hass, DOMAIN)
        return self.json(
            {
                "integration": DOMAIN,
                "version": str(integration.version or "0"),
                "platforms": async_supported_platforms(hass),
            }
        )
