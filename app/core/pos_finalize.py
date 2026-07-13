"""Shared POS checkout finalization: stock, sales row, line snapshots, payments, audit."""

from __future__ import annotations

from typing import Any

from app.core.pos_payment_model import (
    assert_payment_lines_match_total,
    normalize_payment_lines,
    sale_display_payment_method,
)


def find_sale_by_idempotency_key(connection: Any, tenant_id: int, idempotency_key: str) -> int | None:
    row = connection.execute(
        "SELECT id FROM sales WHERE tenant_id = ? AND idempotency_key = ? LIMIT 1",
        (tenant_id, idempotency_key),
    ).fetchone()
    return int(row["id"]) if row else None


def _rowcount(connection: Any, cursor: Any) -> int:
    rc = getattr(cursor, "rowcount", None)
    if rc is not None:
        return int(rc)
    inner = getattr(connection, "_connection", None)
    if inner is not None:
        return int(getattr(inner, "total_changes", 0) or 0)
    return -1


def deduct_line_stock(connection: Any, item: dict[str, Any]) -> None:
    qty = int(item["quantity"])
    if item.get("variant_id"):
        cur = connection.execute(
            """
            UPDATE product_variants
            SET stock = stock - ?
            WHERE id = ? AND stock >= ?
            """,
            (qty, item["variant_id"], qty),
        )
    else:
        cur = connection.execute(
            """
            UPDATE products
            SET stock = stock - ?
            WHERE id = ? AND stock >= ?
            """,
            (qty, item["id"], qty),
        )
    if _rowcount(connection, cur) != 1:
        raise ValueError("Stock no longer available for one or more items. Refresh and try again.")


def finalize_pos_sale(
    connection: Any,
    *,
    tenant_id: int,
    cashier_user_id: int | None,
    cart: list[dict[str, Any]],
    subtotal: float,
    discount_type: str,
    discount_rate: float,
    discount_amount: float,
    discount_note: str,
    service_reference: str,
    tax: float,
    total: float,
    payment_method: str,
    cash_shift_id: int | None,
    idempotency_key: str | None,
    payment_lines: list[dict[str, Any]] | None = None,
    payment_status: str = "completed",
    payment_reference: str = "",
) -> tuple[int, bool]:
    """
    Returns (sale_id, created).
    If idempotency_key matches an existing sale, returns (existing_id, False) without mutating stock.
    """
    if idempotency_key:
        existing = find_sale_by_idempotency_key(connection, tenant_id, idempotency_key)
        if existing is not None:
            return existing, False

    for item in cart:
        deduct_line_stock(connection, item)

    lines = normalize_payment_lines(
        payment_lines,
        sale_total=total,
        default_method=payment_method,
        default_status=payment_status,
        default_reference=payment_reference,
    )
    assert_payment_lines_match_total(lines, total)
    display_method = sale_display_payment_method(lines)

    sale_row = connection.execute(
        """
        INSERT INTO sales (
            tenant_id,
            subtotal, discount_type, discount_rate, discount_amount, discount_note,
            service_reference,
            tax, total, payment_method, status, cashier_user_id, cash_shift_id,
            idempotency_key
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'completed', ?, ?, ?)
        RETURNING id
        """,
        (
            tenant_id,
            subtotal,
            discount_type,
            discount_rate,
            discount_amount,
            discount_note,
            service_reference,
            tax,
            total,
            display_method,
            cashier_user_id,
            cash_shift_id,
            idempotency_key,
        ),
    ).fetchone()
    sale_id = int(sale_row["id"])

    connection.executemany(
        """
        INSERT INTO sale_items (
            tenant_id, sale_id, product_id, variant_id, quantity, unit_price, line_total,
            product_name, sku
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                tenant_id,
                sale_id,
                item["id"],
                item["variant_id"],
                item["quantity"],
                item["unit_price"],
                item["line_total"],
                str(item["name"]),
                str(item["sku"]),
            )
            for item in cart
        ],
    )

    from app.core.inventory import log_stock_movement
    from app.modules.web import queries as web_queries

    for item in cart:
        log_stock_movement(
            connection,
            int(item["id"]),
            -int(item["quantity"]),
            "sale",
            item["variant_id"],
            tenant_id=tenant_id,
        )
        web_queries.maybe_create_low_stock_alert(connection, int(item["id"]), "checkout")

    for pl in lines:
        connection.execute(
            """
            INSERT INTO payments (
                tenant_id, sale_id, amount, method, status, reference, reverses_payment_id
            )
            VALUES (?, ?, ?, ?, ?, ?, NULL)
            """,
            (
                tenant_id,
                sale_id,
                pl["amount"],
                pl["method"],
                pl["status"],
                pl["reference"],
            ),
        )

    web_queries.log_audit(
        connection,
        "checkout",
        "sale",
        sale_id,
        f"Sale completed via {display_method}; discount={discount_type}; total={total:.2f}",
    )

    return sale_id, True
