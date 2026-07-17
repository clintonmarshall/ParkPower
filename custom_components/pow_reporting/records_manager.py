"""Customer, vehicle and user-group record management."""

from __future__ import annotations

from typing import Any

from .models import (
    CustomerRecord,
    RecordStatus,
    UserGroupRecord,
    VehicleRecord,
    new_record_id,
    utc_now,
)

RECORD_COLLECTIONS = {
    "customer": "customers",
    "vehicle": "vehicles",
    "user_group": "user_groups",
}

RECORD_MODELS = {
    "customer": CustomerRecord,
    "vehicle": VehicleRecord,
    "user_group": UserGroupRecord,
}


class RecordsManager:
    """Manage locally stored customer, vehicle and user-group records."""

    def __init__(self, data: dict[str, Any]) -> None:
        """Initialize from storage data."""
        self.data = data
        self.customers = [
            CustomerRecord.from_dict(record)
            for record in data.get("customers", [])
            if isinstance(record, dict)
        ]
        self.vehicles = [
            VehicleRecord.from_dict(record)
            for record in data.get("vehicles", [])
            if isinstance(record, dict)
        ]
        self.user_groups = [
            UserGroupRecord.from_dict(record)
            for record in data.get("user_groups", [])
            if isinstance(record, dict)
        ]

    def dump(self) -> dict[str, Any]:
        """Return storage data with current records."""
        self.data["customers"] = [record.as_dict() for record in self.customers]
        self.data["vehicles"] = [record.as_dict() for record in self.vehicles]
        self.data["user_groups"] = [record.as_dict() for record in self.user_groups]
        return self.data

    def list_records(self, *, include_archived: bool = False, query: str = "") -> dict[str, Any]:
        """Return records for admin screens."""
        return {
            "customers": _filter_records(self.customers, include_archived, query),
            "vehicles": _filter_records(self.vehicles, include_archived, query),
            "user_groups": _filter_records(self.user_groups, include_archived, query),
        }

    def upsert(self, record_type: str, fields: dict[str, Any]) -> dict[str, Any]:
        """Create or update a record."""
        collection_name = _collection_name(record_type)
        collection = getattr(self, collection_name)
        model = RECORD_MODELS[record_type]
        record_id = str(fields.get("id") or new_record_id(_prefix(record_type)))
        existing_index = next(
            (index for index, record in enumerate(collection) if record.id == record_id),
            None,
        )

        now = utc_now()
        existing = collection[existing_index].as_dict() if existing_index is not None else {}
        payload = {
            **existing,
            **_clean_fields(record_type, fields),
            "id": record_id,
            "created_at": existing.get("created_at") or now,
            "updated_at": now,
        }
        record = model.from_dict(payload)
        if record_type == "customer":
            self._validate_outlet_assignments(record)
        if existing_index is None:
            collection.append(record)
        else:
            collection[existing_index] = record
        return record.as_dict()

    def customer_for_outlet(self, outlet_entity_id: str) -> CustomerRecord | None:
        """Return the active customer explicitly assigned to an outlet."""
        return next(
            (
                customer
                for customer in self.customers
                if customer.status == RecordStatus.ACTIVE
                and outlet_entity_id in customer.outlet_entity_ids
            ),
            None,
        )

    def _validate_outlet_assignments(self, candidate: CustomerRecord) -> None:
        """Prevent ambiguous ownership of an outlet by active customers."""
        if candidate.status != RecordStatus.ACTIVE:
            return
        assigned = set(candidate.outlet_entity_ids)
        for customer in self.customers:
            if customer.id == candidate.id or customer.status != RecordStatus.ACTIVE:
                continue
            duplicate = assigned.intersection(customer.outlet_entity_ids)
            if duplicate:
                outlet = sorted(duplicate)[0]
                raise ValueError(
                    f"{outlet} is already assigned to {customer.display_name or customer.id}"
                )

    def archive(self, record_type: str, record_id: str) -> dict[str, Any] | None:
        """Archive a record without deleting historic references."""
        collection = getattr(self, _collection_name(record_type))
        record = next((item for item in collection if item.id == record_id), None)
        if record is None:
            return None
        record.status = RecordStatus.ARCHIVED
        record.updated_at = utc_now()
        return record.as_dict()


def _collection_name(record_type: str) -> str:
    """Return the backing collection name or raise."""
    if record_type not in RECORD_COLLECTIONS:
        raise ValueError(f"Unknown record type: {record_type}")
    return RECORD_COLLECTIONS[record_type]


def _prefix(record_type: str) -> str:
    """Return the ID prefix for a record type."""
    return {
        "customer": "cus",
        "vehicle": "veh",
        "user_group": "grp",
    }[record_type]


def _filter_records(records: list[Any], include_archived: bool, query: str) -> list[dict[str, Any]]:
    """Filter records for admin search."""
    needle = query.strip().lower()
    rows = []
    for record in records:
        row = record.as_dict()
        if not include_archived and row.get("status") == RecordStatus.ARCHIVED.value:
            continue
        if needle and needle not in " ".join(str(value) for value in row.values()).lower():
            continue
        rows.append(row)
    return rows


def _clean_fields(record_type: str, fields: dict[str, Any]) -> dict[str, Any]:
    """Keep only fields supported by each record type."""
    allowed = {
        "customer": {
            "id",
            "display_name",
            "contact_email",
            "contact_telephone",
            "apartment_unit_company",
            "billing_reference",
            "outlet_entity_ids",
            "billing_rate_per_kwh",
            "billing_currency",
            "user_group",
            "status",
            "notes",
        },
        "vehicle": {
            "id",
            "customer_id",
            "registration",
            "make_model_description",
            "notes",
            "status",
        },
        "user_group": {
            "id",
            "name",
            "default_tariff",
            "priority",
            "charging_allowed",
            "free_charging",
            "discount_percentage",
            "status",
        },
    }[record_type]
    return {key: value for key, value in fields.items() if key in allowed}
