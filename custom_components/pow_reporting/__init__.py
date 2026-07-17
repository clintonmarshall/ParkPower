"""Adaptive Services ParkPower integration."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import voluptuous as vol
from aiohttp import web
from homeassistant.components import frontend, panel_custom, websocket_api
from homeassistant.components.http import HomeAssistantView, StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import floor_registry as fr
from homeassistant.helpers import label_registry as lr
from homeassistant.helpers.storage import Store
import homeassistant.helpers.config_validation as cv

from .const import (
    CONF_CHARGING_START_DELAY_SECONDS,
    CONF_CHARGING_START_WATTS,
    CONF_DASHBOARD_NAME,
    CONF_ENTITY_FILTER,
    CONF_ENABLE_CUSTOMER_PORTAL,
    CONF_LOGO_URL,
    CONF_MAXIMUM_SESSION_HOURS,
    CONF_METER_STALE_MINUTES,
    CONF_OFFLINE_TIMEOUT_MINUTES,
    CONF_PORTAL_NAME,
    CONF_PORTAL_URL_PATH,
    CONF_PUBLIC_PORT,
    CONF_SIDEBAR_ICON,
    CONF_CHARGING_STOP_DELAY_MINUTES,
    CONF_CHARGING_STOP_WATTS,
    CONF_URL_PATH,
    DEFAULT_SESSION_THRESHOLDS,
    DEFAULT_DASHBOARD_NAME,
    DEFAULT_ENTITY_FILTER,
    DEFAULT_PORTAL_NAME,
    DEFAULT_PORTAL_URL_PATH,
    DEFAULT_PUBLIC_PORT,
    DEFAULT_SIDEBAR_ICON,
    DEFAULT_URL_PATH,
    DOMAIN,
    PANEL_JS_URL,
)
from .coordinator import PowReportingCoordinator
from .reporting import build_management_report, default_filter_period
from .tenant_billing import TenantBillingManager
from .websocket import (
    async_load_hierarchy_manager,
    async_load_records_manager,
    async_load_session_manager,
    async_register_websocket_commands as async_register_session_websocket_commands,
    async_save_session_manager,
)

PLATFORMS: list[Platform] = []
STORAGE_KEY = f"{DOMAIN}.outlet_log"
BILLING_STORAGE_KEY = f"{DOMAIN}.billing"
STORAGE_VERSION = 1
MAX_LOG_ROWS = 2000
MAX_SESSION_ROWS = 10000
DEFAULT_ENERGY_RATE = 0.32
DEFAULT_STALE_AFTER_MINUTES = DEFAULT_SESSION_THRESHOLDS[CONF_METER_STALE_MINUTES]

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Optional(CONF_DASHBOARD_NAME, default=DEFAULT_DASHBOARD_NAME): cv.string,
                vol.Optional(CONF_URL_PATH, default=DEFAULT_URL_PATH): cv.string,
                vol.Optional(CONF_SIDEBAR_ICON, default=DEFAULT_SIDEBAR_ICON): cv.icon,
                vol.Optional(CONF_LOGO_URL, default=""): cv.string,
                vol.Optional(
                    CONF_ENTITY_FILTER,
                    default=DEFAULT_ENTITY_FILTER,
                ): cv.string,
                vol.Optional(CONF_ENABLE_CUSTOMER_PORTAL, default=True): cv.boolean,
                vol.Optional(CONF_PORTAL_NAME, default=DEFAULT_PORTAL_NAME): cv.string,
                vol.Optional(CONF_PORTAL_URL_PATH, default=DEFAULT_PORTAL_URL_PATH): cv.string,
                vol.Optional(CONF_PUBLIC_PORT, default=DEFAULT_PUBLIC_PORT): cv.port,
                vol.Optional(
                    CONF_CHARGING_START_WATTS,
                    default=DEFAULT_SESSION_THRESHOLDS[CONF_CHARGING_START_WATTS],
                ): vol.Coerce(float),
                vol.Optional(
                    CONF_CHARGING_START_DELAY_SECONDS,
                    default=DEFAULT_SESSION_THRESHOLDS[CONF_CHARGING_START_DELAY_SECONDS],
                ): cv.positive_int,
                vol.Optional(
                    CONF_CHARGING_STOP_WATTS,
                    default=DEFAULT_SESSION_THRESHOLDS[CONF_CHARGING_STOP_WATTS],
                ): vol.Coerce(float),
                vol.Optional(
                    CONF_CHARGING_STOP_DELAY_MINUTES,
                    default=DEFAULT_SESSION_THRESHOLDS[CONF_CHARGING_STOP_DELAY_MINUTES],
                ): cv.positive_int,
                vol.Optional(
                    CONF_MAXIMUM_SESSION_HOURS,
                    default=DEFAULT_SESSION_THRESHOLDS[CONF_MAXIMUM_SESSION_HOURS],
                ): cv.positive_int,
                vol.Optional(
                    CONF_METER_STALE_MINUTES,
                    default=DEFAULT_SESSION_THRESHOLDS[CONF_METER_STALE_MINUTES],
                ): cv.positive_int,
                vol.Optional(
                    CONF_OFFLINE_TIMEOUT_MINUTES,
                    default=DEFAULT_SESSION_THRESHOLDS[CONF_OFFLINE_TIMEOUT_MINUTES],
                ): cv.positive_int,
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Set up the dashboard from YAML."""
    _async_register_websocket_commands(hass)

    yaml_config = config.get(DOMAIN)
    if yaml_config is None:
        await _async_register_public_http(hass, {})
        return True

    await _async_register_public_http(hass, yaml_config)
    await _async_register_dashboard(hass, yaml_config)
    await _async_sync_session_thresholds(hass, yaml_config)
    await _async_start_coordinator(hass, yaml_config)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the reporting dashboard panel."""
    _async_register_websocket_commands(hass)

    options = {**entry.data, **entry.options}
    await _async_register_public_http(hass, options)
    await _async_register_dashboard(hass, options)
    await _async_sync_session_thresholds(hass, options)
    await _async_start_coordinator(hass, options)

    entry.async_on_unload(
        entry.add_update_listener(_async_update_listener)
    )
    entry.async_on_unload(
        lambda: frontend.async_remove_panel(
            hass,
            options.get(CONF_URL_PATH, DEFAULT_URL_PATH),
        )
    )
    entry.async_on_unload(
        lambda: frontend.async_remove_panel(
            hass,
            options.get(CONF_PORTAL_URL_PATH, DEFAULT_PORTAL_URL_PATH),
        )
    )
    entry.async_on_unload(lambda: _async_stop_coordinator(hass))
    return True


async def _async_register_public_http(
    hass: HomeAssistant,
    options: dict[str, Any],
) -> None:
    """Register unauthenticated public portal views served by Home Assistant."""
    data = hass.data.setdefault(DOMAIN, {})
    data["public_options"] = options
    if not data.get("public_http_registered"):
        hass.http.register_view(ParkPowerPublicPortalView())
        hass.http.register_view(ParkPowerPublicSettingsView())
        hass.http.register_view(ParkPowerPublicSummaryView())
        hass.http.register_view(ParkPowerPublicOutletsView())
        hass.http.register_view(ParkPowerPublicBillingView())
        hass.http.register_view(ParkPowerPublicControlView())
        hass.http.register_view(ParkPowerPublicAllOffView())
        data["public_http_registered"] = True

    await _async_start_public_portal_server(hass, options)


async def _async_start_public_portal_server(
    hass: HomeAssistant,
    options: dict[str, Any],
) -> None:
    """Start the standalone public portal listener on its own TCP port."""
    data = hass.data.setdefault(DOMAIN, {})
    port = int(options.get(CONF_PUBLIC_PORT, DEFAULT_PUBLIC_PORT) or DEFAULT_PUBLIC_PORT)
    if data.get("public_runner") and data.get("public_port") == port:
        return

    runner = data.pop("public_runner", None)
    if runner is not None:
        await runner.cleanup()

    app = web.Application()
    app["hass"] = hass
    app.router.add_get("/", _public_portal_http_handler)
    app.router.add_get("/parkpower-public", _public_portal_http_handler)
    app.router.add_get("/api/parkpower-public/settings", _public_settings_http_handler)
    app.router.add_get("/api/parkpower-public/summary", _public_summary_http_handler)
    app.router.add_get("/api/parkpower-public/outlets", _public_outlets_http_handler)
    app.router.add_get("/api/parkpower-public/billing", _public_billing_http_handler)
    app.router.add_post("/api/parkpower-public/outlets/{entity_id}/control", _public_control_http_handler)
    app.router.add_post("/api/parkpower-public/all-off", _public_all_off_http_handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    data["public_runner"] = runner
    data["public_port"] = port
    data["public_url"] = f"http://0.0.0.0:{port}"


async def _async_stop_public_portal_server(hass: HomeAssistant) -> None:
    """Stop the standalone public portal listener."""
    data = hass.data.setdefault(DOMAIN, {})
    runner = data.pop("public_runner", None)
    data.pop("public_port", None)
    data.pop("public_url", None)
    if runner is not None:
        await runner.cleanup()


async def _async_register_dashboard(
    hass: HomeAssistant,
    options: dict[str, Any],
) -> None:
    """Register the reporting dashboard panel."""
    url_path = options.get(CONF_URL_PATH, DEFAULT_URL_PATH)
    sidebar_title = options.get(CONF_DASHBOARD_NAME, DEFAULT_DASHBOARD_NAME)
    sidebar_icon = options.get(CONF_SIDEBAR_ICON, DEFAULT_SIDEBAR_ICON)
    entity_filter = options.get(CONF_ENTITY_FILTER, DEFAULT_ENTITY_FILTER)
    logo_url = options.get(CONF_LOGO_URL, "")

    panel_path = Path(__file__).parent / "frontend" / "pow-reporting-panel.js"
    await _async_register_static_path_once(hass, panel_path)

    await panel_custom.async_register_panel(
        hass,
        webcomponent_name="pow-reporting-panel",
        frontend_url_path=url_path,
        sidebar_title=sidebar_title,
        sidebar_icon=sidebar_icon,
        require_admin=False,
        module_url=PANEL_JS_URL,
        config={
            "name": sidebar_title,
            "logo_url": logo_url,
            "entity_filter": entity_filter,
            "mode": "admin",
        },
    )

    if options.get(CONF_ENABLE_CUSTOMER_PORTAL, True):
        portal_url_path = options.get(CONF_PORTAL_URL_PATH, DEFAULT_PORTAL_URL_PATH)
        portal_title = options.get(CONF_PORTAL_NAME, DEFAULT_PORTAL_NAME)
        await panel_custom.async_register_panel(
            hass,
            webcomponent_name="pow-reporting-panel",
            frontend_url_path=portal_url_path,
            sidebar_title=portal_title,
            sidebar_icon="mdi:ev-station",
            require_admin=False,
            module_url=PANEL_JS_URL,
            config={
                "name": portal_title,
                "logo_url": logo_url,
                "entity_filter": entity_filter,
                "mode": "portal",
            },
        )


def _async_register_websocket_commands(hass: HomeAssistant) -> None:
    """Register websocket commands once."""
    if hass.data.setdefault(DOMAIN, {}).get("websocket_registered"):
        return

    websocket_api.async_register_command(hass, _websocket_get_outlet_log)
    websocket_api.async_register_command(hass, _websocket_get_billing_report)
    websocket_api.async_register_command(hass, _websocket_get_management_report)
    websocket_api.async_register_command(hass, _websocket_save_billing_settings)
    websocket_api.async_register_command(hass, _websocket_create_tenant_statement)
    websocket_api.async_register_command(hass, _websocket_send_tenant_statement)
    websocket_api.async_register_command(hass, _websocket_control_outlet)
    websocket_api.async_register_command(hass, _websocket_all_off)
    websocket_api.async_register_command(hass, _websocket_auto_name_entities)
    async_register_session_websocket_commands(hass)
    hass.data[DOMAIN]["websocket_registered"] = True


async def _async_register_static_path_once(
    hass: HomeAssistant,
    panel_path: Path,
) -> None:
    """Register static frontend assets once per Home Assistant runtime."""
    data = hass.data.setdefault(DOMAIN, {})
    if data.get("static_path_registered"):
        return
    try:
        await hass.http.async_register_static_paths(
            [
                StaticPathConfig(
                    PANEL_JS_URL,
                    str(panel_path),
                    cache_headers=False,
                )
            ]
        )
    except RuntimeError as err:
        if "already registered" not in str(err):
            raise
    data["static_path_registered"] = True


async def _async_load_log(hass: HomeAssistant) -> dict[str, Any]:
    """Load outlet audit log data."""
    store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
    data = await store.async_load()
    if not isinstance(data, dict):
        return {"events": [], "active_references": {}}
    data.setdefault("events", [])
    data.setdefault("active_references", {})
    return data


async def _async_save_log(hass: HomeAssistant, data: dict[str, Any]) -> None:
    """Save outlet audit log data."""
    data["events"] = data.get("events", [])[-MAX_LOG_ROWS:]
    store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
    await store.async_save(data)


async def _async_log_outlet_event(
    hass: HomeAssistant,
    *,
    switch_entity_id: str,
    action: str,
    reference: str,
    outlet_name: str,
    success: bool,
    error: str = "",
) -> dict[str, Any]:
    """Append an outlet control event."""
    data = await _async_load_log(hass)
    event = {
        "time": datetime.now().astimezone().isoformat(timespec="seconds"),
        "switch_entity_id": switch_entity_id,
        "outlet_name": outlet_name,
        "action": action,
        "reference": reference,
        "success": success,
        "error": error,
    }
    data["events"].append(event)

    if success and action == "turn_on":
        data["active_references"][switch_entity_id] = reference
    elif success and action == "turn_off":
        data["active_references"].pop(switch_entity_id, None)

    await _async_save_log(hass, data)
    return event


async def _async_load_billing(hass: HomeAssistant) -> dict[str, Any]:
    """Load persisted billing/session data."""
    store = Store(hass, STORAGE_VERSION, BILLING_STORAGE_KEY)
    data = await store.async_load()
    if not isinstance(data, dict):
        data = {}
    settings = data.get("settings")
    if not isinstance(settings, dict):
        settings = {}
    settings.setdefault("energy_rate", DEFAULT_ENERGY_RATE)
    settings.setdefault("currency", "AUD")
    settings.setdefault("notify_service", "")
    settings.setdefault("sender_name", "ParkPower")
    active = data.get("active")
    if not isinstance(active, dict):
        active = {}
    completed = data.get("completed")
    if not isinstance(completed, list):
        completed = []
    statements = data.get("statements")
    if not isinstance(statements, list):
        statements = []
    return {
        "settings": settings,
        "active": active,
        "completed": completed,
        "statements": statements,
    }


async def _async_save_billing(hass: HomeAssistant, data: dict[str, Any]) -> None:
    """Persist billing/session data."""
    data["completed"] = data.get("completed", [])[-MAX_SESSION_ROWS:]
    data["statements"] = data.get("statements", [])[-2000:]
    store = Store(hass, STORAGE_VERSION, BILLING_STORAGE_KEY)
    await store.async_save(data)


def _normalise_energy_kwh(state: Any) -> float | None:
    """Return an energy state as kWh."""
    if state is None:
        return None
    try:
        value = float(state.state)
    except (TypeError, ValueError):
        return None
    unit = state.attributes.get("unit_of_measurement")
    if unit == "Wh":
        return value / 1000
    return value


def _find_device_energy_state(hass: HomeAssistant, switch_entity_id: str) -> Any:
    """Find the most useful energy sensor for a switch's device."""
    entity_registry = er.async_get(hass)
    switch_entry = entity_registry.async_get(switch_entity_id)
    device_id = getattr(switch_entry, "device_id", None)
    if not device_id:
        return None

    candidates = []
    for entry in getattr(entity_registry, "entities", {}).values():
        if getattr(entry, "device_id", None) != device_id or not entry.entity_id.startswith("sensor."):
            continue
        state = hass.states.get(entry.entity_id)
        if state is None:
            continue
        unit = state.attributes.get("unit_of_measurement")
        device_class = state.attributes.get("device_class")
        if device_class == "energy" or unit in {"Wh", "kWh"}:
            text = " ".join(
                str(item or "")
                for item in [
                    entry.entity_id,
                    state.attributes.get("friendly_name"),
                    getattr(entry, "original_name", None),
                ]
            ).lower()
            score = 10
            if "daily" in text:
                score += 10
            if "total" in text and "daily" not in text:
                score -= 2
            candidates.append((score, state))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]


def _session_cost(energy_kwh: float | None, rate: float) -> float | None:
    """Calculate session cost when energy is available."""
    if energy_kwh is None:
        return None
    return round(energy_kwh * rate, 4)


async def _async_record_session_event(
    hass: HomeAssistant,
    *,
    switch_entity_id: str,
    action: str,
    reference: str,
    outlet_name: str,
    event: dict[str, Any],
    customer_id: str = "",
) -> None:
    """Start or complete a persisted billing session."""
    if not event.get("success"):
        return

    data = await _async_load_billing(hass)
    settings = data["settings"]
    energy_state = _find_device_energy_state(hass, switch_entity_id)
    energy_entity_id = energy_state.entity_id if energy_state is not None else ""
    energy_kwh = _normalise_energy_kwh(energy_state)
    now = event.get("time") or datetime.now().astimezone().isoformat(timespec="seconds")
    records = await async_load_records_manager(hass)
    customer = next((row for row in records.customers if row.id == customer_id), None)
    if customer is None:
        customer = records.customer_for_outlet(switch_entity_id)
    group = next(
        (row for row in records.user_groups if customer and row.id == customer.user_group),
        None,
    )
    base_rate = float(
        customer.billing_rate_per_kwh
        if customer and customer.billing_rate_per_kwh
        else settings.get("energy_rate", DEFAULT_ENERGY_RATE)
    )
    discount = min(100.0, max(0.0, group.discount_percentage if group else 0.0))
    session_rate = 0.0 if group and group.free_charging else base_rate * (1 - discount / 100)
    session_currency = customer.billing_currency if customer else settings.get("currency", "AUD")
    resolved_reference = reference
    if not resolved_reference and customer is not None:
        resolved_reference = customer.billing_reference or customer.display_name

    if action == "turn_on":
        outlet_health = await _outlet_health_for_switch(hass, switch_entity_id)
        if outlet_health and not _can_start_session_from_health(outlet_health):
            return
        data["active"][switch_entity_id] = {
            "session_id": uuid4().hex,
            "switch_entity_id": switch_entity_id,
            "outlet_name": outlet_name,
            "reference": resolved_reference,
            "customer_id": customer.id if customer else "",
            "start_time": now,
            "start_energy_kwh": energy_kwh,
            "energy_entity_id": energy_entity_id,
            "rate": session_rate,
            "currency": session_currency,
            "billing_status": "draft",
        }
    elif action == "turn_off":
        active = data["active"].pop(switch_entity_id, None)
        if active is not None:
            start_time = datetime.fromisoformat(active["start_time"])
            end_time = datetime.fromisoformat(now)
            start_energy = active.get("start_energy_kwh")
            energy_used = (
                max(0, energy_kwh - start_energy)
                if energy_kwh is not None and isinstance(start_energy, (int, float))
                else None
            )
            stored_rate = active.get("rate")
            rate = float(
                stored_rate
                if stored_rate is not None
                else settings.get("energy_rate", DEFAULT_ENERGY_RATE)
            )
            data["completed"].append(
                {
                    **active,
                    "reference": reference or active.get("reference", ""),
                    "outlet_name": outlet_name or active.get("outlet_name", switch_entity_id),
                    "end_time": now,
                    "end_energy_kwh": energy_kwh,
                    "duration_seconds": max(0, round((end_time - start_time).total_seconds())),
                    "energy_kwh": round(energy_used, 4) if energy_used is not None else None,
                    "cost": _session_cost(energy_used, rate),
                }
            )

    await _async_save_billing(hass, data)
    manager = await async_load_session_manager(hass)
    if action == "turn_on":
        manager.start_session(
            outlet_entity_id=switch_entity_id,
            outlet_display_name=outlet_name,
            customer_reference=resolved_reference,
            start_meter_reading=energy_kwh,
            metadata={
                "customer_id": customer.id if customer else "",
                "user_group": customer.user_group if customer else "",
            },
        )
    elif action == "turn_off":
        manager.complete_session(
            outlet_entity_id=switch_entity_id,
            reason=reference or "Relay switched off",
            end_meter_reading=energy_kwh,
        )
    await async_save_session_manager(hass, manager)


def _billing_report(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Return billing data with active durations and completed costs."""
    now = datetime.now().astimezone()
    settings = data["settings"]
    rate = float(settings.get("energy_rate", DEFAULT_ENERGY_RATE) or DEFAULT_ENERGY_RATE)
    active = []
    for session in data["active"].values():
        start_time = datetime.fromisoformat(session["start_time"])
        current_energy = _normalise_energy_kwh(hass.states.get(session.get("energy_entity_id", "")))
        state = hass.states.get(session.get("energy_entity_id", ""))
        if state is not None and _state_is_stale(state, DEFAULT_STALE_AFTER_MINUTES):
            current_energy = None
        start_energy = session.get("start_energy_kwh")
        energy_used = (
            max(0, current_energy - start_energy)
            if current_energy is not None and isinstance(start_energy, (int, float))
            else None
        )
        stored_rate = session.get("rate")
        session_rate = float(stored_rate if stored_rate is not None else rate)
        active.append(
            {
                **session,
                "active": True,
                "duration_seconds": max(0, round((now - start_time).total_seconds())),
                "current_energy_kwh": current_energy,
                "energy_kwh": round(energy_used, 4) if energy_used is not None else None,
                "cost": _session_cost(energy_used, session_rate),
            }
        )
    completed = []
    for session in data["completed"]:
        stored_rate = session.get("rate")
        session_rate = float(stored_rate if stored_rate is not None else rate)
        energy_kwh = session.get("energy_kwh")
        completed.append(
            {
                **session,
                "active": False,
                "cost": session.get("cost") if session.get("cost") is not None else _session_cost(energy_kwh, session_rate),
            }
        )
    return {
        "settings": settings,
        "active": active,
        "completed": completed,
        "sessions": [*completed, *active],
        "statements": data.get("statements", []),
    }


def _public_options(hass: HomeAssistant) -> dict[str, Any]:
    """Return current public portal options."""
    return hass.data.setdefault(DOMAIN, {}).get("public_options", {})


def _normalise_power_w(state: Any) -> float | None:
    """Return a power state as watts."""
    if state is None:
        return None
    try:
        value = float(state.state)
    except (TypeError, ValueError):
        return None
    unit = state.attributes.get("unit_of_measurement")
    if unit == "kW":
        return value * 1000
    return value


def _state_name(state: Any, fallback: str) -> str:
    """Return a friendly name for a state."""
    return state.attributes.get("friendly_name", fallback) if state is not None else fallback


def _sensor_score(entity_id: str, state: Any, *, kind: str) -> int:
    """Return a score for selecting the best sensor for a public outlet."""
    text = " ".join(
        str(item or "")
        for item in [
            entity_id,
            getattr(state, "name", ""),
            state.attributes.get("friendly_name") if state is not None else "",
        ]
    ).lower()
    if kind == "power":
        return 0
    score = 0
    if "total" in text:
        score += 30
    if "cumulative" in text:
        score += 25
    if "lifetime" in text:
        score += 25
    if "import" in text:
        score += 10
    if "daily" in text:
        score -= 25
    if "today" in text:
        score -= 20
    return score


def _best_device_sensor(
    hass: HomeAssistant,
    entity_registry: Any,
    device_id: str | None,
    *,
    kind: str,
) -> tuple[str, Any] | tuple[None, None]:
    """Find the best power or energy sensor for a device."""
    if not device_id:
        return None, None
    candidates = []
    for entry in getattr(entity_registry, "entities", {}).values():
        entity_id = getattr(entry, "entity_id", "")
        if getattr(entry, "device_id", None) != device_id or not entity_id.startswith("sensor."):
            continue
        state = hass.states.get(entity_id)
        if state is None:
            continue
        unit = state.attributes.get("unit_of_measurement")
        device_class = state.attributes.get("device_class")
        if kind == "power" and not (device_class == "power" or unit in {"W", "kW"}):
            continue
        if kind == "energy" and not (device_class == "energy" or unit in {"Wh", "kWh"}):
            continue
        candidates.append((_sensor_score(entity_id, state, kind=kind), entity_id, state))
    if not candidates:
        return None, None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1], candidates[0][2]


def _mapped_or_best_sensor(
    hass: HomeAssistant,
    entity_registry: Any,
    device_id: str | None,
    mapped_entity_id: Any,
    *,
    kind: str,
) -> tuple[str | None, Any]:
    """Return an explicitly mapped sensor before falling back to device discovery."""
    mapped = str(mapped_entity_id or "").strip()
    if mapped:
        return mapped, hass.states.get(mapped)
    return _best_device_sensor(hass, entity_registry, device_id, kind=kind)


def _state_timestamp(state: Any) -> datetime | None:
    """Return the best timestamp available for a Home Assistant State."""
    if state is None:
        return None
    return getattr(state, "last_updated", None) or getattr(state, "last_changed", None)


def _iso_timestamp(value: datetime | None) -> str:
    """Return a JSON timestamp for optional datetimes."""
    return value.isoformat(timespec="seconds") if value else ""


def _state_is_stale(state: Any, stale_after_minutes: int | float) -> bool:
    """Return true when a state has not updated inside the stale window."""
    timestamp = _state_timestamp(state)
    if timestamp is None:
        return True
    return datetime.now().astimezone() - timestamp > timedelta(minutes=float(stale_after_minutes))


def _state_is_online(state: Any) -> bool | None:
    """Interpret common HA/MQTT online/offline states."""
    if state is None:
        return None
    value = str(state.state).strip().lower()
    if value in {"on", "online", "home", "connected", "true", "1"}:
        return True
    if value in {"off", "offline", "not_home", "disconnected", "false", "0", "unavailable", "unknown"}:
        return False
    return None


def _read_number_from_state(hass: HomeAssistant, entity_id: Any) -> float | None:
    """Read a numeric HA state without unit conversion."""
    state = hass.states.get(str(entity_id or ""))
    if state is None:
        return None
    try:
        return float(state.state)
    except (TypeError, ValueError):
        return None


def _source_type_for_entry(entity_entry: Any) -> str:
    """Infer outlet source type from the HA registry entry."""
    platform = str(getattr(entity_entry, "platform", "") or "").lower()
    if platform == "mqtt":
        return "mqtt"
    if platform == "esphome":
        return "esphome_native"
    return "manual"


def _outlet_connectivity(
    *,
    switch_state: Any,
    power_state: Any,
    energy_state: Any,
    availability_state: Any,
    rssi_state: Any,
    mapping: dict[str, Any],
    stale_after_minutes: int,
) -> dict[str, Any]:
    """Build a connectivity snapshot for one outlet."""
    availability = _state_is_online(availability_state)
    if availability is None:
        switch_value = str(getattr(switch_state, "state", "") or "").lower()
        availability = switch_value not in {"unavailable", "unknown", ""}
    manual_availability = str(mapping.get("outlet_availability") or "").strip().lower()
    if manual_availability in {"online", "offline", "available", "unavailable"}:
        availability = manual_availability in {"online", "available"}

    telemetry_states = [state for state in (power_state, energy_state) if state is not None]
    timestamps = [timestamp for timestamp in (_state_timestamp(state) for state in telemetry_states) if timestamp is not None]
    last_seen_dt = max(timestamps, default=None)
    if not last_seen_dt:
        last_seen_dt = _state_timestamp(switch_state)
    mapped_last_seen = str(mapping.get("outlet_last_seen") or "").strip()
    if mapped_last_seen:
        try:
            last_seen_dt = datetime.fromisoformat(mapped_last_seen)
        except ValueError:
            pass

    is_stale = False
    if telemetry_states:
        is_stale = all(_state_is_stale(state, stale_after_minutes) for state in telemetry_states)
    elif last_seen_dt is not None:
        is_stale = datetime.now().astimezone() - last_seen_dt > timedelta(minutes=stale_after_minutes)

    rssi = _normalise_power_w(rssi_state)
    if rssi is None and mapping.get("outlet_rssi") not in (None, ""):
        try:
            rssi = float(mapping["outlet_rssi"])
        except (TypeError, ValueError):
            rssi = None

    state = "online"
    if availability is False:
        state = "offline"
    elif is_stale:
        state = "stale"

    return {
        "state": state,
        "outlet_last_seen": _iso_timestamp(last_seen_dt),
        "outlet_rssi": rssi,
        "outlet_availability": "online" if availability else "offline",
        "is_stale": state == "stale",
    }


def _gateway_snapshot(hass: HomeAssistant, mapping: dict[str, Any]) -> dict[str, Any] | None:
    """Return gateway metadata and status discovered from mapped HA/MQTT entities."""
    gateway_id = str(mapping.get("gateway_id") or "").strip()
    gateway_name = str(mapping.get("gateway_name") or gateway_id).strip()
    if not gateway_id and not gateway_name:
        return None

    def entity(suffix: str) -> str:
        base = (gateway_id or gateway_name).lower().replace(" ", "_").replace("-", "_")
        return f"sensor.{base}_{suffix}"

    online_entity_id = str(mapping.get("gateway_online_entity_id") or "").strip()
    if not online_entity_id and (gateway_id or gateway_name):
        base = (gateway_id or gateway_name).lower().replace(" ", "_").replace("-", "_")
        online_entity_id = f"binary_sensor.{base}_online"
    uptime_state = hass.states.get(str(mapping.get("gateway_uptime_entity_id") or entity("uptime")))
    client_count = _read_number_from_state(hass, mapping.get("gateway_client_count_entity_id") or entity("client_count"))
    uplink_rssi = _read_number_from_state(hass, mapping.get("gateway_uplink_rssi_entity_id") or entity("uplink_rssi"))
    free_heap = _read_number_from_state(hass, mapping.get("gateway_free_heap_entity_id") or entity("free_heap"))
    online_state = hass.states.get(online_entity_id)
    online = _state_is_online(online_state)

    if client_count is None and mapping.get("gateway_client_count") not in (None, ""):
        try:
            client_count = float(mapping["gateway_client_count"])
        except (TypeError, ValueError):
            client_count = None
    if uplink_rssi is None and mapping.get("gateway_uplink_rssi") not in (None, ""):
        try:
            uplink_rssi = float(mapping["gateway_uplink_rssi"])
        except (TypeError, ValueError):
            uplink_rssi = None

    last_seen = _state_timestamp(online_state) or _state_timestamp(uptime_state)
    mapped_last_seen = str(mapping.get("gateway_last_seen") or "").strip()
    if mapped_last_seen:
        try:
            last_seen = datetime.fromisoformat(mapped_last_seen)
        except ValueError:
            pass

    status = str(mapping.get("gateway_status") or "").strip().lower()
    if online is False:
        status = "offline"
    elif online is True and not status:
        status = "online"
    elif not status:
        status = "unknown"

    return {
        "gateway_id": gateway_id,
        "gateway_name": gateway_name,
        "gateway_zone": mapping.get("gateway_zone", ""),
        "gateway_ip": mapping.get("gateway_ip", ""),
        "gateway_ssid": mapping.get("gateway_ssid", ""),
        "gateway_subnet": mapping.get("gateway_subnet", ""),
        "gateway_last_seen": _iso_timestamp(last_seen),
        "gateway_uplink_rssi": uplink_rssi,
        "gateway_client_count": client_count,
        "gateway_status": status,
        "gateway_uptime": uptime_state.state if uptime_state is not None else "",
        "gateway_free_heap": free_heap,
        "gateway_online_entity_id": online_entity_id,
    }


def _can_start_session_from_health(outlet: dict[str, Any]) -> bool:
    """Return true when outlet telemetry is fresh enough to start billing."""
    if outlet.get("connectivity_state") in {"offline", "stale", "gateway_unreachable"}:
        return False
    gateway = outlet.get("gateway") or {}
    return gateway.get("gateway_status") != "offline"


async def _outlet_health_for_switch(hass: HomeAssistant, switch_entity_id: str) -> dict[str, Any] | None:
    """Return the current public outlet snapshot for one switch."""
    outlets = await _public_outlet_data(hass)
    return next((outlet for outlet in outlets if outlet.get("id") == switch_entity_id), None)


async def _public_outlet_data(hass: HomeAssistant) -> list[dict[str, Any]]:
    """Return outlet data for the unauthenticated public portal."""
    options = _public_options(hass)
    entity_filter = options.get(CONF_ENTITY_FILTER, DEFAULT_ENTITY_FILTER)
    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)
    area_registry = ar.async_get(hass)
    floor_registry = fr.async_get(hass)
    label_registry = lr.async_get(hass)
    log_data = await _async_load_log(hass)
    billing = _billing_report(hass, await _async_load_billing(hass))
    active_sessions = {session["switch_entity_id"]: session for session in billing["active"]}
    settings = billing["settings"]
    hierarchy = await async_load_hierarchy_manager(hass)
    outlet_mappings = {
        record.get("switch_entity_id"): record
        for record in hierarchy.list_records(include_archived=False).get("outlet_mappings", [])
        if record.get("switch_entity_id")
    }

    outlets = []
    for state in hass.states.async_all("switch"):
        entity_id = state.entity_id
        entity_entry = entity_registry.async_get(entity_id)
        mapping = outlet_mappings.get(entity_id, {})
        if not mapping and not _is_managed_switch(entity_id, entity_entry, state, entity_filter):
            continue
        device_id = getattr(entity_entry, "device_id", None) if entity_entry is not None else None
        device_entry = device_registry.async_get(device_id) if device_id else None
        area_entry = _area_for_entity(entity_entry, device_entry, area_registry)
        floor_entry = _floor_for_area(area_entry, floor_registry)
        labels = _label_entries(label_registry, list(getattr(entity_entry, "labels", []) or []))
        power_entity_id, power_state = _mapped_or_best_sensor(
            hass,
            entity_registry,
            device_id,
            mapping.get("power_entity_id"),
            kind="power",
        )
        energy_entity_id, energy_state = _mapped_or_best_sensor(
            hass,
            entity_registry,
            device_id,
            mapping.get("energy_entity_id"),
            kind="energy",
        )
        availability_state = hass.states.get(str(mapping.get("availability_entity_id") or ""))
        rssi_state = hass.states.get(str(mapping.get("rssi_entity_id") or ""))
        energy_kwh = _normalise_energy_kwh(energy_state)
        stale_minutes = int(float(mapping.get("stale_after_minutes") or DEFAULT_STALE_AFTER_MINUTES))
        connectivity = _outlet_connectivity(
            switch_state=state,
            power_state=power_state,
            energy_state=energy_state,
            availability_state=availability_state,
            rssi_state=rssi_state,
            mapping=mapping,
            stale_after_minutes=stale_minutes,
        )
        gateway = _gateway_snapshot(hass, mapping)
        if gateway and gateway.get("gateway_status") == "offline":
            connectivity["state"] = "gateway_unreachable"
            connectivity["is_stale"] = True
        if connectivity["is_stale"]:
            energy_kwh = None
        active_session = active_sessions.get(entity_id)
        reference = (
            log_data.get("active_references", {}).get(entity_id)
            or (active_session or {}).get("reference")
            or ""
        )
        rate = float(settings.get("energy_rate", DEFAULT_ENERGY_RATE) or DEFAULT_ENERGY_RATE)
        outlets.append(
            {
                "id": entity_id,
                "name": _state_name(state, entity_id),
                "state": state.state,
                "connectivity_state": connectivity["state"],
                "outlet_last_seen": connectivity["outlet_last_seen"],
                "outlet_rssi": connectivity["outlet_rssi"],
                "outlet_availability": connectivity["outlet_availability"],
                "stale_after_minutes": stale_minutes,
                "mqtt_topic_prefix": mapping.get("mqtt_topic_prefix", ""),
                "source_type": mapping.get("source_type") or _source_type_for_entry(entity_entry),
                "reference": reference,
                "area": mapping.get("area") or _entry_name(area_entry) or "Unassigned",
                "level": mapping.get("level") or _entry_name(floor_entry) or _entry_name(area_entry) or "Unassigned",
                "bay": mapping.get("bay", ""),
                "labels": [_entry_name(label) for label in labels if _entry_name(label)],
                "power_entity_id": power_entity_id,
                "power_w": None if connectivity["is_stale"] else _normalise_power_w(power_state),
                "energy_entity_id": energy_entity_id,
                "energy_kwh": energy_kwh,
                "meter_cost": None if connectivity["is_stale"] else _session_cost(energy_kwh, rate),
                "session": active_session,
                "gateway": gateway,
            }
        )
    return sorted(outlets, key=lambda outlet: outlet["name"])


async def _public_summary(hass: HomeAssistant) -> dict[str, Any]:
    """Return public portal summary data."""
    outlets = await _public_outlet_data(hass)
    settings = (await _async_load_billing(hass))["settings"]
    currency = settings.get("currency", "AUD")
    return {
        "outlet_count": len(outlets),
        "outlets_on": sum(1 for outlet in outlets if outlet["state"] == "on"),
        "total_power_w": sum(float(outlet["power_w"] or 0) for outlet in outlets),
        "total_energy_kwh": sum(float(outlet["energy_kwh"] or 0) for outlet in outlets),
        "total_cost": sum(float(outlet["meter_cost"] or 0) for outlet in outlets),
        "currency": currency,
    }


def _session_period_start(period: str) -> datetime | None:
    """Return the local period start for a public billing report."""
    now = datetime.now().astimezone()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "week":
        return start - timedelta(days=6)
    if period == "month":
        return start.replace(day=1)
    if period == "all":
        return None
    return start


async def _public_billing_report(hass: HomeAssistant, period: str) -> dict[str, Any]:
    """Return unauthenticated public billing report data."""
    report = _billing_report(hass, await _async_load_billing(hass))
    start = _session_period_start(period)
    completed = report["completed"]
    if start is not None:
        completed = [
            session
            for session in completed
            if datetime.fromisoformat(session.get("end_time") or session.get("start_time")) >= start
        ]
    session_totals = {
        "sessions": len(completed),
        "duration_seconds": sum(int(session.get("duration_seconds") or 0) for session in completed),
        "energy_kwh": round(sum(float(session.get("energy_kwh") or 0) for session in completed), 4),
        "cost": round(sum(float(session.get("cost") or 0) for session in completed), 4),
    }
    active_totals = {
        "sessions": len(report["active"]),
        "duration_seconds": sum(int(session.get("duration_seconds") or 0) for session in report["active"]),
        "energy_kwh": round(sum(float(session.get("energy_kwh") or 0) for session in report["active"]), 4),
        "cost": round(sum(float(session.get("cost") or 0) for session in report["active"]), 4),
    }
    meter_summary = await _public_summary(hass)
    public_settings = {
        "energy_rate": report["settings"].get("energy_rate", DEFAULT_ENERGY_RATE),
        "currency": report["settings"].get("currency", "AUD"),
    }
    return {
        "settings": public_settings,
        "period": period,
        "active": report["active"],
        "completed": completed,
        "session_totals": session_totals,
        "active_totals": active_totals,
        "meter_totals": {
            "outlets": meter_summary["outlet_count"],
            "energy_kwh": round(meter_summary["total_energy_kwh"], 4),
            "cost": round(meter_summary["total_cost"], 4),
            "currency": meter_summary["currency"],
        },
    }


async def _public_portal_http_handler(request: web.Request) -> web.Response:
    """Return the public portal HTML for either HA HTTP or the public port."""
    return web.FileResponse(Path(__file__).parent / "frontend" / "public-portal.html")


async def _public_settings_http_handler(request: web.Request) -> web.Response:
    """Return public portal settings."""
    hass = request.app["hass"]
    options = _public_options(hass)
    return web.json_response(
        {
            "name": options.get(CONF_PORTAL_NAME) or options.get(CONF_DASHBOARD_NAME, DEFAULT_DASHBOARD_NAME),
            "logo_url": options.get(CONF_LOGO_URL, ""),
            "accent": "#0f766e",
        }
    )


async def _public_summary_http_handler(request: web.Request) -> web.Response:
    """Return public summary data."""
    return web.json_response(await _public_summary(request.app["hass"]))


async def _public_outlets_http_handler(request: web.Request) -> web.Response:
    """Return public outlet data."""
    return web.json_response({"outlets": await _public_outlet_data(request.app["hass"])})


async def _public_billing_http_handler(request: web.Request) -> web.Response:
    """Return public billing data."""
    period = request.query.get("period", "day")
    if period not in {"day", "week", "month", "all"}:
        period = "day"
    return web.json_response(await _public_billing_report(request.app["hass"], period))


async def _public_control_http_handler(
    request: web.Request,
    entity_id: str | None = None,
) -> web.Response:
    """Control one outlet from the public portal."""
    hass = request.app["hass"]
    entity_id = entity_id or request.match_info["entity_id"]
    body = await request.json()
    action = body.get("action")
    if action not in {"turn_on", "turn_off"}:
        return web.json_response({"error": "Invalid action"}, status=400)
    reference = str(body.get("reference", "")).strip()
    if action == "turn_on" and not reference:
        return web.json_response({"error": "Reference is required before powering an outlet on"}, status=400)
    outlet_name = str(body.get("outlet_name", "")).strip() or entity_id
    if action == "turn_on":
        outlet_health = await _outlet_health_for_switch(hass, entity_id)
        if outlet_health and not _can_start_session_from_health(outlet_health):
            return web.json_response(
                {
                    "error": (
                        "Outlet telemetry is offline, stale, or gateway unreachable; "
                        "ParkPower will not start a billing session from stale data"
                    )
                },
                status=409,
            )
    try:
        await hass.services.async_call("switch", action, {"entity_id": entity_id}, blocking=True)
        event = await _async_log_outlet_event(
            hass,
            switch_entity_id=entity_id,
            action=action,
            reference=reference,
            outlet_name=outlet_name,
            success=True,
        )
        await _async_record_session_event(
            hass,
            switch_entity_id=entity_id,
            action=action,
            reference=reference,
            outlet_name=outlet_name,
            event=event,
        )
        return web.json_response({"event": event})
    except Exception as err:  # noqa: BLE001 - surfaced to public UI
        event = await _async_log_outlet_event(
            hass,
            switch_entity_id=entity_id,
            action=action,
            reference=reference,
            outlet_name=outlet_name,
            success=False,
            error=str(err),
        )
        return web.json_response({"error": str(err), "event": event}, status=500)


async def _public_all_off_http_handler(request: web.Request) -> web.Response:
    """Turn all supplied public portal outlets off."""
    hass = request.app["hass"]
    body = await request.json()
    ids = body.get("ids")
    if not isinstance(ids, list) or not ids:
        return web.json_response({"error": "At least one outlet is required"}, status=400)
    reference = str(body.get("reference", "")).strip() or "Public portal ALL Off"
    events = []
    for entity_id in ids:
        state = hass.states.get(entity_id)
        outlet_name = _state_name(state, entity_id)
        try:
            await hass.services.async_call("switch", "turn_off", {"entity_id": entity_id}, blocking=True)
            event = await _async_log_outlet_event(
                hass,
                switch_entity_id=entity_id,
                action="turn_off",
                reference=reference,
                outlet_name=outlet_name,
                success=True,
            )
            await _async_record_session_event(
                hass,
                switch_entity_id=entity_id,
                action="turn_off",
                reference=reference,
                outlet_name=outlet_name,
                event=event,
            )
            events.append(event)
        except Exception as err:  # noqa: BLE001 - surfaced to public UI
            events.append(
                await _async_log_outlet_event(
                    hass,
                    switch_entity_id=entity_id,
                    action="turn_off",
                    reference=reference,
                    outlet_name=outlet_name,
                    success=False,
                    error=str(err),
                )
            )
    return web.json_response({"events": events})


class ParkPowerPublicPortalView(HomeAssistantView):
    """Serve the unauthenticated public ParkPower portal."""

    url = "/parkpower-public"
    name = "api:parkpower_public:portal"
    requires_auth = False

    async def get(self, request: web.Request) -> web.Response:
        """Return the public portal HTML."""
        return await _public_portal_http_handler(request)


class ParkPowerPublicSettingsView(HomeAssistantView):
    """Return public portal settings."""

    url = "/api/parkpower-public/settings"
    name = "api:parkpower_public:settings"
    requires_auth = False

    async def get(self, request: web.Request) -> web.Response:
        """Return branding settings."""
        return await _public_settings_http_handler(request)


class ParkPowerPublicSummaryView(HomeAssistantView):
    """Return public portal summary."""

    url = "/api/parkpower-public/summary"
    name = "api:parkpower_public:summary"
    requires_auth = False

    async def get(self, request: web.Request) -> web.Response:
        """Return summary data."""
        return await _public_summary_http_handler(request)


class ParkPowerPublicOutletsView(HomeAssistantView):
    """Return public portal outlets."""

    url = "/api/parkpower-public/outlets"
    name = "api:parkpower_public:outlets"
    requires_auth = False

    async def get(self, request: web.Request) -> web.Response:
        """Return outlet data."""
        return await _public_outlets_http_handler(request)


class ParkPowerPublicBillingView(HomeAssistantView):
    """Return public portal billing data."""

    url = "/api/parkpower-public/billing"
    name = "api:parkpower_public:billing"
    requires_auth = False

    async def get(self, request: web.Request) -> web.Response:
        """Return public billing data."""
        return await _public_billing_http_handler(request)


class ParkPowerPublicControlView(HomeAssistantView):
    """Control one outlet from the public portal."""

    url = "/api/parkpower-public/outlets/{entity_id}/control"
    name = "api:parkpower_public:control"
    requires_auth = False

    async def post(self, request: web.Request, entity_id: str) -> web.Response:
        """Control one outlet."""
        return await _public_control_http_handler(request, entity_id)


class ParkPowerPublicAllOffView(HomeAssistantView):
    """Turn all public portal outlets off."""

    url = "/api/parkpower-public/all-off"
    name = "api:parkpower_public:all_off"
    requires_auth = False

    async def post(self, request: web.Request) -> web.Response:
        """Turn all supplied outlets off."""
        return await _public_all_off_http_handler(request)


@websocket_api.websocket_command(
    {
        vol.Required("type"): "pow_reporting/get_outlet_log",
    }
)
@websocket_api.async_response
async def _websocket_get_outlet_log(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return outlet audit log data."""
    connection.send_result(msg["id"], await _async_load_log(hass))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "pow_reporting/get_billing_report",
    }
)
@websocket_api.async_response
async def _websocket_get_billing_report(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return persisted billing/session report data."""
    report = _billing_report(hass, await _async_load_billing(hass))
    if not connection.user.is_admin:
        report.pop("statements", None)
    connection.send_result(msg["id"], report)


@websocket_api.websocket_command(
    {
        vol.Required("type"): "pow_reporting/get_management_report",
        vol.Optional("period", default="month"): vol.In(["day", "week", "month", "all", "custom"]),
        vol.Optional("filters", default={}): dict,
    }
)
@websocket_api.async_response
async def _websocket_get_management_report(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return Phase 4 management reporting data."""
    filters = dict(default_filter_period(msg["period"]))
    filters.update(msg["filters"])
    billing = _billing_report(hass, await _async_load_billing(hass))
    session_manager = await async_load_session_manager(hass)
    records_manager = await async_load_records_manager(hass)
    outlets = await _public_outlet_data(hass)
    connection.send_result(
        msg["id"],
        build_management_report(
            billing_report=billing,
            session_data=session_manager.dump(),
            records=records_manager.list_records(include_archived=True),
            outlets=outlets,
            filters=filters,
        ),
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): "pow_reporting/save_billing_settings",
        vol.Optional("energy_rate", default=DEFAULT_ENERGY_RATE): vol.Coerce(float),
        vol.Optional("currency", default="AUD"): cv.string,
        vol.Optional("notify_service", default=""): cv.string,
        vol.Optional("sender_name", default="ParkPower"): cv.string,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def _websocket_save_billing_settings(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Persist billing report settings."""
    data = await _async_load_billing(hass)
    energy_rate = max(0, float(msg["energy_rate"]))
    data["settings"] = {
        **data["settings"],
        "energy_rate": energy_rate,
        "currency": msg["currency"].strip() or "AUD",
        "notify_service": msg["notify_service"].strip(),
        "sender_name": msg["sender_name"].strip() or "ParkPower",
    }
    await _async_save_billing(hass, data)
    connection.send_result(msg["id"], _billing_report(hass, data))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "pow_reporting/create_tenant_statement",
        vol.Required("customer_id"): cv.string,
        vol.Required("period_start"): cv.string,
        vol.Required("period_end"): cv.string,
        vol.Optional("rate_per_kwh"): vol.Coerce(float),
        vol.Optional("include_invoiced", default=False): cv.boolean,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def _websocket_create_tenant_statement(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Create and persist an email-ready tenant statement."""
    data = await _async_load_billing(hass)
    records = await async_load_records_manager(hass)
    manager = TenantBillingManager(data, records.dump())
    try:
        statement = manager.create_statement(
            customer_id=msg["customer_id"],
            period_start=msg["period_start"],
            period_end=msg["period_end"],
            rate_per_kwh=msg.get("rate_per_kwh"),
            include_invoiced=msg["include_invoiced"],
        )
    except ValueError as err:
        connection.send_error(msg["id"], "invalid_statement", str(err))
        return
    await _async_save_billing(hass, manager.dump())
    connection.send_result(msg["id"], {"statement": statement})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "pow_reporting/send_tenant_statement",
        vol.Required("statement_id"): cv.string,
        vol.Optional("notify_service", default=""): cv.string,
        vol.Optional("recipient", default=""): cv.string,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def _websocket_send_tenant_statement(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Email a statement with an auditable delivery result."""
    data = await _async_load_billing(hass)
    records = await async_load_records_manager(hass)
    manager = TenantBillingManager(data, records.dump())
    try:
        statement = manager.validate_send(msg["statement_id"])
    except ValueError as err:
        connection.send_error(msg["id"], "statement_not_sendable", str(err))
        return

    notify_service = (msg["notify_service"] or data["settings"].get("notify_service", "")).strip()
    notify_service = notify_service.removeprefix("notify.")
    recipient = (msg["recipient"] or statement.get("email_to") or "").strip()
    if not notify_service:
        connection.send_error(msg["id"], "notify_service_required", "Configure an email notify service")
        return
    if not recipient:
        connection.send_error(msg["id"], "recipient_required", "Tenant email is required")
        return
    if not hass.services.has_service("notify", notify_service):
        connection.send_error(
            msg["id"],
            "notify_service_not_found",
            f"Home Assistant service notify.{notify_service} was not found",
        )
        return

    error = ""
    try:
        await hass.services.async_call(
            "notify",
            notify_service,
            {
                "target": [recipient],
                "title": statement["email_subject"],
                "message": statement["email_body"],
            },
            blocking=True,
        )
    except Exception as err:  # noqa: BLE001 - stored and returned to the admin
        error = str(err)

    statement = manager.record_delivery(
        statement_id=msg["statement_id"],
        notify_service=notify_service,
        recipient=recipient,
        success=not error,
        error=error,
    )
    await _async_save_billing(hass, manager.dump())
    if error:
        connection.send_error(msg["id"], "email_delivery_failed", error)
        return
    connection.send_result(msg["id"], {"statement": statement})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "pow_reporting/control_outlet",
        vol.Required("switch_entity_id"): cv.entity_id,
        vol.Required("action"): vol.In(["turn_on", "turn_off"]),
        vol.Optional("reference", default=""): cv.string,
        vol.Optional("outlet_name", default=""): cv.string,
        vol.Optional("customer_id", default=""): cv.string,
    }
)
@websocket_api.async_response
async def _websocket_control_outlet(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Turn one outlet on or off and log the action."""
    switch_entity_id = msg["switch_entity_id"]
    action = msg["action"]
    reference = msg["reference"].strip()
    outlet_name = msg["outlet_name"].strip() or switch_entity_id
    customer_id = msg["customer_id"].strip()
    if action == "turn_on":
        outlet_health = await _outlet_health_for_switch(hass, switch_entity_id)
        if outlet_health and not _can_start_session_from_health(outlet_health):
            event = await _async_log_outlet_event(
                hass,
                switch_entity_id=switch_entity_id,
                action=action,
                reference=reference,
                outlet_name=outlet_name,
                success=False,
                error="Outlet telemetry is offline, stale, or gateway unreachable",
            )
            connection.send_error(
                msg["id"],
                "stale_outlet_telemetry",
                "Outlet telemetry is offline, stale, or gateway unreachable; ParkPower will not start a billing session",
            )
            return

    try:
        await hass.services.async_call(
            "switch",
            action,
            {"entity_id": switch_entity_id},
            blocking=True,
        )
    except Exception as err:  # noqa: BLE001 - surfaced to the dashboard
        event = await _async_log_outlet_event(
            hass,
            switch_entity_id=switch_entity_id,
            action=action,
            reference=reference,
            outlet_name=outlet_name,
            success=False,
            error=str(err),
        )
        connection.send_result(msg["id"], {"event": event})
        return

    event = await _async_log_outlet_event(
        hass,
        switch_entity_id=switch_entity_id,
        action=action,
        reference=reference,
        outlet_name=outlet_name,
        success=True,
    )
    await _async_record_session_event(
        hass,
        switch_entity_id=switch_entity_id,
        action=action,
        reference=reference,
        outlet_name=outlet_name,
        event=event,
        customer_id=customer_id,
    )
    connection.send_result(msg["id"], {"event": event})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "pow_reporting/all_off",
        vol.Required("switch_entity_ids"): vol.All(cv.ensure_list, [cv.entity_id]),
        vol.Optional("reference", default="Master ALL Off"): cv.string,
    }
)
@websocket_api.async_response
async def _websocket_all_off(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Turn all supplied outlets off and log each outlet."""
    reference = msg["reference"].strip() or "Master ALL Off"
    events = []

    for switch_entity_id in msg["switch_entity_ids"]:
        outlet_name = hass.states.get(switch_entity_id)
        name = outlet_name.attributes.get("friendly_name", switch_entity_id) if outlet_name else switch_entity_id
        try:
            await hass.services.async_call(
                "switch",
                "turn_off",
                {"entity_id": switch_entity_id},
                blocking=True,
            )
            event = await _async_log_outlet_event(
                hass,
                switch_entity_id=switch_entity_id,
                action="turn_off",
                reference=reference,
                outlet_name=name,
                success=True,
            )
            await _async_record_session_event(
                hass,
                switch_entity_id=switch_entity_id,
                action="turn_off",
                reference=reference,
                outlet_name=name,
                event=event,
            )
            events.append(event)
        except Exception as err:  # noqa: BLE001 - surfaced to the dashboard
            events.append(
                await _async_log_outlet_event(
                    hass,
                    switch_entity_id=switch_entity_id,
                    action="turn_off",
                    reference=reference,
                    outlet_name=name,
                    success=False,
                    error=str(err),
                )
            )

    connection.send_result(msg["id"], {"events": events})


def _registry_get(registry: Any, collection: str, key: str) -> Any:
    """Return a registry entry by key across HA registry implementations."""
    getter = getattr(registry, f"async_get_{collection[:-1]}", None)
    if getter:
        found = getter(key)
        if found is not None:
            return found
    generic_getter = getattr(registry, "async_get", None)
    if generic_getter:
        found = generic_getter(key)
        if found is not None:
            return found
    values = getattr(registry, collection, None) or getattr(registry, f"_{collection}", None) or {}
    if hasattr(values, "get"):
        return values.get(key)
    data = getattr(values, "data", {})
    if hasattr(data, "get"):
        return data.get(key)
    return None


def _label_entries(label_registry: Any, label_ids: list[str]) -> list[Any]:
    """Return label entries for a list of label ids."""
    labels = []
    for label_id in label_ids:
        label = _registry_get(label_registry, "labels", label_id)
        if label is not None:
            labels.append(label)
    return labels


def _entry_name(entry: Any) -> str:
    """Return a display-ish name for a registry entry."""
    return getattr(entry, "name", None) or getattr(entry, "name_by_user", None) or ""


def _area_for_entity(
    entity_entry: Any,
    device_entry: Any,
    area_registry: Any,
) -> Any:
    """Resolve the effective area for an entity/device."""
    area_id = getattr(entity_entry, "area_id", None) or getattr(device_entry, "area_id", None)
    if not area_id:
        return None
    return _registry_get(area_registry, "areas", area_id)


def _floor_for_area(area_entry: Any, floor_registry: Any) -> Any:
    """Resolve the floor for an area."""
    floor_id = getattr(area_entry, "floor_id", None)
    if not floor_id:
        return None
    return _registry_get(floor_registry, "floors", floor_id)


def _normalise_level_name(
    floor_entry: Any,
    area_entry: Any,
    labels: list[Any],
    extra_texts: list[str],
) -> str:
    """Return compact level text such as L1 or B2."""
    candidates = [
        _entry_name(floor_entry),
        _entry_name(area_entry),
        *[_entry_name(label) for label in labels],
        *extra_texts,
    ]
    for candidate in candidates:
        text = candidate or ""
        lowered = text.lower()
        match = __import__("re").search(r"(?:level|lvl|l)\s*([0-9]+)", lowered)
        if match:
            return f"L{match.group(1)}"
        match = __import__("re").search(r"(?:basement|b)\s*([0-9]+)", lowered)
        if match:
            return f"B{match.group(1)}"
    floor_level = getattr(floor_entry, "level", None)
    if isinstance(floor_level, int):
        return f"L{floor_level}" if floor_level >= 0 else f"B{abs(floor_level)}"
    return (_entry_name(floor_entry) or _entry_name(area_entry) or "L").replace(" ", "")


def _normalise_bay_name(labels: list[Any], extra_texts: list[str]) -> str:
    """Return compact bay text such as B7 or S014."""
    import re

    candidates = [_entry_name(label) or getattr(label, "label_id", "") for label in labels]
    candidates.extend(extra_texts)
    for text in candidates:
        match = re.search(r"\bbay\s*([A-Za-z0-9-]+)", text, re.IGNORECASE)
        if match:
            return f"B{match.group(1)}"
        match = re.search(r"\bb\s*([0-9][A-Za-z0-9-]*)", text, re.IGNORECASE)
        if match:
            return f"B{match.group(1)}"
        match = re.search(r"\b(?:spot|space)\s*([A-Za-z0-9-]+)", text, re.IGNORECASE)
        if match:
            return f"S{match.group(1)}"
        match = re.search(r"\bs\s*([0-9][A-Za-z0-9-]*)", text, re.IGNORECASE)
        if match:
            return f"S{match.group(1)}"
    return ""


def _suffix_for_entity(entity_id: str, entity_entry: Any, state: Any) -> str:
    """Return the outlet entity suffix based on domain, class, unit, and name."""
    domain = entity_id.split(".", 1)[0]
    if domain == "switch":
        return "Control"
    if domain != "sensor":
        return ""

    attributes = getattr(state, "attributes", {}) if state is not None else {}
    device_class = attributes.get("device_class") or getattr(entity_entry, "device_class", None)
    unit = attributes.get("unit_of_measurement", "")
    name_text = " ".join(
        str(item or "")
        for item in [
            attributes.get("friendly_name"),
            getattr(entity_entry, "name", None),
            getattr(entity_entry, "original_name", None),
            entity_id,
        ]
    ).lower()

    if device_class == "power" or unit in {"W", "kW"}:
        return "Watts"
    if device_class == "current" or unit == "A" or "current" in name_text or "amp" in name_text:
        return "Amps"
    if device_class == "voltage" or unit == "V" or "voltage" in name_text:
        return "Voltage"
    if device_class == "energy" or unit in {"Wh", "kWh"}:
        if "total daily" in name_text or "daily energy" in name_text:
            return "Daily Wh"
        return "Wh"
    if "power factor" in name_text:
        return "Power Factor"
    return ""


def _is_managed_switch(entity_id: str, entity_entry: Any, state: Any, entity_filter: str) -> bool:
    """Return true if the switch looks like a controllable POW outlet."""
    attributes = getattr(state, "attributes", {}) if state is not None else {}
    haystack = " ".join(
        str(item or "")
        for item in [
            entity_id,
            attributes.get("friendly_name"),
            getattr(entity_entry, "original_name", None),
            getattr(entity_entry, "name", None),
        ]
    ).lower()
    keywords = [item.strip().lower() for item in entity_filter.split(",") if item.strip()]
    return (
        entity_id.startswith("switch.")
        and not any(word in haystack for word in ("restart", "reboot", "firmware", "update"))
        and (not keywords or any(keyword in haystack for keyword in keywords))
    )


def _build_auto_name_plan(
    hass: HomeAssistant,
    *,
    entity_filter: str,
) -> list[dict[str, Any]]:
    """Build proposed entity display names from HA area/floor/label metadata."""
    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)
    area_registry = ar.async_get(hass)
    floor_registry = fr.async_get(hass)
    label_registry = lr.async_get(hass)

    entries = list(getattr(entity_registry, "entities", {}).values())
    managed_device_ids = {
        getattr(entry, "device_id", None)
        for entry in entries
        if _is_managed_switch(entry.entity_id, entry, hass.states.get(entry.entity_id), entity_filter)
    }
    managed_device_ids.discard(None)

    plan = []
    for entry in entries:
        device_id = getattr(entry, "device_id", None)
        if device_id not in managed_device_ids or entry.entity_id.split(".", 1)[0] not in {"switch", "sensor"}:
            continue

        state = hass.states.get(entry.entity_id)
        if entry.entity_id.startswith("switch.") and not _is_managed_switch(entry.entity_id, entry, state, entity_filter):
            continue

        device_entry = _registry_get(device_registry, "devices", device_id)
        area_entry = _area_for_entity(entry, device_entry, area_registry)
        floor_entry = _floor_for_area(area_entry, floor_registry)
        label_ids = [
            *(getattr(entry, "labels", []) or []),
            *(getattr(device_entry, "labels", []) or []),
            *(getattr(area_entry, "labels", []) or []),
        ]
        labels = _label_entries(label_registry, list(dict.fromkeys(label_ids)))
        state_name = state.attributes.get("friendly_name", "") if state else ""
        extra_texts = [
            state_name,
            getattr(entry, "name", None) or "",
            getattr(entry, "original_name", None) or "",
            getattr(device_entry, "name_by_user", None) or "",
            getattr(device_entry, "name", None) or "",
            entry.entity_id,
        ]
        level = _normalise_level_name(floor_entry, area_entry, labels, extra_texts)
        bay = _normalise_bay_name(labels, extra_texts)
        if not bay:
            continue

        suffix = _suffix_for_entity(entry.entity_id, entry, state)
        if not suffix:
            continue
        proposed = f"{level}-{bay} {suffix}"
        current = getattr(entry, "name", None) or (state.attributes.get("friendly_name") if state else entry.entity_id)
        plan.append(
            {
                "entity_id": entry.entity_id,
                "current_name": current,
                "proposed_name": proposed,
                "area": _entry_name(area_entry),
                "floor": _entry_name(floor_entry),
                "labels": [_entry_name(label) for label in labels],
                "changed": current != proposed,
            }
        )

    return sorted(plan, key=lambda item: item["proposed_name"])


@websocket_api.websocket_command(
    {
        vol.Required("type"): "pow_reporting/auto_name_entities",
        vol.Optional("apply", default=False): cv.boolean,
        vol.Optional("entity_filter", default=DEFAULT_ENTITY_FILTER): cv.string,
    }
)
@websocket_api.async_response
async def _websocket_auto_name_entities(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Preview or apply HA entity display names from area/floor/bay labels."""
    plan = _build_auto_name_plan(hass, entity_filter=msg["entity_filter"])

    if msg["apply"]:
        entity_registry = er.async_get(hass)
        for item in plan:
            if item["changed"]:
                entity_registry.async_update_entity(
                    item["entity_id"],
                    name=item["proposed_name"],
                )
        plan = _build_auto_name_plan(hass, entity_filter=msg["entity_filter"])

    connection.send_result(
        msg["id"],
        {
            "applied": msg["apply"],
            "entities": plan,
            "changed_count": sum(1 for item in plan if item["changed"]),
        },
    )


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload the dashboard panel."""
    options = {**entry.data, **entry.options}
    _async_stop_coordinator(hass)
    frontend.async_remove_panel(hass, options.get(CONF_URL_PATH, DEFAULT_URL_PATH))
    frontend.async_remove_panel(hass, options.get(CONF_PORTAL_URL_PATH, DEFAULT_PORTAL_URL_PATH))
    await _async_stop_public_portal_server(hass)
    return True


async def _async_sync_session_thresholds(
    hass: HomeAssistant,
    options: dict[str, Any],
) -> None:
    """Persist configured session thresholds into the session store."""
    threshold_keys = set(DEFAULT_SESSION_THRESHOLDS)
    incoming = {key: options[key] for key in threshold_keys if key in options}
    if not incoming:
        return
    manager = await async_load_session_manager(hass)
    manager.thresholds.update(incoming)
    await async_save_session_manager(hass, manager)


async def _async_start_coordinator(
    hass: HomeAssistant,
    options: dict[str, Any],
) -> None:
    """Start the live entity coordinator."""
    _async_stop_coordinator(hass)
    coordinator = PowReportingCoordinator(hass, options)
    hass.data.setdefault(DOMAIN, {})["coordinator"] = coordinator
    await coordinator.async_start()


def _async_stop_coordinator(hass: HomeAssistant) -> None:
    """Stop the live entity coordinator if it is running."""
    coordinator = hass.data.setdefault(DOMAIN, {}).pop("coordinator", None)
    if coordinator is not None:
        coordinator.async_stop()


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)
