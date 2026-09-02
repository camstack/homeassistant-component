"""Camera entities backed by a CamStack hub.

The one platform that does NOT read its value from the push transport. Stills
come from the `snapshot` capability and live video is **native WebRTC**: the
hub's `webrtc-session` capability takes a browser offer and answers it
directly, so the stream is not re-encoded and Home Assistant needs no
intermediate `stream` worker.

── One entity per camera, or one per STREAM ────────────────────────────────

Until 2026-08-14 this module built exactly one entity per exported camera,
from the export MEMBERSHIP, and ignored the `camera` components the hub
announced. The hub had been announcing them for a week: six per camera, each
carrying a broker RTSP url. On the operator's instance that read as **165
camstack entities and two `camera` ones** — the twelve announced components
built nothing, and neither side said so, because `/api/camstack/version`
answers `camera` truthfully (this platform exists) and the hub read that as
"it will build my camera components".

So the hub now announces one component per entry of
`webrtcSession.listStreams` — adaptive, each assigned profile, each raw
substream — carrying the `stream_target` verbatim, and this module builds one
entity per component and hands that target straight back to `handleOffer`. No
url crosses in either direction.

**The membership entity is unchanged in KIND**, and the hub deliberately
announces no component for the adaptive stream: that entity IS the adaptive
stream, it carries `camstack_<deviceId>_camera`, and a second component
claiming the same identity would collide on it. So the operator's existing
`camera.<name>` gains one sibling per profile and per substream — the streams
membership cannot express.
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
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from webrtc_models import RTCIceCandidate, RTCIceCandidateInit

from .api import CamStackError
from .const import DOMAIN, MANUFACTURER
from .coordinator import CamStackConfigEntry, CamStackCoordinator, CamStackDevice
from .entity import CamStackPushEntity
from .push import CamStackPushHub

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
    """Set up the pushed stream entities, and the membership fallback."""
    push = entry.runtime_data.push
    push.async_register_platform(
        Platform.CAMERA, CamStackPushCamera, async_add_entities
    )

    coordinator = entry.runtime_data.coordinator
    known: set[int] = set()

    @callback
    def _async_add_new() -> None:
        """Add the primary entity for cameras exported since the last read."""
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


class _CamStackCameraSession(Camera):
    """The half both camera paths share: a still, and a WebRTC session.

    Split out rather than duplicated because the ICE bookkeeping is the part
    that is easy to get subtly wrong, and two copies of it would drift the
    moment one path grew a fix.
    """

    _attr_supported_features = CameraEntityFeature.STREAM

    def __init__(self) -> None:
        """Prepare the session bookkeeping."""
        Camera.__init__(self)
        # Home Assistant's session id is not the hub's; the map is what lets a
        # close or a candidate from the frontend reach the right hub session.
        self._hub_sessions: dict[str, str] = {}
        self._ice_tasks: dict[str, asyncio.Task[None]] = {}

    # --- what the two paths answer differently ------------------------------

    @property
    def _camstack_device_id(self) -> int | None:
        """Return the hub's numeric id for this camera, if it is known."""
        raise NotImplementedError

    @property
    def _stream_target(self) -> dict[str, Any] | None:
        """Return the stream to negotiate, or None for the hub's own choice."""
        return None

    async def _camstack_query(self, path: str, payload: dict[str, Any]) -> Any:
        """Run a hub query."""
        raise NotImplementedError

    async def _camstack_mutate(self, path: str, payload: dict[str, Any]) -> Any:
        """Run a hub mutation."""
        raise NotImplementedError

    # --- the shared behaviour -----------------------------------------------

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return a still image from the hub's snapshot capability."""
        device_id = self._camstack_device_id
        if device_id is None:
            return None
        try:
            result = await self._camstack_query(
                "snapshot.getSnapshot", {"deviceId": device_id}
            )
        except CamStackError as err:
            _LOGGER.debug("Snapshot failed for device %s: %s", device_id, err)
            return None
        if not isinstance(result, dict):
            return None
        encoded = result.get("base64")
        if not isinstance(encoded, str):
            return None
        try:
            return base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            _LOGGER.debug("Undecodable snapshot for device %s", device_id)
            return None

    async def async_handle_async_webrtc_offer(
        self, offer_sdp: str, session_id: str, send_message: WebRTCSendMessage
    ) -> None:
        """Send the browser's offer to the hub and stream back its answer."""
        device_id = self._camstack_device_id
        if device_id is None:
            send_message(
                WebRTCError(
                    "webrtc_offer_failed", "the hub does not export this camera"
                )
            )
            return
        target = self._stream_target
        try:
            result = await self._camstack_mutate(
                "webrtcSession.handleOffer",
                {
                    "deviceId": device_id,
                    "sdpOffer": offer_sdp,
                    # The stream this entity IS. Omitted rather than defaulted
                    # when the hub named none: `handleOffer` picks adaptive on
                    # its own, and inventing a target here would silently show
                    # a different picture from the one the entity promises.
                    **({} if target is None else {"target": target}),
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
            _LOGGER.error("WebRTC offer refused for device %s: %s", device_id, err)
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
            self._pump_ice_candidates(device_id, hub_session_id, send_message),
            name=f"camstack-ice-{device_id}-{session_id}",
        )

    async def _pump_ice_candidates(
        self, device_id: int, hub_session_id: str, send_message: WebRTCSendMessage
    ) -> None:
        """Forward the hub's ICE candidates to the browser until gathering ends."""
        sent = 0
        deadline = asyncio.get_running_loop().time() + _ICE_POLL_TIMEOUT
        while asyncio.get_running_loop().time() < deadline:
            try:
                result = await self._camstack_query(
                    "webrtcSession.getIceCandidates",
                    {"deviceId": device_id, "sessionId": hub_session_id},
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
        device_id = self._camstack_device_id
        hub_session_id = self._hub_sessions.get(session_id)
        if device_id is None or hub_session_id is None:
            return
        try:
            await self._camstack_mutate(
                "webrtcSession.addIceCandidate",
                {
                    "deviceId": device_id,
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
        device_id = self._camstack_device_id
        if device_id is None:
            return
        try:
            await self._camstack_mutate(
                "webrtcSession.closeSession",
                {"deviceId": device_id, "sessionId": hub_session_id},
            )
        except CamStackError as err:
            _LOGGER.debug("Could not close hub session %s: %s", hub_session_id, err)


class CamStackPushCamera(CamStackPushEntity, _CamStackCameraSession):
    """One STREAM of a camera, as the hub announced it.

    The only push entity with no `state_topic`: a camera's whole content is
    the stream it names, and there is no value for camstack to publish. The
    hub therefore announces neither a state nor a command topic for one — see
    the note on `HaComponent.stream_target`.
    """

    def __init__(
        self, hub: CamStackPushHub, device_key: str, component_key: str
    ) -> None:
        """Bind the entity to the component and prepare its sessions."""
        CamStackPushEntity.__init__(self, hub, device_key, component_key)
        _CamStackCameraSession.__init__(self)

    @property
    def _camstack_device_id(self) -> int | None:
        """Return the hub's numeric id, from the MEMBERSHIP.

        The push carries no numeric id of its own: it carries the device
        KEY, which is `camstack-<deviceId>`. `snapshot.getSnapshot` and
        `webrtcSession.handleOffer` take the number, so it is looked up
        through the coordinator by the key both halves agree on rather than
        parsed back out of the key — parsing a wire format is how the two
        sides stop agreeing on what it means.

        Read live rather than captured at construction: an entity outlives
        the membership answer that built it, and a captured value would go on
        addressing a device the hub no longer exports.
        """
        coordinator = self._hub.entry.runtime_data.coordinator
        data = coordinator.data
        if data is None:
            return None
        for device in data.devices.values():
            if device.device_key == self._device_key:
                return device.device_id
        return None

    @property
    def _stream_target(self) -> dict[str, Any] | None:
        """Return the stream this entity shows, as the hub named it."""
        target = self._component.get("stream_target")
        return target if isinstance(target, dict) else None

    @property
    def is_on(self) -> bool:
        """A stream the hub still announces is on."""
        return bool(self._component)

    async def _camstack_query(self, path: str, payload: dict[str, Any]) -> Any:
        """Run a hub query through the shared client."""
        return await self._hub.client.query(path, payload)

    async def _camstack_mutate(self, path: str, payload: dict[str, Any]) -> Any:
        """Run a hub mutation through the shared client."""
        return await self._hub.client.mutate(path, payload)


class CamStackCamera(CoordinatorEntity[CamStackCoordinator], _CamStackCameraSession):
    """The camera itself: the device's primary entity, adaptive.

    Built from the export membership, and named `None` so it takes the
    device's own name. It negotiates NO target, which is what makes it the
    adaptive stream — and it is why the hub announces no component for one.
    """

    _attr_has_entity_name = True
    _attr_name = None

    def __init__(
        self, coordinator: CamStackCoordinator, device: CamStackDevice
    ) -> None:
        """Build the entity and prepare its WebRTC session bookkeeping."""
        CoordinatorEntity.__init__(self, coordinator)
        _CamStackCameraSession.__init__(self)
        self._device_id = device.device_id
        self._device_key = device.device_key
        # `camstack_<deviceId>_camera` — the same numeric identity every
        # entity the hub pushes is keyed on. The hub announces NO component
        # for the adaptive stream precisely so this one owns the key: an
        # announced `615_camera` reaches the registry as `camstack_615_camera`
        # too, and Home Assistant drops the loser of that race. Pinned on the
        # hub side by `camera-entities.spec.ts`.
        self._attr_unique_id = f"{DOMAIN}_{device.device_id}_camera"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.device_key)},
            name=device.name,
            manufacturer=MANUFACTURER,
        )

    @property
    def _device(self) -> CamStackDevice | None:
        """Return the current record for this camera."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.devices.get(self._device_id)

    @property
    def _camstack_device_id(self) -> int | None:
        """Return the hub's numeric id for this camera."""
        return self._device_id

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

    async def _camstack_query(self, path: str, payload: dict[str, Any]) -> Any:
        """Run a hub query through the coordinator's client."""
        return await self.coordinator.client.query(path, payload)

    async def _camstack_mutate(self, path: str, payload: dict[str, Any]) -> Any:
        """Run a hub mutation through the coordinator's client."""
        return await self.coordinator.client.mutate(path, payload)

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
