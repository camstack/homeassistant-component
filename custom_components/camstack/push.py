"""The push receiver: everything CamStack sends, and everything it is told.

CamStack pushes; Home Assistant never polls it for a value. The hub's
`homeassistant-export` addon POSTs four message types at
:data:`~.const.PUSH_VIEW_URL`, and this module is the only thing that
understands them:

``heartbeat``
    Liveness, on a fixed cadence, and the ONLY availability signal there is.
    When it stops every entity goes unavailable — deliberately no second
    source that could disagree about whether camstack is alive.

``batch``
    State updates accumulate hub-side and arrive together. Across ~880
    entities that batching is the difference between an installation and a
    storm.

``state_update``
    One topic, one value, always a STRING. Deduped hub-side, so an unchanged
    value never arrives twice.

``entity_change``
    A device's COMPLETE component set — never a delta, and re-sent in full on
    every reconcile. A hub event is telemetry and may be dropped, so this
    module DIFFS against a full snapshot and never accumulates: a lost message
    is repaired by the next one instead of leaving half a device behind.

Two consequences of the hub's dedup cache that are load-bearing here:

* the hub drops that cache the moment the link comes back, and re-pushes
  everything on its next reconcile. Its notion of "the link came back" is a
  failed POST followed by a successful one — see :mod:`.push_view` for why
  this component has to manufacture that edge rather than wait for it.
* between a restart and that reconcile there is nothing on the wire to
  rebuild from, so structure AND last values are persisted here. Without it
  every entity is blank for up to five minutes after each restart.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .api import CamStackError
from .const import (
    COMMAND_ROUTE_PATH,
    DOMAIN,
    HEARTBEAT_TIMEOUT,
    LINK_CHECK_INTERVAL,
    MANUFACTURER,
    SIGNAL_LINK,
    SIGNAL_STATE,
    SIGNAL_STRUCTURE,
    STORAGE_KEY,
    STORAGE_SAVE_DELAY,
    STORAGE_VERSION,
)

_LOGGER = logging.getLogger(__name__)

type EntityFactory = Callable[[CamStackPushHub, str, str], Entity]


@dataclass(frozen=True, slots=True)
class PushDevice:
    """The `dev` block of an `entity_change`, as Home Assistant needs it."""

    key: str
    name: str
    manufacturer: str
    model: str

    @classmethod
    def from_payload(cls, device_key: str, payload: Any) -> PushDevice:
        """Build a device block, falling back rather than refusing.

        A device that arrives with a malformed `dev` still has entities worth
        creating; dropping it would lose them over a cosmetic field.
        """
        block = payload if isinstance(payload, dict) else {}
        return cls(
            key=device_key,
            name=str(block.get("name") or device_key),
            manufacturer=str(block.get("mf") or MANUFACTURER),
            model=str(block.get("mdl") or ""),
        )


def push_unique_id(device_key: str, component_key: str, component: Any) -> str:
    """Return the Home Assistant `unique_id` for one pushed component.

    The hub SENDS `unique_id` (`<stableId>_<entity>`) and documents it as a
    wire format: it is derived from the device's stable id rather than its
    numeric one, so a re-adopted camera keeps its history instead of orphaning
    every automation pointing at it. It is used verbatim, and the
    `<device_id>_<component_key>` form is only a fallback for a component that
    somehow arrives without one — computing it when the hub sent something
    else is how the two sides stop agreeing on an entity's identity.
    """
    raw = component.get("unique_id") if isinstance(component, dict) else None
    if isinstance(raw, str) and raw:
        return f"{DOMAIN}_{raw}"
    return f"{DOMAIN}_{device_key}_{component_key}"


class CamStackPushHub:
    """Holds everything camstack has pushed, and hands it to the entities."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: Any) -> None:
        """Prepare the store. No I/O and no network happens here."""
        self.hass = hass
        self.entry = entry
        self.client = client
        self.devices: dict[str, PushDevice] = {}
        self.components: dict[str, dict[str, dict[str, Any]]] = {}
        self.states: dict[str, str] = {}
        # Armed at every setup, answered once. See `push_view.py`.
        self.resync_pending = True
        self._last_heartbeat: datetime | None = None
        self._link_alive = False
        self._platforms: dict[
            str, tuple[EntityFactory, AddConfigEntryEntitiesCallback]
        ] = {}
        self._unsupported: set[str] = set()
        self._cancel_watchdog: Callable[[], None] | None = None
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{STORAGE_KEY}.{entry.entry_id}"
        )

    # --- lifecycle ---------------------------------------------------------

    async def async_load(self) -> None:
        """Restore the last known structure and values from disk."""
        stored = await self._store.async_load()
        if not isinstance(stored, dict):
            return
        devices = stored.get("devices")
        components = stored.get("components")
        states = stored.get("states")
        if isinstance(components, dict):
            for device_key, cmps in components.items():
                if isinstance(cmps, dict):
                    self.components[device_key] = {
                        key: value
                        for key, value in cmps.items()
                        if isinstance(value, dict)
                    }
        if isinstance(devices, dict):
            for device_key, block in devices.items():
                if device_key in self.components:
                    self.devices[device_key] = PushDevice.from_payload(
                        device_key, block
                    )
        if isinstance(states, dict):
            self.states = {
                topic: value
                for topic, value in states.items()
                if isinstance(value, str)
            }
        _LOGGER.debug(
            "Restored %d device(s) and %d value(s) from the push store",
            len(self.components),
            len(self.states),
        )

    @callback
    def async_start(self) -> None:
        """Start the availability watchdog."""
        self._cancel_watchdog = async_track_time_interval(
            self.hass, self._async_check_link, LINK_CHECK_INTERVAL
        )

    @callback
    def async_stop(self) -> None:
        """Stop the watchdog. The store flushes itself on shutdown."""
        if self._cancel_watchdog is not None:
            self._cancel_watchdog()
            self._cancel_watchdog = None

    # --- what the entities read -------------------------------------------

    @property
    def link_alive(self) -> bool:
        """Return whether camstack is still sending heartbeats."""
        return self._link_alive

    def component(self, device_key: str, component_key: str) -> dict[str, Any]:
        """Return one component definition, or an empty one if it is gone."""
        return self.components.get(device_key, {}).get(component_key, {})

    def state(self, topic: str | None) -> str | None:
        """Return the last value pushed on a topic."""
        if topic is None:
            return None
        return self.states.get(topic)

    def device_info(self, device_key: str) -> DeviceInfo:
        """Return the Home Assistant device an entity belongs to.

        Keyed on the hub's `device_id` so that the pushed entities and the
        camera entity — which comes from the hub API, not the push — land on
        one device instead of two that look like duplicates.
        """
        device = self.devices.get(device_key)
        if device is None:
            return DeviceInfo(
                identifiers={(DOMAIN, device_key)}, manufacturer=MANUFACTURER
            )
        return DeviceInfo(
            identifiers={(DOMAIN, device_key)},
            name=device.name,
            manufacturer=device.manufacturer,
            model=device.model or None,
        )

    # --- platforms ---------------------------------------------------------

    @callback
    def async_register_platform(
        self,
        platform: Platform,
        factory: EntityFactory,
        async_add_entities: AddConfigEntryEntitiesCallback,
    ) -> None:
        """Let one platform build its entities, now and as they arrive."""
        self._platforms[platform.value] = (factory, async_add_entities)
        pending = [
            (device_key, component_key)
            for device_key, cmps in self.components.items()
            for component_key, component in cmps.items()
            if component.get("platform") == platform.value
        ]
        self._async_add(pending)

    @callback
    def _async_add(self, keys: Iterable[tuple[str, str]]) -> None:
        """Create entities for components, grouped by their platform."""
        by_platform: dict[str, list[Entity]] = {}
        for device_key, component_key in keys:
            component = self.component(device_key, component_key)
            platform = component.get("platform")
            if not isinstance(platform, str):
                continue
            registered = self._platforms.get(platform)
            if registered is None:
                self._warn_unsupported(platform)
                continue
            factory, _adder = registered
            by_platform.setdefault(platform, []).append(
                factory(self, device_key, component_key)
            )
        for platform, entities in by_platform.items():
            _factory, adder = self._platforms[platform]
            adder(entities)

    def _warn_unsupported(self, platform: str) -> None:
        """Name a platform this component cannot build, exactly once.

        Silence would read as "camstack exported nothing", which is a
        different problem with a different fix.
        """
        if platform in self._unsupported or platform in self._platforms:
            return
        self._unsupported.add(platform)
        _LOGGER.warning(
            "CamStack pushed a '%s' entity, which this version of the "
            "integration does not build. Its other entities are unaffected",
            platform,
        )

    # --- messages ----------------------------------------------------------

    @callback
    def async_handle_message(self, message: Any) -> None:
        """Apply one pushed message. Unknown types are ignored, not guessed."""
        if not isinstance(message, dict):
            return
        kind = message.get("type")
        if kind == "batch":
            items = message.get("items")
            for item in items if isinstance(items, list) else []:
                self.async_handle_message(item)
            return
        if kind == "state_update":
            self._async_state_update(message)
            return
        if kind == "entity_change":
            self._async_entity_change(message)
            return
        if kind == "heartbeat":
            self._async_heartbeat()
            return
        _LOGGER.debug("Ignoring a push message of unknown type: %r", kind)

    @callback
    def _async_heartbeat(self) -> None:
        """Record liveness, and wake every entity when it returns."""
        self._last_heartbeat = dt_util.utcnow()
        if self._link_alive:
            return
        self._link_alive = True
        _LOGGER.info("CamStack is pushing again; entities are available")
        async_dispatcher_send(
            self.hass, SIGNAL_LINK.format(entry_id=self.entry.entry_id)
        )

    @callback
    def _async_check_link(self, _now: datetime) -> None:
        """Mark everything unavailable when the heartbeat stops."""
        if not self._link_alive:
            return
        last = self._last_heartbeat
        if last is not None and dt_util.utcnow() - last < HEARTBEAT_TIMEOUT:
            return
        self._link_alive = False
        # A branch that blanks every entity may not be silent: from the
        # operator's side this is indistinguishable from the integration
        # breaking, and the difference is the whole diagnosis.
        _LOGGER.warning(
            "No CamStack heartbeat for %s; marking every entity unavailable. "
            "The hub's homeassistant-export addon has stopped pushing",
            HEARTBEAT_TIMEOUT,
        )
        async_dispatcher_send(
            self.hass, SIGNAL_LINK.format(entry_id=self.entry.entry_id)
        )

    @callback
    def _async_state_update(self, message: dict[str, Any]) -> None:
        """Store one value and wake the single entity that owns its topic."""
        topic = message.get("topic")
        value = message.get("value")
        if not isinstance(topic, str) or not isinstance(value, str):
            _LOGGER.debug("Discarding a malformed state_update: %r", message)
            return
        self.states[topic] = value
        self._async_save()
        async_dispatcher_send(
            self.hass,
            SIGNAL_STATE.format(entry_id=self.entry.entry_id, topic=topic),
        )

    @callback
    def _async_entity_change(self, message: dict[str, Any]) -> None:
        """Rebuild one device from a COMPLETE component set.

        Diffed, never accumulated: the hub re-sends this in full on every
        reconcile precisely so that a dropped message repairs itself, and
        merging would instead make a stale entity permanent.
        """
        device_key = message.get("device_id")
        cmps = message.get("cmps")
        if not isinstance(device_key, str) or not isinstance(cmps, dict):
            _LOGGER.debug("Discarding a malformed entity_change: %r", message)
            return
        incoming = {
            key: value for key, value in cmps.items() if isinstance(value, dict)
        }
        previous = self.components.get(device_key, {})
        self.devices[device_key] = PushDevice.from_payload(
            device_key, message.get("dev")
        )
        self.components[device_key] = incoming
        self._async_save()

        removed = set(previous) - set(incoming)
        for component_key in removed:
            self._async_remove_entity(device_key, component_key, previous)
        added = set(incoming) - set(previous)
        if added:
            self._async_add((device_key, key) for key in added)
        if removed or added:
            _LOGGER.debug(
                "%s: %d entities (+%d, -%d)",
                device_key,
                len(incoming),
                len(added),
                len(removed),
            )
        # A renamed zone or a changed option list keeps its entity and changes
        # its presentation, so the survivors are told rather than rebuilt.
        async_dispatcher_send(
            self.hass, SIGNAL_STRUCTURE.format(entry_id=self.entry.entry_id)
        )

    @callback
    def _async_remove_entity(
        self,
        device_key: str,
        component_key: str,
        previous: dict[str, dict[str, Any]],
    ) -> None:
        """Delete an entity the hub no longer exports."""
        component = previous.get(component_key, {})
        platform = component.get("platform")
        if not isinstance(platform, str):
            return
        registry = er.async_get(self.hass)
        entity_id = registry.async_get_entity_id(
            platform, DOMAIN, push_unique_id(device_key, component_key, component)
        )
        if entity_id is not None:
            registry.async_remove(entity_id)

    @callback
    def _async_save(self) -> None:
        """Persist structure and values, coalesced."""
        self._store.async_delay_save(self._data_to_save, STORAGE_SAVE_DELAY)

    @callback
    def _data_to_save(self) -> dict[str, Any]:
        """Return the snapshot written to disk."""
        return {
            "devices": {
                key: {
                    "name": device.name,
                    "mf": device.manufacturer,
                    "mdl": device.model,
                }
                for key, device in self.devices.items()
            },
            "components": self.components,
            "states": self.states,
        }

    # --- commands ----------------------------------------------------------

    async def async_send_command(self, topic: str | None, value: str) -> None:
        """Send one command back to camstack.

        The hub refuses an unroutable command rather than approximating it, so
        a failure here is surfaced to the caller: a switch that silently did
        nothing but still flipped in the UI is the worst of both.
        """
        if not topic:
            raise HomeAssistantError("this entity is not commandable")
        try:
            await self.client.async_post_addon_route(
                COMMAND_ROUTE_PATH, {"topic": topic, "value": value}
            )
        except CamStackError as err:
            raise HomeAssistantError(f"CamStack refused the command: {err}") from err
