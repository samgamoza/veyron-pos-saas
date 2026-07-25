"""Tenant-isolation regression tests.

Guards the fix for customers/customer_addresses being globally shared: before this,
`customers` had no tenant_id and a global UNIQUE(phone), so one merchant could read
and overwrite another merchant's customer record.

Run standalone with:  python -m unittest tests.test_tenant_isolation -v
"""

from __future__ import annotations

import os
import tempfile
import unittest

if not os.environ.get("SQLITE_DATABASE_PATH"):
    _tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    _tmp.close()
    os.environ["SQLITE_DATABASE_PATH"] = _tmp.name

from app.core.db import get_raw_connection  # noqa: E402
from app.core.flask_app import create_flask_application  # noqa: E402
from app.core.tenant.connection import SCOPED_TABLES  # noqa: E402
from app.modules.etown.order_service import MarketplaceOrderService  # noqa: E402

SHARED_PHONE = "09990001111"


class TestCustomerTenantIsolation(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = create_flask_application()
        cls.app.config["TESTING"] = True
        cls.service = MarketplaceOrderService()
        with get_raw_connection() as conn:
            existing = conn.execute("SELECT id FROM tenants WHERE id = 2").fetchone()
            if existing is None:
                conn.execute(
                    "INSERT INTO tenants (id, name, is_active) VALUES (2, 'Second Tenant', 1)"
                )

    def test_customers_and_addresses_are_tenant_scoped_tables(self) -> None:
        # Without these entries neither the app-layer scoper nor RLS protects them.
        self.assertIn("customers", SCOPED_TABLES)
        self.assertIn("customer_addresses", SCOPED_TABLES)

    def test_same_phone_creates_separate_customer_per_tenant(self) -> None:
        with get_raw_connection() as conn:
            id_t1 = self.service._ensure_customer(conn, 1, SHARED_PHONE, "Alice From Tenant One")
            id_t2 = self.service._ensure_customer(conn, 2, SHARED_PHONE, "Bob From Tenant Two")

            self.assertIsNotNone(id_t1)
            self.assertIsNotNone(id_t2)
            # The core leak: tenant 2 must NOT reuse tenant 1's customer row.
            self.assertNotEqual(id_t1, id_t2)

            row1 = conn.execute("SELECT tenant_id, full_name FROM customers WHERE id = ?", (id_t1,)).fetchone()
            row2 = conn.execute("SELECT tenant_id, full_name FROM customers WHERE id = ?", (id_t2,)).fetchone()
            self.assertEqual(int(row1["tenant_id"]), 1)
            self.assertEqual(int(row2["tenant_id"]), 2)
            # Names must not bleed across tenants.
            self.assertEqual(row1["full_name"], "Alice From Tenant One")
            self.assertEqual(row2["full_name"], "Bob From Tenant Two")

    def test_lookup_within_same_tenant_is_idempotent(self) -> None:
        with get_raw_connection() as conn:
            first = self.service._ensure_customer(conn, 1, "09990002222", "Repeat Customer")
            second = self.service._ensure_customer(conn, 1, "09990002222", "Repeat Customer")
            self.assertEqual(first, second)

    def test_tenant_one_cannot_see_tenant_two_customer_by_phone(self) -> None:
        with get_raw_connection() as conn:
            self.service._ensure_customer(conn, 2, "09990003333", "Tenant Two Only")
            leaked = conn.execute(
                "SELECT id FROM customers WHERE tenant_id = ? AND phone = ?",
                (1, "09990003333"),
            ).fetchone()
            self.assertIsNone(leaked, "Tenant 1 must not see a customer created by tenant 2.")


if __name__ == "__main__":
    unittest.main()
