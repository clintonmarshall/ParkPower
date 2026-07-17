"""Tests for ParkPower load-management policy decisions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import types
from unittest import TestCase

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "pow_reporting"
package = types.ModuleType("custom_components.pow_reporting")
package.__path__ = [str(PACKAGE_ROOT)]
sys.modules.setdefault("custom_components.pow_reporting", package)

from custom_components.pow_reporting.load_management import LoadManagementEngine


class LoadManagementEngineTest(TestCase):
    """Validate circuit load-management decisions."""

    def _hierarchy(self, **group_overrides):
        group = {
            "id": "cct_1",
            "name": "EV Row",
            "enabled": True,
            "maximum_power": 5000,
            "reserve_margin": 500,
            "warning_threshold": 1000,
            "maximum_simultaneous_outlets": 2,
            "load_management_mode": "priority",
            **group_overrides,
        }
        return {
            "circuit_groups": [group],
            "outlet_mappings": [
                {"switch_entity_id": "switch.bay_1", "circuit_group_id": "cct_1"},
                {"switch_entity_id": "switch.bay_2", "circuit_group_id": "cct_1"},
                {"switch_entity_id": "switch.bay_3", "circuit_group_id": "cct_1"},
            ],
        }

    def test_pause_when_effective_power_limit_is_exceeded(self) -> None:
        """The engine pauses enough active outlets to return under the limit."""
        engine = LoadManagementEngine(
            hierarchy=self._hierarchy(),
            outlet_power_w={"switch.bay_1": 2200, "switch.bay_2": 1800, "switch.bay_3": 1200},
            switch_states={"switch.bay_1": "on", "switch.bay_2": "on", "switch.bay_3": "on"},
            active_sessions={
                "switch.bay_1": {"start_time": "2026-07-17T09:00:00+10:00", "priority": 1},
                "switch.bay_2": {"start_time": "2026-07-17T09:05:00+10:00", "priority": 0},
                "switch.bay_3": {"start_time": "2026-07-17T09:10:00+10:00", "priority": 0},
            },
        )

        result = engine.evaluate()

        self.assertEqual(result["groups"][0]["effective_limit_w"], 4500)
        self.assertEqual(result["actions"][0]["action"], "pause")
        self.assertEqual(result["actions"][0]["switch_entity_id"], "switch.bay_2")

    def test_monitor_only_never_recommends_relay_actions(self) -> None:
        """Monitor-only groups report overloads but do not change relays."""
        engine = LoadManagementEngine(
            hierarchy=self._hierarchy(load_management_mode="monitor_only"),
            outlet_power_w={"switch.bay_1": 6000},
            switch_states={"switch.bay_1": "on"},
            active_sessions={"switch.bay_1": {"start_time": "2026-07-17T09:00:00+10:00"}},
        )

        result = engine.evaluate()

        self.assertEqual(result["actions"], [])
        self.assertEqual(result["groups"][0]["over_limit"], True)

    def test_resume_one_paused_outlet_when_capacity_returns(self) -> None:
        """A paused outlet is resumed when the group is safely below limits."""
        now = datetime(2026, 7, 17, 10, tzinfo=timezone(timedelta(hours=10)))
        engine = LoadManagementEngine(
            hierarchy=self._hierarchy(maximum_simultaneous_outlets=2),
            outlet_power_w={"switch.bay_1": 1200, "switch.bay_2": 0},
            switch_states={"switch.bay_1": "on", "switch.bay_2": "off"},
            active_sessions={
                "switch.bay_1": {"start_time": "2026-07-17T09:00:00+10:00"},
                "switch.bay_2": {"start_time": "2026-07-17T09:05:00+10:00"},
            },
            paused_outlets={"switch.bay_2": {"paused_at": "2026-07-17T09:30:00+10:00"}},
            now=now,
        )

        result = engine.evaluate()

        self.assertEqual(result["actions"], [
            {
                "action": "resume",
                "switch_entity_id": "switch.bay_2",
                "circuit_group_id": "cct_1",
                "circuit_group_name": "EV Row",
                "reason": "capacity_available",
            }
        ])

    def test_relay_limits_block_rapid_toggling(self) -> None:
        """Minimum relay durations stop immediate pause/resume churn."""
        now = datetime(2026, 7, 17, 10, tzinfo=timezone(timedelta(hours=10)))
        engine = LoadManagementEngine(
            hierarchy=self._hierarchy(minimum_relay_on_duration=300),
            outlet_power_w={"switch.bay_1": 6000},
            switch_states={"switch.bay_1": "on"},
            active_sessions={"switch.bay_1": {"start_time": "2026-07-17T09:00:00+10:00"}},
            relay_events={"switch.bay_1": ["2026-07-17T09:58:00+10:00"]},
            now=now,
        )

        self.assertEqual(engine.evaluate()["actions"], [])


if __name__ == "__main__":
    import unittest

    unittest.main()
