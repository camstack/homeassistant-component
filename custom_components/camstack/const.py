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

# tRPC event category carrying `{deviceId, capName, slice}`.
EVENT_DEVICE_STATE_CHANGED: Final = "device.state-changed"

MANUFACTURER: Final = "CamStack"
