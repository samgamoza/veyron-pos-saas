from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Iterable

from app.core.subscription.model import Subscription
from .model import Invoice

STATUS_PENDING = "pending"
STATUS_PAID = "paid"
STATUS_FAILED = "failed"
STATUS_CANCELED = "canceled"


class BillingService:
    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def create_invoice(
        self,
        tenant_id: int,
        subscription_id: int,
        plan_id: int,
        amount: float,
        tax_rate: float = 0.0,
        status: str = STATUS_PENDING,
        due_days: int | None = None,
        payment_reference: str | None = None,
    ) -> Invoice:
        tax_amount = round(amount * float(tax_rate), 2)
        total_amount = round(amount + tax_amount, 2)
        due_at = None
        if due_days is not None:
            due_at = (datetime.utcnow() + timedelta(days=due_days)).isoformat()

        row = self.connection.execute(
            """
            INSERT INTO invoices (
                tenant_id,
                subscription_id,
                plan_id,
                amount,
                tax_rate,
                tax_amount,
                total_amount,
                status,
                issued_at,
                due_at,
                paid_at,
                payment_reference
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?, NULL, ?)
            RETURNING id, tenant_id, subscription_id, plan_id, amount, tax_rate, tax_amount, total_amount, status, issued_at, due_at, paid_at, payment_reference
            """,
            (tenant_id, subscription_id, plan_id, amount, tax_rate, tax_amount, total_amount, status, due_at, payment_reference),
        ).fetchone()
        return self._row_to_invoice(row)

    def auto_generate_invoice_for_subscription(
        self,
        subscription: Subscription,
        tax_rate: float = 0.0,
        due_days: int | None = None,
    ) -> Invoice:
        return self.create_invoice(
            tenant_id=subscription.tenant_id,
            subscription_id=subscription.id,
            plan_id=subscription.plan_id,
            amount=self._get_plan_price(subscription.plan_id),
            tax_rate=tax_rate,
            due_days=due_days,
        )

    def get_invoice_by_id(self, invoice_id: int) -> Invoice | None:
        row = self.connection.execute(
            "SELECT id, tenant_id, subscription_id, plan_id, amount, tax_rate, tax_amount, total_amount, status, issued_at, due_at, paid_at, payment_reference FROM invoices WHERE id = ?",
            (invoice_id,),
        ).fetchone()
        return self._row_to_invoice(row)

    def get_invoices_for_tenant(self, tenant_id: int, status: str | None = None) -> list[Invoice]:
        sql = "SELECT id, tenant_id, subscription_id, plan_id, amount, tax_rate, tax_amount, total_amount, status, issued_at, due_at, paid_at, payment_reference FROM invoices WHERE tenant_id = ?"
        params: tuple[Any, ...] = (tenant_id,)
        if status:
            sql += " AND status = ?"
            params += (status,)
        sql += " ORDER BY issued_at DESC, id DESC"
        rows = self.connection.execute(sql, params).fetchall()
        return [self._row_to_invoice(row) for row in rows]

    def mark_invoice_paid(self, invoice_id: int, payment_reference: str | None = None) -> Invoice | None:
        self.connection.execute(
            "UPDATE invoices SET status = ?, paid_at = CURRENT_TIMESTAMP, payment_reference = ? WHERE id = ?",
            (STATUS_PAID, payment_reference, invoice_id),
        )
        return self.get_invoice_by_id(invoice_id)

    def mark_invoice_failed(self, invoice_id: int, payment_reference: str | None = None) -> Invoice | None:
        self.connection.execute(
            "UPDATE invoices SET status = ?, payment_reference = ? WHERE id = ?",
            (STATUS_FAILED, payment_reference, invoice_id),
        )
        return self.get_invoice_by_id(invoice_id)

    def _get_plan_price(self, plan_id: int) -> float:
        row = self.connection.execute("SELECT price FROM plans WHERE id = ?", (plan_id,)).fetchone()
        if row is None:
            raise ValueError(f"Plan not found: {plan_id}")
        return float(row["price"])

    def _row_to_invoice(self, row: Any) -> Invoice | None:
        if row is None:
            return None
        return Invoice(
            id=row["id"],
            tenant_id=row["tenant_id"],
            subscription_id=row["subscription_id"],
            plan_id=row["plan_id"],
            amount=float(row["amount"]),
            tax_rate=float(row["tax_rate"]),
            tax_amount=float(row["tax_amount"]),
            total_amount=float(row["total_amount"]),
            status=row["status"],
            issued_at=row["issued_at"],
            due_at=row["due_at"],
            paid_at=row["paid_at"],
            payment_reference=row["payment_reference"],
        )
