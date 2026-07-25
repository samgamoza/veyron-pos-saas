"""Multi-branch locations: tenant scoping, default resolution, and sale attribution."""

from __future__ import annotations

import json
import os
import tempfile
import unittest

if not os.environ.get("SQLITE_DATABASE_PATH"):
    _tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    _tmp.close()
    os.environ["SQLITE_DATABASE_PATH"] = _tmp.name

from app.core.db import get_raw_connection  # noqa: E402
from app.core.flask_app import create_flask_application  # noqa: E402
from app.core.locations import LocationService  # noqa: E402
from app.core.tenant.connection import SCOPED_TABLES  # noqa: E402


class LocationServiceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = create_flask_application()
        cls.app.config["TESTING"] = True
        with get_raw_connection() as conn:
            if conn.execute("SELECT id FROM tenants WHERE id = 2").fetchone() is None:
                conn.execute("INSERT INTO tenants (id, name, is_active) VALUES (2, 'Loc Tenant Two', 1)")

    def test_locations_is_tenant_scoped(self) -> None:
        self.assertIn("locations", SCOPED_TABLES)

    def test_default_tenant_has_seeded_default_branch(self) -> None:
        with get_raw_connection() as conn:
            default = LocationService(conn).get_default(1)
        self.assertIsNotNone(default)
        self.assertTrue(default["is_default"])

    def test_first_branch_becomes_default_and_switching_works(self) -> None:
        with get_raw_connection() as conn:
            svc = LocationService(conn)
            main_id = svc.ensure_default(2, name="Two Main")
            self.assertEqual(svc.get_default(2)["id"], main_id)

            second = svc.create_location(2, "Two Annex", code="ANX")
            # Adding a non-first branch must NOT steal default.
            self.assertEqual(svc.get_default(2)["id"], main_id)

            svc.set_default(2, second)
            self.assertEqual(svc.get_default(2)["id"], second)

            names = {loc["name"] for loc in svc.list_locations(2)}
            self.assertEqual(names, {"Two Main", "Two Annex"})

    def test_branches_do_not_leak_across_tenants(self) -> None:
        with get_raw_connection() as conn:
            svc = LocationService(conn)
            svc.create_location(1, "Tenant1 Only Branch", code="T1")
            names_t2 = {loc["name"] for loc in svc.list_locations(2)}
            self.assertNotIn("Tenant1 Only Branch", names_t2)
            # Cross-tenant fetch by id returns nothing.
            t1_branch = svc.create_location(1, "Another T1 Branch")
            self.assertIsNone(svc.get_location(2, t1_branch))

    def test_resolve_active_falls_back_to_default(self) -> None:
        with get_raw_connection() as conn:
            svc = LocationService(conn)
            # Invalid/foreign id falls back to the tenant's default.
            resolved = svc.resolve_active_location_id(1, requested=999999)
            self.assertEqual(resolved, svc.get_default(1)["id"])

    def test_cannot_deactivate_default_branch(self) -> None:
        with get_raw_connection() as conn:
            svc = LocationService(conn)
            default_id = svc.get_default(1)["id"]
            with self.assertRaises(ValueError):
                svc.update_location(1, default_id, is_active=False)


class SaleRecordsLocationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = create_flask_application()
        cls.app.config["TESTING"] = True
        with get_raw_connection() as conn:
            prow = conn.execute(
                """
                INSERT INTO products (tenant_id,name,sku,price,stock,reorder_level,cost,status,sort_order,is_public)
                VALUES (1,'Loc Sale Item','LOC-SKU-1',50,100,1,10,'active',0,1)
                RETURNING id
                """
            ).fetchone()
            cls.product_id = int(prow["id"])
            urow = conn.execute("SELECT id FROM users WHERE tenant_id = 1 LIMIT 1").fetchone()
            cls.user_id = int(urow["id"]) if urow else None

    def test_checkout_records_default_location_on_sale(self) -> None:
        if self.user_id is None:
            self.skipTest("no seeded tenant user")
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess["user_id"] = self.user_id
            sess["tenant_id"] = 1
            sess["is_super_admin"] = False
        res = client.post(
            "/api/pos/orders",
            json={
                "lines": [{"product_id": self.product_id, "quantity": 1}],
                "payment_method": "cash",
                "idempotency_key": "loc-e2e-1",
            },
        )
        self.assertIn(res.status_code, (200, 201), res.get_data(as_text=True))
        sale_id = json.loads(res.get_data(as_text=True))["data"]["sale_id"]
        with get_raw_connection() as conn:
            row = conn.execute("SELECT location_id FROM sales WHERE id = ?", (sale_id,)).fetchone()
            default = LocationService(conn).get_default(1)
        self.assertIsNotNone(row["location_id"], "sale must be attributed to a branch")
        self.assertEqual(int(row["location_id"]), int(default["id"]))


if __name__ == "__main__":
    unittest.main()
