"""End-to-end VAT: a real POS checkout must persist the correct tax and total.

Complements tests/test_vat.py (pure math) by proving the wiring — tenant settings
are actually read and applied on the checkout path.
"""

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

# 112.00 VAT-inclusive == 100.00 net + 12.00 VAT
UNIT_PRICE = 112.00


class VatCheckoutTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = create_flask_application()
        cls.app.config["TESTING"] = True
        with get_raw_connection() as conn:
            for key, value in (("vat_rate", "0.12"), ("vat_inclusive", "1"), ("vat_registered", "1")):
                conn.execute(
                    """
                    INSERT INTO app_settings (tenant_id, key, value) VALUES (1, ?, ?)
                    ON CONFLICT(tenant_id, key) DO UPDATE SET value = excluded.value
                    """,
                    (key, value),
                )
            prow = conn.execute(
                """
                INSERT INTO products (tenant_id,name,sku,price,stock,reorder_level,cost,status,sort_order,is_public)
                VALUES (1,'VAT Test Item','VAT-SKU-1',?,1000,1,50,'active',0,1)
                RETURNING id
                """,
                (UNIT_PRICE,),
            ).fetchone()
            cls.product_id = int(prow["id"])
            urow = conn.execute("SELECT id FROM users WHERE tenant_id = 1 LIMIT 1").fetchone()
            cls.user_id = int(urow["id"]) if urow else None

    def _client(self):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess["user_id"] = self.user_id
            sess["tenant_id"] = 1
            sess["is_super_admin"] = False
        return client

    def _checkout(self, discount_type: str, key: str) -> dict:
        res = self._client().post(
            "/api/pos/orders",
            json={
                "lines": [{"product_id": self.product_id, "quantity": 1}],
                "payment_method": "cash",
                "discount_type": discount_type,
                "idempotency_key": key,
            },
        )
        self.assertIn(res.status_code, (200, 201), res.get_data(as_text=True))
        sale_id = json.loads(res.get_data(as_text=True))["data"]["sale_id"]
        with get_raw_connection() as conn:
            row = conn.execute(
                "SELECT subtotal, discount_amount, tax, total FROM sales WHERE id = ?", (sale_id,)
            ).fetchone()
        return {k: float(row[k]) for k in ("subtotal", "discount_amount", "tax", "total")}

    def setUp(self) -> None:
        if self.user_id is None:
            self.skipTest("no seeded tenant user available")

    def test_ordinary_sale_extracts_vat_without_inflating_total(self) -> None:
        t = self._checkout("none", "vat-e2e-plain")
        self.assertEqual(t["total"], 112.00, "VAT-inclusive total must stay 112.00")
        self.assertEqual(t["tax"], 12.00, "VAT must be extracted, not added")

    def test_senior_sale_is_vat_exempt(self) -> None:
        t = self._checkout("senior", "vat-e2e-senior")
        self.assertEqual(t["tax"], 0.00, "Senior sales are VAT-exempt")
        # net 100.00 less 20% => 80.00 due (NOT 112 * 0.8 = 89.60)
        self.assertEqual(t["total"], 80.00)
        self.assertEqual(t["discount_amount"], 20.00)
        self.assertNotEqual(t["total"], 89.60, "Must not discount the VAT-inclusive price")


if __name__ == "__main__":
    unittest.main()
