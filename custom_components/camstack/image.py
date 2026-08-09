"""Images built from the components CamStack pushes.

**Pictures never cross the push transport.** A detection crop is several
hundred kilobytes and there are dozens of image entities per camera; sending
the bytes would turn every detection into a multi-megabyte POST. What arrives
is a signed, expiring URL, and this entity fetches the bytes from it on
demand — over the same TLS settings the rest of the integration uses, because
a hub with a self-signed certificate would otherwise serve broken images only.

Above `MAX_VALUE_CHARS` the hub collapses ANY value to
`__image_updated__:<ts>`. That is a backstop for something that grew
unexpectedly, not the normal path for a picture: it says the image changed
without saying where to get it, so the entity keeps the frame it has rather
than blanking one that is merely stale.
"""

from __future__ import annotations

import logging
from datetime import datetime

import aiohttp
from homeassistant.components.image import ImageEntity
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import IMAGE_SIGNAL_PREFIX, MIN_IMAGE_FETCH_INTERVAL
from .coordinator import CamStackConfigEntry
from .entity import CamStackPushEntity
from .push import CamStackPushHub

_LOGGER = logging.getLogger(__name__)

_FETCH_TIMEOUT = aiohttp.ClientTimeout(total=20)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CamStackConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Let the push hub create image entities as it learns about them."""
    entry.runtime_data.push.async_register_platform(
        Platform.IMAGE, CamStackImage, async_add_entities
    )


class CamStackImage(CamStackPushEntity, ImageEntity):
    """The last picture for one detection class, zone or camera."""

    _attr_content_type = "image/jpeg"

    def __init__(
        self, hub: CamStackPushHub, device_key: str, component_key: str
    ) -> None:
        """Build the entity and prepare its one-frame cache."""
        CamStackPushEntity.__init__(self, hub, device_key, component_key)
        ImageEntity.__init__(self, hub.hass)
        self._seen_value: str | None = None
        self._cached_url: str | None = None
        self._cached_bytes: bytes | None = None
        self._fetched_at: datetime | None = None

    @property
    def _url(self) -> str | None:
        """Return the URL to fetch, or None when there is nothing to fetch."""
        value = self._value
        if value is None or value.startswith(IMAGE_SIGNAL_PREFIX):
            return None
        return value if value.startswith(("http://", "https://")) else None

    @callback
    def _async_pushed(self) -> None:
        """Stamp the entity when a NEW picture arrives.

        Home Assistant re-fetches on `image_last_updated`, so stamping on
        every wake — a heartbeat, a structure change — would re-download an
        identical frame for every image entity on every reconcile.
        """
        value = self._value
        if value is not None and value != self._seen_value:
            self._seen_value = value
            self._attr_image_last_updated = dt_util.utcnow()
        super()._async_pushed()

    async def async_image(self) -> bytes | None:
        """Return the picture, fetching it at most once per interval."""
        url = self._url
        if url is None:
            return self._cached_bytes
        now = dt_util.utcnow()
        if url == self._cached_url and self._cached_bytes is not None:
            return self._cached_bytes
        if (
            self._fetched_at is not None
            and now - self._fetched_at < MIN_IMAGE_FETCH_INTERVAL
            and self._cached_bytes is not None
        ):
            return self._cached_bytes
        self._fetched_at = now
        try:
            async with self._hub.client.session.get(
                url, ssl=self._hub.client.verify_ssl, timeout=_FETCH_TIMEOUT
            ) as response:
                if response.status != 200:
                    # The link is signed and expiring. An expired one is the
                    # normal end of a picture's life, not a fault, but silence
                    # here reads as "the camera stopped sending images".
                    _LOGGER.debug(
                        "CamStack image %s answered %s", self.entity_id, response.status
                    )
                    return self._cached_bytes
                payload = await response.read()
        except (TimeoutError, aiohttp.ClientError) as err:
            _LOGGER.debug("Could not fetch CamStack image %s: %s", self.entity_id, err)
            return self._cached_bytes
        self._cached_url = url
        self._cached_bytes = payload
        return payload
