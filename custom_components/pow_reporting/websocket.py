"""WebSocket API for ParkPower session management."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import CONF_ENTITY_FILTER, DEFAULT_ENTITY_FILTER, DOMAIN
from .hierarchy_manager import HIERARCHY_COLLECTIONS, HierarchyManager
from .outlet_registry import discover_outlets
from .records_manager import RecordsManager
from .session_manager import SessionManager
from .storage import PowReportingStore


def async_register_websocket_commands(hass: HomeAssistant) -> None:
    """Register session websocket commands once."""
    websocket_api.async_register_command(hass, _websocket_get_sessions)
    websocket_api.async_register_command(hass, _websocket_session_action)
    websocket_api.async_register_command(hass, _websocket_get_records)
    websocket_api.async_register_command(hass, _websocket_save_record)
    websocket_api.async_register_command(hass, _websocket_archive_record)
    websocket_api.async_register_command(hass, _websocket_get_hierarchy)
    websocket_api.async_register_command(hass, _websocket_save_hierarchy_record)
    websocket_api.async_register_command(hass, _websocket_archive_hierarchy_record)


async def async_load_session_manager(hass: HomeAssistant) -> SessionManager:
    """Load the stored session manager."""
    store = PowReportingStore(hass)
    return SessionManager(await store.async_load())


async def async_save_session_manager(hass: HomeAssistant, manager: SessionManager) -> None:
    """Persist the session manager."""
    store = PowReportingStore(hass)
    await store.async_save(manager.dump())


async def async_load_records_manager(hass: HomeAssistant) -> RecordsManager:
    """Load the stored records manager."""
    store = PowReportingStore(hass)
    return RecordsManager(await store.async_load())


async def async_save_records_manager(hass: HomeAssistant, manager: RecordsManager) -> None:
    """Persist the records manager."""
    store = PowReportingStore(hass)
    await store.async_save(manager.dump())


async def async_load_hierarchy_manager(hass: HomeAssistant) -> HierarchyManager:
    """Load the stored hierarchy manager."""
    store = PowReportingStore(hass)
    return HierarchyManager(await store.async_load())


async def async_save_hierarchy_manager(hass: HomeAssistant, manager: HierarchyManager) -> None:
    """Persist the hierarchy manager."""
    store = PowReportingStore(hass)
    await store.async_save(manager.dump())


@websocket_api.websocket_command(
    {
        vol.Required("type"): "pow_reporting/get_sessions",
    }
)
@websocket_api.async_response
async def _websocket_get_sessions(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return all stored sessions and active-session indexes."""
    manager = await async_load_session_manager(hass)
    connection.send_result(msg["id"], manager.dump())


@websocket_api.websocket_command(
    {
        vol.Required("type"): "pow_reporting/session_action",
        vol.Required("action"): vol.In(["complete", "cancel", "annotate", "correct"]),
        vol.Optional("session_id"): cv.string,
        vol.Optional("outlet_entity_id"): cv.entity_id,
        vol.Optional("reason", default="Manual admin action"): cv.string,
        vol.Optional("note", default=""): cv.string,
        vol.Optional("fields", default={}): dict,
    }
)
@websocket_api.async_response
async def _websocket_session_action(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Apply a manual admin action to a session."""
    manager = await async_load_session_manager(hass)
    action = msg["action"]
    session = None

    if action == "complete":
        outlet_entity_id = msg.get("outlet_entity_id")
        if outlet_entity_id:
            session = manager.complete_session(
                outlet_entity_id=outlet_entity_id,
                reason=msg["reason"],
            )
    elif action == "cancel" and msg.get("session_id"):
        session = manager.cancel_session(msg["session_id"], msg["reason"])
    elif action == "annotate" and msg.get("session_id"):
        session = manager.annotate_session(msg["session_id"], msg["note"])
    elif action == "correct" and msg.get("session_id"):
        session = manager.correct_session(msg["session_id"], msg["fields"], msg["reason"])

    if session is None:
        connection.send_error(msg["id"], "session_not_found", "No matching session was found")
        return

    await async_save_session_manager(hass, manager)
    connection.send_result(msg["id"], {"session": session.as_dict()})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "pow_reporting/get_records",
        vol.Optional("include_archived", default=False): cv.boolean,
        vol.Optional("query", default=""): cv.string,
    }
)
@websocket_api.async_response
async def _websocket_get_records(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return customer, vehicle and user-group records."""
    manager = await async_load_records_manager(hass)
    connection.send_result(
        msg["id"],
        manager.list_records(
            include_archived=msg["include_archived"],
            query=msg["query"],
        ),
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): "pow_reporting/save_record",
        vol.Required("record_type"): vol.In(["customer", "vehicle", "user_group"]),
        vol.Required("fields"): dict,
    }
)
@websocket_api.async_response
async def _websocket_save_record(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Create or update a local admin record."""
    manager = await async_load_records_manager(hass)
    try:
        record = manager.upsert(msg["record_type"], msg["fields"])
    except ValueError as err:
        connection.send_error(msg["id"], "invalid_record", str(err))
        return
    await async_save_records_manager(hass, manager)
    connection.send_result(msg["id"], {"record": record})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "pow_reporting/get_hierarchy",
        vol.Optional("include_archived", default=False): cv.boolean,
        vol.Optional("query", default=""): cv.string,
    }
)
@websocket_api.async_response
async def _websocket_get_hierarchy(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return local hierarchy records and discovered HA outlet defaults."""
    manager = await async_load_hierarchy_manager(hass)
    records = manager.list_records(
        include_archived=msg["include_archived"],
        query=msg["query"],
    )
    records["discovered_outlets"] = _discovered_outlets(hass)
    connection.send_result(msg["id"], records)


@websocket_api.websocket_command(
    {
        vol.Required("type"): "pow_reporting/save_hierarchy_record",
        vol.Required("record_type"): vol.In(list(HIERARCHY_COLLECTIONS)),
        vol.Required("fields"): dict,
    }
)
@websocket_api.async_response
async def _websocket_save_hierarchy_record(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Create or update a local hierarchy record."""
    manager = await async_load_hierarchy_manager(hass)
    record = manager.upsert(msg["record_type"], msg["fields"])
    await async_save_hierarchy_manager(hass, manager)
    connection.send_result(msg["id"], {"record": record})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "pow_reporting/archive_hierarchy_record",
        vol.Required("record_type"): vol.In(list(HIERARCHY_COLLECTIONS)),
        vol.Required("record_id"): cv.string,
    }
)
@websocket_api.async_response
async def _websocket_archive_hierarchy_record(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Archive a local hierarchy record."""
    manager = await async_load_hierarchy_manager(hass)
    record = manager.archive(msg["record_type"], msg["record_id"])
    if record is None:
        connection.send_error(msg["id"], "hierarchy_record_not_found", "No matching hierarchy record was found")
        return
    await async_save_hierarchy_manager(hass, manager)
    connection.send_result(msg["id"], {"record": record})


def _discovered_outlets(hass: HomeAssistant) -> list[dict[str, Any]]:
    """Return HA-discovered outlet mappings with registry metadata defaults."""
    entry = next(iter(hass.config_entries.async_entries(DOMAIN)), None)
    entity_filter = entry.options.get(CONF_ENTITY_FILTER) if entry else DEFAULT_ENTITY_FILTER
    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)
    rows = []
    for mapping in discover_outlets(hass, entity_filter=entity_filter or DEFAULT_ENTITY_FILTER).values():
        switch_entry = entity_registry.async_get(mapping.switch_entity_id)
        device = device_registry.async_get(mapping.device_id) if mapping.device_id else None
        area_id = getattr(switch_entry, "area_id", None) or getattr(device, "area_id", "")
        rows.append(
            {
                "switch_entity_id": mapping.switch_entity_id,
                "switch_name": mapping.switch_name,
                "power_entity_id": mapping.power_entity_id,
                "energy_entity_id": mapping.energy_entity_id,
                "device_id": mapping.device_id,
                "ha_area_id": area_id or "",
                "ha_floor_id": getattr(switch_entry, "floor_id", "") or "",
                "ha_label_ids": sorted(getattr(switch_entry, "labels", []) or []),
            }
        )
    return rows


@websocket_api.websocket_command(
    {
        vol.Required("type"): "pow_reporting/archive_record",
        vol.Required("record_type"): vol.In(["customer", "vehicle", "user_group"]),
        vol.Required("record_id"): cv.string,
    }
)
@websocket_api.async_response
async def _websocket_archive_record(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Archive a local admin record."""
    manager = await async_load_records_manager(hass)
    record = manager.archive(msg["record_type"], msg["record_id"])
    if record is None:
        connection.send_error(msg["id"], "record_not_found", "No matching record was found")
        return
    await async_save_records_manager(hass, manager)
    connection.send_result(msg["id"], {"record": record})
