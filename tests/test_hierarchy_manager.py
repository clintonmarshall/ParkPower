"""Tests for ParkPower hierarchy and electrical records."""

from __future__ import annotations

from pathlib import Path
import sys
import types
from unittest import TestCase

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "pow_reporting"
package = types.ModuleType("custom_components.pow_reporting")
package.__path__ = [str(PACKAGE_ROOT)]
sys.modules.setdefault("custom_components.pow_reporting", package)

from custom_components.pow_reporting.hierarchy_manager import HierarchyManager


class HierarchyManagerTest(TestCase):
    """Validate local site/electrical hierarchy records."""

    def _manager(self) -> HierarchyManager:
        return HierarchyManager(
            {
                "organisations": [],
                "sites": [],
                "buildings": [],
                "distribution_boards": [],
                "circuit_groups": [],
                "outlet_mappings": [],
            }
        )

    def test_create_full_hierarchy(self) -> None:
        """Organisation through outlet mappings can be created together."""
        manager = self._manager()

        organisation = manager.upsert("organisation", {"name": "Adaptive Services"})
        site = manager.upsert("site", {"organisation_id": organisation["id"], "name": "Car Park A"})
        building = manager.upsert("building", {"site_id": site["id"], "name": "Level 1"})
        board = manager.upsert(
            "distribution_board",
            {
                "building_id": building["id"],
                "name": "DB-L1",
                "maximum_current": "63",
                "maximum_power": "14500",
                "enabled": "on",
            },
        )
        circuit = manager.upsert(
            "circuit_group",
            {
                "distribution_board_id": board["id"],
                "name": "EV row",
                "maximum_simultaneous_outlets": "4",
                "load_management_mode": "priority",
            },
        )
        outlet = manager.upsert(
            "outlet",
            {
                "switch_entity_id": "switch.bay_01",
                "power_entity_id": "sensor.bay_01_power",
                "energy_entity_id": "sensor.bay_01_energy",
                "site_id": site["id"],
                "building_id": building["id"],
                "distribution_board_id": board["id"],
                "circuit_group_id": circuit["id"],
                "bay": "Bay 01",
                "ha_label_ids": "Bay 01,EV",
            },
        )

        records = manager.list_records()
        self.assertEqual(records["organisations"][0]["name"], "Adaptive Services")
        self.assertEqual(records["sites"][0]["organisation_id"], organisation["id"])
        self.assertEqual(records["buildings"][0]["site_id"], site["id"])
        self.assertEqual(records["distribution_boards"][0]["maximum_current"], 63.0)
        self.assertEqual(records["distribution_boards"][0]["enabled"], True)
        self.assertEqual(records["circuit_groups"][0]["maximum_simultaneous_outlets"], 4.0)
        self.assertEqual(outlet["ha_label_ids"], ["Bay 01", "EV"])

    def test_outlet_gateway_connectivity_fields_are_stored(self) -> None:
        """Outlet assignments can carry gateway and MQTT connectivity metadata."""
        manager = self._manager()

        outlet = manager.upsert(
            "outlet",
            {
                "switch_entity_id": "switch.bay_01",
                "gateway_id": "gw_l1_east",
                "gateway_name": "L1 East Bridge",
                "gateway_zone": "L1 East",
                "gateway_ip": "192.168.40.2",
                "gateway_ssid": "ParkPower-Bridge",
                "gateway_subnet": "192.168.40.0/24",
                "gateway_uplink_rssi": "-67",
                "gateway_client_count": "8",
                "gateway_online_entity_id": "binary_sensor.gw_l1_east_online",
                "gateway_uplink_rssi_entity_id": "sensor.gw_l1_east_uplink_rssi",
                "outlet_availability": "online",
                "outlet_rssi": "-71",
                "stale_after_minutes": "20",
                "mqtt_topic_prefix": "parkpower/l1/bay01",
                "source_type": "mqtt",
            },
        )

        self.assertEqual(outlet["gateway_id"], "gw_l1_east")
        self.assertEqual(outlet["gateway_client_count"], 8.0)
        self.assertEqual(outlet["gateway_uplink_rssi"], -67.0)
        self.assertEqual(outlet["outlet_rssi"], -71.0)
        self.assertEqual(outlet["stale_after_minutes"], 20.0)
        self.assertEqual(outlet["source_type"], "mqtt")

    def test_archive_hides_record_by_default(self) -> None:
        """Archived hierarchy records are retained but hidden."""
        manager = self._manager()
        site = manager.upsert("site", {"name": "Archive Site"})
        archived = manager.archive("site", site["id"])

        self.assertEqual(archived["status"], "archived")
        self.assertEqual(manager.list_records()["sites"], [])
        self.assertEqual(len(manager.list_records(include_archived=True)["sites"]), 1)

    def test_update_preserves_created_at(self) -> None:
        """Updating a hierarchy record preserves creation timestamp."""
        manager = self._manager()
        board = manager.upsert("distribution_board", {"name": "Original"})

        updated = manager.upsert(
            "distribution_board",
            {
                "id": board["id"],
                "name": "Updated",
                "warning_threshold": "9000",
            },
        )

        self.assertEqual(updated["id"], board["id"])
        self.assertEqual(updated["created_at"], board["created_at"])
        self.assertEqual(updated["name"], "Updated")
        self.assertEqual(updated["warning_threshold"], 9000.0)

    def test_search_matches_any_field(self) -> None:
        """Admin search can find hierarchy records by secondary fields."""
        manager = self._manager()
        manager.upsert("outlet", {"switch_entity_id": "switch.l1_bay_1", "bay": "L1-B1"})
        manager.upsert("outlet", {"switch_entity_id": "switch.l2_bay_1", "bay": "L2-B1"})

        records = manager.list_records(query="l2-b1")

        self.assertEqual(len(records["outlet_mappings"]), 1)
        self.assertEqual(records["outlet_mappings"][0]["switch_entity_id"], "switch.l2_bay_1")


if __name__ == "__main__":
    import unittest

    unittest.main()
