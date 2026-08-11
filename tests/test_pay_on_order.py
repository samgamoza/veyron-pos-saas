"""Pay-on-order: optional online payment for a scanned QR order.

An order is created 'unpaid' (pay-at-counter by default). Clicking "Pay online
now" opens a PayMongo checkout for that ORDER (not a sale — orders have no
sale_id, so this is a separate code path from POS payments). The shared webhook
must settle the correct side (payments vs orders) by matching reference.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import tempfile
import unittest
from unittest.mock import patch

if not os.environ.get("SQLITE_DATABASE_PATH"):
    _tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    _tmp.close()
    os.environ["SQLITE_DATABASE_PATH"] = _tmp.name

# Must match the value other test modules set for PAYMONGO_WEBHOOK_SECRET: it's a
# single process-wide env var (one platform, one webhook secret), so every test
# module that touches it has to agree on the value regardless of import order.
WEBHOOK_SECRET = "whsk_test_secret"
os.environ.setdefault("PAYMONGO_SECRET_KEY", "sk_test_dummy")
os.environ.setdefault("PAYMONGO_PUBLIC_KEY", "pk_test_dummy")
os.environ["PAYMONGO_WEBHOOK_SECRET"] = WEBHOOK_SECRET

from app.core.db import get_raw_connection  # noqa: E402
from app.core.flask_app import create_flask_application  # noqa: E402


def _signed_headers(body: bytes, secret: str = WEBHOOK_SECRET, ts: str = "1700000000") -> dict:
    sig = hmac.new(secret.encode(), f"{ts}.{body.decode()}".encode(), hashlib.sha256).hexdigest()
    return {"Paymongo-Signature": f"t={ts},te={sig}", "Content-Type": "application/json"}


def _event(event_type: str, resource_id: str) -> bytes:
    return json.dumps(
        {"data": {"id": "evt_order_1", "attributes": {"type": event_type, "data": {"id": resource_id, "attributes": {}}}}}
    ).encode()


class PayOnOrderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = create_flask_application()
        cls.app.config["TESTING"] = True
        cls.app.config["WTF_CSRF_ENABLED"] = False
        with get_raw_connection() as conn:
            conn.execute("UPDATE tenants SET marketplace_enabled = 0, plan_name = 'growth' WHERE id = 1")
            conn.execute(
                "INSERT INTO app_settings (tenant_id, key, value) VALUES (1, 'qr_ordering_enabled', '1') "
                "ON CONFLICT(tenant_id, key) DO UPDATE SET value = excluded.value"
            )
            prow = conn.execute(
                """
                INSERT INTO products (tenant_id,name,sku,price,stock,reorder_level,cost,status,sort_order,is_public)
                VALUES (1,'Pay Order Item','PAY-ORD-SKU',100,100,1,10,'active',0,1) RETURNING id
                """
            ).fetchone()
            cls.product_id = int(prow["id"])

    def _place_order(self, phone: str) -> int:
        res = self.app.test_client().post(
            "/order/1",
            data={"guest_name": "Pay Tester", "guest_phone": phone, f"qty_{self.product_id}": "1"},
        )
        self.assertEqual(res.status_code, 302)
        order_id = int(res.headers["Location"].rstrip("/").split("/")[-1])
        return order_id

    def test_confirmation_shows_pay_button_when_unpaid(self) -> None:
        order_id = self._place_order("09171110001")
        res = self.app.test_client().get(f"/order/1/confirmation/{order_id}")
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)
        self.assertIn("Pay online now", html)
        self.assertIn("Pay at counter", html)

    def test_pay_now_opens_checkout_and_marks_awaiting(self) -> None:
        order_id = self._place_order("09171110002")
        fake = {
            "data": {
                "id": "cs_order_1",
                "attributes": {
                    "checkout_url": "https://checkout.paymongo.com/cs_order_1",
                    "payment_intent": {"id": "pi_order_1"},
                },
            }
        }
        with patch("app.core.payments.paymongo_gateway.PayMongoGateway._request", return_value=fake):
            res = self.app.test_client().post(f"/order/1/{order_id}/pay", data={})
        self.assertEqual(res.status_code, 302)
        self.assertEqual(res.headers["Location"], "https://checkout.paymongo.com/cs_order_1")
        with get_raw_connection() as conn:
            row = conn.execute(
                "SELECT payment_status, payment_reference, payment_method FROM orders WHERE id = ?", (order_id,)
            ).fetchone()
        self.assertEqual(row["payment_status"], "awaiting_payment")
        self.assertEqual(row["payment_reference"], "cs_order_1")
        self.assertEqual(row["payment_method"], "paymongo")

    def test_webhook_settles_order_not_sale(self) -> None:
        order_id = self._place_order("09171110003")
        fake = {"data": {"id": "cs_order_2", "attributes": {"checkout_url": "https://x/cs_order_2"}}}
        with patch("app.core.payments.paymongo_gateway.PayMongoGateway._request", return_value=fake):
            self.app.test_client().post(f"/order/1/{order_id}/pay", data={})

        body = _event("checkout_session.payment.paid", "cs_order_2")
        res = self.app.test_client().post(
            "/api/payments/webhook/paymongo", data=body, headers=_signed_headers(body)
        )
        self.assertEqual(res.status_code, 200, res.get_data(as_text=True))
        payload = json.loads(res.get_data(as_text=True))["data"]
        self.assertTrue(payload["matched"])
        self.assertEqual(payload["order_id"], order_id)
        with get_raw_connection() as conn:
            row = conn.execute("SELECT payment_status FROM orders WHERE id = ?", (order_id,)).fetchone()
        self.assertEqual(row["payment_status"], "paid")

        # Idempotent replay must not error or re-fire.
        res2 = self.app.test_client().post(
            "/api/payments/webhook/paymongo", data=body, headers=_signed_headers(body)
        )
        self.assertEqual(res2.status_code, 200)
        self.assertFalse(json.loads(res2.get_data(as_text=True))["data"]["changed"])

    def test_confirmation_hides_pay_button_once_paid(self) -> None:
        order_id = self._place_order("09171110004")
        fake = {"data": {"id": "cs_order_3", "attributes": {"checkout_url": "https://x/cs_order_3"}}}
        with patch("app.core.payments.paymongo_gateway.PayMongoGateway._request", return_value=fake):
            self.app.test_client().post(f"/order/1/{order_id}/pay", data={})
        body = _event("checkout_session.payment.paid", "cs_order_3")
        self.app.test_client().post("/api/payments/webhook/paymongo", data=body, headers=_signed_headers(body))

        res = self.app.test_client().get(f"/order/1/confirmation/{order_id}")
        html = res.get_data(as_text=True)
        self.assertNotIn("Pay online now", html)
        self.assertIn("Paid", html)

    def test_cannot_pay_for_already_paid_order(self) -> None:
        order_id = self._place_order("09171110005")
        fake = {"data": {"id": "cs_order_4", "attributes": {"checkout_url": "https://x/cs_order_4"}}}
        with patch("app.core.payments.paymongo_gateway.PayMongoGateway._request", return_value=fake):
            self.app.test_client().post(f"/order/1/{order_id}/pay", data={})
        body = _event("checkout_session.payment.paid", "cs_order_4")
        self.app.test_client().post("/api/payments/webhook/paymongo", data=body, headers=_signed_headers(body))

        with patch("app.core.payments.paymongo_gateway.PayMongoGateway._request", return_value=fake):
            res = self.app.test_client().post(f"/order/1/{order_id}/pay", data={})
        self.assertEqual(res.status_code, 302)
        self.assertIn("/order/1/confirmation/", res.headers["Location"])
        with get_raw_connection() as conn:
            row = conn.execute("SELECT payment_status FROM orders WHERE id = ?", (order_id,)).fetchone()
        self.assertEqual(row["payment_status"], "paid", "must not be reset by a second checkout attempt")

    def test_pay_for_unknown_order_404s(self) -> None:
        res = self.app.test_client().post("/order/1/999999/pay", data={})
        # Route reaches order but service raises ValueError -> redirected with flash,
        # OR order_confirmation itself 404s since the order doesn't exist for that tenant.
        self.assertIn(res.status_code, (302, 404))

    def test_pos_sale_payment_unaffected_by_order_webhook_matching(self) -> None:
        """Regression: the sale-payment webhook path must still work unchanged."""
        with get_raw_connection() as conn:
            srow = conn.execute(
                """
                INSERT INTO sales (tenant_id, subtotal, discount_type, discount_rate, discount_amount,
                                   discount_note, tax, total, payment_method, status)
                VALUES (1, 50, 'none', 0, 0, '', 0, 50, 'paymongo', 'completed') RETURNING id
                """
            ).fetchone()
            sale_id = int(srow["id"])
            conn.execute(
                "INSERT INTO payments (tenant_id, sale_id, amount, method, status, reference) "
                "VALUES (1, ?, 50, 'paymongo', 'pending', 'cs_sale_regress')",
                (sale_id,),
            )
        body = _event("checkout_session.payment.paid", "cs_sale_regress")
        res = self.app.test_client().post(
            "/api/payments/webhook/paymongo", data=body, headers=_signed_headers(body)
        )
        self.assertEqual(res.status_code, 200)
        payload = json.loads(res.get_data(as_text=True))["data"]
        self.assertEqual(payload.get("sale_id"), sale_id)
        self.assertNotIn("order_id", payload)


if __name__ == "__main__":
    unittest.main()
