from __future__ import annotations

from typing import Any

from app.core.billing.service import BillingService
from app.core.db import get_connection


def _invoice_to_dict(inv: Any) -> dict[str, Any]:
    return {
        "id": inv.id,
        "tenant_id": inv.tenant_id,
        "subscription_id": inv.subscription_id,
        "plan_id": inv.plan_id,
        "amount": inv.amount,
        "tax_rate": inv.tax_rate,
        "tax_amount": inv.tax_amount,
        "total_amount": inv.total_amount,
        "status": inv.status,
        "issued_at": inv.issued_at,
        "due_at": inv.due_at,
        "paid_at": inv.paid_at,
        "payment_reference": inv.payment_reference,
    }


class BillingApiService:
    def list_invoices(self, tenant_id: int, status: str | None = None) -> list[dict[str, Any]]:
        with get_connection() as connection:
            svc = BillingService(connection)
            invoices = svc.get_invoices_for_tenant(tenant_id, status=status)
        return [_invoice_to_dict(i) for i in invoices]

    def record_payment(self, tenant_id: int, invoice_id: int, payment_reference: str | None) -> dict[str, Any]:
        with get_connection() as connection:
            svc = BillingService(connection)
            inv = svc.get_invoice_by_id(invoice_id)
            if inv is None or inv.tenant_id != tenant_id:
                raise ValueError("Invoice not found.")
            updated = svc.mark_invoice_paid(invoice_id, payment_reference=payment_reference)
            if updated is None:
                raise ValueError("Unable to record payment.")
        return _invoice_to_dict(updated)


billing_api_service = BillingApiService()
