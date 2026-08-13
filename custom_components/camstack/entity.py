"""Shared base for every entity CamStack pushes.

The component is GENERIC. It builds entities out of the component set the hub
sends and knows nothing about cameras, zones or detection classes: all of that
stays on the camstack side, which is what lets the hub add an entity without
this component being released. Anything in here that special-cased a camera
would put the two shipping chains back out of step.
"""

from __future__ import annotations

from typing import Any

from homeassistant.const import EntityCategory
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.typing import UndefinedType

from .const import SIGNAL_LINK, SIGNAL_STATE, SIGNAL_STRUCTURE
from .push import CamStackPushHub, push_unique_id

_ENTITY_CATEGORIES: dict[str, EntityCategory] = {
    "config": EntityCategory.CONFIG,
    "diagnostic": EntityCategory.DIAGNOSTIC,
}

# What a write-only control carries. The hub's route ignores the value for a
# verb — it is the topic that names the method — and this is the payload its
# `button` form already declares, so one string covers both shapes.
VERB_PAYLOAD = "PRESS"


class CamStackPushEntity(Entity):
    """One Home Assistant entity backed by one pushed component."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self, hub: CamStackPushHub, device_key: str, component_key: str
    ) -> None:
        """Bind the entity to the component the hub announced."""
        self._hub = hub
        self._device_key = device_key
        self._component_key = component_key
        self._attr_unique_id = push_unique_id(
            device_key, component_key, hub.component(device_key, component_key)
        )

    @property
    def _component(self) -> dict[str, Any]:
        """Return this entity's component definition, live."""
        return self._hub.component(self._device_key, self._component_key)

    @property
    def _state_topic(self) -> str | None:
        """Return the topic carrying this entity's value.

        Fixed for the life of the entity: the hub builds every topic from the
        device key and the entity name, both of which are what the component
        key is derived from, so one cannot change without the other.
        """
        topic = self._component.get("state_topic")
        return topic if isinstance(topic, str) else None

    @property
    def _command_topic(self) -> str | None:
        """Return the topic commands for this entity are sent on."""
        topic = self._component.get("command_topic")
        return topic if isinstance(topic, str) else None

    @property
    def _value(self) -> str | None:
        """Return the last value pushed for this entity."""
        return self._hub.state(self._state_topic)

    @property
    def name(self) -> str | UndefinedType | None:
        """Return the name the hub gave this entity."""
        name = self._component.get("name")
        return name if isinstance(name, str) else None

    @property
    def icon(self) -> str | None:
        """Return the icon the hub chose, if any."""
        icon = self._component.get("icon")
        return icon if isinstance(icon, str) else None

    @property
    def entity_category(self) -> EntityCategory | None:
        """Return the category the hub assigned."""
        raw = self._component.get("entity_category")
        return _ENTITY_CATEGORIES.get(raw) if isinstance(raw, str) else None

    @property
    def entity_registry_enabled_default(self) -> bool:
        """Honour the hub's pressure valve.

        A camera carries ~73 entities. The hub marks everything an operator
        would not automate on as disabled, and overriding that here would put
        all of them in the state machine.
        """
        return self._component.get("enabled_by_default", True) is not False

    @property
    def available(self) -> bool:
        """Return whether camstack is still alive.

        The heartbeat is the ONE source of truth for that, deliberately: a
        second signal is a second thing that can disagree, and an entity that
        looks healthy while its feed is dead is worse than one that is
        plainly unavailable.
        """
        return self._hub.link_alive

    @property
    def device_info(self) -> DeviceInfo:
        """Attach the entity to the device the hub announced it under."""
        return self._hub.device_info(self._device_key)

    # --- native sub-controls ------------------------------------------------
    #
    # A native platform is ONE entity that owns several values: a cover has a
    # state, a position and a tilt, and Home Assistant expects them on one
    # entity rather than three. The hub sends them as a `controls` map, each
    # naming the topic the DEGRADED entity already published on — so a native
    # entity introduces no topic and no vocabulary of its own, and the value
    # plane is exactly the one that was there before.

    def _control(self, name: str) -> dict[str, Any]:
        """Return one declared sub-control, or an empty one.

        Empty is the ordinary case, not an error: the hub omits a control
        whose capability has no route (so it offers no command) and one whose
        device does not report the value at all. Every platform decides its
        supported features from which controls came back non-empty, which is
        what stops Home Assistant offering a slider the device has not got.
        """
        controls = self._component.get("controls")
        if not isinstance(controls, dict):
            return {}
        control = controls.get(name)
        return control if isinstance(control, dict) else {}

    def _control_topic(self, name: str, key: str) -> str | None:
        """Return one of a sub-control's topics."""
        topic = self._control(name).get(key)
        return topic if isinstance(topic, str) else None

    def _control_value(self, name: str) -> str | None:
        """Return the last value pushed for a sub-control."""
        return self._hub.state(self._control_topic(name, "state_topic"))

    def _can_command(self, name: str) -> bool:
        """Whether the hub declared a route for this sub-control."""
        return self._control_topic(name, "command_topic") is not None

    async def _async_command_control(
        self, name: str, value: str, *, optimistic: bool = True
    ) -> None:
        """Send one sub-control's command, and show the result.

        The optimistic write happens only AFTER the hub accepted it — a
        refusal raises and the entity does not move, which is the whole
        reason the hub refuses rather than approximating. Buttons and verbs
        pass `optimistic=False`: pressing `open` does not make a cover open,
        it makes it START opening, and the hub says when it did.
        """
        await self._hub.async_send_command(
            self._control_topic(name, "command_topic"), value
        )
        topic = self._control_topic(name, "state_topic")
        if optimistic and topic is not None:
            self._hub.states[topic] = value
            self.async_write_ha_state()

    async def _async_command(self, value: str, *, optimistic: bool = True) -> None:
        """Send the entity's OWN command, on the topic the hub named."""
        await self._hub.async_send_command(self._command_topic, value)
        topic = self._state_topic
        if optimistic and topic is not None:
            self._hub.states[topic] = value
            self.async_write_ha_state()

    def _has_verb(self, name: str) -> bool:
        """Whether this entity can perform a verb at all.

        Two shapes reach the same capability method and both are accepted:
        the hub may route the verb as its own write-only control
        (`cover_open`), or as a payload on the entity's own command topic.
        Which one arrives depends on the hub's route table, and a component
        that understood only one would offer half a cover on the other.
        """
        return self._can_command(name) or self._command_topic is not None

    async def _async_verb(self, name: str, payload: str) -> None:
        """Perform one verb, through whichever shape the hub declared.

        Never optimistic. Pressing `open` does not make a cover open — it
        makes it START opening, and the hub says when it did. Writing `open`
        into the state here would show a shutter as open while it is still
        moving, and every automation waiting on it would fire early.
        """
        if self._can_command(name):
            await self._async_command_control(name, VERB_PAYLOAD, optimistic=False)
            return
        await self._async_command(payload, optimistic=False)

    def _subscribed_topics(self) -> list[str]:
        """Return every topic that changes this entity's state.

        A native entity is fed by several: subscribing only to the primary
        would leave a cover's position frozen at whatever it held when Home
        Assistant started, with nothing in the log to say why.
        """
        topics = [] if self._state_topic is None else [self._state_topic]
        controls = self._component.get("controls")
        for control in controls.values() if isinstance(controls, dict) else []:
            topic = control.get("state_topic") if isinstance(control, dict) else None
            if isinstance(topic, str) and topic not in topics:
                topics.append(topic)
        return topics

    async def async_added_to_hass(self) -> None:
        """Subscribe to this entity's topics, and to the link's health."""
        await super().async_added_to_hass()
        for topic in self._subscribed_topics():
            self.async_on_remove(
                async_dispatcher_connect(
                    self.hass,
                    SIGNAL_STATE.format(entry_id=self._hub.entry.entry_id, topic=topic),
                    self._async_pushed,
                )
            )
        for signal in (
            SIGNAL_LINK.format(entry_id=self._hub.entry.entry_id),
            SIGNAL_STRUCTURE.format(entry_id=self._hub.entry.entry_id),
        ):
            self.async_on_remove(
                async_dispatcher_connect(self.hass, signal, self._async_pushed)
            )

    @callback
    def _async_pushed(self) -> None:
        """Re-render after a value, a structure change or a link change."""
        self.async_write_ha_state()


def as_bool(value: str | None, component: dict[str, Any]) -> bool | None:
    """Turn a pushed value into a boolean, or None when it is unknown.

    Every value on the wire is a STRING and the comparison is
    case-insensitive, matching the hub's own note that the component
    lowercases binary values verbatim. A value that is neither payload is
    `None` rather than `False`: "not the on payload" and "off" are different
    answers, and reporting the second for the first is how a sensor that
    stopped reporting looks like one reporting all-clear.
    """
    if value is None:
        return None
    payload_on = str(component.get("payload_on", "true")).lower()
    payload_off = str(component.get("payload_off", "false")).lower()
    lowered = value.lower()
    if lowered == payload_on:
        return True
    if lowered == payload_off:
        return False
    return None


def as_float(value: str | None) -> float | None:
    """Turn a pushed value into a number, or None when it will not convert.

    `None` rather than `0`: "never observed" and "zero" are different
    answers, and a cover reporting the second for the first looks closed
    when nobody knows where it is.
    """
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def as_int(value: str | None) -> int | None:
    """As :func:`as_float`, for the positions Home Assistant wants as ints."""
    number = as_float(value)
    return None if number is None else round(number)
