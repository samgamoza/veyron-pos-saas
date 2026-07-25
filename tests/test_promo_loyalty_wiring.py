"""End-to-end: promo applied at POS checkout, loyalty accrued on eTown order."""

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
from app.core.loyalty import LoyaltyService  # noqa: E402
from app.core.promotions import PromotionService  # noqa: E402


class PromoAtCheckoutTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = create_flask_application()
        cls.app.config["TESTING"] = True
        with get_raw_connection() as conn:
            for key, value in (("vat_rate", "0.12"), ("vat_inclusive", "1"), ("vat_registered", "1")):
                conn.execute(
                    "INSERT INTO app_settings (tenant_id, key, value) VALUES (1, ?, ?) "
                    "ON CONFLICT(tenant_id, key) DO UPDATE SET value = excluded.value",
                    (key, value),
                )
            prow = conn.execute(
                """
                INSERT INTO products (tenant_id,name,sku,price,stock,reorder_level,cost,status,sort_order,is_public)
                VALUES (1,'Promo Item','PROMO-SKU',100,100,1,10,'active',0,1) RETURNING id
                """
            ).fetchone()
            cls.product_id = int(prow["id"])
            PromotionService(conn).create_promotion(1, "Ten Off", "TAKE10", discount_type="percent", value=10)
            urow = conn.execute("SELECT id FROM users WHERE tenant_id = 1 LIMIT 1").fetchone()
            cls.user_id = int(urow["id"]) if urow else None

    def _client(self):
        c = self.app.test_client()
        with c.session_transaction() as s:
            s["user_id"] = self.user_id
            s["tenant_id"] = 1
            s["is_super_admin"] = False
        return c

    def test_promo_reduces_total_and_increments_usage(self) -> None:
        if self.user_id is None:
            self.skipTest("no seeded user")
        res = self._client().post(
            "/api/pos/orders",
            json={"lines": [{"product_id": self.product_id, "quantity": 2}], "payment_method": "cash",
                  "promo_code": "take10", "idempotency_key": "promo-e2e-1"},
        )
        self.assertIn(res.status_code, (200, 201), res.get_data(as_text=True))
        sale_id = json.loads(res.get_data(as_text=True))["data"]["sale_id"]
        with get_raw_connection() as conn:
            row = conn.execute("SELECT discount_amount, total, discount_type FROM sales WHERE id = ?", (sale_id,)).fetchone()
            used = conn.execute("SELECT used_count FROM promotions WHERE tenant_id=1 AND code='TAKE10'").fetchone()["used_count"]
        # 200 subtotal, 10% promo -> 20 off, total 180
        self.assertEqual(float(row["discount_amount"]), 20.0)
        self.assertEqual(float(row["total"]), 180.0)
        self.assertEqual(row["discount_type"], "promo")
        self.assertEqual(int(used), 1)

    def test_invalid_promo_rejected(self) -> None:
        if self.user_id is None:
            self.skipTest("no seeded user")
        res = self._client().post(
            "/api/pos/orders",
            json={"lines": [{"product_id": self.product_id, "quantity": 1}], "payment_method": "cash",
                  "promo_code": "NOTREAL", "idempotency_key": "promo-e2e-bad"},
        )
        self.assertEqual(res.status_code, 400)
        self.assertEqual(json.loads(res.get_data(as_text=True))["error"]["code"], "checkout_failed")


class LoyaltyAccrualTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = create_flask_application()
        cls.app.config["TESTING"] = True
        with get_raw_connection() as conn:
            conn.execute("UPDATE tenants SET marketplace_enabled = 1 WHERE id = 1")
            for key, value in (("loyalty_enabled", "1"), ("loyalty_earn_rate", "1")):
                conn.execute(
                    "INSERT INTO app_settings (tenant_id, key, value) VALUES (1, ?, ?) "
                    "ON CONFLICT(tenant_id, key) DO UPDATE SET value = excluded.value",
                    (key, value),
                )
            prow = conn.execute(
                """
                INSERT INTO products (tenant_id,name,sku,price,stock,reorder_level,cost,status,sort_order,is_public)
                VALUES (1,'Loyalty Item','LOY-SKU',60,100,1,10,'active',0,1) RETURNING id
                """
            ).fetchone()
            cls.product_id = int(prow["id"])

    def test_etown_order_accrues_points_for_customer(self) -> None:
        client = self.app.test_client()
        res = client.post(
            "/etown/shop/1/order.json",
            data=json.dumps({
                "guest_name": "Points Buyer",
                "guest_phone": "09171112222",
                "delivery_line1": "1 St", "delivery_city": "City",
                "items": [{"product_id": self.product_id, "quantity": 2}],  # 120 total
            }),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 201, res.get_data(as_text=True))
        with get_raw_connection() as conn:
            cust = conn.execute(
                "SELECT id FROM customers WHERE tenant_id=1 AND phone='09171112222'"
            ).fetchone()
            self.assertIsNotNone(cust)
            balance = LoyaltyService(conn).balance(1, int(cust["id"]))
        self.assertEqual(balance, 120, "1 pt/peso on a 120 order")


if __name__ == "__main__":
    unittest.main()
