"""Customer-facing payment flows (PayMongo checkout + webhook reconciliation).

Distinct from `billing_api_service`, which handles subscription invoices the tenant
pays to the platform. This module handles payments a tenant's *customers* make.

Two independent flows share one webhook:
  1. POS sale: a sale is finalized with payment_status='pending' (see pos_finalize).
     create_checkout() opens a PayMongo Checkout Session and stores the session id on
     the sale's pending `payments` row.
  2. eTown/QR order (pay-now-or-at-counter): create_checkout_for_order() opens a
     Checkout Session for an `orders` row directly (orders has its own
     payment_status/payment_reference — no `payments` row, since orders have no
     sale_id and `payments.sale_id` is NOT NULL).
  In both cases the customer pays; PayMongo calls the webhook; handle_paymongo_webhook()
  verifies the signature and settles whichever side (payments or orders) matches the
  checkout session reference.
"""

from __future__ import annotations

import json
from typing import Any

from app.core.db import get_connection, get_raw_connection
from app.core.payments.service import PaymentService

GATEWAY = "paymongo"
PAID_EVENTS = {"checkout_session.payment.paid", "payment.paid"}
FAILED_EVENTS = {"payment.failed"}


class WebhookVerificationError(RuntimeError):
    """Raised when a webhook signature cannot be verified — treat as hostile."""


class PaymentsApiService:
    def create_checkout(
        self,
        tenant_id: int,
        sale_id: int,
        *,
        methods: list[str] | None = None,
        success_url: str | None = None,
        cancel_url: str | None = None,
    ) -> dict[str, Any]:
        with get_connection() as connection:
            sale = connection.execute(
                "SELECT id, total, status FROM sales WHERE id = ? AND tenant_id = ?",
                (sale_id, tenant_id),
            ).fetchone()
            if sale is None:
                raise ValueError("Sale not found for this tenant.")
            if str(sale["status"]) != "completed":
                raise ValueError(f"Cannot collect payment for a {sale['status']} sale.")

            amount = float(sale["total"])
            if amount <= 0:
                raise ValueError("Sale total must be greater than zero.")

            metadata: dict[str, Any] = {
                "reference": f"sale-{sale_id}",
                "description": f"Sale #{sale_id}",
            }
            if methods:
                metadata["methods"] = methods
            if success_url:
                metadata["success_url"] = success_url
            if cancel_url:
                metadata["cancel_url"] = cancel_url

            result = PaymentService(connection).charge(tenant_id, GATEWAY, amount, "PHP", metadata)
            session_id = result.get("checkout_session_id") or ""

            existing = connection.execute(
                """
                SELECT id FROM payments
                WHERE tenant_id = ? AND sale_id = ? AND method = ? AND status = 'pending'
                """,
                (tenant_id, sale_id, GATEWAY),
            ).fetchone()

            if existing is not None:
                payment_id = int(existing["id"])
                connection.execute(
                    "UPDATE payments SET reference = ? WHERE tenant_id = ? AND id = ?",
                    (session_id, tenant_id, payment_id),
                )
            else:
                row = connection.execute(
                    """
                    INSERT INTO payments (tenant_id, sale_id, amount, method, status, reference)
                    VALUES (?, ?, ?, ?, 'pending', ?)
                    RETURNING id
                    """,
                    (tenant_id, sale_id, amount, GATEWAY, session_id),
                ).fetchone()
                payment_id = int(row["id"])

        return {
            "payment_id": payment_id,
            "sale_id": sale_id,
            "amount": amount,
            "checkout_url": result.get("checkout_url"),
            "checkout_session_id": session_id,
            "status": "pending",
        }

    def create_checkout_for_order(
        self,
        tenant_id: int,
        order_id: int,
        *,
        methods: list[str] | None = None,
        success_url: str | None = None,
        cancel_url: str | None = None,
    ) -> dict[str, Any]:
        """Open a PayMongo checkout for an eTown/QR order. Pay-now is optional —
        an order left unpaid is simply settled at the counter."""
        with get_connection() as connection:
            order = connection.execute(
                "SELECT id, total, status, payment_status FROM orders WHERE id = ? AND tenant_id = ?",
                (order_id, tenant_id),
            ).fetchone()
            if order is None:
                raise ValueError("Order not found for this tenant.")
            if str(order["payment_status"]) == "paid":
                raise ValueError("This order has already been paid.")
            if str(order["status"]) == "cancelled":
                raise ValueError("This order has been cancelled.")

            amount = float(order["total"])
            if amount <= 0:
                raise ValueError("Order total must be greater than zero.")

            metadata: dict[str, Any] = {
                "reference": f"order-{order_id}",
                "description": f"Order #{order_id}",
            }
            if methods:
                metadata["methods"] = methods
            if success_url:
                metadata["success_url"] = success_url
            if cancel_url:
                metadata["cancel_url"] = cancel_url

            result = PaymentService(connection).charge(tenant_id, GATEWAY, amount, "PHP", metadata)
            session_id = result.get("checkout_session_id") or ""

            connection.execute(
                "UPDATE orders SET payment_status = 'awaiting_payment', payment_method = ?, "
                "payment_reference = ? WHERE tenant_id = ? AND id = ?",
                (GATEWAY, session_id, tenant_id, order_id),
            )

        return {
            "order_id": order_id,
            "amount": amount,
            "checkout_url": result.get("checkout_url"),
            "checkout_session_id": session_id,
            "status": "awaiting_payment",
        }

    def handle_paymongo_webhook(self, raw_body: bytes, headers: dict[str, Any]) -> dict[str, Any]:
        """Verify and apply a PayMongo webhook. Idempotent.

        Uses a raw (cross-tenant) connection deliberately: webhooks arrive with no
        session and therefore no tenant context, and must reconcile a payment that
        belongs to some tenant. The signature check is the trust boundary.
        """
        try:
            payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
        except (ValueError, UnicodeDecodeError) as exc:
            raise WebhookVerificationError("Malformed webhook body.") from exc

        with get_raw_connection() as connection:
            event = PaymentService(connection).handle_webhook(
                GATEWAY, payload, {**headers, "raw_body": raw_body}
            )
            if not event.get("verified"):
                raise WebhookVerificationError("Webhook signature verification failed.")

            event_type = str(event.get("event") or "")
            resource_id = event.get("resource_id")
            if not resource_id:
                return {"event": event_type, "matched": False, "reason": "no resource id"}

            new_status = None
            if event_type in PAID_EVENTS:
                new_status = "completed"
            elif event_type in FAILED_EVENTS:
                new_status = "failed"
            if new_status is None:
                return {"event": event_type, "matched": False, "reason": "event ignored"}

            payment = connection.execute(
                "SELECT id, tenant_id, sale_id, status FROM payments WHERE reference = ?",
                (resource_id,),
            ).fetchone()
            if payment is not None:
                if str(payment["status"]) == new_status:
                    return {"event": event_type, "matched": True, "changed": False, "status": new_status}

                connection.execute(
                    "UPDATE payments SET status = ? WHERE id = ?",
                    (new_status, int(payment["id"])),
                )
                connection.execute(
                    """
                    INSERT INTO audit_logs (tenant_id, user_id, action, entity_type, entity_id, details)
                    VALUES (?, NULL, ?, 'payment', ?, ?)
                    """,
                    (
                        int(payment["tenant_id"]),
                        f"payment_{new_status}",
                        int(payment["id"]),
                        f"PayMongo {event_type} for sale {payment['sale_id']} (ref {resource_id}).",
                    ),
                )
                return {
                    "event": event_type,
                    "matched": True,
                    "changed": True,
                    "status": new_status,
                    "payment_id": int(payment["id"]),
                    "sale_id": int(payment["sale_id"]),
                }

            # No POS sale payment matched — try an eTown/QR order (pay-now flow).
            order_status = "paid" if new_status == "completed" else "payment_failed"
            order = connection.execute(
                "SELECT id, tenant_id, payment_status FROM orders WHERE payment_reference = ?",
                (resource_id,),
            ).fetchone()
            if order is None:
                return {"event": event_type, "matched": False, "reason": "no matching payment"}

            if str(order["payment_status"]) == order_status:
                return {"event": event_type, "matched": True, "changed": False, "status": order_status}

            connection.execute(
                "UPDATE orders SET payment_status = ? WHERE id = ?",
                (order_status, int(order["id"])),
            )
            connection.execute(
                """
                INSERT INTO audit_logs (tenant_id, user_id, action, entity_type, entity_id, details)
                VALUES (?, NULL, ?, 'order', ?, ?)
                """,
                (
                    int(order["tenant_id"]),
                    f"order_{order_status}",
                    int(order["id"]),
                    f"PayMongo {event_type} for order {order['id']} (ref {resource_id}).",
                ),
            )
            return {
                "event": event_type,
                "matched": True,
                "changed": True,
                "status": order_status,
                "order_id": int(order["id"]),
            }


payments_api_service = PaymentsApiService()
