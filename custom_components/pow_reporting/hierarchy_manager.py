"""Site, electrical hierarchy and outlet assignment records."""

from __future__ import annotations

from typing import Any

from .models import RecordStatus, new_record_id, utc_now

HIERARCHY_COLLECTIONS = {
    "organisation": ("organisations", "org"),
    "site": ("sites", "site"),
    "building": ("buildings", "bld"),
    "distribution_board": ("distribution_boards", "db"),
    "circuit_group": ("circuit_groups", "cct"),
    "outlet": ("outlet_mappings", "out"),
}

HIERARCHY_COLLECTION_NAMES = [value[0] for value in HIERARCHY_COLLECTIONS.values()]

ELECTRICAL_FIELDS = {
    "maximum_current",
    "maximum_power",
    "reserve_margin",
    "warning_threshold",
    "maximum_simultaneous_outlets",
    "allocation_interval",
    "minimum_relay_on_duration",
    "minimum_relay_off_duration",
    "maximum_relay_operations_per_hour",
}

OUTLET_NUMBER_FIELDS = {
    "gateway_uplink_rssi",
    "gateway_client_count",
    "outlet_rssi",
    "stale_after_minutes",
}

ALLOWED_FIELDS = {
    "organisation": {
        "id",
        "name",
        "billing_reference",
        "notes",
        "status",
    },
    "site": {
        "id",
        "organisation_id",
        "name",
        "address",
        "timezone",
        "notes",
        "status",
    },
    "building": {
        "id",
        "site_id",
        "name",
        "ha_floor_id",
        "notes",
        "status",
    },
    "distribution_board": {
        "id",
        "building_id",
        "name",
        "maximum_current",
        "maximum_power",
        "reserve_margin",
        "warning_threshold",
        "main_meter_power_entity",
        "enabled",
        "notes",
        "status",
    },
    "circuit_group": {
        "id",
        "distribution_board_id",
        "name",
        "maximum_current",
        "maximum_power",
        "reserve_margin",
        "warning_threshold",
        "maximum_simultaneous_outlets",
        "load_management_mode",
        "allocation_interval",
        "minimum_relay_on_duration",
        "minimum_relay_off_duration",
        "maximum_relay_operations_per_hour",
        "main_meter_power_entity",
        "enabled",
        "priority",
        "notes",
        "status",
    },
    "outlet": {
        "id",
        "switch_entity_id",
        "power_entity_id",
        "energy_entity_id",
        "availability_entity_id",
        "rssi_entity_id",
        "site_id",
        "building_id",
        "distribution_board_id",
        "circuit_group_id",
        "level",
        "area",
        "bay",
        "gateway_id",
        "gateway_name",
        "gateway_zone",
        "gateway_ip",
        "gateway_ssid",
        "gateway_subnet",
        "gateway_last_seen",
        "gateway_uplink_rssi",
        "gateway_client_count",
        "gateway_status",
        "gateway_uptime_entity_id",
        "gateway_client_count_entity_id",
        "gateway_uplink_rssi_entity_id",
        "gateway_free_heap_entity_id",
        "gateway_online_entity_id",
        "outlet_last_seen",
        "outlet_rssi",
        "outlet_availability",
        "stale_after_minutes",
        "mqtt_topic_prefix",
        "source_type",
        "ha_area_id",
        "ha_floor_id",
        "ha_label_ids",
        "notes",
        "status",
    },
}


class HierarchyManager:
    """Manage locally stored commercial and electrical hierarchy records."""

    def __init__(self, data: dict[str, Any]) -> None:
        """Initialize from Home Assistant storage data."""
        self.data = data
        for collection_name in HIERARCHY_COLLECTION_NAMES:
            setattr(
                self,
                collection_name,
                [
                    _normalise_record(record)
                    for record in data.get(collection_name, [])
                    if isinstance(record, dict)
                ],
            )

    def dump(self) -> dict[str, Any]:
        """Return storage data with current hierarchy records."""
        for collection_name in HIERARCHY_COLLECTION_NAMES:
            self.data[collection_name] = list(getattr(self, collection_name))
        return self.data

    def list_records(self, *, include_archived: bool = False, query: str = "") -> dict[str, Any]:
        """Return hierarchy records for the admin dashboard."""
        return {
            collection_name: _filter_records(
                getattr(self, collection_name),
                include_archived,
                query,
            )
            for collection_name in HIERARCHY_COLLECTION_NAMES
        }

    def upsert(self, record_type: str, fields: dict[str, Any]) -> dict[str, Any]:
        """Create or update one hierarchy record."""
        collection_name, prefix = _collection(record_type)
        collection = getattr(self, collection_name)
        record_id = str(fields.get("id") or new_record_id(prefix))
        existing_index = next(
            (index for index, record in enumerate(collection) if record.get("id") == record_id),
            None,
        )
        now = utc_now()
        existing = collection[existing_index] if existing_index is not None else {}
        payload = {
            **existing,
            **_clean_fields(record_type, fields),
            "id": record_id,
            "status": str(fields.get("status") or existing.get("status") or RecordStatus.ACTIVE.value),
            "created_at": existing.get("created_at") or now,
            "updated_at": now,
        }
        record = _normalise_record(payload)
        if existing_index is None:
            collection.append(record)
        else:
            collection[existing_index] = record
        return record

    def archive(self, record_type: str, record_id: str) -> dict[str, Any] | None:
        """Archive a hierarchy record without deleting historic references."""
        collection_name, _prefix = _collection(record_type)
        collection = getattr(self, collection_name)
        record = next((item for item in collection if item.get("id") == record_id), None)
        if record is None:
            return None
        record["status"] = RecordStatus.ARCHIVED.value
        record["updated_at"] = utc_now()
        return record


def _collection(record_type: str) -> tuple[str, str]:
    """Return storage collection and ID prefix for a record type."""
    if record_type not in HIERARCHY_COLLECTIONS:
        raise ValueError(f"Unknown hierarchy record type: {record_type}")
    return HIERARCHY_COLLECTIONS[record_type]


def _filter_records(records: list[dict[str, Any]], include_archived: bool, query: str) -> list[dict[str, Any]]:
    """Filter records for admin search."""
    needle = query.strip().lower()
    rows = []
    for record in records:
        if not include_archived and record.get("status") == RecordStatus.ARCHIVED.value:
            continue
        if needle and needle not in " ".join(str(value) for value in record.values()).lower():
            continue
        rows.append(dict(record))
    return rows


def _clean_fields(record_type: str, fields: dict[str, Any]) -> dict[str, Any]:
    """Keep and coerce fields supported by each hierarchy record type."""
    allowed = ALLOWED_FIELDS[record_type]
    cleaned = {key: value for key, value in fields.items() if key in allowed}
    for key in ELECTRICAL_FIELDS.intersection(cleaned):
        cleaned[key] = _optional_number(cleaned[key])
    for key in OUTLET_NUMBER_FIELDS.intersection(cleaned):
        cleaned[key] = _optional_number(cleaned[key])
    if "source_type" in cleaned and cleaned["source_type"] not in {"esphome_native", "mqtt", "manual"}:
        cleaned["source_type"] = "manual"
    if "priority" in cleaned:
        cleaned["priority"] = int(_optional_number(cleaned["priority"]) or 0)
    if "enabled" in cleaned:
        cleaned["enabled"] = _coerce_bool(cleaned["enabled"])
    if "ha_label_ids" in cleaned:
        value = cleaned["ha_label_ids"]
        if isinstance(value, str):
            cleaned["ha_label_ids"] = [item.strip() for item in value.split(",") if item.strip()]
        elif isinstance(value, list):
            cleaned["ha_label_ids"] = [str(item) for item in value if item]
        else:
            cleaned["ha_label_ids"] = []
    return cleaned


def _normalise_record(record: dict[str, Any]) -> dict[str, Any]:
    """Return a JSON-safe record with a valid status."""
    normalised = dict(record)
    status = str(normalised.get("status") or RecordStatus.ACTIVE.value)
    normalised["status"] = status if status in RecordStatus._value2member_map_ else RecordStatus.ACTIVE.value
    return normalised


def _optional_number(value: Any) -> float | None:
    """Return a float when a numeric value was supplied."""
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_bool(value: Any) -> bool:
    """Return a bool for form/websocket values."""
    if isinstance(value, bool):
        return value
    return str(value).lower() in {"1", "true", "yes", "on", "enabled"}
