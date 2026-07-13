"""POS payment lifecycle, split-tender validation, and tender bucketing for shifts."""

from __future__ import annotations

from typing import Any

PAYMENT_STATUS_PENDING = "pending"
PAYMENT_STATUS_COMPLETED = "completed"
PAYMENT_STATUS_FAILED = "failed"
PAYMENT_STATUS_REFUNDED = "refunded"

ALLOWED_PAYMENT_STATUSES = frozenset(
    {PAYMENT_STATUS_PENDING, PAYMENT_STATUS_COMPLETED, PAYMENT_STATUS_FAILED, PAYMENT_STATUS_REFUNDED}
)
ALLOWED_NEW_CHARGE_STATUSES = frozenset({PAYMENT_STATUS_PENDING, PAYMENT_STATUS_COMPLETED})


def tender_bucket(method: str) -> str:
    m = (method or "").strip().lower()
    if m == "cash":
        return "cash"
    if m == "card":
        return "card"
    if m in ("gcash", "mobile wallet", "maya"):
        return "wallet"
    return "other"


def normalize_payment_lines(
    payment_lines: list[dict[str, Any]] | None,
    *,
    sale_total: float,
    default_method: str,
    default_status: str = PAYMENT_STATUS_COMPLETED,
    default_reference: str = "",
) -> list[dict[str, Any]]:
    if payment_lines is not None and len(payment_lines) == 0:
        raise ValueError("payments must not be empty when provided.")

    if not payment_lines:
        return [
            {
                "amount": round(float(sale_total), 2),
                "method": default_method,
                "status": default_status,
                "reference": default_reference,
            }
        ]
    out: list[dict[str, Any]] = []
    for p in payment_lines:
        st = (p.get("status") or PAYMENT_STATUS_COMPLETED).strip().lower()
        if st not in ALLOWED_NEW_CHARGE_STATUSES:
            raise ValueError(f"Invalid payment status for capture: {st}. Use pending or completed.")
        out.append(
            {
                "amount": round(float(p["amount"]), 2),
                "method": str(p.get("method") or default_method).strip(),
                "status": st,
                "reference": str(p.get("reference") or "").strip(),
            }
        )
    return out


def assert_payment_lines_match_total(lines: list[dict[str, Any]], sale_total: float, *, tol: float = 0.01) -> None:
    total = round(sum(float(x["amount"]) for x in lines), 2)
    expected = round(float(sale_total), 2)
    if abs(total - expected) > tol:
        raise ValueError(f"Payment lines sum {total} does not match sale total {expected}.")


def sale_display_payment_method(lines: list[dict[str, Any]]) -> str:
    if len(lines) > 1:
        return "Split"
    return lines[0]["method"]


def compute_shift_tender_totals(
    connection: Any,
    *,
    shift_id: int,
    tenant_id: int,
    cashier_user_id: int,
) -> dict[str, Any]:
    """Expected tender totals from completed sales on this shift (positive captures only)."""
    rows = connection.execute(
        """
        SELECT p.method, COALESCE(SUM(p.amount), 0) AS amt
        FROM payments p
        JOIN sales s ON s.id = p.sale_id AND s.tenant_id = p.tenant_id
        WHERE s.cash_shift_id = ?
          AND s.tenant_id = ?
          AND s.cashier_user_id = ?
          AND s.status = 'completed'
          AND p.tenant_id = ?
          AND p.reverses_payment_id IS NULL
          AND p.amount > 0
          AND p.status = ?
        GROUP BY p.method
        """,
        (shift_id, tenant_id, cashier_user_id, tenant_id, PAYMENT_STATUS_COMPLETED),
    ).fetchall()

    cash = card = wallet = other = 0.0
    breakdown: dict[str, float] = {}
    for row in rows:
        method = str(row["method"] or "")
        amt = float(row["amt"] or 0)
        breakdown[method] = round(amt, 2)
        b = tender_bucket(method)
        if b == "cash":
            cash += amt
        elif b == "card":
            card += amt
        elif b == "wallet":
            wallet += amt
        else:
            other += amt

    return {
        "cash": round(cash, 2),
        "card": round(card, 2),
        "wallet": round(wallet, 2),
        "other": round(other, 2),
        "breakdown": breakdown,
    }
