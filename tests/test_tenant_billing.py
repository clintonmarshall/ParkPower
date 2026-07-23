"""Tests for consolidated ParkPower tenant statements."""

from __future__ import annotations

from pathlib import Path
import sys
import types
from unittest import TestCase

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "pow_reporting"
package = types.ModuleType("custom_components.pow_reporting")
package.__path__ = [str(PACKAGE_ROOT)]
sys.modules.setdefault("custom_components.pow_reporting", package)

from custom_components.pow_reporting.tenant_billing import TenantBillingManager


class TenantBillingManagerTest(TestCase):
    """Validate multi-meter statements and delivery state changes."""

    def _billing_data(self) -> dict:
        return {
            "settings": {
                "energy_rate": 0.32,
                "currency": "AUD",
                "sender_name": "Adaptive Services",
            },
            "active": {},
            "completed": [
                self._session("one", "switch.bay_1", 10.0, "2026-07-05T10:00:00+10:00"),
                self._session("two", "switch.bay_2", 5.0, "2026-07-06T10:00:00+10:00"),
                self._session(
                    "other",
                    "switch.bay_1",
                    99.0,
                    "2026-07-06T10:00:00+10:00",
                    customer_id="cus_other",
                ),
            ],
            "statements": [],
        }

    def _records_data(self) -> dict:
        return {
            "customers": [
                {
                    "id": "cus_tenant",
                    "display_name": "Alex Tenant",
                    "contact_email": "alex@example.test",
                    "billing_reference": "APT-14",
                    "billing_rate_per_kwh": 0.32,
                    "billing_currency": "AUD",
                    "outlet_entity_ids": ["switch.bay_1", "switch.bay_2"],
                    "user_group": "grp_residents",
                }
            ],
            "user_groups": [
                {
                    "id": "grp_residents",
                    "name": "Residents",
                    "discount_percentage": 10,
                }
            ],
        }

    def _session(
        self,
        session_id: str,
        switch_entity_id: str,
        energy_kwh: float,
        end_time: str,
        *,
        customer_id: str = "cus_tenant",
    ) -> dict:
        return {
            "session_id": session_id,
            "customer_id": customer_id,
            "billing_status": "draft",
            "switch_entity_id": switch_entity_id,
            "outlet_name": switch_entity_id.replace("switch.", "").replace("_", " ").title(),
            "start_time": "2026-07-05T08:00:00+10:00",
            "end_time": end_time,
            "energy_kwh": energy_kwh,
        }

    def _statement(self, manager: TenantBillingManager) -> dict:
        return manager.create_statement(
            customer_id="cus_tenant",
            period_start="2026-07-01T00:00:00+10:00",
            period_end="2026-08-01T00:00:00+10:00",
        )

    def test_statement_combines_multiple_tenant_meters(self) -> None:
        manager = TenantBillingManager(self._billing_data(), self._records_data())

        statement = self._statement(manager)

        self.assertEqual(statement["session_count"], 2)
        self.assertEqual(statement["meter_count"], 2)
        self.assertEqual(statement["total_energy_kwh"], 15.0)
        self.assertEqual(statement["rate_per_kwh"], 0.288)
        self.assertEqual(statement["total_amount"], 4.32)
        self.assertIn("Bay 1", statement["email_body"])
        self.assertIn("Bay 2", statement["email_body"])

    def test_successful_delivery_marks_only_included_sessions_invoiced(self) -> None:
        manager = TenantBillingManager(self._billing_data(), self._records_data())
        statement = self._statement(manager)

        manager.record_delivery(
            statement_id=statement["statement_id"],
            notify_service="smtp",
            recipient="alex@example.test",
            success=True,
        )

        self.assertEqual(statement["status"], "invoiced")
        statuses = {row["session_id"]: row["billing_status"] for row in manager.sessions}
        self.assertEqual(statuses["one"], "invoiced")
        self.assertEqual(statuses["two"], "invoiced")
        self.assertEqual(statuses["other"], "draft")

    def test_failed_delivery_keeps_statement_draft_and_records_error(self) -> None:
        manager = TenantBillingManager(self._billing_data(), self._records_data())
        statement = self._statement(manager)

        manager.record_delivery(
            statement_id=statement["statement_id"],
            notify_service="smtp",
            recipient="alex@example.test",
            success=False,
            error="SMTP unavailable",
        )

        self.assertEqual(statement["status"], "draft")
        self.assertEqual(statement["delivery_attempts"][0]["error"], "SMTP unavailable")

    def test_overlapping_draft_cannot_send_after_first_is_invoiced(self) -> None:
        manager = TenantBillingManager(self._billing_data(), self._records_data())
        first = self._statement(manager)
        second = self._statement(manager)
        manager.record_delivery(
            statement_id=first["statement_id"],
            notify_service="smtp",
            recipient="alex@example.test",
            success=True,
        )

        with self.assertRaisesRegex(ValueError, "already invoiced"):
            manager.validate_send(second["statement_id"])

    def test_historical_session_without_id_gets_stable_statement_id(self) -> None:
        billing = self._billing_data()
        billing["completed"][0].pop("session_id")
        manager = TenantBillingManager(billing, self._records_data())

        first = self._statement(manager)
        first_id = first["line_items"][0]["session_id"]
        second = self._statement(manager)

        self.assertTrue(first_id.startswith("legacy_"))
        self.assertEqual(second["line_items"][0]["session_id"], first_id)

    def test_free_charging_group_produces_zero_value_statement(self) -> None:
        records = self._records_data()
        records["user_groups"][0]["free_charging"] = True
        manager = TenantBillingManager(self._billing_data(), records)

        statement = self._statement(manager)

        self.assertEqual(statement["rate_per_kwh"], 0.0)
        self.assertEqual(statement["total_amount"], 0.0)

    def test_manual_bill_uses_tenant_contact_without_billing_sessions(self) -> None:
        manager = TenantBillingManager(self._billing_data(), self._records_data())

        statement = manager.create_manual_bill(
            customer_id="cus_tenant",
            billing_reference="INV-1042",
            description="Manual car park power adjustment",
            amount=18.456,
            currency="aud",
            due_date="2026-08-15",
            notes="Please include the invoice reference with payment.",
        )

        self.assertEqual(statement["statement_type"], "manual_bill")
        self.assertEqual(statement["email_to"], "alex@example.test")
        self.assertEqual(statement["total_amount"], 18.46)
        self.assertEqual(statement["currency"], "AUD")
        self.assertEqual(statement["session_count"], 0)
        self.assertEqual(statement["meter_count"], 0)
        self.assertIn("INV-1042", statement["email_subject"])
        self.assertIn("15 Aug 2026", statement["email_body"])
        self.assertIn("AUD 18.46", statement["email_body"])

    def test_manual_bill_can_use_an_unregistered_recipient(self) -> None:
        manager = TenantBillingManager(self._billing_data(), self._records_data())

        statement = manager.create_manual_bill(
            recipient="accounts@example.test",
            recipient_name="Accounts Team",
            billing_reference="MANUAL-7",
            description="Replacement outlet service",
            amount=45,
        )

        self.assertEqual(statement["customer_id"], "")
        self.assertEqual(statement["customer_name"], "Accounts Team")
        self.assertEqual(statement["email_to"], "accounts@example.test")

    def test_manual_bill_validation_rejects_negative_amount(self) -> None:
        manager = TenantBillingManager(self._billing_data(), self._records_data())

        with self.assertRaisesRegex(ValueError, "non-negative"):
            manager.create_manual_bill(
                recipient="accounts@example.test",
                billing_reference="MANUAL-8",
                description="Adjustment",
                amount=-1,
            )

    def test_manual_bill_delivery_does_not_invoice_sessions(self) -> None:
        manager = TenantBillingManager(self._billing_data(), self._records_data())
        statement = manager.create_manual_bill(
            customer_id="cus_tenant",
            billing_reference="INV-1043",
            description="Manual adjustment",
            amount=10,
        )

        manager.record_delivery(
            statement_id=statement["statement_id"],
            notify_service="smtp:mail.example.test:587",
            recipient=statement["email_to"],
            success=True,
        )

        self.assertEqual(statement["status"], "invoiced")
        self.assertTrue(all(row["billing_status"] == "draft" for row in manager.sessions))


if __name__ == "__main__":
    import unittest

    unittest.main()
