"""Plan gating for QR scan-to-order."""

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
from app.core.subscription.entitlements import DEMO_MAX_ONLINE_ORDERS_PER_MONTH  # noqa: E402


def _set_setting(conn, tenant_id, key, value):
    conn.execute(
        "INSERT INTO app_settings (tenant_id, key, value) VALUES (?, ?, ?) "
        "ON CONFLICT(tenant_id, key) DO UPDATE SET value = excluded.value",
        (tenant_id, key, value),
    )


class QrPlanGatingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = create_flask_application()
        cls.app.config["TESTING"] = True
        cls.app.config["WTF_CSRF_ENABLED"] = False
        with get_raw_connection() as conn:
            prow = conn.execute(
                "SELECT id FROM products WHERE tenant_id = 1 AND sku = 'QR-SKU-1' LIMIT 1"
            ).fetchone()
            if prow is None:
                prow = conn.execute(
                    """
                    INSERT INTO products (tenant_id,name,sku,price,stock,reorder_level,cost,status,sort_order,is_public)
                    VALUES (1,'QR Latte','QR-SKU-1',80,100,1,20,'active',0,1) RETURNING id
                    """
                ).fetchone()
            cls.product_id = int(prow["id"])
            _set_setting(conn, 1, "qr_ordering_enabled", "1")

    def test_starter_plan_blocks_public_order_page(self) -> None:
        with get_raw_connection() as conn:
            conn.execute("UPDATE tenants SET plan_name = 'starter', qr_ordering_override = '' WHERE id = 1")
        try:
            self.assertEqual(self.app.test_client().get("/order/1").status_code, 404)
        finally:
            with get_raw_connection() as conn:
                conn.execute("UPDATE tenants SET plan_name = 'growth' WHERE id = 1")

    def test_growth_plan_allows_order_page(self) -> None:
        with get_raw_connection() as conn:
            conn.execute("UPDATE tenants SET plan_name = 'growth', qr_ordering_override = '' WHERE id = 1")
        res = self.app.test_client().get("/order/1")
        self.assertEqual(res.status_code, 200)
        self.assertIn("QR Latte", res.get_data(as_text=True))

    def test_demo_cap_blocks_order_after_limit(self) -> None:
        with get_raw_connection() as conn:
            conn.execute("UPDATE tenants SET plan_name = 'demo', qr_ordering_override = '' WHERE id = 1")
            conn.execute("DELETE FROM order_items WHERE tenant_id = 1")
            conn.execute("DELETE FROM orders WHERE tenant_id = 1")
            for i in range(DEMO_MAX_ONLINE_ORDERS_PER_MONTH):
                conn.execute(
                    """
                    INSERT INTO orders (
                        tenant_id, guest_name, guest_phone, status, subtotal, tax, total, notes,
                        delivery_line1, delivery_line2, delivery_city, delivery_notes
                    ) VALUES (1, ?, '09170000000', 'pending', 80, 0, 80, '', '', '', '', '')
                    """,
                    (f"Cap Test {i}",),
                )
        try:
            res = self.app.test_client().post(
                "/order/1",
                data={
                    "guest_name": "Over Cap",
                    "guest_phone": "09179998888",
                    f"qty_{self.product_id}": "1",
                },
            )
            self.assertEqual(res.status_code, 302)
            self.assertIn("/order/1", res.headers["Location"])
            follow = self.app.test_client().get(res.headers["Location"])
            self.assertIn(str(DEMO_MAX_ONLINE_ORDERS_PER_MONTH), follow.get_data(as_text=True))
        finally:
            with get_raw_connection() as conn:
                conn.execute("DELETE FROM order_items WHERE tenant_id = 1")
                conn.execute("DELETE FROM orders WHERE tenant_id = 1")
                conn.execute("UPDATE tenants SET plan_name = 'growth' WHERE id = 1")

    def test_super_admin_force_on_unlocks_starter(self) -> None:
        with get_raw_connection() as conn:
            conn.execute(
                "UPDATE tenants SET plan_name = 'starter', qr_ordering_override = 'force_on' WHERE id = 1"
            )
        try:
            self.assertEqual(self.app.test_client().get("/order/1").status_code, 200)
        finally:
            with get_raw_connection() as conn:
                conn.execute(
                    "UPDATE tenants SET plan_name = 'growth', qr_ordering_override = '' WHERE id = 1"
                )

    def test_owner_qr_svg_forbidden_on_starter(self) -> None:
        with get_raw_connection() as conn:
            conn.execute("UPDATE tenants SET plan_name = 'starter', qr_ordering_override = '' WHERE id = 1")
            u = conn.execute("SELECT id FROM users WHERE tenant_id=1 AND role='owner' LIMIT 1").fetchone()
        if u is None:
            self.skipTest("no owner user")
        client = self.app.test_client()
        with client.session_transaction() as s:
            s["user_id"] = int(u["id"])
            s["tenant_id"] = 1
            s["is_super_admin"] = False
        try:
            self.assertEqual(client.get("/owner/qr.svg").status_code, 403)
        finally:
            with get_raw_connection() as conn:
                conn.execute("UPDATE tenants SET plan_name = 'growth' WHERE id = 1")


if __name__ == "__main__":
    unittest.main()
