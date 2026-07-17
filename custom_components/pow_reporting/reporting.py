"""Management reporting helpers for ParkPower."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Any


def build_management_report(
    *,
    billing_report: dict[str, Any],
    session_data: dict[str, Any],
    records: dict[str, Any],
    outlets: list[dict[str, Any]],
    filters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build Phase 4 reporting data from current local stores."""
    filters = filters or {}
    completed = [dict(session) for session in billing_report.get("completed", []) if isinstance(session, dict)]
    active = [dict(session) for session in billing_report.get("active", []) if isinstance(session, dict)]
    managed_sessions = [dict(session) for session in session_data.get("sessions", []) if isinstance(session, dict)]
    filtered_outlets = _filter_outlets(outlets, filters)
    filtered_completed = _filter_sessions(completed, filters)
    if _has_outlet_operational_filter(filters):
        outlet_ids = {outlet.get("id") for outlet in filtered_outlets}
        filtered_completed = [
            session
            for session in filtered_completed
            if session.get("switch_entity_id") in outlet_ids
        ]
    currency = billing_report.get("settings", {}).get("currency", "AUD")
    today_start = _start_of_day()
    month_start = today_start.replace(day=1)

    kpis = _build_kpis(
        outlets=filtered_outlets,
        active=active,
        completed=completed,
        filtered_completed=filtered_completed,
        managed_sessions=managed_sessions,
        today_start=today_start,
        month_start=month_start,
    )
    charts = _build_charts(filtered_completed, filtered_outlets)
    statement = _monthly_statement(filtered_completed, currency)
    rows = [_report_row(session, records) for session in filtered_completed]

    return {
        "filters": filters,
        "currency": currency,
        "kpis": kpis,
        "charts": charts,
        "statement": statement,
        "sessions": rows,
        "outlets": filtered_outlets,
        "load_management": session_data.get("load_management", {}),
        "gateways": _gateway_summary(outlets),
        "diagnostics": _diagnostics(outlets),
        "billing_states": ["draft", "approved", "invoiced", "paid", "waived", "disputed"],
        "connectivity_states": ["online", "offline", "stale", "gateway_unreachable"],
    }


def _filter_sessions(sessions: list[dict[str, Any]], filters: dict[str, Any]) -> list[dict[str, Any]]:
    """Apply report filters to completed billing sessions."""
    start = _parse_time(filters.get("start"))
    end = _parse_time(filters.get("end"))
    outlet = str(filters.get("outlet") or "").strip()
    reference = str(filters.get("reference") or "").strip().lower()
    billing_status = str(filters.get("billing_status") or "").strip()
    rows = []
    for session in sessions:
        session_time = _parse_time(session.get("end_time") or session.get("start_time"))
        if start and session_time and session_time < start:
            continue
        if end and session_time and session_time > end:
            continue
        if outlet and session.get("switch_entity_id") != outlet:
            continue
        if reference and reference not in str(session.get("reference") or "").lower():
            continue
        if billing_status and str(session.get("billing_status") or "draft") != billing_status:
            continue
        rows.append(session)
    return rows


def _filter_outlets(outlets: list[dict[str, Any]], filters: dict[str, Any]) -> list[dict[str, Any]]:
    """Apply operational filters to current outlet rows."""
    gateway = str(filters.get("gateway") or "").strip().lower()
    zone = str(filters.get("zone") or "").strip().lower()
    connectivity = str(filters.get("connectivity") or "").strip().lower()
    stale_offline = bool(filters.get("stale_offline"))
    rows = []
    for outlet in outlets:
        outlet_gateway = outlet.get("gateway") or {}
        gateway_text = " ".join(
            str(value or "")
            for value in [
                outlet_gateway.get("gateway_id"),
                outlet_gateway.get("gateway_name"),
            ]
        ).lower()
        zone_text = str(outlet_gateway.get("gateway_zone") or outlet.get("area") or outlet.get("level") or "").lower()
        state = str(outlet.get("connectivity_state") or "").lower()
        if gateway and gateway not in gateway_text:
            continue
        if zone and zone not in zone_text:
            continue
        if connectivity and state != connectivity:
            continue
        if stale_offline and state not in {"offline", "stale", "gateway_unreachable"}:
            continue
        rows.append(dict(outlet))
    return rows


def _has_outlet_operational_filter(filters: dict[str, Any]) -> bool:
    """Return true when filters depend on current outlet metadata."""
    return any(filters.get(key) for key in ("gateway", "zone", "connectivity", "stale_offline"))


def _build_kpis(
    *,
    outlets: list[dict[str, Any]],
    active: list[dict[str, Any]],
    completed: list[dict[str, Any]],
    filtered_completed: list[dict[str, Any]],
    managed_sessions: list[dict[str, Any]],
    today_start: datetime,
    month_start: datetime,
) -> dict[str, Any]:
    """Calculate management KPIs."""
    outlet_count = len(outlets)
    outlet_states = Counter(str(outlet.get("state") or "unknown") for outlet in outlets)
    connectivity_states = Counter(str(outlet.get("connectivity_state") or "unknown") for outlet in outlets)
    active_ids = {session.get("switch_entity_id") for session in active}
    waiting_states = {"authorised", "waiting_for_load", "idle_grace_period"}
    waiting = sum(1 for session in managed_sessions if session.get("state") in waiting_states)
    paused = sum(1 for session in managed_sessions if session.get("state") == "paused_load_limit")
    faulted = sum(1 for session in managed_sessions if session.get("state") in {"fault", "requires_review"})
    today_sessions = [session for session in completed if _after(session.get("end_time"), today_start)]
    month_sessions = [session for session in completed if _after(session.get("end_time"), month_start)]
    energy_total = sum(_number(session.get("energy_kwh")) for session in filtered_completed)
    cost_total = sum(_number(session.get("cost")) for session in filtered_completed)
    duration_total = sum(_number(session.get("duration_seconds")) for session in filtered_completed)
    live_power = sum(_number(outlet.get("power_w")) for outlet in outlets if outlet.get("connectivity_state") == "online")
    average_sessions = max(len(filtered_completed), 1)

    return {
        "total_managed_outlets": outlet_count,
        "available_outlets": max(0, outlet_count - len(active_ids)),
        "active_charging_outlets": len(active_ids),
        "waiting_outlets": waiting,
        "load_managed_paused_outlets": paused,
        "offline_outlets": (
            outlet_states.get("unavailable", 0)
            + outlet_states.get("unknown", 0)
            + connectivity_states.get("offline", 0)
            + connectivity_states.get("gateway_unreachable", 0)
        ),
        "stale_outlets": connectivity_states.get("stale", 0),
        "gateway_unreachable_outlets": connectivity_states.get("gateway_unreachable", 0),
        "faulted_outlets": faulted,
        "sessions_today": len(today_sessions),
        "energy_today": round(sum(_number(session.get("energy_kwh")) for session in today_sessions), 4),
        "energy_this_month": round(sum(_number(session.get("energy_kwh")) for session in month_sessions), 4),
        "estimated_cost_recovery": round(cost_total, 4),
        "peak_charging_demand": round(live_power, 2),
        "average_kwh_per_session": round(energy_total / average_sessions, 4),
        "average_charging_duration": round(duration_total / average_sessions),
        "utilisation_percentage": round((len(active_ids) / outlet_count) * 100, 2) if outlet_count else 0,
    }


def _build_charts(sessions: list[dict[str, Any]], outlets: list[dict[str, Any]]) -> dict[str, Any]:
    """Return small chart-ready series."""
    energy_by_day: defaultdict[str, float] = defaultdict(float)
    sessions_by_day: defaultdict[str, int] = defaultdict(int)
    charging_by_hour: defaultdict[str, float] = defaultdict(float)
    charging_by_weekday: defaultdict[str, float] = defaultdict(float)
    top_outlets: defaultdict[str, float] = defaultdict(float)
    costs: defaultdict[str, float] = defaultdict(float)

    for session in sessions:
        end_time = _parse_time(session.get("end_time") or session.get("start_time"))
        if end_time is None:
            continue
        day = end_time.date().isoformat()
        hour = f"{end_time.hour:02d}:00"
        weekday = end_time.strftime("%A")
        energy = _number(session.get("energy_kwh"))
        cost = _number(session.get("cost"))
        outlet = str(session.get("outlet_name") or session.get("switch_entity_id") or "Unknown")
        energy_by_day[day] += energy
        sessions_by_day[day] += 1
        charging_by_hour[hour] += energy
        charging_by_weekday[weekday] += energy
        top_outlets[outlet] += energy
        costs[day] += cost

    return {
        "energy_by_day": _series(energy_by_day),
        "sessions_by_day": _series(sessions_by_day),
        "charging_by_hour": _series(charging_by_hour),
        "charging_by_day_of_week": _series(charging_by_weekday),
        "top_outlets": _top_series(top_outlets),
        "top_customers": [],
        "energy_by_user_group": [],
        "costs_and_recoverable_amounts": _series(costs),
        "site_load_vs_limit": [
            {"label": outlet.get("name") or outlet.get("id"), "value": _number(outlet.get("power_w"))}
            for outlet in outlets
            if _number(outlet.get("power_w")) > 0
        ],
}


def _gateway_summary(outlets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return one current row per configured gateway."""
    gateways: dict[str, dict[str, Any]] = {}
    for outlet in outlets:
        gateway = outlet.get("gateway") or {}
        gateway_id = str(gateway.get("gateway_id") or gateway.get("gateway_name") or "")
        if not gateway_id:
            continue
        row = gateways.setdefault(
            gateway_id,
            {
                **gateway,
                "outlet_count": 0,
                "offline_outlets": 0,
                "stale_outlets": 0,
                "mqtt_outlets": 0,
            },
        )
        row["outlet_count"] += 1
        if outlet.get("connectivity_state") in {"offline", "gateway_unreachable"}:
            row["offline_outlets"] += 1
        if outlet.get("connectivity_state") == "stale":
            row["stale_outlets"] += 1
        if outlet.get("source_type") == "mqtt":
            row["mqtt_outlets"] += 1
    return sorted(gateways.values(), key=lambda row: str(row.get("gateway_name") or row.get("gateway_id") or ""))


def _diagnostics(outlets: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Return admin diagnostics for network-aware deployments."""
    gateways = _gateway_summary(outlets)
    return {
        "outlets_with_no_gateway_mapping": [
            _outlet_diag(outlet)
            for outlet in outlets
            if not (outlet.get("gateway") or {}).get("gateway_id") and not (outlet.get("gateway") or {}).get("gateway_name")
        ],
        "gateways_with_too_many_clients": [
            gateway
            for gateway in gateways
            if _number(gateway.get("gateway_client_count")) > 12
        ],
        "weak_rssi_outlets": [
            _outlet_diag(outlet)
            for outlet in outlets
            if outlet.get("outlet_rssi") is not None and _number(outlet.get("outlet_rssi")) < -75
        ],
        "offline_stale_outlets": [
            _outlet_diag(outlet)
            for outlet in outlets
            if outlet.get("connectivity_state") in {"offline", "stale", "gateway_unreachable"}
        ],
        "gateways_with_weak_uplink_rssi": [
            gateway
            for gateway in gateways
            if gateway.get("gateway_uplink_rssi") is not None and _number(gateway.get("gateway_uplink_rssi")) < -70
        ],
        "sonoffs_connected_through_nat_mqtt": [
            _outlet_diag(outlet)
            for outlet in outlets
            if outlet.get("source_type") == "mqtt" or outlet.get("mqtt_topic_prefix")
        ],
    }


def _outlet_diag(outlet: dict[str, Any]) -> dict[str, Any]:
    """Return compact diagnostic outlet data."""
    gateway = outlet.get("gateway") or {}
    return {
        "id": outlet.get("id"),
        "name": outlet.get("name"),
        "area": outlet.get("area"),
        "level": outlet.get("level"),
        "bay": outlet.get("bay"),
        "connectivity_state": outlet.get("connectivity_state"),
        "outlet_rssi": outlet.get("outlet_rssi"),
        "source_type": outlet.get("source_type"),
        "gateway": gateway.get("gateway_name") or gateway.get("gateway_id") or "",
    }


def _monthly_statement(sessions: list[dict[str, Any]], currency: str) -> dict[str, Any]:
    """Build current monthly statement totals."""
    energy = sum(_number(session.get("energy_kwh")) for session in sessions)
    cost = sum(_number(session.get("cost")) for session in sessions)
    invoiced = sum(_number(session.get("cost")) for session in sessions if session.get("billing_status") == "invoiced")
    paid = sum(_number(session.get("cost")) for session in sessions if session.get("billing_status") == "paid")
    waived = sum(_number(session.get("cost")) for session in sessions if session.get("billing_status") == "waived")
    return {
        "currency": currency,
        "billing_period": "filtered",
        "total_measured_charging_energy": round(energy, 4),
        "underlying_electricity_cost": round(cost, 4),
        "energy_charges": round(cost, 4),
        "session_charges": 0,
        "management_fees": 0,
        "discounts": 0,
        "waived_sessions": round(waived, 4),
        "manual_adjustments": 0,
        "total_recoverable_amount": round(max(0, cost - waived), 4),
        "amount_marked_invoiced": round(invoiced, 4),
        "amount_marked_paid": round(paid, 4),
        "outstanding_amount": round(max(0, cost - waived - paid), 4),
    }


def _report_row(session: dict[str, Any], records: dict[str, Any]) -> dict[str, Any]:
    """Return a CSV/report-friendly session row."""
    return {
        "start": session.get("start_time", ""),
        "end": session.get("end_time", ""),
        "outlet": session.get("outlet_name", ""),
        "switch_entity_id": session.get("switch_entity_id", ""),
        "reference": session.get("reference", ""),
        "customer": _record_name(records.get("customers", []), session.get("customer_id")),
        "vehicle": _record_name(records.get("vehicles", []), session.get("vehicle_id"), "registration"),
        "user_group": _record_name(records.get("user_groups", []), session.get("user_group"), "name"),
        "duration_seconds": session.get("duration_seconds", 0),
        "energy_kwh": session.get("energy_kwh", 0),
        "cost": session.get("cost", 0),
        "billing_status": session.get("billing_status", "draft"),
        "currency": session.get("currency", ""),
    }


def _series(values: dict[str, float | int]) -> list[dict[str, Any]]:
    return [{"label": key, "value": round(value, 4) if isinstance(value, float) else value} for key, value in sorted(values.items())]


def _top_series(values: dict[str, float], limit: int = 10) -> list[dict[str, Any]]:
    rows = sorted(values.items(), key=lambda item: item[1], reverse=True)[:limit]
    return [{"label": key, "value": round(value, 4)} for key, value in rows]


def _record_name(records: list[dict[str, Any]], record_id: Any, key: str = "display_name") -> str:
    return next((str(record.get(key) or "") for record in records if record.get("id") == record_id), "")


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _start_of_day() -> datetime:
    now = datetime.now().astimezone()
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _after(value: Any, threshold: datetime) -> bool:
    parsed = _parse_time(value)
    return bool(parsed and parsed >= threshold)


def default_filter_period(period: str) -> dict[str, str]:
    """Return start/end ISO filters for a named period."""
    now = datetime.now().astimezone()
    start = _start_of_day()
    if period == "week":
        start = start - timedelta(days=6)
    elif period == "month":
        start = start.replace(day=1)
    elif period == "all":
        return {}
    return {"start": start.isoformat(timespec="seconds"), "end": now.isoformat(timespec="seconds")}


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
