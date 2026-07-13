from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Invoice:
    id: int
    tenant_id: int
    subscription_id: int
    plan_id: int
    amount: float
    tax_rate: float
    tax_amount: float
    total_amount: float
    status: str
    issued_at: str
    due_at: str | None
    paid_at: str | None
    payment_reference: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "subscription_id": self.subscription_id,
            "plan_id": self.plan_id,
            "amount": self.amount,
            "tax_rate": self.tax_rate,
            "tax_amount": self.tax_amount,
            "total_amount": self.total_amount,
            "status": self.status,
            "issued_at": self.issued_at,
            "due_at": self.due_at,
            "paid_at": self.paid_at,
            "payment_reference": self.payment_reference,
        }

    @property
    def ready_for_pdf(self) -> dict[str, Any]:
        return self.as_dict()
