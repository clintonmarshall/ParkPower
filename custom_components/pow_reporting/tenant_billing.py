"""Consolidated tenant statements for ParkPower billing sessions."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from hashlib import sha256
from typing import Any

from .models import BillingState, CustomerRecord, new_record_id, utc_now

MONEY_QUANTUM = Decimal("0.01")


class TenantBillingManager:
    """Build statements across all meters assigned to a tenant."""

    def __init__(self, billing_data: dict[str, Any], records_data: dict[str, Any]) -> None:
        """Initialize from the legacy billing store and local records store."""
        self.billing_data = billing_data
        self.customers = [
            CustomerRecord.from_dict(row)
            for row in records_data.get("customers", [])
            if isinstance(row, dict)
        ]
        self.user_groups = [
            row for row in records_data.get("user_groups", []) if isinstance(row, dict)
        ]
        self.sessions = [
            row for row in billing_data.get("completed", []) if isinstance(row, dict)
        ]
        self.statements = [
            row for row in billing_data.get("statements", []) if isinstance(row, dict)
        ]
        self.settings = billing_data.setdefault("settings", {})

    def dump(self) -> dict[str, Any]:
        """Return billing data with current statements and sessions."""
        self.billing_data["completed"] = self.sessions
        self.billing_data["statements"] = self.statements
        return self.billing_data

    def list_statements(self, customer_id: str = "") -> list[dict[str, Any]]:
        """Return statements newest first."""
        rows = self.statements
        if customer_id:
            rows = [row for row in rows if row.get("customer_id") == customer_id]
        return sorted(rows, key=lambda row: str(row.get("created_at") or ""), reverse=True)

    def create_statement(
        self,
        *,
        customer_id: str,
        period_start: str,
        period_end: str,
        rate_per_kwh: float | None = None,
        include_invoiced: bool = False,
    ) -> dict[str, Any]:
        """Create a draft from completed sessions explicitly linked to a tenant."""
        customer = self._customer(customer_id)
        start = _parse_time(period_start, "period_start")
        end = _parse_time(period_end, "period_end")
        if end <= start:
            raise ValueError("Billing period end must be after its start")

        configured_rate = customer.billing_rate_per_kwh or self.settings.get("energy_rate", 0)
        base_rate = Decimal(str(configured_rate if rate_per_kwh is None else rate_per_kwh))
        if not base_rate.is_finite() or base_rate < 0:
            raise ValueError("Billing rate must be a non-negative number")
        group = next(
            (row for row in self.user_groups if row.get("id") == customer.user_group),
            {},
        )
        discount = Decimal(str(group.get("discount_percentage") or 0))
        discount = min(Decimal("100"), max(Decimal(), discount))
        rate = Decimal() if group.get("free_charging") else base_rate * (Decimal("1") - discount / 100)

        eligible = []
        for session in self.sessions:
            if str(session.get("customer_id") or "") != customer_id:
                continue
            ended_at = _safe_parse_time(str(session.get("end_time") or ""))
            if ended_at is None or not start <= ended_at < end:
                continue
            status = str(session.get("billing_status") or BillingState.DRAFT.value)
            if not include_invoiced and status in {
                BillingState.INVOICED.value,
                BillingState.PAID.value,
                BillingState.WAIVED.value,
            }:
                continue
            eligible.append(session)

        eligible.sort(key=lambda row: str(row.get("end_time") or ""))
        line_items = [self._line_item(session, rate) for session in eligible]
        meter_summaries = _summarise_meters(line_items)
        total_energy = sum((Decimal(str(row["energy_kwh"])) for row in line_items), Decimal())
        total_amount = sum((Decimal(str(row["amount"])) for row in line_items), Decimal())
        statement = {
            "statement_id": new_record_id("stmt"),
            "customer_id": customer.id,
            "customer_name": customer.display_name,
            "billing_reference": customer.billing_reference,
            "email_to": customer.contact_email,
            "period_start": start.isoformat(timespec="seconds"),
            "period_end": end.isoformat(timespec="seconds"),
            "created_at": utc_now(),
            "status": BillingState.DRAFT.value,
            "currency": customer.billing_currency or self.settings.get("currency", "AUD"),
            "base_rate_per_kwh": float(base_rate),
            "rate_per_kwh": float(rate),
            "discount_percentage": float(discount),
            "session_count": len(line_items),
            "meter_count": len(meter_summaries),
            "total_energy_kwh": float(total_energy),
            "total_amount": float(total_amount.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)),
            "line_items": line_items,
            "meter_summaries": meter_summaries,
            "email_subject": "",
            "email_body": "",
            "sent_at": "",
            "notify_service": "",
            "delivery_attempts": [],
        }
        statement["email_subject"] = self._email_subject(statement)
        statement["email_body"] = self._email_body(statement)
        self.statements.append(statement)
        return statement

    def get_statement(self, statement_id: str) -> dict[str, Any] | None:
        """Return a stored statement by ID."""
        return next(
            (row for row in self.statements if row.get("statement_id") == statement_id),
            None,
        )

    def validate_send(self, statement_id: str) -> dict[str, Any]:
        """Prevent duplicate delivery or rebilling sessions from another draft."""
        statement = self.get_statement(statement_id)
        if statement is None:
            raise ValueError("Statement not found")
        if statement.get("status") == BillingState.INVOICED.value:
            raise ValueError("Statement has already been emailed")
        session_ids = {row.get("session_id") for row in statement.get("line_items", [])}
        if any(
            _session_id(session) in session_ids
            and session.get("billing_status") in {BillingState.INVOICED.value, BillingState.PAID.value}
            for session in self.sessions
        ):
            raise ValueError(
                "One or more sessions were already invoiced by another statement; create a new draft"
            )
        return statement

    def record_delivery(
        self,
        *,
        statement_id: str,
        notify_service: str,
        recipient: str,
        success: bool,
        error: str = "",
    ) -> dict[str, Any]:
        """Audit delivery and mark included sessions invoiced after success."""
        statement = self.get_statement(statement_id)
        if statement is None:
            raise ValueError("Statement not found")
        attempt = {
            "time": utc_now(),
            "notify_service": notify_service,
            "recipient": recipient,
            "success": success,
            "error": error,
        }
        statement.setdefault("delivery_attempts", []).append(attempt)
        if success:
            statement["status"] = BillingState.INVOICED.value
            statement["sent_at"] = attempt["time"]
            statement["notify_service"] = notify_service
            session_ids = {row.get("session_id") for row in statement.get("line_items", [])}
            for session in self.sessions:
                if _session_id(session) in session_ids:
                    session["billing_status"] = BillingState.INVOICED.value
        return statement

    def _customer(self, customer_id: str) -> CustomerRecord:
        customer = next((row for row in self.customers if row.id == customer_id), None)
        if customer is None:
            raise ValueError("Tenant not found")
        return customer

    def _line_item(self, session: dict[str, Any], rate: Decimal) -> dict[str, Any]:
        try:
            energy = Decimal(str(session.get("energy_kwh") or 0))
        except Exception:  # noqa: BLE001 - malformed historical energy is billed as zero for review
            energy = Decimal()
        override = session.get("billing_amount_override")
        amount = Decimal(str(override)) if override not in (None, "") else energy * rate
        amount = amount.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
        return {
            "session_id": _session_id(session),
            "switch_entity_id": str(session.get("switch_entity_id") or ""),
            "outlet_name": str(session.get("outlet_name") or session.get("switch_entity_id") or ""),
            "reference": str(session.get("reference") or ""),
            "start_time": str(session.get("start_time") or ""),
            "end_time": str(session.get("end_time") or ""),
            "energy_kwh": float(energy),
            "amount": float(amount),
        }

    def _email_subject(self, statement: dict[str, Any]) -> str:
        sender = self.settings.get("sender_name") or "ParkPower"
        reference = statement.get("billing_reference") or statement.get("customer_name")
        return f"{sender} power statement - {reference}"

    def _email_body(self, statement: dict[str, Any]) -> str:
        currency = statement["currency"]
        lines = [
            f"Hello {statement['customer_name']},",
            "",
            "Your car park power usage statement is ready.",
            f"Billing period: {_display_date(statement['period_start'])} to {_display_date(statement['period_end'])}",
            f"Billing reference: {statement['billing_reference'] or '-'}",
            f"Meters/outlets: {statement['meter_count']}",
            f"Charging sessions: {statement['session_count']}",
            f"Energy used: {statement['total_energy_kwh']:.3f} kWh",
            f"Amount: {currency} {statement['total_amount']:.2f}",
            "",
            "Usage by meter:",
        ]
        for meter in statement["meter_summaries"]:
            lines.append(
                f"- {meter['outlet_name']}: {meter['energy_kwh']:.3f} kWh, "
                f"{currency} {meter['amount']:.2f} ({meter['session_count']} sessions)"
            )
        lines.extend(["", "Regards,", self.settings.get("sender_name") or "ParkPower"])
        return "\n".join(lines)


def _session_id(session: dict[str, Any]) -> str:
    """Return a stable identifier for new and historical billing sessions."""
    if session.get("session_id"):
        return str(session["session_id"])
    source = "|".join(
        str(session.get(key) or "")
        for key in ("switch_entity_id", "start_time", "end_time", "start_energy_kwh", "end_energy_kwh")
    )
    return f"legacy_{sha256(source.encode()).hexdigest()[:20]}"


def _summarise_meters(line_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    meters: dict[str, dict[str, Any]] = {}
    for row in line_items:
        entity_id = row["switch_entity_id"]
        meter = meters.setdefault(
            entity_id,
            {
                "switch_entity_id": entity_id,
                "outlet_name": row["outlet_name"] or entity_id,
                "session_count": 0,
                "energy_kwh": 0.0,
                "amount": 0.0,
            },
        )
        meter["session_count"] += 1
        meter["energy_kwh"] += row["energy_kwh"]
        meter["amount"] += row["amount"]
    for meter in meters.values():
        meter["energy_kwh"] = round(meter["energy_kwh"], 6)
        meter["amount"] = round(meter["amount"], 2)
    return sorted(meters.values(), key=lambda row: row["outlet_name"].lower())


def _parse_time(value: str, field_name: str) -> datetime:
    parsed = _safe_parse_time(value)
    if parsed is None:
        raise ValueError(f"{field_name} must be an ISO date or timestamp")
    return parsed.astimezone() if parsed.tzinfo is None else parsed


def _safe_parse_time(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _display_date(value: str) -> str:
    parsed = _safe_parse_time(value)
    return parsed.strftime("%d %b %Y") if parsed else value
