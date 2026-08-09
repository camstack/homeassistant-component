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

    async def async_added_to_hass(self) -> None:
        """Subscribe to this entity's topic, and to the link's health."""
        await super().async_added_to_hass()
        topic = self._state_topic
        if topic is not None:
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
