"""Financial reversal rows for void/refund (negative payments linked to originals)."""

from __future__ import annotations

from typing import Any

from app.core.pos_payment_model import (
    PAYMENT_STATUS_COMPLETED,
    PAYMENT_STATUS_FAILED,
    PAYMENT_STATUS_PENDING,
    PAYMENT_STATUS_REFUNDED,
)


def apply_sale_financial_reversal(connection: Any, tenant_id: int, sale_id: int, reason: str) -> None:
    """
    For each original capture row on the sale:
    - completed + positive amount: insert negative reversal (status refunded), mark original refunded.
    - pending + positive amount: mark failed (cancelled), no negative row.
    Skips rows that are already reversals (reverses_payment_id set) or non-positive amounts.
    """
    rows = connection.execute(
        """
        SELECT id, amount, method, status FROM payments
        WHERE sale_id = ? AND tenant_id = ? AND reverses_payment_id IS NULL
        ORDER BY id ASC
        """,
        (sale_id, tenant_id),
    ).fetchall()

    ref_note = f"reversal:{reason}"
    for r in rows:
        pid = int(r["id"])
        amt = float(r["amount"])
        st = (r["status"] or "").strip().lower()
        method = str(r["method"] or "")

        if amt <= 0:
            continue

        if st == PAYMENT_STATUS_COMPLETED:
            connection.execute(
                """
                INSERT INTO payments (
                    tenant_id, sale_id, amount, method, status, reference, reverses_payment_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (tenant_id, sale_id, round(-amt, 2), method, PAYMENT_STATUS_REFUNDED, ref_note, pid),
            )
            connection.execute(
                "UPDATE payments SET status = ? WHERE id = ?",
                (PAYMENT_STATUS_REFUNDED, pid),
            )
        elif st == PAYMENT_STATUS_PENDING:
            connection.execute(
                """
                UPDATE payments SET status = ?, reference = ?
                WHERE id = ?
                """,
                (PAYMENT_STATUS_FAILED, f"cancelled:{reason}", pid),
            )
