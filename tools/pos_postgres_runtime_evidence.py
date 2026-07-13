#!/usr/bin/env python3
"""
Postgres-only POS runtime evidence (sections 2–7). Requires DATABASE_URL=postgresql://...
Skips with exit 0 if sqlite or unset. PYTHONPATH=repo root.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_REPO / ".env")

_durl = os.getenv("DATABASE_URL", "").strip()
if not _durl or _durl.lower().startswith("sqlite"):
    print("SKIP_POSTGRES_RUNTIME: DATABASE_URL empty or sqlite")
    raise SystemExit(0)

from app.core.db import DATABASE_ENGINE, get_connection, get_raw_connection  # noqa: E402

if DATABASE_ENGINE != "postgres":
    print("SKIP_POSTGRES_RUNTIME: DATABASE_ENGINE=", DATABASE_ENGINE)
    raise SystemExit(0)


def p(title: str) -> None:
    print(f"\n=== {title} ===")


def q(conn, sql: str, params: tuple = ()):
    return conn.execute(sql, params).fetchall()


def main() -> None:
    from flask import g, session  # noqa: E402

    from app.core.flask_app import create_flask_application  # noqa: E402
    from app.core.pos_finalize import finalize_pos_sale  # noqa: E402
    from app.core.pos_payment_model import compute_shift_tender_totals  # noqa: E402
    from app.core.pos_refund import apply_sale_financial_reversal  # noqa: E402
    from app.modules.web import queries as web_queries  # noqa: E402

    app = create_flask_application()
    app.config["TESTING"] = True

    SKUS = ("pg_rtv_a", "pg_rtv_b", "pg_rtv_ref", "pg_rtv_one")

    def cleanup_skus() -> None:
        with get_raw_connection() as conn:
            conn.execute(
                """
                DELETE FROM payments WHERE sale_id IN (
                  SELECT id FROM sales WHERE tenant_id = 1 AND (
                    idempotency_key LIKE 'pg-rtv%%' OR idempotency_key = 'pg-smoke-executemany'
                  )
                )
                """
            )
            conn.execute(
                """
                DELETE FROM sale_items WHERE sale_id IN (
                  SELECT id FROM sales WHERE tenant_id = 1 AND (
                    idempotency_key LIKE 'pg-rtv%%' OR idempotency_key = 'pg-smoke-executemany'
                  )
                )
                """
            )
            conn.execute(
                """
                DELETE FROM sales WHERE tenant_id = 1 AND (
                  idempotency_key LIKE 'pg-rtv%%' OR idempotency_key = 'pg-smoke-executemany'
                )
                """
            )
            conn.execute(
                "DELETE FROM stock_movements WHERE product_id IN (SELECT id FROM products WHERE tenant_id = 1 AND sku IN ('pg_rtv_a','pg_rtv_b','pg_rtv_ref','pg_rtv_one'))",
            )
            conn.execute(
                "DELETE FROM products WHERE tenant_id = 1 AND sku IN ('pg_rtv_a','pg_rtv_b','pg_rtv_ref','pg_rtv_one')",
            )

    cleanup_skus()

    with get_raw_connection() as conn:
        u = conn.execute(
            "SELECT id FROM users WHERE tenant_id = 1 AND username = 'cashier' LIMIT 1",
        ).fetchone()
        if u is None:
            print("FAIL: no cashier user")
            raise SystemExit(1)
        cashier_id = int(u["id"])

        conn.execute(
            """
            INSERT INTO products (tenant_id, name, sku, price, stock, reorder_level, cost, status, sort_order)
            VALUES (1, 'PG RTV A', 'pg_rtv_a', 50.0, 100, 5, 0, 'active', 0)
            """,
        )
        conn.execute(
            """
            INSERT INTO products (tenant_id, name, sku, price, stock, reorder_level, cost, status, sort_order)
            VALUES (1, 'PG RTV B', 'pg_rtv_b', 30.0, 100, 5, 0, 'active', 0)
            """,
        )
        pa = conn.execute(
            "SELECT id, stock FROM products WHERE sku = 'pg_rtv_a' AND tenant_id = 1",
        ).fetchone()
        pb = conn.execute(
            "SELECT id, stock FROM products WHERE sku = 'pg_rtv_b' AND tenant_id = 1",
        ).fetchone()
        pid_a = int(pa["id"])
        pid_b = int(pb["id"])

    def ctx():
        c = app.test_request_context()
        c.push()
        session["user_id"] = cashier_id
        session["tenant_id"] = 1
        g.tenant_id = 1
        return c

    cart_a = lambda qty=1: [
        {
            "id": pid_a,
            "variant_id": None,
            "name": "PG RTV A",
            "sku": "pg_rtv_a",
            "quantity": qty,
            "unit_price": 50.0,
            "line_total": 50.0 * qty,
        }
    ]
    cart_b = lambda qty=1: [
        {
            "id": pid_b,
            "variant_id": None,
            "name": "PG RTV B",
            "sku": "pg_rtv_b",
            "quantity": qty,
            "unit_price": 30.0,
            "line_total": 30.0 * qty,
        }
    ]

    # --- 2 standard sale ---
    p("2_STANDARD_SALE_POSTGRES")
    c = ctx()
    try:
        with get_connection() as conn:
            sid, _ = finalize_pos_sale(
                conn,
                tenant_id=1,
                cashier_user_id=cashier_id,
                cart=cart_a(1),
                subtotal=50.0,
                discount_type="none",
                discount_rate=0.0,
                discount_amount=0.0,
                discount_note="",
                service_reference="",
                tax=0.0,
                total=50.0,
                payment_method="Cash",
                cash_shift_id=None,
                idempotency_key="pg-rtv-std",
            )
        print("sale_id", sid)
        with get_raw_connection() as conn:
            print("SALES_ROW", [dict(r) for r in q(conn, "SELECT * FROM sales WHERE id = ?", (sid,))])
            print("SALE_ITEMS", [dict(r) for r in q(conn, "SELECT * FROM sale_items WHERE sale_id = ? ORDER BY id", (sid,))])
            print("PAYMENTS", [dict(r) for r in q(conn, "SELECT * FROM payments WHERE sale_id = ? ORDER BY id", (sid,))])
            print("PRODUCT_STOCK", [dict(r) for r in q(conn, "SELECT id, sku, stock FROM products WHERE id = ?", (pid_a,))])
            print(
                "STOCK_MOVEMENTS_LAST",
                [
                    dict(r)
                    for r in q(
                        conn,
                        "SELECT * FROM stock_movements WHERE product_id = ? ORDER BY id DESC LIMIT 2",
                        (pid_a,),
                    )
                ],
            )
    finally:
        c.pop()

    # --- 3 idempotency ---
    p("3_IDEMPOTENCY_POSTGRES")
    c = ctx()
    try:
        with get_raw_connection() as conn:
            conn.execute("UPDATE products SET stock = 10 WHERE id = ?", (pid_a,))
        idem = "pg-rtv-idem"
        with get_connection() as conn:
            s1, cr1 = finalize_pos_sale(
                conn,
                tenant_id=1,
                cashier_user_id=cashier_id,
                cart=cart_a(1),
                subtotal=50.0,
                discount_type="none",
                discount_rate=0.0,
                discount_amount=0.0,
                discount_note="",
                service_reference="",
                tax=0.0,
                total=50.0,
                payment_method="Card",
                cash_shift_id=None,
                idempotency_key=idem,
                payment_lines=[{"amount": 50.0, "method": "Card", "status": "completed"}],
            )
        with get_connection() as conn:
            s2, cr2 = finalize_pos_sale(
                conn,
                tenant_id=1,
                cashier_user_id=cashier_id,
                cart=cart_a(1),
                subtotal=50.0,
                discount_type="none",
                discount_rate=0.0,
                discount_amount=0.0,
                discount_note="",
                service_reference="",
                tax=0.0,
                total=50.0,
                payment_method="Card",
                cash_shift_id=None,
                idempotency_key=idem,
                payment_lines=[{"amount": 50.0, "method": "Card", "status": "completed"}],
            )
        print("sale_ids", s1, s2, "created_flags", cr1, cr2)
        with get_raw_connection() as conn:
            sc = q(conn, "SELECT COUNT(*) AS c FROM sales WHERE tenant_id = 1 AND idempotency_key = ?", (idem,))[0]["c"]
            pc = q(conn, "SELECT COUNT(*) AS c FROM payments WHERE sale_id = ?", (s1,))[0]["c"]
            st = q(conn, "SELECT stock FROM products WHERE id = ?", (pid_a,))[0]["stock"]
            print("sales_count_same_idempotency_key", int(sc))
            print("payment_count_for_sale", int(pc))
            print("stock_after_idem_replay", int(st))
    finally:
        c.pop()

    # --- 4 split ---
    p("4_SPLIT_PAYMENT_POSTGRES")
    c = ctx()
    try:
        with get_raw_connection() as conn:
            conn.execute("UPDATE products SET stock = 10 WHERE id = ?", (pid_b,))
        with get_connection() as conn:
            sid3, _ = finalize_pos_sale(
                conn,
                tenant_id=1,
                cashier_user_id=cashier_id,
                cart=cart_b(1),
                subtotal=30.0,
                discount_type="none",
                discount_rate=0.0,
                discount_amount=0.0,
                discount_note="",
                service_reference="",
                tax=0.0,
                total=30.0,
                payment_method="Cash",
                cash_shift_id=None,
                idempotency_key="pg-rtv-split",
                payment_lines=[
                    {"amount": 10.0, "method": "Cash", "status": "completed"},
                    {"amount": 20.0, "method": "Card", "status": "completed"},
                ],
            )
        print("sale_id", sid3)
        with get_raw_connection() as conn:
            pm = dict(q(conn, "SELECT payment_method, total FROM sales WHERE id = ?", (sid3,))[0])
            print("sales_header", pm)
            rows = [
                dict(r)
                for r in q(
                    conn,
                    "SELECT amount, method, status FROM payments WHERE sale_id = ? ORDER BY id",
                    (sid3,),
                )
            ]
            print("payment_rows", rows)
            print("sum_amounts", sum(float(r["amount"]) for r in rows))
    finally:
        c.pop()

    # --- 5 refund ---
    p("5_REFUND_POSTGRES")
    c = ctx()
    try:
        with get_raw_connection() as conn:
            conn.execute(
                """
                INSERT INTO products (tenant_id, name, sku, price, stock, reorder_level, cost, status, sort_order)
                VALUES (1, 'PG RTV REF', 'pg_rtv_ref', 50.0, 10, 5, 0, 'active', 0)
                """,
            )
        with get_raw_connection() as conn:
            pr = conn.execute(
                "SELECT id, stock FROM products WHERE sku = 'pg_rtv_ref' AND tenant_id = 1",
            ).fetchone()
            pid_r = int(pr["id"])
        cart_r = [
            {
                "id": pid_r,
                "variant_id": None,
                "name": "PG RTV REF",
                "sku": "pg_rtv_ref",
                "quantity": 1,
                "unit_price": 50.0,
                "line_total": 50.0,
            }
        ]
        with get_connection() as conn:
            sid_r, _ = finalize_pos_sale(
                conn,
                tenant_id=1,
                cashier_user_id=cashier_id,
                cart=cart_r,
                subtotal=50.0,
                discount_type="none",
                discount_rate=0.0,
                discount_amount=0.0,
                discount_note="",
                service_reference="",
                tax=0.0,
                total=50.0,
                payment_method="GCash",
                cash_shift_id=None,
                idempotency_key="pg-rtv-refund-target",
                payment_lines=[{"amount": 50.0, "method": "GCash", "status": "completed"}],
            )
        with get_raw_connection() as conn:
            stk_before = int(conn.execute("SELECT stock FROM products WHERE id = ?", (pid_r,)).fetchone()["stock"])
        print("stock_before_refund", stk_before)
        with get_connection() as conn:
            apply_sale_financial_reversal(conn, 1, sid_r, "refund")
            web_queries.build_sale_stock_return(conn, sid_r, "refund")
            conn.execute("UPDATE sales SET status = 'refunded' WHERE id = ?", (sid_r,))
        with get_raw_connection() as conn:
            print(
                "payments_all",
                [
                    dict(r)
                    for r in q(
                        conn,
                        "SELECT id, amount, status, reverses_payment_id FROM payments WHERE sale_id = ? ORDER BY id",
                        (sid_r,),
                    )
                ],
            )
            net = float(q(conn, "SELECT COALESCE(SUM(amount), 0) AS s FROM payments WHERE sale_id = ?", (sid_r,))[0]["s"])
            print("sum_payments", net)
            print("sale_status", dict(q(conn, "SELECT id, status FROM sales WHERE id = ?", (sid_r,))[0]))
            print("stock_after_refund", int(q(conn, "SELECT stock FROM products WHERE id = ?", (pid_r,))[0]["stock"]))
    finally:
        c.pop()

    # --- 6 shift ---
    p("6_SHIFT_CLOSE_POSTGRES")
    c = ctx()
    try:
        with get_raw_connection() as conn:
            conn.execute(
                "DELETE FROM cash_register_shifts WHERE tenant_id = 1 AND cashier_user_id = ?",
                (cashier_id,),
            )
            conn.execute("UPDATE products SET stock = 50 WHERE id IN (?, ?)", (pid_a, pid_b))
        with get_raw_connection() as conn:
            row = conn.execute(
                """
                INSERT INTO cash_register_shifts (tenant_id, cashier_user_id, opening_cash, expected_cash, status)
                VALUES (1, ?, 200, 200, 'open')
                RETURNING id
                """,
                (cashier_id,),
            ).fetchone()
            shift_id = int(row["id"])
        with get_connection() as conn:
            s_cash, _ = finalize_pos_sale(
                conn,
                tenant_id=1,
                cashier_user_id=cashier_id,
                cart=cart_a(1),
                subtotal=50.0,
                discount_type="none",
                discount_rate=0.0,
                discount_amount=0.0,
                discount_note="",
                service_reference="",
                tax=0.0,
                total=50.0,
                payment_method="Cash",
                cash_shift_id=shift_id,
                idempotency_key="pg-rtv-shift-cash",
                payment_lines=[{"amount": 50.0, "method": "Cash", "status": "completed"}],
            )
            s_mix, _ = finalize_pos_sale(
                conn,
                tenant_id=1,
                cashier_user_id=cashier_id,
                cart=cart_b(1),
                subtotal=30.0,
                discount_type="none",
                discount_rate=0.0,
                discount_amount=0.0,
                discount_note="",
                service_reference="",
                tax=0.0,
                total=30.0,
                payment_method="Split",
                cash_shift_id=shift_id,
                idempotency_key="pg-rtv-shift-mix",
                payment_lines=[
                    {"amount": 10.0, "method": "GCash", "status": "completed"},
                    {"amount": 20.0, "method": "Card", "status": "completed"},
                ],
            )
        print("sales_in_shift", s_cash, s_mix, "shift_id", shift_id)
        with get_connection() as conn:
            tenders = compute_shift_tender_totals(conn, shift_id=shift_id, tenant_id=1, cashier_user_id=cashier_id)
        opening = 200.0
        expected_cash = round(opening + tenders["cash"], 2)
        breakdown_json = json.dumps(tenders["breakdown"], sort_keys=True)
        with get_raw_connection() as conn:
            conn.execute(
                """
                UPDATE cash_register_shifts
                SET expected_cash = ?, expected_card_total = ?, expected_wallet_total = ?, expected_other_total = ?,
                    tender_breakdown_json = ?, actual_cash = ?, variance = ?, status = 'closed', closed_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    expected_cash,
                    tenders["card"],
                    tenders["wallet"],
                    tenders["other"],
                    breakdown_json,
                    expected_cash,
                    0.0,
                    shift_id,
                ),
            )
        with get_raw_connection() as conn:
            row = dict(q(conn, "SELECT * FROM cash_register_shifts WHERE id = ?", (shift_id,))[0])
        print("expected_cash_total", row["expected_cash"])
        print("expected_card_total", row["expected_card_total"])
        print("expected_wallet_total", row["expected_wallet_total"])
        print("expected_other_total", row["expected_other_total"])
        print("tender_breakdown_json", row["tender_breakdown_json"])
    finally:
        c.pop()

    # --- 7 oversell ---
    p("7_OVERSELL_POSTGRES")
    c = ctx()
    try:
        with get_raw_connection() as conn:
            conn.execute(
                """
                INSERT INTO products (tenant_id, name, sku, price, stock, reorder_level, cost, status, sort_order)
                VALUES (1, 'PG RTV ONE', 'pg_rtv_one', 1.0, 1, 5, 0, 'active', 0)
                """,
            )
            p1 = int(conn.execute("SELECT id FROM products WHERE sku = 'pg_rtv_one' AND tenant_id = 1").fetchone()["id"])
        cart_one = [
            {
                "id": p1,
                "variant_id": None,
                "name": "PG RTV ONE",
                "sku": "pg_rtv_one",
                "quantity": 1,
                "unit_price": 1.0,
                "line_total": 1.0,
            }
        ]
        with get_raw_connection() as conn:
            sb = int(conn.execute("SELECT stock FROM products WHERE id = ?", (p1,)).fetchone()["stock"])
        print("stock_before", sb)
        with get_connection() as conn:
            finalize_pos_sale(
                conn,
                tenant_id=1,
                cashier_user_id=cashier_id,
                cart=cart_one,
                subtotal=1.0,
                discount_type="none",
                discount_rate=0.0,
                discount_amount=0.0,
                discount_note="",
                service_reference="",
                tax=0.0,
                total=1.0,
                payment_method="Cash",
                cash_shift_id=None,
                idempotency_key="pg-rtv-os-1",
            )
        with get_raw_connection() as conn:
            sa1 = int(conn.execute("SELECT stock FROM products WHERE id = ?", (p1,)).fetchone()["stock"])
        print("stock_after_first", sa1)
        try:
            with get_connection() as conn:
                finalize_pos_sale(
                    conn,
                    tenant_id=1,
                    cashier_user_id=cashier_id,
                    cart=cart_one,
                    subtotal=1.0,
                    discount_type="none",
                    discount_rate=0.0,
                    discount_amount=0.0,
                    discount_note="",
                    service_reference="",
                    tax=0.0,
                    total=1.0,
                    payment_method="Cash",
                    cash_shift_id=None,
                    idempotency_key="pg-rtv-os-2",
                )
            print("second_checkout_UNEXPECTED_success")
        except ValueError as e:
            print("second_checkout_ValueError", repr(str(e)))
        with get_raw_connection() as conn:
            sa2 = int(conn.execute("SELECT stock FROM products WHERE id = ?", (p1,)).fetchone()["stock"])
        print("stock_after_second_attempt", sa2)
    finally:
        c.pop()

    cleanup_skus()
    with get_raw_connection() as conn:
        conn.execute("DELETE FROM cash_register_shifts WHERE tenant_id = 1 AND cashier_user_id = ?", (cashier_id,))
    print("\nDONE_POSTGRES_RUNTIME")


if __name__ == "__main__":
    main()
