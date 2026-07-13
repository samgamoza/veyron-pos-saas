from __future__ import annotations

from typing import Any

from .gateway import PaymentGateway


class PayPalGateway(PaymentGateway):
    @classmethod
    def gateway_name(cls) -> str:
        return "paypal"

    def charge(self, amount: float, currency: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        client_id = self.config.get("client_id")
        client_secret = self.config.get("client_secret")
        if not client_id or not client_secret:
            raise ValueError("PayPal client credentials are required.")

        return {
            "provider": self.gateway_name(),
            "status": "success",
            "amount": amount,
            "currency": currency,
            "transaction_id": f"paypal_{int(amount * 100)}_{hash(client_id) & 0xFFFF}",
            "metadata": metadata or {},
        }

    def refund(self, payment_id: str, amount: float | None = None) -> dict[str, Any]:
        if not self.config.get("client_id") or not self.config.get("client_secret"):
            raise ValueError("PayPal client credentials are required.")

        return {
            "provider": self.gateway_name(),
            "status": "success",
            "payment_id": payment_id,
            "refund_amount": amount,
        }

    def handle_webhook(self, payload: dict[str, Any], headers: dict[str, Any]) -> dict[str, Any]:
        event_type = payload.get("event_type", payload.get("type", "unknown"))
        return {
            "provider": self.gateway_name(),
            "event": event_type,
            "payload": payload,
            "headers": headers,
        }
