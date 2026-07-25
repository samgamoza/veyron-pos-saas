"""PayMongo checkout + webhook wiring.

No network and no real keys: the gateway's HTTP layer is mocked. Verifies the
signature trust boundary, idempotency, and that a paid webhook actually settles
the payment row.
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

WEBHOOK_SECRET = "whsk_test_secret"
os.environ["PAYMONGO_SECRET_KEY"] = "sk_test_dummy"
os.environ["PAYMONGO_PUBLIC_KEY"] = "pk_test_dummy"
os.environ["PAYMONGO_WEBHOOK_SECRET"] = WEBHOOK_SECRET

from app.core.db import get_raw_connection  # noqa: E402
from app.core.flask_app import create_flask_application  # noqa: E402

SESSION_REF = "cs_test_webhook_1"


def _signed_headers(body: bytes, secret: str = WEBHOOK_SECRET, ts: str = "1700000000") -> dict:
    sig = hmac.new(secret.encode(), f"{ts}.{body.decode()}".encode(), hashlib.sha256).hexdigest()
    return {"Paymongo-Signature": f"t={ts},te={sig}", "Content-Type": "application/json"}


def _event(event_type: str, resource_id: str) -> bytes:
    return json.dumps(
        {"data": {"id": "evt_1", "attributes": {"type": event_type, "data": {"id": resource_id, "attributes": {}}}}}
    ).encode()


class PaymentsWebhookTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = create_flask_application()
        cls.app.config["TESTING"] = True
        cls.client = cls.app.test_client()
        with get_raw_connection() as conn:
            row = conn.execute(
                """
                INSERT INTO sales (tenant_id, subtotal, discount_type, discount_rate, discount_amount,
                                   discount_note, tax, total, payment_method, status)
                VALUES (1, 100, 'none', 0, 0, '', 0, 100, 'paymongo', 'completed')
                RETURNING id
                """
            ).fetchone()
            cls.sale_id = int(row["id"])
            conn.execute(
                """
                INSERT INTO payments (tenant_id, sale_id, amount, method, status, reference)
                VALUES (1, ?, 100, 'paymongo', 'pending', ?)
                """,
                (cls.sale_id, SESSION_REF),
            )

    def _payment_status(self) -> str:
        with get_raw_connection() as conn:
            row = conn.execute("SELECT status FROM payments WHERE reference = ?", (SESSION_REF,)).fetchone()
            return str(row["status"])

    def test_unsigned_webhook_is_rejected(self) -> None:
        body = _event("checkout_session.payment.paid", SESSION_REF)
        res = self.client.post("/api/payments/webhook/paymongo", data=body,
                               headers={"Content-Type": "application/json"})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(json.loads(res.get_data(as_text=True))["error"]["code"], "webhook_unverified")
        self.assertEqual(self._payment_status(), "pending", "Unverified webhook must not settle payment")

    def test_forged_signature_is_rejected(self) -> None:
        body = _event("checkout_session.payment.paid", SESSION_REF)
        res = self.client.post("/api/payments/webhook/paymongo", data=body,
                               headers=_signed_headers(body, secret="wrong_secret"))
        self.assertEqual(res.status_code, 400)
        self.assertEqual(self._payment_status(), "pending")

    def test_valid_webhook_settles_payment_and_is_idempotent(self) -> None:
        body = _event("checkout_session.payment.paid", SESSION_REF)
        res = self.client.post("/api/payments/webhook/paymongo", data=body, headers=_signed_headers(body))
        self.assertEqual(res.status_code, 200, res.get_data(as_text=True))
        payload = json.loads(res.get_data(as_text=True))["data"]
        self.assertTrue(payload["matched"])
        self.assertTrue(payload["changed"])
        self.assertEqual(self._payment_status(), "completed")

        # Replay the exact same event: must be a no-op, not a double-settle.
        res2 = self.client.post("/api/payments/webhook/paymongo", data=body, headers=_signed_headers(body))
        self.assertEqual(res2.status_code, 200)
        self.assertFalse(json.loads(res2.get_data(as_text=True))["data"]["changed"])

    def test_unknown_reference_returns_200_so_provider_stops_retrying(self) -> None:
        body = _event("checkout_session.payment.paid", "cs_does_not_exist")
        res = self.client.post("/api/payments/webhook/paymongo", data=body, headers=_signed_headers(body))
        self.assertEqual(res.status_code, 200)
        self.assertFalse(json.loads(res.get_data(as_text=True))["data"]["matched"])


class PaymentsCheckoutTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = create_flask_application()
        cls.app.config["TESTING"] = True
        cls.client = cls.app.test_client()
        with get_raw_connection() as conn:
            row = conn.execute(
                """
                INSERT INTO sales (tenant_id, subtotal, discount_type, discount_rate, discount_amount,
                                   discount_note, tax, total, payment_method, status)
                VALUES (1, 250, 'none', 0, 0, '', 0, 250, 'paymongo', 'completed')
                RETURNING id
                """
            ).fetchone()
            cls.sale_id = int(row["id"])
            user = conn.execute("SELECT id FROM users WHERE tenant_id = 1 LIMIT 1").fetchone()
            cls.user_id = int(user["id"]) if user else None

    def _login(self) -> None:
        with self.client.session_transaction() as sess:
            sess["user_id"] = self.user_id
            sess["tenant_id"] = 1
            sess["is_super_admin"] = False

    def test_checkout_requires_authentication(self) -> None:
        # Fresh client: the shared client carries a session cookie from other tests.
        anon = self.app.test_client()
        res = anon.post("/api/payments/checkout", json={"sale_id": self.sale_id})
        self.assertIn(res.status_code, (401, 403), res.get_data(as_text=True))

    def test_checkout_creates_session_and_returns_url(self) -> None:
        if self.user_id is None:
            self.skipTest("no seeded tenant user available")
        self._login()
        fake = {
            "data": {
                "id": "cs_checkout_1",
                "attributes": {
                    "checkout_url": "https://checkout.paymongo.com/cs_checkout_1",
                    "payment_intent": {"id": "pi_1"},
                },
            }
        }
        with patch(
            "app.core.payments.paymongo_gateway.PayMongoGateway._request",
            return_value=fake,
        ):
            res = self.client.post(
                "/api/payments/checkout",
                json={"sale_id": self.sale_id, "methods": ["gcash", "qrph"]},
            )
        self.assertEqual(res.status_code, 200, res.get_data(as_text=True))
        data = json.loads(res.get_data(as_text=True))["data"]
        self.assertEqual(data["checkout_url"], "https://checkout.paymongo.com/cs_checkout_1")
        self.assertEqual(data["status"], "pending")

        with get_raw_connection() as conn:
            row = conn.execute(
                "SELECT status, reference, sale_id FROM payments WHERE reference = 'cs_checkout_1'"
            ).fetchone()
            self.assertIsNotNone(row, "checkout must persist a pending payment row")
            self.assertEqual(str(row["status"]), "pending")
            self.assertEqual(int(row["sale_id"]), self.sale_id)

    def test_checkout_rejects_unknown_sale(self) -> None:
        if self.user_id is None:
            self.skipTest("no seeded tenant user available")
        self._login()
        res = self.client.post("/api/payments/checkout", json={"sale_id": 999999})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(json.loads(res.get_data(as_text=True))["error"]["code"], "checkout_failed")


if __name__ == "__main__":
    unittest.main()
