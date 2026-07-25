"""Loyalty points: append-only ledger, tenant-scoped.

A customer's balance is the sum of their ledger entries — never a mutable column,
so accrual and redemption are auditable and can't silently drift. Per-tenant
settings live in app_settings: loyalty_enabled, loyalty_earn_rate (points per peso),
loyalty_redeem_rate (peso value per point).
"""

from __future__ import annotations

import math
from typing import Any

SETTING_KEYS = ("loyalty_enabled", "loyalty_earn_rate", "loyalty_redeem_rate")


def _money(value: float) -> float:
    return round(float(value) + 0.0, 2)


class LoyaltyError(ValueError):
    """Raised when a loyalty operation is invalid (e.g. redeeming more than the balance)."""


class LoyaltyService:
    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def settings(self, tenant_id: int) -> dict[str, Any]:
        rows = self.connection.execute(
            "SELECT key, value FROM app_settings WHERE tenant_id = ? AND key IN (?, ?, ?)",
            (tenant_id, *SETTING_KEYS),
        ).fetchall()
        values = {str(r["key"]): r["value"] for r in rows or []}

        def _num(key: str, default: float) -> float:
            try:
                return float(values.get(key, default))
            except (TypeError, ValueError):
                return default

        return {
            "enabled": str(values.get("loyalty_enabled", "0")).strip() in {"1", "true", "yes", "on"},
            "earn_rate": _num("loyalty_earn_rate", 1.0),
            "redeem_rate": _num("loyalty_redeem_rate", 1.0),
        }

    def balance(self, tenant_id: int, customer_id: int) -> int:
        row = self.connection.execute(
            "SELECT COALESCE(SUM(points_change), 0) AS bal FROM loyalty_ledger "
            "WHERE tenant_id = ? AND customer_id = ?",
            (tenant_id, customer_id),
        ).fetchone()
        return int(row["bal"] if row else 0)

    def earn_for_sale(self, tenant_id: int, customer_id: int, sale_id: int | None, amount_spent: float) -> int:
        """Accrue points for a completed sale. Returns points earned (0 if disabled)."""
        cfg = self.settings(tenant_id)
        if not cfg["enabled"] or cfg["earn_rate"] <= 0 or customer_id is None:
            return 0
        points = int(math.floor(float(amount_spent) * cfg["earn_rate"]))
        if points <= 0:
            return 0
        self._append(tenant_id, customer_id, sale_id, points, "earn")
        return points

    def quote_redemption(self, tenant_id: int, customer_id: int, points_requested: int, subtotal: float) -> dict[str, Any]:
        """How many points can actually be redeemed and the peso discount they yield.

        Capped by the customer's balance and by the sale subtotal (points can't make
        a sale negative).
        """
        cfg = self.settings(tenant_id)
        if not cfg["enabled"] or cfg["redeem_rate"] <= 0:
            return {"points": 0, "discount_amount": 0.0}
        points_requested = max(0, int(points_requested or 0))
        available = self.balance(tenant_id, customer_id)
        points = min(points_requested, available)
        if points <= 0:
            return {"points": 0, "discount_amount": 0.0}
        discount = points * cfg["redeem_rate"]
        if discount > float(subtotal or 0.0):
            # Cap to subtotal; only redeem the points actually consumed.
            points = int(math.floor(float(subtotal) / cfg["redeem_rate"]))
            discount = points * cfg["redeem_rate"]
        return {"points": points, "discount_amount": _money(discount)}

    def redeem_for_sale(self, tenant_id: int, customer_id: int, sale_id: int | None, points: int) -> float:
        """Deduct points for a redemption. Returns the peso value redeemed."""
        cfg = self.settings(tenant_id)
        points = int(points or 0)
        if points <= 0:
            return 0.0
        if not cfg["enabled"] or cfg["redeem_rate"] <= 0:
            raise LoyaltyError("Loyalty redemption is not enabled.")
        available = self.balance(tenant_id, customer_id)
        if points > available:
            raise LoyaltyError("Not enough points to redeem.")
        self._append(tenant_id, customer_id, sale_id, -points, "redeem")
        return _money(points * cfg["redeem_rate"])

    def history(self, tenant_id: int, customer_id: int, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT id, sale_id, points_change, reason, created_at FROM loyalty_ledger "
            "WHERE tenant_id = ? AND customer_id = ? ORDER BY id DESC LIMIT ?",
            (tenant_id, customer_id, limit),
        ).fetchall()
        return [dict(r) for r in rows or []]

    def _append(self, tenant_id: int, customer_id: int, sale_id: int | None, points_change: int, reason: str) -> None:
        self.connection.execute(
            "INSERT INTO loyalty_ledger (tenant_id, customer_id, sale_id, points_change, reason) "
            "VALUES (?, ?, ?, ?, ?)",
            (tenant_id, customer_id, sale_id, points_change, reason),
        )
