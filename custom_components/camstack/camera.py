"""Camera entities backed by a CamStack hub.

The one entity that does NOT come over the push transport. Stills come from
the `snapshot` capability and live video is **native WebRTC**: the hub's
`webrtc-session` capability takes a browser offer and answers it directly, so
the stream is not re-encoded and Home Assistant needs no intermediate `stream`
worker. Both are addressed by the hub's numeric device id, which the push
never carries — which is why membership is still read from the hub.

The entity joins the SAME Home Assistant device as the camera's pushed
entities, keyed on the hub's `device_id`. Anything else would put the live
view on a second device sitting next to the sensors it belongs with.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import logging
from typing import Any

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.components.camera.webrtc import (
    WebRTCAnswer,
    WebRTCCandidate,
    WebRTCError,
    WebRTCSendMessage,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from webrtc_models import RTCIceCandidate, RTCIceCandidateInit

from .api import CamStackError
from .const import DOMAIN, MANUFACTURER
from .coordinator import CamStackConfigEntry, CamStackCoordinator, CamStackDevice

_LOGGER = logging.getLogger(__name__)

# The hub gathers candidates asynchronously; this is a poll of ITS pending set,
# not a retry of the offer.
_ICE_POLL_INTERVAL = 0.25
_ICE_POLL_TIMEOUT = 20.0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CamStackConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up one camera entity per exported camera, now and later."""
    coordinator = entry.runtime_data.coordinator
    known: set[int] = set()

    @callback
    def _async_add_new() -> None:
        """Add entities for cameras exported since the last read."""
        data = coordinator.data
        if data is None:
            return
        fresh = [camera for camera in data.cameras() if camera.device_id not in known]
        if not fresh:
            return
        known.update(camera.device_id for camera in fresh)
        async_add_entities(CamStackCamera(coordinator, camera) for camera in fresh)

    _async_add_new()
    entry.async_on_unload(coordinator.async_add_listener(_async_add_new))


class CamStackCamera(CoordinatorEntity[CamStackCoordinator], Camera):
    """A CamStack camera, with stills and native WebRTC live video."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_supported_features = CameraEntityFeature.STREAM

    def __init__(
        self, coordinator: CamStackCoordinator, device: CamStackDevice
    ) -> None:
        """Build the entity and prepare its WebRTC session bookkeeping."""
        CoordinatorEntity.__init__(self, coordinator)
        Camera.__init__(self)
        self._device_id = device.device_id
        self._device_key = device.device_key
        # Derived from the stable id, like every other entity the hub exports:
        # a numeric id is reallocated by a re-sync, and an entity keyed on one
        # would follow whichever camera inherited the number.
        self._attr_unique_id = f"{DOMAIN}_{device.stable_id}_camera"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.device_key)},
            name=device.name,
            manufacturer=MANUFACTURER,
        )
        # Home Assistant's session id is not the hub's; the map is what lets a
        # close or a candidate from the frontend reach the right hub session.
        self._hub_sessions: dict[str, str] = {}
        self._ice_tasks: dict[str, asyncio.Task[None]] = {}

    @property
    def _device(self) -> CamStackDevice | None:
        """Return the current record for this camera."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.devices.get(self._device_id)

    @property
    def available(self) -> bool:
        """Report a camera the hub disabled as unavailable, not as idle."""
        device = self._device
        return super().available and device is not None and not device.disabled

    @property
    def is_on(self) -> bool:
        """Return whether the camera is switched on at the hub."""
        device = self._device
        return device is not None and not device.disabled

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return a still image from the hub's snapshot capability."""
        try:
            result = await self.coordinator.client.query(
                "snapshot.getSnapshot", {"deviceId": self._device_id}
            )
        except CamStackError as err:
            _LOGGER.debug("Snapshot failed for device %s: %s", self._device_id, err)
            return None
        if not isinstance(result, dict):
            return None
        encoded = result.get("base64")
        if not isinstance(encoded, str):
            return None
        try:
            return base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            _LOGGER.debug("Undecodable snapshot for device %s", self._device_id)
            return None

    async def async_handle_async_webrtc_offer(
        self, offer_sdp: str, session_id: str, send_message: WebRTCSendMessage
    ) -> None:
        """Send the browser's offer to the hub and stream back its answer."""
        try:
            result = await self.coordinator.client.mutate(
                "webrtcSession.handleOffer",
                {
                    "deviceId": self._device_id,
                    "sdpOffer": offer_sdp,
                    # The hub validates `kind` against a closed enum; Home
                    # Assistant's player is a browser, and an invented value
                    # would be rejected outright rather than merely mislabelled.
                    "consumerAttribution": {
                        "kind": "webrtc-browser",
                        "label": "Home Assistant",
                        "sessionId": session_id,
                    },
                },
            )
        except CamStackError as err:
            _LOGGER.error(
                "WebRTC offer refused for device %s: %s", self._device_id, err
            )
            send_message(WebRTCError("webrtc_offer_failed", str(err)))
            return

        answer = result.get("sdpAnswer") if isinstance(result, dict) else None
        hub_session_id = result.get("sessionId") if isinstance(result, dict) else None
        if not isinstance(answer, str) or not isinstance(hub_session_id, str):
            send_message(
                WebRTCError("webrtc_offer_failed", "hub returned no usable answer")
            )
            return

        self._hub_sessions[session_id] = hub_session_id
        send_message(WebRTCAnswer(answer))
        self._ice_tasks[session_id] = self.hass.async_create_background_task(
            self._pump_ice_candidates(hub_session_id, send_message),
            name=f"camstack-ice-{self._device_id}-{session_id}",
        )

    async def _pump_ice_candidates(
        self, hub_session_id: str, send_message: WebRTCSendMessage
    ) -> None:
        """Forward the hub's ICE candidates to the browser until gathering ends."""
        sent = 0
        deadline = asyncio.get_running_loop().time() + _ICE_POLL_TIMEOUT
        while asyncio.get_running_loop().time() < deadline:
            try:
                result = await self.coordinator.client.query(
                    "webrtcSession.getIceCandidates",
                    {"deviceId": self._device_id, "sessionId": hub_session_id},
                )
            except CamStackError as err:
                _LOGGER.debug("ICE poll failed for %s: %s", hub_session_id, err)
                return
            if not isinstance(result, dict):
                return
            candidates = result.get("candidates")
            if isinstance(candidates, list):
                for entry in candidates[sent:]:
                    if isinstance(entry, dict):
                        send_message(WebRTCCandidate(_to_ha_candidate(entry)))
                sent = len(candidates)
            if result.get("done"):
                return
            await asyncio.sleep(_ICE_POLL_INTERVAL)
        _LOGGER.debug("ICE gathering did not finish for session %s", hub_session_id)

    async def async_on_webrtc_candidate(
        self, session_id: str, candidate: RTCIceCandidate
    ) -> None:
        """Forward a browser ICE candidate to the hub."""
        hub_session_id = self._hub_sessions.get(session_id)
        if hub_session_id is None:
            return
        try:
            await self.coordinator.client.mutate(
                "webrtcSession.addIceCandidate",
                {
                    "deviceId": self._device_id,
                    "sessionId": hub_session_id,
                    "candidate": candidate.candidate,
                    "sdpMid": getattr(candidate, "sdp_mid", None),
                    "sdpMLineIndex": getattr(candidate, "sdp_m_line_index", None),
                },
            )
        except CamStackError as err:
            _LOGGER.debug("Could not forward ICE candidate: %s", err)

    @callback
    def close_webrtc_session(self, session_id: str) -> None:
        """Release the hub session behind a closed browser session."""
        task = self._ice_tasks.pop(session_id, None)
        if task is not None:
            task.cancel()
        hub_session_id = self._hub_sessions.pop(session_id, None)
        if hub_session_id is None:
            return
        self.hass.async_create_task(self._async_close_session(hub_session_id))

    async def _async_close_session(self, hub_session_id: str) -> None:
        """Tell the hub the session is over."""
        try:
            await self.coordinator.client.mutate(
                "webrtcSession.closeSession",
                {"deviceId": self._device_id, "sessionId": hub_session_id},
            )
        except CamStackError as err:
            _LOGGER.debug("Could not close hub session %s: %s", hub_session_id, err)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the hub's device id, which every hub log line is keyed on."""
        return {
            "camstack_device_id": self._device_id,
            "camstack_device_key": self._device_key,
        }


def _to_ha_candidate(entry: dict[str, Any]) -> RTCIceCandidateInit:
    """Convert a hub ICE candidate into the shape Home Assistant expects."""
    sdp_mid = entry.get("sdpMid")
    sdp_m_line_index = entry.get("sdpMLineIndex")
    return RTCIceCandidateInit(
        candidate=str(entry.get("candidate") or ""),
        sdp_mid=sdp_mid if isinstance(sdp_mid, str) else None,
        sdp_m_line_index=(
            sdp_m_line_index if isinstance(sdp_m_line_index, int) else None
        ),
    )
