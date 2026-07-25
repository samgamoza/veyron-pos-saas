"""PayMongo gateway (Philippine payments: GCash, Maya, QRPH, cards).

Uses PayMongo **Checkout Sessions**: one server call creates a hosted checkout page
covering every enabled method and returns a ``checkout_url`` to send the customer to.
Payment confirmation arrives asynchronously via webhook (``handle_webhook``), which is
the correct model for e-wallets.

HTTP lives in a single ``_request`` method so tests can mock it without network or keys.
No third-party HTTP dependency — stdlib ``urllib`` only.

Config (dict passed to __init__, sourced from the encrypted per-tenant store or, as a
platform fallback, ``config_from_env``):
    secret_key      PayMongo secret key (sk_test_… / sk_live_…). Server-side only.
    public_key      PayMongo public key (pk_…). Safe for client use.
    webhook_secret  Signing secret for webhook signature verification (whsk_…).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import urllib.error
import urllib.request
from typing import Any

from .gateway import PaymentGateway

PAYMONGO_API_BASE = "https://api.paymongo.com/v1"
DEFAULT_METHODS = ("gcash", "paymaya", "qrph", "card")


class PayMongoError(RuntimeError):
    """Raised when the PayMongo API returns an error or is misconfigured."""


def _to_centavos(amount: float) -> int:
    # PayMongo amounts are integer centavos.
    return int(round(float(amount) * 100))


class PayMongoGateway(PaymentGateway):
    @classmethod
    def gateway_name(cls) -> str:
        return "paymongo"

    @classmethod
    def config_from_env(cls) -> dict[str, Any] | None:
        """Platform-level config from environment. Returns None if no secret key set."""
        secret = os.getenv("PAYMONGO_SECRET_KEY", "").strip()
        if not secret:
            return None
        return {
            "secret_key": secret,
            "public_key": os.getenv("PAYMONGO_PUBLIC_KEY", "").strip(),
            "webhook_secret": os.getenv("PAYMONGO_WEBHOOK_SECRET", "").strip(),
        }

    # --- HTTP -----------------------------------------------------------------
    def _auth_header(self) -> str:
        secret = self.config.get("secret_key")
        if not secret:
            raise PayMongoError("PayMongo secret_key is required.")
        token = base64.b64encode(f"{secret}:".encode("utf-8")).decode("ascii")
        return f"Basic {token}"

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{PAYMONGO_API_BASE}{path}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Authorization", self._auth_header())
        request.add_header("Content-Type", "application/json")
        request.add_header("Accept", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            raise PayMongoError(f"PayMongo API {exc.code} for {method} {path}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise PayMongoError(f"PayMongo API unreachable for {method} {path}: {exc.reason}") from exc
        return json.loads(body) if body else {}

    # --- Operations -----------------------------------------------------------
    def charge(self, amount: float, currency: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        """Create a hosted Checkout Session and return its checkout_url.

        metadata (all optional):
            methods        list of payment_method_types (default GCash/Maya/QRPH/card)
            description    shown on the checkout page
            reference      merchant reference (e.g. sale id / idempotency key)
            line_items     explicit PayMongo line_items; else a single line for `amount`
            success_url    redirect after payment
            cancel_url     redirect if the customer cancels
        """
        meta = metadata or {}
        methods = list(meta.get("methods") or DEFAULT_METHODS)
        line_items = meta.get("line_items") or [
            {
                "currency": (currency or "PHP").upper(),
                "amount": _to_centavos(amount),
                "name": str(meta.get("description") or "POS Sale"),
                "quantity": 1,
            }
        ]
        attributes: dict[str, Any] = {
            "line_items": line_items,
            "payment_method_types": methods,
            "description": str(meta.get("description") or "POS Sale"),
            "send_email_receipt": bool(meta.get("send_email_receipt", False)),
            "show_line_items": True,
        }
        if meta.get("reference"):
            attributes["reference_number"] = str(meta["reference"])
        if meta.get("success_url"):
            attributes["success_url"] = str(meta["success_url"])
        if meta.get("cancel_url"):
            attributes["cancel_url"] = str(meta["cancel_url"])

        result = self._request("POST", "/checkout_sessions", {"data": {"attributes": attributes}})
        data = result.get("data", {})
        attrs = data.get("attributes", {})
        payment_intent = attrs.get("payment_intent") or {}
        return {
            "provider": self.gateway_name(),
            "status": "pending",
            "amount": amount,
            "currency": (currency or "PHP").upper(),
            "checkout_session_id": data.get("id"),
            "checkout_url": attrs.get("checkout_url"),
            "payment_intent_id": payment_intent.get("id") if isinstance(payment_intent, dict) else payment_intent,
            "reference": meta.get("reference"),
            "raw": result,
        }

    def refund(self, payment_id: str, amount: float | None = None) -> dict[str, Any]:
        if not payment_id:
            raise PayMongoError("payment_id is required to refund.")
        attributes: dict[str, Any] = {"payment_id": payment_id, "reason": "requested_by_customer"}
        if amount is not None:
            attributes["amount"] = _to_centavos(amount)
        result = self._request("POST", "/refunds", {"data": {"attributes": attributes}})
        data = result.get("data", {})
        attrs = data.get("attributes", {})
        return {
            "provider": self.gateway_name(),
            "status": attrs.get("status", "pending"),
            "payment_id": payment_id,
            "refund_id": data.get("id"),
            "refund_amount": amount,
            "raw": result,
        }

    def handle_webhook(self, payload: dict[str, Any], headers: dict[str, Any]) -> dict[str, Any]:
        """Normalize a PayMongo webhook event.

        When a webhook_secret is configured, the caller should pass the raw request
        body via headers['raw_body'] and the 'Paymongo-Signature' header so the
        signature can be verified. Unverifiable events are marked verified=False.
        """
        verified = self._verify_signature(headers)
        data = payload.get("data", {})
        attrs = data.get("attributes", {})
        event_type = attrs.get("type") or payload.get("type") or "unknown"
        resource = attrs.get("data", {})
        resource_attrs = resource.get("attributes", {}) if isinstance(resource, dict) else {}
        return {
            "provider": self.gateway_name(),
            "event": event_type,
            "verified": verified,
            "resource_id": resource.get("id") if isinstance(resource, dict) else None,
            "resource_status": resource_attrs.get("status"),
            "amount": (resource_attrs.get("amount") or 0) / 100 if resource_attrs.get("amount") else None,
            "payload": payload,
        }

    def _verify_signature(self, headers: dict[str, Any]) -> bool:
        secret = self.config.get("webhook_secret")
        raw_body = headers.get("raw_body")
        signature = headers.get("Paymongo-Signature") or headers.get("paymongo-signature")
        if not secret or not raw_body or not signature:
            # No secret configured, or nothing to verify against: cannot assert authenticity.
            return False
        parts = dict(
            piece.split("=", 1) for piece in str(signature).split(",") if "=" in piece
        )
        timestamp = parts.get("t")
        provided = parts.get("te") or parts.get("li")  # te = test mode, li = live mode
        if not timestamp or not provided:
            return False
        body_str = raw_body.decode("utf-8") if isinstance(raw_body, (bytes, bytearray)) else str(raw_body)
        expected = hmac.new(
            secret.encode("utf-8"),
            f"{timestamp}.{body_str}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, provided)
