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

# The event stream is the live path; this reconcile exists because events are
# telemetry and may be dropped. It is a correctness backstop, not the feed.
RECONCILE_INTERVAL: Final = timedelta(seconds=60)

# Capability slice names the integration reads. Kept as constants because they
# are wire identifiers, not free text.
CAP_DEVICE_STATUS: Final = "device-status"
CAP_MOTION: Final = "motion"
CAP_DOORBELL: Final = "doorbell"
CAP_BATTERY: Final = "battery"
CAP_ZONE_ANALYTICS: Final = "zone-analytics"
CAP_AUDIO_METRICS: Final = "audio-metrics"
CAP_PRIVACY_MASK: Final = "privacy-mask"
CAP_CAMERA_STREAMS: Final = "camera-streams"

# The hub addon that owns per-device Home Assistant export membership. It is
# a `device-export` provider, exactly like the Alexa and HomeKit exporters,
# and its exposed list is the ONLY per-device "export this to Home Assistant"
# authority the hub has. The id says `mqtt` for historical reasons — the addon
# also drives MQTT discovery — but the list names DEVICES, not topics, and
# applies with or without a broker.
HA_EXPORT_ADDON_ID: Final = "export-ha-mqtt"

# tRPC event category carrying `{deviceId, capName, slice}`.
EVENT_DEVICE_STATE_CHANGED: Final = "device.state-changed"

MANUFACTURER: Final = "CamStack"
