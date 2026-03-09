"""Constants for the CamStack integration."""

DOMAIN = "camstack"

CONF_MODE = "mode"
CONF_URL_BASE = "url_base"
CONF_SCRYPTED_URL = "scrypted_url"
CONF_SCRYPTED_USERNAME = "scrypted_username"
CONF_SCRYPTED_PASSWORD = "scrypted_password"
CONF_CAMERAS_TO_IMPORT = "cameras_to_import"
CONF_PANEL_TITLE = "panel_title"
CONF_PANEL_ICON = "panel_icon"

# Deployment modes
MODE_EMBEDDED = "embedded"
MODE_PROXY_EXTERNAL = "proxy_external"
MODE_PROXY_SCRYPTED = "proxy_scrypted"

# Default panel config
DEFAULT_PANEL_TITLE = "CamStack"
DEFAULT_PANEL_ICON = "mdi:cctv"

# Scrypted CamStack path (when using proxy_scrypted)
SCRYPTED_CAMSTACK_PATH = "/endpoint/@apocaliss92/scrypted-advanced-notifier/public/camstack"

# Panel
PANEL_URL = "/api/panel_custom/camstack"
PANEL_FILENAME = "camstack-panel.js"
PANEL_NAME = "camstack-panel"
