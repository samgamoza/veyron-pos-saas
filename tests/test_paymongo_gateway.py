from __future__ import annotations

import hashlib
import hmac
import json
import unittest

from app.core.payments.paymongo_gateway import PayMongoGateway, PayMongoError


class PayMongoChargeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.gateway = PayMongoGateway({"secret_key": "sk_test_abc", "public_key": "pk_test_abc"})
        self.captured: dict[str, object] = {}

        def fake_request(method, path, payload=None):
            self.captured = {"method": method, "path": path, "payload": payload}
            return {
                "data": {
                    "id": "cs_123",
                    "attributes": {
                        "checkout_url": "https://checkout.paymongo.com/cs_123",
                        "payment_intent": {"id": "pi_456"},
                    },
                }
            }

        self.gateway._request = fake_request  # type: ignore[assignment]

    def test_charge_builds_checkout_session_and_returns_url(self) -> None:
        result = self.gateway.charge(
            250.0, "PHP", {"reference": "sale_99", "methods": ["gcash", "qrph"]}
        )
        self.assertEqual(self.captured["method"], "POST")
        self.assertEqual(self.captured["path"], "/checkout_sessions")
        attrs = self.captured["payload"]["data"]["attributes"]
        # amount converted to centavos
        self.assertEqual(attrs["line_items"][0]["amount"], 25000)
        self.assertEqual(attrs["line_items"][0]["currency"], "PHP")
        self.assertEqual(attrs["payment_method_types"], ["gcash", "qrph"])
        self.assertEqual(attrs["reference_number"], "sale_99")
        # normalized response
        self.assertEqual(result["status"], "pending")
        self.assertEqual(result["checkout_url"], "https://checkout.paymongo.com/cs_123")
        self.assertEqual(result["checkout_session_id"], "cs_123")
        self.assertEqual(result["payment_intent_id"], "pi_456")

    def test_charge_defaults_methods(self) -> None:
        self.gateway.charge(20.0, "php", {})
        attrs = self.captured["payload"]["data"]["attributes"]
        self.assertEqual(attrs["payment_method_types"], ["gcash", "paymaya", "qrph", "card"])
        self.assertEqual(attrs["line_items"][0]["currency"], "PHP")

    def test_missing_secret_key_raises(self) -> None:
        with self.assertRaises(PayMongoError):
            PayMongoGateway({})._auth_header()


class PayMongoRefundTest(unittest.TestCase):
    def test_refund_builds_payload(self) -> None:
        gateway = PayMongoGateway({"secret_key": "sk_test_abc"})
        captured = {}

        def fake_request(method, path, payload=None):
            captured.update({"method": method, "path": path, "payload": payload})
            return {"data": {"id": "ref_1", "attributes": {"status": "pending"}}}

        gateway._request = fake_request  # type: ignore[assignment]
        result = gateway.refund("pay_123", 100.0)
        self.assertEqual(captured["path"], "/refunds")
        attrs = captured["payload"]["data"]["attributes"]
        self.assertEqual(attrs["payment_id"], "pay_123")
        self.assertEqual(attrs["amount"], 10000)
        self.assertEqual(result["refund_id"], "ref_1")


class PayMongoWebhookSignatureTest(unittest.TestCase):
    def _sign(self, secret: str, timestamp: str, body: bytes) -> str:
        return hmac.new(secret.encode(), f"{timestamp}.{body.decode()}".encode(), hashlib.sha256).hexdigest()

    def test_valid_signature_verifies(self) -> None:
        secret = "whsk_test_secret"
        gateway = PayMongoGateway({"secret_key": "sk_test", "webhook_secret": secret})
        body = json.dumps({"data": {"attributes": {"type": "checkout_session.payment.paid"}}}).encode()
        ts = "1700000000"
        sig = self._sign(secret, ts, body)
        headers = {"raw_body": body, "Paymongo-Signature": f"t={ts},te={sig}"}
        out = gateway.handle_webhook(json.loads(body), headers)
        self.assertTrue(out["verified"])
        self.assertEqual(out["event"], "checkout_session.payment.paid")

    def test_tampered_signature_fails(self) -> None:
        secret = "whsk_test_secret"
        gateway = PayMongoGateway({"secret_key": "sk_test", "webhook_secret": secret})
        body = json.dumps({"data": {"attributes": {"type": "payment.paid"}}}).encode()
        headers = {"raw_body": body, "Paymongo-Signature": "t=1700000000,te=deadbeef"}
        out = gateway.handle_webhook(json.loads(body), headers)
        self.assertFalse(out["verified"])

    def test_no_secret_is_unverified(self) -> None:
        gateway = PayMongoGateway({"secret_key": "sk_test"})
        out = gateway.handle_webhook({"data": {"attributes": {"type": "payment.paid"}}}, {})
        self.assertFalse(out["verified"])


if __name__ == "__main__":
    unittest.main()
