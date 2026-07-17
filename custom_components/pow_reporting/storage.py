"""Home Assistant storage helpers for ParkPower."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DEFAULT_SESSION_THRESHOLDS, DOMAIN
from .hierarchy_manager import HIERARCHY_COLLECTION_NAMES

SESSION_STORAGE_KEY = f"{DOMAIN}.sessions"
SESSION_STORAGE_VERSION = 1
MAX_SESSION_ROWS = 5000
MAX_RECORD_ROWS = 2000


class PowReportingStore:
    """Small wrapper around Home Assistant Store with schema defaults."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the store."""
        self._store: Store[dict[str, Any]] = Store(
            hass,
            SESSION_STORAGE_VERSION,
            SESSION_STORAGE_KEY,
        )

    async def async_load(self) -> dict[str, Any]:
        """Load and migrate stored session data."""
        data = await self._store.async_load()
        if not isinstance(data, dict):
            return _empty_data()
        return _migrate_data(data)

    async def async_save(self, data: dict[str, Any]) -> None:
        """Persist session data."""
        data = _migrate_data(data)
        data["sessions"] = list(data.get("sessions", []))[-MAX_SESSION_ROWS:]
        data["customers"] = list(data.get("customers", []))[-MAX_RECORD_ROWS:]
        data["vehicles"] = list(data.get("vehicles", []))[-MAX_RECORD_ROWS:]
        data["user_groups"] = list(data.get("user_groups", []))[-MAX_RECORD_ROWS:]
        for collection_name in HIERARCHY_COLLECTION_NAMES:
            data[collection_name] = list(data.get(collection_name, []))[-MAX_RECORD_ROWS:]
        await self._store.async_save(data)


def _empty_data() -> dict[str, Any]:
    """Return the current empty schema."""
    return {
        "schema_version": 4,
        "sessions": [],
        "active_sessions": {},
        "thresholds": dict(DEFAULT_SESSION_THRESHOLDS),
        "outlet_state": {},
        "load_management": {
            "paused_outlets": {},
            "relay_events": {},
            "last_evaluation": {},
        },
        "customers": [],
        "vehicles": [],
        "user_groups": [],
        "organisations": [],
        "sites": [],
        "buildings": [],
        "distribution_boards": [],
        "circuit_groups": [],
        "outlet_mappings": [],
    }


def _migrate_data(data: dict[str, Any]) -> dict[str, Any]:
    """Migrate storage in-place without invalidating existing records."""
    migrated = {**_empty_data(), **data}
    migrated["schema_version"] = 4
    migrated["sessions"] = list(migrated.get("sessions") or [])
    migrated["customers"] = list(migrated.get("customers") or [])
    migrated["vehicles"] = list(migrated.get("vehicles") or [])
    migrated["user_groups"] = list(migrated.get("user_groups") or [])
    for collection_name in HIERARCHY_COLLECTION_NAMES:
        migrated[collection_name] = list(migrated.get(collection_name) or [])
    migrated["active_sessions"] = dict(migrated.get("active_sessions") or {})
    migrated["thresholds"] = {
        **DEFAULT_SESSION_THRESHOLDS,
        **dict(migrated.get("thresholds") or {}),
    }
    migrated["outlet_state"] = dict(migrated.get("outlet_state") or {})
    load_management = dict(migrated.get("load_management") or {})
    migrated["load_management"] = {
        "paused_outlets": dict(load_management.get("paused_outlets") or {}),
        "relay_events": dict(load_management.get("relay_events") or {}),
        "last_evaluation": dict(load_management.get("last_evaluation") or {}),
    }
    return migrated
