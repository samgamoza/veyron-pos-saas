"""
eTown marketplace order flow (SQLite). Run in isolation so DATABASE path is set before app import:

    python -m unittest tests.test_etown_order -v
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["SQLITE_DATABASE_PATH"] = _tmp.name

from app.core.db import get_raw_connection  # noqa: E402
from app.core.flask_app import create_flask_application  # noqa: E402


class TestEtownOrder(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = create_flask_application()
        cls.app.config["TESTING"] = True
        with get_raw_connection() as conn:
            conn.execute("UPDATE tenants SET marketplace_enabled = 1 WHERE id = 1")
            conn.execute(
                """
                INSERT INTO products (tenant_id, name, sku, price, stock, reorder_level, cost, status, sort_order, is_public)
                VALUES (1, 'Etown Test Product', 'ETOWN-TEST-1', 25.5, 10, 5, 0, 'active', 0, 1)
                """
            )
        cls.client = cls.app.test_client()

    def test_place_order_json_creates_rows(self) -> None:
        with self.app.app_context():
            with get_raw_connection() as conn:
                row = conn.execute(
                    "SELECT id FROM products WHERE sku = 'ETOWN-TEST-1' AND tenant_id = 1"
                ).fetchone()
                self.assertIsNotNone(row)
                pid = int(row["id"])

        payload = {
            "guest_name": "Test Customer",
            "guest_phone": "09171234567",
            "delivery_line1": "123 Test St",
            "delivery_city": "Testville",
            "items": [{"product_id": pid, "quantity": 2}],
        }
        res = self.client.post(
            f"/etown/shop/1/order.json",
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 201, res.get_data(as_text=True))
        data = json.loads(res.get_data(as_text=True))
        self.assertTrue(data.get("ok"))
        oid = int(data["order_id"])
        self.assertGreater(oid, 0)

        with self.app.app_context():
            with get_raw_connection() as conn:
                o = conn.execute("SELECT * FROM orders WHERE id = ?", (oid,)).fetchone()
                self.assertIsNotNone(o)
                self.assertEqual(int(o["tenant_id"]), 1)
                self.assertEqual(o["guest_phone"], "09171234567")
                items = conn.execute(
                    "SELECT COUNT(*) AS c FROM order_items WHERE order_id = ?", (oid,)
                ).fetchone()
                self.assertEqual(int(items["c"]), 1)


if __name__ == "__main__":
    unittest.main()
