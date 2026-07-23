"""Constants for Adaptive Services ParkPower."""

DOMAIN = "pow_reporting"

CONF_DASHBOARD_NAME = "dashboard_name"
CONF_LOGO_URL = "logo_url"
CONF_SIDEBAR_ICON = "sidebar_icon"
CONF_URL_PATH = "url_path"
CONF_ENTITY_FILTER = "entity_filter"
CONF_ENABLE_CUSTOMER_PORTAL = "enable_customer_portal"
CONF_PORTAL_NAME = "portal_name"
CONF_PORTAL_URL_PATH = "portal_url_path"
CONF_PUBLIC_PORT = "public_port"
CONF_CHARGING_START_WATTS = "charging_start_watts"
CONF_CHARGING_START_DELAY_SECONDS = "charging_start_delay_seconds"
CONF_CHARGING_STOP_WATTS = "charging_stop_watts"
CONF_CHARGING_STOP_DELAY_MINUTES = "charging_stop_delay_minutes"
CONF_MAXIMUM_SESSION_HOURS = "maximum_session_hours"
CONF_METER_STALE_MINUTES = "meter_stale_minutes"
CONF_OFFLINE_TIMEOUT_MINUTES = "offline_timeout_minutes"

DEFAULT_DASHBOARD_NAME = "Adaptive Services ParkPower"
DEFAULT_PORTAL_NAME = "ParkPower Portal"
DEFAULT_SIDEBAR_ICON = "mdi:chart-line"
DEFAULT_URL_PATH = "parkpower"
DEFAULT_PORTAL_URL_PATH = "parkpower-portal"
DEFAULT_PUBLIC_PORT = 4177
DEFAULT_ENTITY_FILTER = "sonoff,pow,esphome"
DEFAULT_LOGO_URL = "/api/parkpower-public/logo.png"

PANEL_JS_PATH = f"/api/{DOMAIN}/static/pow-reporting-panel.js"
PANEL_JS_URL = f"{PANEL_JS_PATH}?v=0.9.1"

DEFAULT_SESSION_THRESHOLDS = {
    CONF_CHARGING_START_WATTS: 50.0,
    CONF_CHARGING_START_DELAY_SECONDS: 30,
    CONF_CHARGING_STOP_WATTS: 20.0,
    CONF_CHARGING_STOP_DELAY_MINUTES: 10,
    CONF_MAXIMUM_SESSION_HOURS: 24,
    CONF_METER_STALE_MINUTES: 15,
    CONF_OFFLINE_TIMEOUT_MINUTES: 5,
}
