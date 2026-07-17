"""Data models for ParkPower managed charging sessions."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4


class SessionState(StrEnum):
    """Known lifecycle states for a managed outlet session."""

    AVAILABLE = "available"
    AUTHORISED = "authorised"
    WAITING_FOR_LOAD = "waiting_for_load"
    CHARGING = "charging"
    IDLE_GRACE_PERIOD = "idle_grace_period"
    PAUSED_LOAD_LIMIT = "paused_load_limit"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAULT = "fault"
    OFFLINE = "offline"
    REQUIRES_REVIEW = "requires_review"


class BillingState(StrEnum):
    """Billing lifecycle for a completed session."""

    DRAFT = "draft"
    APPROVED = "approved"
    INVOICED = "invoiced"
    PAID = "paid"
    WAIVED = "waived"
    DISPUTED = "disputed"


class RecordStatus(StrEnum):
    """Lifecycle for locally managed admin records."""

    ACTIVE = "active"
    ARCHIVED = "archived"


TERMINAL_SESSION_STATES = {
    SessionState.COMPLETED,
    SessionState.CANCELLED,
    SessionState.FAULT,
}


def utc_now() -> str:
    """Return an ISO timestamp suitable for storage."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def new_record_id(prefix: str) -> str:
    """Return a stable local record ID."""
    return f"{prefix}_{uuid4().hex[:12]}"


@dataclass(slots=True)
class SessionEvent:
    """An auditable event in a charging session timeline."""

    time: str
    event: str
    from_state: str | None = None
    to_state: str | None = None
    reason: str = ""
    note: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable representation."""
        return {
            "time": self.time,
            "event": self.event,
            "from_state": self.from_state,
            "to_state": self.to_state,
            "reason": self.reason,
            "note": self.note,
            "data": self.data,
        }


@dataclass(slots=True)
class ChargingSession:
    """Stored state for an active or completed outlet session."""

    session_id: str
    outlet_entity_id: str
    outlet_display_name: str
    state: SessionState
    site: str = ""
    level: str = ""
    area: str = ""
    bay: str = ""
    customer_reference: str = ""
    customer_id: str = ""
    vehicle_id: str = ""
    user_group: str = ""
    start_timestamp: str = ""
    charging_start_timestamp: str = ""
    end_timestamp: str = ""
    start_meter_reading: float | None = None
    end_meter_reading: float | None = None
    energy_consumed_kwh: float = 0.0
    elapsed_session_seconds: int = 0
    active_charging_seconds: int = 0
    paused_seconds: int = 0
    tariff_snapshot: dict[str, Any] = field(default_factory=dict)
    cost_components: dict[str, Any] = field(default_factory=dict)
    completion_reason: str = ""
    billing_status: BillingState = BillingState.DRAFT
    adjustment_history: list[dict[str, Any]] = field(default_factory=list)
    session_event_timeline: list[dict[str, Any]] = field(default_factory=list)
    last_power_watts: float | None = None
    last_meter_reading: float | None = None
    last_meter_timestamp: str = ""
    above_threshold_since: str = ""
    below_threshold_since: str = ""

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable representation."""
        return {
            "session_id": self.session_id,
            "outlet_entity_id": self.outlet_entity_id,
            "outlet_display_name": self.outlet_display_name,
            "state": self.state.value,
            "site": self.site,
            "level": self.level,
            "area": self.area,
            "bay": self.bay,
            "customer_reference": self.customer_reference,
            "customer_id": self.customer_id,
            "vehicle_id": self.vehicle_id,
            "user_group": self.user_group,
            "start_timestamp": self.start_timestamp,
            "charging_start_timestamp": self.charging_start_timestamp,
            "end_timestamp": self.end_timestamp,
            "start_meter_reading": self.start_meter_reading,
            "end_meter_reading": self.end_meter_reading,
            "energy_consumed_kwh": self.energy_consumed_kwh,
            "elapsed_session_seconds": self.elapsed_session_seconds,
            "active_charging_seconds": self.active_charging_seconds,
            "paused_seconds": self.paused_seconds,
            "tariff_snapshot": self.tariff_snapshot,
            "cost_components": self.cost_components,
            "completion_reason": self.completion_reason,
            "billing_status": self.billing_status.value,
            "adjustment_history": self.adjustment_history,
            "session_event_timeline": self.session_event_timeline,
            "last_power_watts": self.last_power_watts,
            "last_meter_reading": self.last_meter_reading,
            "last_meter_timestamp": self.last_meter_timestamp,
            "above_threshold_since": self.above_threshold_since,
            "below_threshold_since": self.below_threshold_since,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ChargingSession":
        """Build a session from storage, tolerating older/missing fields."""
        state = _enum_value(
            SessionState,
            data.get("state"),
            SessionState.WAITING_FOR_LOAD,
        )
        billing_status = _enum_value(
            BillingState,
            data.get("billing_status"),
            BillingState.DRAFT,
        )
        return cls(
            session_id=str(data["session_id"]),
            outlet_entity_id=str(data["outlet_entity_id"]),
            outlet_display_name=str(data.get("outlet_display_name") or data["outlet_entity_id"]),
            state=state,
            site=str(data.get("site") or ""),
            level=str(data.get("level") or ""),
            area=str(data.get("area") or ""),
            bay=str(data.get("bay") or ""),
            customer_reference=str(data.get("customer_reference") or data.get("reference") or ""),
            customer_id=str(data.get("customer_id") or ""),
            vehicle_id=str(data.get("vehicle_id") or ""),
            user_group=str(data.get("user_group") or ""),
            start_timestamp=str(data.get("start_timestamp") or ""),
            charging_start_timestamp=str(data.get("charging_start_timestamp") or ""),
            end_timestamp=str(data.get("end_timestamp") or ""),
            start_meter_reading=_optional_float(data.get("start_meter_reading")),
            end_meter_reading=_optional_float(data.get("end_meter_reading")),
            energy_consumed_kwh=float(data.get("energy_consumed_kwh") or 0),
            elapsed_session_seconds=int(data.get("elapsed_session_seconds") or 0),
            active_charging_seconds=int(data.get("active_charging_seconds") or 0),
            paused_seconds=int(data.get("paused_seconds") or 0),
            tariff_snapshot=dict(data.get("tariff_snapshot") or {}),
            cost_components=dict(data.get("cost_components") or {}),
            completion_reason=str(data.get("completion_reason") or ""),
            billing_status=billing_status,
            adjustment_history=list(data.get("adjustment_history") or []),
            session_event_timeline=list(data.get("session_event_timeline") or []),
            last_power_watts=_optional_float(data.get("last_power_watts")),
            last_meter_reading=_optional_float(data.get("last_meter_reading")),
            last_meter_timestamp=str(data.get("last_meter_timestamp") or ""),
            above_threshold_since=str(data.get("above_threshold_since") or ""),
            below_threshold_since=str(data.get("below_threshold_since") or ""),
        )


def _optional_float(value: Any) -> float | None:
    """Return a float when storage supplied a numeric value."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _enum_value(enum_type: Any, value: Any, default: Any) -> Any:
    """Return a valid enum value, falling back for older/corrupt storage."""
    try:
        return enum_type(value)
    except (TypeError, ValueError):
        return default


@dataclass(slots=True)
class CustomerRecord:
    """Locally stored customer account."""

    id: str
    display_name: str
    contact_email: str = ""
    contact_telephone: str = ""
    apartment_unit_company: str = ""
    billing_reference: str = ""
    outlet_entity_ids: list[str] = field(default_factory=list)
    billing_rate_per_kwh: float = 0.0
    billing_currency: str = "AUD"
    user_group: str = ""
    status: RecordStatus = RecordStatus.ACTIVE
    notes: str = ""
    created_at: str = ""
    updated_at: str = ""

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable representation."""
        return {
            "id": self.id,
            "display_name": self.display_name,
            "contact_email": self.contact_email,
            "contact_telephone": self.contact_telephone,
            "apartment_unit_company": self.apartment_unit_company,
            "billing_reference": self.billing_reference,
            "outlet_entity_ids": self.outlet_entity_ids,
            "billing_rate_per_kwh": self.billing_rate_per_kwh,
            "billing_currency": self.billing_currency,
            "user_group": self.user_group,
            "status": self.status.value,
            "notes": self.notes,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CustomerRecord":
        """Build a customer record from storage."""
        return cls(
            id=str(data.get("id") or new_record_id("cus")),
            display_name=str(data.get("display_name") or ""),
            contact_email=str(data.get("contact_email") or ""),
            contact_telephone=str(data.get("contact_telephone") or ""),
            apartment_unit_company=str(data.get("apartment_unit_company") or ""),
            billing_reference=str(data.get("billing_reference") or ""),
            outlet_entity_ids=[
                str(entity_id)
                for entity_id in data.get("outlet_entity_ids", [])
                if entity_id
            ],
            billing_rate_per_kwh=float(data.get("billing_rate_per_kwh") or 0),
            billing_currency=str(data.get("billing_currency") or "AUD").upper(),
            user_group=str(data.get("user_group") or ""),
            status=_enum_value(RecordStatus, data.get("status"), RecordStatus.ACTIVE),
            notes=str(data.get("notes") or ""),
            created_at=str(data.get("created_at") or ""),
            updated_at=str(data.get("updated_at") or ""),
        )


@dataclass(slots=True)
class VehicleRecord:
    """Locally stored customer vehicle."""

    id: str
    customer_id: str
    registration: str
    make_model_description: str = ""
    notes: str = ""
    status: RecordStatus = RecordStatus.ACTIVE
    created_at: str = ""
    updated_at: str = ""

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable representation."""
        return {
            "id": self.id,
            "customer_id": self.customer_id,
            "registration": self.registration,
            "make_model_description": self.make_model_description,
            "notes": self.notes,
            "status": self.status.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "VehicleRecord":
        """Build a vehicle record from storage."""
        return cls(
            id=str(data.get("id") or new_record_id("veh")),
            customer_id=str(data.get("customer_id") or ""),
            registration=str(data.get("registration") or ""),
            make_model_description=str(data.get("make_model_description") or ""),
            notes=str(data.get("notes") or ""),
            status=_enum_value(RecordStatus, data.get("status"), RecordStatus.ACTIVE),
            created_at=str(data.get("created_at") or ""),
            updated_at=str(data.get("updated_at") or ""),
        )


@dataclass(slots=True)
class UserGroupRecord:
    """Locally stored user group."""

    id: str
    name: str
    default_tariff: str = ""
    priority: int = 0
    charging_allowed: bool = True
    free_charging: bool = False
    discount_percentage: float = 0.0
    status: RecordStatus = RecordStatus.ACTIVE
    created_at: str = ""
    updated_at: str = ""

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable representation."""
        return {
            "id": self.id,
            "name": self.name,
            "default_tariff": self.default_tariff,
            "priority": self.priority,
            "charging_allowed": self.charging_allowed,
            "free_charging": self.free_charging,
            "discount_percentage": self.discount_percentage,
            "status": self.status.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "UserGroupRecord":
        """Build a user-group record from storage."""
        return cls(
            id=str(data.get("id") or new_record_id("grp")),
            name=str(data.get("name") or ""),
            default_tariff=str(data.get("default_tariff") or ""),
            priority=int(data.get("priority") or 0),
            charging_allowed=bool(data.get("charging_allowed", True)),
            free_charging=bool(data.get("free_charging", False)),
            discount_percentage=float(data.get("discount_percentage") or 0),
            status=_enum_value(RecordStatus, data.get("status"), RecordStatus.ACTIVE),
            created_at=str(data.get("created_at") or ""),
            updated_at=str(data.get("updated_at") or ""),
        )
