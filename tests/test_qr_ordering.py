"""QR scan-to-order: public order page, gating, order creation, and owner QR endpoint."""

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
from app.core.qrcodes import render_qr_svg  # noqa: E402


def _set_setting(conn, tenant_id, key, value):
    conn.execute(
        "INSERT INTO app_settings (tenant_id, key, value) VALUES (?, ?, ?) "
        "ON CONFLICT(tenant_id, key) DO UPDATE SET value = excluded.value",
        (tenant_id, key, value),
    )


class ScanToOrderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = create_flask_application()
        cls.app.config["TESTING"] = True
        cls.app.config["WTF_CSRF_ENABLED"] = False
        with get_raw_connection() as conn:
            conn.execute("UPDATE tenants SET marketplace_enabled = 0, plan_name = 'growth' WHERE id = 1")
            _set_setting(conn, 1, "qr_ordering_enabled", "1")
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

    def test_order_page_renders_for_non_marketplace_merchant(self) -> None:
        res = self.app.test_client().get("/order/1")
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)
        self.assertIn("QR Latte", html)
        self.assertIn("Place order", html)

    def test_order_page_shows_delivery_option_when_enabled(self) -> None:
        with get_raw_connection() as conn:
            conn.execute("UPDATE tenants SET delivery_enabled = 1 WHERE id = 1")
        try:
            res = self.app.test_client().get("/order/1")
            self.assertEqual(res.status_code, 200)
            html = res.get_data(as_text=True)
            self.assertIn("Deliver to me", html)
        finally:
            with get_raw_connection() as conn:
                conn.execute("UPDATE tenants SET delivery_enabled = 0 WHERE id = 1")

    def test_order_page_carries_table_context(self) -> None:
        res = self.app.test_client().get("/order/1?table=7")
        self.assertEqual(res.status_code, 200)
        self.assertIn("Table 7", res.get_data(as_text=True))

    def test_placing_order_creates_pending_order_with_table_note(self) -> None:
        res = self.app.test_client().post(
            "/order/1",
            data={
                "guest_name": "Scan Customer",
                "guest_phone": "09170009999",
                "table": "12",
                f"qty_{self.product_id}": "2",
            },
        )
        # Post/Redirect/Get: a successful order redirects to its confirmation page.
        self.assertEqual(res.status_code, 302, res.get_data(as_text=True))
        self.assertIn("/order/1/confirmation/", res.headers["Location"])
        with get_raw_connection() as conn:
            row = conn.execute(
                "SELECT status, total, notes FROM orders WHERE tenant_id = 1 ORDER BY id DESC LIMIT 1"
            ).fetchone()
        self.assertEqual(row["status"], "pending")
        self.assertEqual(float(row["total"]), 160.0)  # 2 x 80, VAT-inclusive
        self.assertIn("Table 12", row["notes"])

        confirm = self.app.test_client().get(res.headers["Location"])
        self.assertEqual(confirm.status_code, 200)
        confirm_html = confirm.get_data(as_text=True)
        self.assertIn("Order placed", confirm_html)
        self.assertIn("Table 12", confirm_html)
        self.assertIn("160.00", confirm_html)

    def test_delivery_order_creates_rider_dispatch_row(self) -> None:
        with get_raw_connection() as conn:
            conn.execute("UPDATE tenants SET delivery_enabled = 1 WHERE id = 1")
            _set_setting(conn, 1, "delivery_fee_base", "50")
        try:
            res = self.app.test_client().post(
                "/order/1",
                data={
                    "guest_name": "Delivery Customer",
                    "guest_phone": "09171112222",
                    "fulfillment": "delivery",
                    "delivery_line1": "123 Main St",
                    "delivery_city": "Quezon City",
                    "delivery_notes": "Call on arrival",
                    f"qty_{self.product_id}": "1",
                },
            )
            self.assertEqual(res.status_code, 302, res.get_data(as_text=True))
            with get_raw_connection() as conn:
                order = conn.execute(
                    "SELECT id, delivery_line1, delivery_city FROM orders WHERE tenant_id = 1 ORDER BY id DESC LIMIT 1"
                ).fetchone()
                self.assertEqual(order["delivery_line1"], "123 Main St")
                self.assertEqual(order["delivery_city"], "Quezon City")
                delivery = conn.execute(
                    """
                    SELECT status, reference_type, address, instructions
                    FROM delivery_orders
                    WHERE tenant_id = 1 AND order_id = ? AND reference_type = 'online_order'
                    """,
                    (int(order["id"]),),
                ).fetchone()
            self.assertIsNotNone(delivery)
            self.assertEqual(delivery["status"], "pending")
            self.assertEqual(delivery["reference_type"], "online_order")
            self.assertIn("123 Main St", delivery["address"])
            self.assertIn("Call on arrival", delivery["instructions"])
        finally:
            with get_raw_connection() as conn:
                conn.execute("UPDATE tenants SET delivery_enabled = 0 WHERE id = 1")

    def test_order_page_404_when_disabled(self) -> None:
        with get_raw_connection() as conn:
            _set_setting(conn, 1, "qr_ordering_enabled", "0")
        try:
            res = self.app.test_client().get("/order/1")
            self.assertEqual(res.status_code, 404)
        finally:
            with get_raw_connection() as conn:
                _set_setting(conn, 1, "qr_ordering_enabled", "1")

    def test_order_page_404_for_unknown_tenant(self) -> None:
        self.assertEqual(self.app.test_client().get("/order/999999").status_code, 404)


class OwnerQrTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = create_flask_application()
        cls.app.config["TESTING"] = True
        cls.app.config["WTF_CSRF_ENABLED"] = False
        with get_raw_connection() as conn:
            u = conn.execute("SELECT id FROM users WHERE tenant_id=1 AND role='owner' LIMIT 1").fetchone()
            cls.user_id = int(u["id"]) if u else None
            conn.execute("UPDATE tenants SET plan_name = 'growth' WHERE id = 1")

    def test_qr_svg_helper_encodes_data(self) -> None:
        svg = render_qr_svg("https://example.com/order/1")
        self.assertIn("<svg", svg)
        self.assertIn("path", svg)

    def test_owner_qr_endpoint_returns_svg(self) -> None:
        if self.user_id is None:
            self.skipTest("no owner user")
        client = self.app.test_client()
        with client.session_transaction() as s:
            s["user_id"] = self.user_id
            s["tenant_id"] = 1
            s["is_super_admin"] = False
        res = client.get("/owner/qr.svg?table=5")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.mimetype, "image/svg+xml")
        self.assertIn(b"<svg", res.get_data())

    def test_owner_qr_requires_login(self) -> None:
        res = self.app.test_client().get("/owner/qr.svg", follow_redirects=False)
        self.assertIn(res.status_code, (302, 401, 403))


if __name__ == "__main__":
    unittest.main()
