"""Promotions: tenant-scoped coupon codes applied at checkout.

A promotion is validated against a cart subtotal and quoted as a discount. All
queries are explicitly tenant-scoped (alongside RLS). Redemption count is only
incremented when a sale using the code actually completes.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

DISCOUNT_TYPES = ("percent", "amount")


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _money(value: float) -> float:
    return round(float(value) + 0.0, 2)


class PromotionError(ValueError):
    """Raised when a promotion code is invalid, expired, or not applicable."""


class PromotionService:
    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def list_promotions(self, tenant_id: int, *, include_inactive: bool = False) -> list[dict[str, Any]]:
        sql = (
            "SELECT id, name, code, discount_type, value, min_subtotal, starts_at, ends_at, "
            "usage_limit, used_count, is_active, created_at FROM promotions WHERE tenant_id = ?"
        )
        if not include_inactive:
            sql += " AND is_active = 1"
        sql += " ORDER BY created_at DESC, id DESC"
        rows = self.connection.execute(sql, (tenant_id,)).fetchall()
        return [dict(r) for r in rows or []]

    def create_promotion(
        self,
        tenant_id: int,
        name: str,
        code: str,
        *,
        discount_type: str = "percent",
        value: float = 0.0,
        min_subtotal: float = 0.0,
        starts_at: str | None = None,
        ends_at: str | None = None,
        usage_limit: int | None = None,
    ) -> int:
        name = (name or "").strip()
        code = (code or "").strip().upper()
        discount_type = (discount_type or "percent").strip().lower()
        if not name:
            raise PromotionError("Promotion name is required.")
        if not code:
            raise PromotionError("Promotion code is required.")
        if discount_type not in DISCOUNT_TYPES:
            raise PromotionError("Discount type must be 'percent' or 'amount'.")
        value = float(value or 0.0)
        if value <= 0:
            raise PromotionError("Discount value must be greater than zero.")
        if discount_type == "percent" and value > 100:
            raise PromotionError("Percent discount cannot exceed 100.")

        existing = self.connection.execute(
            "SELECT 1 FROM promotions WHERE tenant_id = ? AND code = ?", (tenant_id, code)
        ).fetchone()
        if existing is not None:
            raise PromotionError(f"A promotion with code '{code}' already exists.")

        row = self.connection.execute(
            """
            INSERT INTO promotions
                (tenant_id, name, code, discount_type, value, min_subtotal, starts_at, ends_at, usage_limit, is_active)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            RETURNING id
            """,
            (tenant_id, name, code, discount_type, value, float(min_subtotal or 0.0),
             starts_at or None, ends_at or None, usage_limit),
        ).fetchone()
        return int(row["id"])

    def set_active(self, tenant_id: int, promotion_id: int, is_active: bool) -> None:
        self.connection.execute(
            "UPDATE promotions SET is_active = ? WHERE tenant_id = ? AND id = ?",
            (1 if is_active else 0, tenant_id, promotion_id),
        )

    def get_by_code(self, tenant_id: int, code: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT id, name, code, discount_type, value, min_subtotal, starts_at, ends_at, "
            "usage_limit, used_count, is_active FROM promotions WHERE tenant_id = ? AND code = ?",
            (tenant_id, (code or "").strip().upper()),
        ).fetchone()
        return dict(row) if row else None

    def validate_and_quote(self, tenant_id: int, code: str, subtotal: float) -> dict[str, Any]:
        """Validate a code against a subtotal and return the discount quote.

        Raises PromotionError with a user-facing reason if not applicable.
        """
        promo = self.get_by_code(tenant_id, code)
        if promo is None:
            raise PromotionError("Promotion code not found.")
        if not promo["is_active"]:
            raise PromotionError("This promotion is no longer active.")

        now = _now()
        if promo["starts_at"] and now < str(promo["starts_at"]):
            raise PromotionError("This promotion has not started yet.")
        if promo["ends_at"] and now > str(promo["ends_at"]):
            raise PromotionError("This promotion has expired.")
        if promo["usage_limit"] is not None and int(promo["used_count"]) >= int(promo["usage_limit"]):
            raise PromotionError("This promotion has reached its usage limit.")

        subtotal = float(subtotal or 0.0)
        if subtotal < float(promo["min_subtotal"] or 0.0):
            raise PromotionError(
                f"A minimum spend of {float(promo['min_subtotal']):.2f} is required for this promotion."
            )

        if promo["discount_type"] == "percent":
            discount_rate = float(promo["value"]) / 100.0
            discount_amount = _money(subtotal * discount_rate)
        else:  # amount
            discount_amount = _money(min(float(promo["value"]), subtotal))
            discount_rate = (discount_amount / subtotal) if subtotal else 0.0

        return {
            "promotion_id": int(promo["id"]),
            "code": promo["code"],
            "discount_type": promo["discount_type"],
            "discount_amount": discount_amount,
            "discount_rate": discount_rate,
        }

    def record_redemption(self, tenant_id: int, promotion_id: int) -> None:
        """Increment usage after a sale using the promotion completes."""
        self.connection.execute(
            "UPDATE promotions SET used_count = used_count + 1 WHERE tenant_id = ? AND id = ?",
            (tenant_id, promotion_id),
        )
