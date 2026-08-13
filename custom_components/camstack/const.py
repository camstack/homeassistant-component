"""Constants for the CamStack integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "camstack"

# Bumped when the shape of `ConfigEntry.data` changes. Version 1 covers BOTH
# pre-merge components: the panel-only one stored a single `url_base`, and the
# entity one stored host/port/credentials. `async_migrate_entry` handles both.
CONFIG_ENTRY_VERSION: Final = 2

CONF_VERIFY_SSL: Final = "verify_ssl"

# Written by `config_entry_oauth2_flow`, not by this component. Named here
# because two other modules read them and a literal in three files is how the
# key that HA actually uses and the key we look for stop being the same one.
CONF_AUTH_IMPLEMENTATION: Final = "auth_implementation"
CONF_TOKEN_BUNDLE: Final = "token"

# The hub registers this integration as a PUBLIC OAuth client. There is no
# secret: the source is published, and PKCE is what binds a code to the
# instance that requested it. See the hub's
# `docs/design/2026-08-08-ha-oauth-onboarding.md`.
OAUTH_INTEGRATION_ID: Final = "homeassistant"
OAUTH_CLIENT_ID: Final = "homeassistant"
OAUTH_AUTHORIZE_PATH: Final = "/api/oauth2/authorize"
OAUTH_TOKEN_PATH: Final = "/api/oauth2/token"
# Absent (404) on every hub released before OAuth onboarding shipped. That is
# the whole point of asking: it tells an OLD hub from a REFUSING one.
OAUTH_DISCOVERY_PATH: Final = "/api/oauth2/integrations"

# Panel/card settings. `panel_url` is written by the migration and never asked
# for: the address comes from `host` and `port`, and asking twice is how the
# panel and the entities end up pointing at different hubs.
CONF_PANEL_ENABLED: Final = "panel_enabled"
CONF_PANEL_TITLE: Final = "panel_title"
CONF_PANEL_ICON: Final = "panel_icon"
CONF_PANEL_URL: Final = "panel_url"

# The only key the panel-only component ever stored.
LEGACY_CONF_URL_BASE: Final = "url_base"

DEFAULT_PORT: Final = 4443
DEFAULT_VERIFY_SSL: Final = False
DEFAULT_PANEL_ENABLED: Final = True
DEFAULT_PANEL_TITLE: Final = "CamStack"
DEFAULT_PANEL_ICON: Final = "mdi:cctv"

# Frontend assets, all served by this integration out of `frontend/`.
STATIC_URL_PATH: Final = "/camstack-frontend"
PANEL_URL_PATH: Final = "camstack"
PANEL_COMPONENT_NAME: Final = "camstack-panel"
PANEL_FILENAME: Final = "camstack-panel.js"
CARD_FILENAME: Final = "camstack-grid-card.js"
CONFIG_VIEW_URL: Final = "/api/camstack/config"

MANUFACTURER: Final = "CamStack"

# --- The push transport ----------------------------------------------------
#
# CamStack PUSHES. Nothing in Home Assistant polls the hub for a value. The
# hub's `homeassistant-export` addon POSTs to the view below, behind Home
# Assistant's own authentication, and every constant here is matched verbatim
# against `packages/addon-provider-homeassistant/src/ha-export/` on the hub.

# Where `push-client.ts` sends. It is a wire format: change it and the hub
# POSTs into a 404 forever, with nothing in Home Assistant's log to say so.
PUSH_VIEW_URL: Final = "/api/camstack/push"

# Where the hub asks what this integration can BUILD, before it decides how
# to shape a device. See `version_view.py`: the hub reads the platform list
# and falls back to the degraded projection for anything absent, so an
# operator who has not updated through HACS keeps working entities.
#
# Absent (404) on every version before the native platforms shipped, which is
# how an OLD integration is told from an unreachable one.
VERSION_VIEW_URL: Final = "/api/camstack/version"

# Commands travel the other way, onto the export addon's `addon-routes`
# surface: POST {topic, value}, Bearer-authenticated with the hub token this
# entry already holds.
COMMAND_ROUTE_PATH: Final = "/addon/homeassistant-export/command"

# The `device-export` provider that owns Home Assistant export membership.
#
# The pin is MANDATORY. `device-export` is a COLLECTION capability, so an
# unpinned read merges every export provider — Alexa's devices and HomeKit's
# would arrive as ours. An unresolvable id is REFUSED by the hub with the
# valid ids enumerated (never answered from another provider), which is why a
# wrong pin here fails loudly instead of importing the wrong set.
HA_EXPORT_ADDON_ID: Final = "homeassistant-export"

# Where an operator turns a device on for Home Assistant. Quoted verbatim in
# the warning logged when nothing is exported, because "no entities" and
# "broken integration" look identical from Home Assistant's side.
EXPORT_PANEL_HINT: Final = (
    "CamStack admin UI -> the device -> Export tab -> Home Assistant Export"
)

# `deviceKeyFor()` on the hub. The HA device identifier is the hub's
# `device_id`, so a camera's pushed entities and its camera entity land on the
# SAME Home Assistant device instead of two that look like duplicates.
DEVICE_KEY_PREFIX: Final = "camstack-"

# The hub's heartbeat cadence (`HEARTBEAT_INTERVAL_MS`). It doubles as the
# availability signal, and it is the ONLY one: there is deliberately no second
# source that could disagree about whether camstack is alive.
HEARTBEAT_INTERVAL: Final = timedelta(seconds=30)
# Three missed heartbeats. Tight enough to notice, loose enough to survive one
# slow reconcile on a busy hub.
HEARTBEAT_TIMEOUT: Final = timedelta(seconds=95)
LINK_CHECK_INTERVAL: Final = timedelta(seconds=15)

# Above `MAX_VALUE_CHARS` the hub collapses a value to this signal. Images are
# always a signed URL, so this is the backstop for anything that grew
# unexpectedly — never the normal path for a picture.
IMAGE_SIGNAL_PREFIX: Final = "__image_updated__:"

# An image entity fetches bytes over HTTP from a signed, expiring URL. The URL
# changes per detection, so this floor is what stops a busy camera turning
# every track into a round trip.
MIN_IMAGE_FETCH_INTERVAL: Final = timedelta(seconds=10)

# Structure and last values survive a Home Assistant restart here. Without it
# every entity would be "restored" and blank until the hub's next reconcile,
# which is up to five minutes away.
STORAGE_KEY: Final = "camstack.push"
STORAGE_VERSION: Final = 1
STORAGE_SAVE_DELAY: Final = 30

# Membership is structure, not value, so it is read on a slow timer rather
# than polled: the hub announces entities, and this only decides which cameras
# get a `camera` entity (the one thing the push catalog does not carry).
MEMBERSHIP_INTERVAL: Final = timedelta(minutes=5)

# Per-entry dispatcher signals. One per topic, so a state update wakes exactly
# the entity that owns it — across ~880 entities the alternative is a storm.
SIGNAL_ADD_ENTITIES: Final = "camstack_add_{entry_id}_{platform}"
SIGNAL_STATE: Final = "camstack_state_{entry_id}_{topic}"
SIGNAL_STRUCTURE: Final = "camstack_structure_{entry_id}"
SIGNAL_LINK: Final = "camstack_link_{entry_id}"
