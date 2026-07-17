"""Load-management policy engine for ParkPower electrical groups."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from .models import RecordStatus

MODE_MONITOR_ONLY = "monitor_only"
MODE_PRIORITY = "priority"
MODE_FIRST_IN_FIRST_OUT = "first_in_first_out"
MODE_MANUAL = "manual"

ACTION_PAUSE = "pause"
ACTION_RESUME = "resume"


@dataclass(slots=True)
class LoadManagementAction:
    """One relay action recommended by the load-management engine."""

    action: str
    switch_entity_id: str
    circuit_group_id: str
    circuit_group_name: str
    reason: str

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-safe action row."""
        return {
            "action": self.action,
            "switch_entity_id": self.switch_entity_id,
            "circuit_group_id": self.circuit_group_id,
            "circuit_group_name": self.circuit_group_name,
            "reason": self.reason,
        }


class LoadManagementEngine:
    """Decide when outlets should be paused or resumed from hierarchy limits."""

    def __init__(
        self,
        *,
        hierarchy: dict[str, Any],
        outlet_power_w: dict[str, float],
        switch_states: dict[str, str],
        active_sessions: dict[str, dict[str, Any]],
        paused_outlets: dict[str, dict[str, Any]] | None = None,
        relay_events: dict[str, list[str]] | None = None,
        now: datetime | None = None,
    ) -> None:
        """Initialize the engine with live state and persisted relay history."""
        self.hierarchy = hierarchy
        self.outlet_power_w = outlet_power_w
        self.switch_states = switch_states
        self.active_sessions = active_sessions
        self.paused_outlets = paused_outlets or {}
        self.relay_events = relay_events or {}
        self.now = now or datetime.now().astimezone()

    def evaluate(self) -> dict[str, Any]:
        """Return recommended relay actions and group status rows."""
        actions: list[LoadManagementAction] = []
        groups = []
        for group in self._active_circuit_groups():
            group_outlets = self._outlets_for_group(group)
            total_power = sum(_number(self.outlet_power_w.get(outlet["switch_entity_id"])) for outlet in group_outlets)
            active_outlets = [
                outlet
                for outlet in group_outlets
                if self.switch_states.get(outlet["switch_entity_id"]) == "on"
            ]
            paused_in_group = [
                outlet
                for outlet in group_outlets
                if outlet["switch_entity_id"] in self.paused_outlets
            ]
            limit_w = _effective_limit(group)
            max_simultaneous = int(_number(group.get("maximum_simultaneous_outlets")))
            mode = str(group.get("load_management_mode") or MODE_MONITOR_ONLY)
            over_limit = bool(limit_w and total_power > limit_w)
            over_count = bool(max_simultaneous and len(active_outlets) > max_simultaneous)

            if mode not in {MODE_MONITOR_ONLY, MODE_MANUAL}:
                if over_limit or over_count:
                    actions.extend(self._pause_actions(group, active_outlets, total_power, limit_w, max_simultaneous))
                elif paused_in_group and self._has_resume_capacity(group, total_power, len(active_outlets)):
                    actions.extend(self._resume_actions(group, paused_in_group, total_power, limit_w, max_simultaneous, len(active_outlets)))

            groups.append(
                {
                    "id": group.get("id"),
                    "name": group.get("name"),
                    "mode": mode,
                    "enabled": group.get("enabled", True) is not False,
                    "power_w": round(total_power, 2),
                    "effective_limit_w": limit_w,
                    "warning_threshold_w": _number_or_none(group.get("warning_threshold")),
                    "active_outlets": len(active_outlets),
                    "paused_outlets": len(paused_in_group),
                    "maximum_simultaneous_outlets": max_simultaneous or None,
                    "over_limit": over_limit,
                    "over_simultaneous_limit": over_count,
                }
            )

        return {
            "actions": [action.as_dict() for action in actions],
            "groups": groups,
        }

    def _active_circuit_groups(self) -> list[dict[str, Any]]:
        """Return enabled, non-archived circuit groups."""
        return [
            group
            for group in self.hierarchy.get("circuit_groups", [])
            if group.get("status", RecordStatus.ACTIVE.value) != RecordStatus.ARCHIVED.value
            and group.get("enabled", True) is not False
        ]

    def _outlets_for_group(self, group: dict[str, Any]) -> list[dict[str, Any]]:
        """Return active outlet mappings assigned to one circuit group."""
        group_id = group.get("id")
        return [
            outlet
            for outlet in self.hierarchy.get("outlet_mappings", [])
            if outlet.get("circuit_group_id") == group_id
            and outlet.get("switch_entity_id")
            and outlet.get("status", RecordStatus.ACTIVE.value) != RecordStatus.ARCHIVED.value
        ]

    def _pause_actions(
        self,
        group: dict[str, Any],
        active_outlets: list[dict[str, Any]],
        total_power: float,
        limit_w: float | None,
        max_simultaneous: int,
    ) -> list[LoadManagementAction]:
        """Recommend pause actions until the group is back under limits."""
        remaining_power = total_power
        remaining_count = len(active_outlets)
        actions: list[LoadManagementAction] = []
        for outlet in self._pause_order(group, active_outlets):
            switch_id = outlet["switch_entity_id"]
            if switch_id in self.paused_outlets or not self._relay_allowed(group, switch_id, "off"):
                continue
            if not _active_session(self.active_sessions, switch_id):
                continue
            actions.append(
                LoadManagementAction(
                    action=ACTION_PAUSE,
                    switch_entity_id=switch_id,
                    circuit_group_id=str(group.get("id") or ""),
                    circuit_group_name=str(group.get("name") or ""),
                    reason="load_limit_exceeded",
                )
            )
            remaining_power -= _number(self.outlet_power_w.get(switch_id))
            remaining_count -= 1
            if (not limit_w or remaining_power <= limit_w) and (not max_simultaneous or remaining_count <= max_simultaneous):
                break
        return actions

    def _resume_actions(
        self,
        group: dict[str, Any],
        paused_outlets: list[dict[str, Any]],
        total_power: float,
        limit_w: float | None,
        max_simultaneous: int,
        active_count: int,
    ) -> list[LoadManagementAction]:
        """Recommend one safe resume action for a group with spare capacity."""
        actions: list[LoadManagementAction] = []
        for outlet in self._resume_order(group, paused_outlets):
            switch_id = outlet["switch_entity_id"]
            if not self._relay_allowed(group, switch_id, "on"):
                continue
            estimated_power = max(_number(self.outlet_power_w.get(switch_id)), _number(group.get("warning_threshold")))
            if limit_w and total_power + estimated_power > limit_w:
                continue
            if max_simultaneous and active_count + 1 > max_simultaneous:
                continue
            actions.append(
                LoadManagementAction(
                    action=ACTION_RESUME,
                    switch_entity_id=switch_id,
                    circuit_group_id=str(group.get("id") or ""),
                    circuit_group_name=str(group.get("name") or ""),
                    reason="capacity_available",
                )
            )
            break
        return actions

    def _pause_order(self, group: dict[str, Any], outlets: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return outlets in the order they should be paused."""
        mode = str(group.get("load_management_mode") or MODE_MONITOR_ONLY)
        if mode == MODE_FIRST_IN_FIRST_OUT:
            return sorted(outlets, key=lambda outlet: _session_started(self.active_sessions, outlet["switch_entity_id"]) or datetime.min.replace(tzinfo=self.now.tzinfo), reverse=True)
        return sorted(
            outlets,
            key=lambda outlet: (
                _session_priority(self.active_sessions, outlet["switch_entity_id"]),
                -_number(self.outlet_power_w.get(outlet["switch_entity_id"])),
            ),
        )

    def _resume_order(self, group: dict[str, Any], outlets: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return paused outlets in the order they should be resumed."""
        mode = str(group.get("load_management_mode") or MODE_MONITOR_ONLY)
        if mode == MODE_FIRST_IN_FIRST_OUT:
            return sorted(outlets, key=lambda outlet: _paused_since(self.paused_outlets, outlet["switch_entity_id"]) or self.now)
        return sorted(
            outlets,
            key=lambda outlet: (
                -_session_priority(self.active_sessions, outlet["switch_entity_id"]),
                _paused_since(self.paused_outlets, outlet["switch_entity_id"]) or self.now,
            ),
        )

    def _has_resume_capacity(self, group: dict[str, Any], total_power: float, active_count: int) -> bool:
        """Return true when a circuit group has obvious spare capacity."""
        limit_w = _effective_limit(group)
        max_simultaneous = int(_number(group.get("maximum_simultaneous_outlets")))
        if limit_w and total_power >= limit_w:
            return False
        if max_simultaneous and active_count >= max_simultaneous:
            return False
        return True

    def _relay_allowed(self, group: dict[str, Any], switch_id: str, direction: str) -> bool:
        """Return false when relay timing/operation guardrails block an action."""
        history = [_parse_time(value) for value in self.relay_events.get(switch_id, [])]
        history = [value for value in history if value is not None]
        last_event = max(history) if history else None
        if last_event:
            field = "minimum_relay_on_duration" if direction == "off" else "minimum_relay_off_duration"
            minimum_seconds = _number(group.get(field))
            if minimum_seconds and (self.now - last_event).total_seconds() < minimum_seconds:
                return False
        max_hourly = int(_number(group.get("maximum_relay_operations_per_hour")))
        if max_hourly:
            recent = [value for value in history if self.now - value <= timedelta(hours=1)]
            if len(recent) >= max_hourly:
                return False
        return True


def _effective_limit(group: dict[str, Any]) -> float | None:
    """Return usable watts after reserve margin."""
    maximum_power = _number_or_none(group.get("maximum_power"))
    if maximum_power is None:
        return None
    return max(0.0, maximum_power - _number(group.get("reserve_margin")))


def _active_session(active_sessions: dict[str, dict[str, Any]], switch_id: str) -> dict[str, Any] | None:
    """Return an active session-like row for an outlet."""
    session = active_sessions.get(switch_id)
    return session if isinstance(session, dict) else None


def _session_started(active_sessions: dict[str, dict[str, Any]], switch_id: str) -> datetime | None:
    """Return the session start time for an outlet."""
    session = _active_session(active_sessions, switch_id)
    return _parse_time(session.get("start_timestamp") or session.get("start_time")) if session else None


def _session_priority(active_sessions: dict[str, dict[str, Any]], switch_id: str) -> int:
    """Return a numeric priority, where higher means more protected."""
    session = _active_session(active_sessions, switch_id)
    try:
        return int(session.get("priority", 0) if session else 0)
    except (TypeError, ValueError):
        return 0


def _paused_since(paused_outlets: dict[str, dict[str, Any]], switch_id: str) -> datetime | None:
    """Return when an outlet was paused by load management."""
    row = paused_outlets.get(switch_id)
    return _parse_time(row.get("paused_at")) if isinstance(row, dict) else None


def _parse_time(value: Any) -> datetime | None:
    """Parse ISO timestamps safely."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _number_or_none(value: Any) -> float | None:
    """Return a float or None for blank values."""
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _number(value: Any) -> float:
    """Return a float, defaulting invalid values to zero."""
    return _number_or_none(value) or 0.0
