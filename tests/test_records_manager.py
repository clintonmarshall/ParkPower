"""Tests for local ParkPower customer records."""

from __future__ import annotations

from pathlib import Path
import sys
import types
from unittest import TestCase

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "pow_reporting"
package = types.ModuleType("custom_components.pow_reporting")
package.__path__ = [str(PACKAGE_ROOT)]
sys.modules.setdefault("custom_components.pow_reporting", package)

from custom_components.pow_reporting.records_manager import RecordsManager


class RecordsManagerTest(TestCase):
    """Validate customer, vehicle and user-group records."""

    def _manager(self) -> RecordsManager:
        return RecordsManager(
            {
                "customers": [],
                "vehicles": [],
                "user_groups": [],
            }
        )

    def test_create_customer_vehicle_and_group(self) -> None:
        """Records can be created and listed together."""
        manager = self._manager()

        group = manager.upsert(
            "user_group",
            {
                "name": "Residents",
                "priority": 5,
                "charging_allowed": True,
                "discount_percentage": 10,
            },
        )
        customer = manager.upsert(
            "customer",
            {
                "display_name": "Alex Smith",
                "contact_email": "alex@example.test",
                "billing_reference": "APT-14",
                "user_group": group["id"],
            },
        )
        vehicle = manager.upsert(
            "vehicle",
            {
                "customer_id": customer["id"],
                "registration": "EV123",
                "make_model_description": "Test EV",
            },
        )

        records = manager.list_records()
        self.assertEqual(records["user_groups"][0]["name"], "Residents")
        self.assertEqual(records["customers"][0]["display_name"], "Alex Smith")
        self.assertEqual(records["vehicles"][0]["registration"], "EV123")
        self.assertEqual(vehicle["customer_id"], customer["id"])

    def test_update_preserves_created_at(self) -> None:
        """Updating a record keeps its creation timestamp and changes values."""
        manager = self._manager()
        customer = manager.upsert("customer", {"display_name": "Original"})

        updated = manager.upsert(
            "customer",
            {
                "id": customer["id"],
                "display_name": "Updated",
            },
        )

        self.assertEqual(updated["id"], customer["id"])
        self.assertEqual(updated["created_at"], customer["created_at"])
        self.assertEqual(updated["display_name"], "Updated")

    def test_archive_hides_record_by_default(self) -> None:
        """Archived records are retained but hidden from default lists."""
        manager = self._manager()
        customer = manager.upsert("customer", {"display_name": "Archive Me"})
        archived = manager.archive("customer", customer["id"])

        self.assertEqual(archived["status"], "archived")
        self.assertEqual(manager.list_records()["customers"], [])
        self.assertEqual(len(manager.list_records(include_archived=True)["customers"]), 1)

    def test_search_matches_any_field(self) -> None:
        """Admin search can find records by secondary fields."""
        manager = self._manager()
        manager.upsert("customer", {"display_name": "Alex", "billing_reference": "BAY-22"})
        manager.upsert("customer", {"display_name": "Casey", "billing_reference": "BAY-24"})

        records = manager.list_records(query="bay-22")

        self.assertEqual(len(records["customers"]), 1)
        self.assertEqual(records["customers"][0]["display_name"], "Alex")

    def test_customer_can_own_multiple_outlets(self) -> None:
        """One tenant can explicitly own several metered outlets."""
        manager = self._manager()
        customer = manager.upsert(
            "customer",
            {
                "display_name": "Multi Meter Tenant",
                "outlet_entity_ids": ["switch.bay_1", "switch.bay_2"],
                "billing_rate_per_kwh": 0.32,
                "billing_currency": "aud",
            },
        )

        self.assertEqual(len(customer["outlet_entity_ids"]), 2)
        self.assertEqual(customer["billing_currency"], "AUD")
        self.assertEqual(manager.customer_for_outlet("switch.bay_2").id, customer["id"])

    def test_outlet_cannot_be_assigned_to_two_active_customers(self) -> None:
        """Ambiguous meter ownership is rejected before billing."""
        manager = self._manager()
        manager.upsert(
            "customer",
            {"display_name": "First", "outlet_entity_ids": ["switch.bay_1"]},
        )

        with self.assertRaisesRegex(ValueError, "already assigned"):
            manager.upsert(
                "customer",
                {"display_name": "Second", "outlet_entity_ids": ["switch.bay_1"]},
            )


if __name__ == "__main__":
    import unittest

    unittest.main()
