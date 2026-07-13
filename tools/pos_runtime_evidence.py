"""Emit runtime DB evidence for POS validation (temp SQLite). Run: python tools/pos_runtime_evidence.py"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from pathlib import Path

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["SQLITE_DATABASE_PATH"] = _tmp.name

from flask import g, session  # noqa: E402

from app.core.db import get_connection, get_raw_connection  # noqa: E402
from app.core.flask_app import create_flask_application  # noqa: E402
from app.core.pos_finalize import finalize_pos_sale  # noqa: E402
from app.core.pos_payment_model import assert_payment_lines_match_total, normalize_payment_lines  # noqa: E402
from app.core.pos_payment_model import compute_shift_tender_totals  # noqa: E402
from app.core.pos_refund import apply_sale_financial_reversal  # noqa: E402


def q(conn, sql, params=()):
    return conn.execute(sql, params).fetchall()


def p(title: str) -> None:
    print(f"\n=== {title} ===")


def main() -> None:
    db_path = Path(os.environ["SQLITE_DATABASE_PATH"])
    print("DB_PATH", db_path)
    app = create_flask_application()
    app.config["TESTING"] = True

    with get_raw_connection() as conn:
        cashier = conn.execute(
            "SELECT id FROM users WHERE username='cashier' AND tenant_id=1"
        ).fetchone()
        cashier_id = int(cashier["id"])
        conn.execute(
            """
            INSERT INTO products (tenant_id, name, sku, price, stock, reorder_level, cost, status, sort_order)
            VALUES (1, 'RTV-A', 'RTV-A', 50.0, 100, 5, 0, 'active', 0)
            """
        )
        conn.execute(
            """
            INSERT INTO products (tenant_id, name, sku, price, stock, reorder_level, cost, status, sort_order)
            VALUES (1, 'RTV-B', 'RTV-B', 30.0, 100, 5, 0, 'active', 0)
            """
        )
        pa = conn.execute("SELECT id FROM products WHERE sku='RTV-A'").fetchone()
        pb = conn.execute("SELECT id FROM products WHERE sku='RTV-B'").fetchone()
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
            "name": "RTV-A",
            "sku": "RTV-A",
            "quantity": qty,
            "unit_price": 50.0,
            "line_total": 50.0 * qty,
        }
    ]
    cart_b = lambda qty=1: [
        {
            "id": pid_b,
            "variant_id": None,
            "name": "RTV-B",
            "sku": "RTV-B",
            "quantity": qty,
            "unit_price": 30.0,
            "line_total": 30.0 * qty,
        }
    ]

    # --- 3 standard sale ---
    p("3_STANDARD_SALE")
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
                idempotency_key=None,
            )
        print("sale_id", sid)
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            print("SALES_ROW", [dict(r) for r in q(conn, "SELECT * FROM sales WHERE id=?", (sid,))])
            print("SALE_ITEMS", [dict(r) for r in q(conn, "SELECT * FROM sale_items WHERE sale_id=?", (sid,))])
            print("PAYMENTS", [dict(r) for r in q(conn, "SELECT * FROM payments WHERE sale_id=?", (sid,))])
            print("PRODUCT_STOCK", [dict(r) for r in q(conn, "SELECT id, sku, stock FROM products WHERE id=?", (pid_a,))])
            print(
                "STOCK_MOVEMENTS_LAST",
                [dict(r) for r in q(conn, "SELECT * FROM stock_movements WHERE product_id=? ORDER BY id DESC LIMIT 2", (pid_a,))],
            )
            print(
                "AUDIT_LAST",
                [dict(r) for r in q(conn, "SELECT * FROM audit_logs WHERE entity_type='sale' AND entity_id=? ORDER BY id DESC LIMIT 1", (sid,))],
            )
    finally:
        c.pop()

    # --- 4 idempotency ---
    p("4_IDEMPOTENCY")
    c = ctx()
    try:
        with get_connection() as conn:
            conn.execute("UPDATE products SET stock=10 WHERE id=?", (pid_a,))
        idem = "idem-rtv-xyz"
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
            )
        print("sale_ids", s1, s2, "created_flags", cr1, cr2)
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            pc = q(conn, "SELECT COUNT(*) AS c FROM payments WHERE sale_id=?", (s1,))[0]["c"]
            st = q(conn, "SELECT stock FROM products WHERE id=?", (pid_a,))[0]["stock"]
            print("payment_count_for_sale", int(pc), "stock_after_idem_replay", int(st))
    finally:
        c.pop()

    # --- 5 split ok ---
    p("5_SPLIT_OK")
    c = ctx()
    try:
        with get_connection() as conn:
            conn.execute("UPDATE products SET stock=10 WHERE id=?", (pid_b,))
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
                idempotency_key=None,
                payment_lines=[
                    {"amount": 10.0, "method": "Cash", "status": "completed"},
                    {"amount": 20.0, "method": "Card", "status": "completed"},
                ],
            )
        print("sale_id", sid3)
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            pm = dict(q(conn, "SELECT payment_method, total FROM sales WHERE id=?", (sid3,))[0])
            print("sales_header", pm)
            rows = [dict(r) for r in q(conn, "SELECT amount, method, status FROM payments WHERE sale_id=? ORDER BY id", (sid3,))]
            print("payment_rows", rows)
            print("sum_amounts", sum(r["amount"] for r in rows))
    finally:
        c.pop()

    # --- 6 split mismatch ---
    p("6_SPLIT_MISMATCH")
    c = ctx()
    try:
        with get_connection() as conn:
            conn.execute("UPDATE products SET stock=10 WHERE id=?", (pid_b,))
        try:
            lines = normalize_payment_lines(
                [{"amount": 10.0, "method": "Cash"}, {"amount": 5.0, "method": "Card"}],
                sale_total=30.0,
                default_method="Cash",
            )
            assert_payment_lines_match_total(lines, 30.0)
            print("UNEXPECTED_NO_ERROR")
        except ValueError as e:
            print("ValueError", repr(str(e)))
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            cnt_before = q(conn, "SELECT COUNT(*) AS c FROM sales")[0]["c"]
        # attempt full finalize with bad lines should abort transaction
        try:
            with get_connection() as conn:
                finalize_pos_sale(
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
                    idempotency_key="bad-split-1",
                    payment_lines=[
                        {"amount": 10.0, "method": "Cash", "status": "completed"},
                        {"amount": 5.0, "method": "Card", "status": "completed"},
                    ],
                )
            print("UNEXPECTED_finalize_succeeded")
        except ValueError as e:
            print("finalize_ValueError", repr(str(e)))
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            cnt_after = q(conn, "SELECT COUNT(*) AS c FROM sales")[0]["c"]
            print("sales_count_before_after", int(cnt_before), int(cnt_after))
    finally:
        c.pop()

    # --- 7 refund ---
    p("7_REFUND")
    c = ctx()
    try:
        with get_connection() as conn:
            conn.execute("UPDATE products SET stock=10 WHERE id=?", (pid_a,))
        with get_connection() as conn:
            sid_r, _ = finalize_pos_sale(
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
                payment_method="GCash",
                cash_shift_id=None,
                idempotency_key="refund-target",
                payment_lines=[{"amount": 50.0, "method": "GCash", "status": "completed"}],
            )
        with sqlite3.connect(db_path) as raw:
            raw.row_factory = sqlite3.Row
            stk_before = int(raw.execute("SELECT stock FROM products WHERE id=?", (pid_a,)).fetchone()["stock"])
        print("stock_before_refund", stk_before)
        with get_connection() as conn:
            apply_sale_financial_reversal(conn, 1, sid_r, "refund")
            from app.modules.web import queries as web_queries

            web_queries.build_sale_stock_return(conn, sid_r, "refund")
            conn.execute("UPDATE sales SET status='refunded' WHERE id=?", (sid_r,))
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            print("payments_all", [dict(r) for r in q(conn, "SELECT id, amount, status, reverses_payment_id FROM payments WHERE sale_id=? ORDER BY id", (sid_r,))])
            net = float(q(conn, "SELECT SUM(amount) AS s FROM payments WHERE sale_id=?", (sid_r,))[0]["s"] or 0)
            print("sum_payments", net)
            print("sale_status", dict(q(conn, "SELECT id, status FROM sales WHERE id=?", (sid_r,))[0]))
            print("stock_after_refund", int(q(conn, "SELECT stock FROM products WHERE id=?", (pid_a,))[0]["stock"]))
    finally:
        c.pop()

    # --- 8 shift close ---
    p("8_SHIFT_RECONCILE")
    c = ctx()
    try:
        with get_raw_connection() as conn:
            conn.execute("DELETE FROM cash_register_shifts WHERE tenant_id=1 AND cashier_user_id=?", (cashier_id,))
            cur = conn.execute(
                """
                INSERT INTO cash_register_shifts (tenant_id, cashier_user_id, opening_cash, expected_cash, status)
                VALUES (1, ?, 200, 200, 'open')
                """,
                (cashier_id,),
            )
            shift_id = int(cur.lastrowid)
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
                idempotency_key="shift-cash",
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
                idempotency_key="shift-mix",
                payment_lines=[
                    {"amount": 10.0, "method": "GCash", "status": "completed"},
                    {"amount": 20.0, "method": "Card", "status": "completed"},
                ],
            )
        print("sales_in_shift", s_cash, s_mix, "shift_id", shift_id)
        with get_connection() as conn:
            tenders = compute_shift_tender_totals(
                conn, shift_id=shift_id, tenant_id=1, cashier_user_id=cashier_id
            )
        opening = 200.0
        expected_cash = round(opening + tenders["cash"], 2)
        breakdown_json = json.dumps(tenders["breakdown"], sort_keys=True)
        with get_raw_connection() as conn:
            conn.execute(
                """
                UPDATE cash_register_shifts
                SET expected_cash=?, expected_card_total=?, expected_wallet_total=?, expected_other_total=?,
                    tender_breakdown_json=?, actual_cash=?, variance=?, status='closed', closed_at=CURRENT_TIMESTAMP
                WHERE id=?
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
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = dict(q(conn, "SELECT * FROM cash_register_shifts WHERE id=?", (shift_id,))[0])
        print("expected_cash_total", row["expected_cash"])
        print("expected_card_total", row["expected_card_total"])
        print("expected_wallet_total", row["expected_wallet_total"])
        print("expected_other_total", row["expected_other_total"])
        print("tender_breakdown_json", row["tender_breakdown_json"])
    finally:
        c.pop()

    # --- 9 oversell ---
    p("9_OVERSELL")
    c = ctx()
    try:
        with get_raw_connection() as conn:
            conn.execute(
                "INSERT INTO products (tenant_id, name, sku, price, stock, reorder_level, cost, status, sort_order) "
                "VALUES (1, 'RTV-ONE', 'RTV-ONE', 1.0, 1, 5, 0, 'active', 0)"
            )
            p1 = int(conn.execute("SELECT id FROM products WHERE sku='RTV-ONE'").fetchone()["id"])
        cart_one = [
            {
                "id": p1,
                "variant_id": None,
                "name": "RTV-ONE",
                "sku": "RTV-ONE",
                "quantity": 1,
                "unit_price": 1.0,
                "line_total": 1.0,
            }
        ]
        with sqlite3.connect(db_path) as raw:
            raw.row_factory = sqlite3.Row
            sb = int(raw.execute("SELECT stock FROM products WHERE id=?", (p1,)).fetchone()["stock"])
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
                idempotency_key="os-1",
            )
        with sqlite3.connect(db_path) as raw:
            raw.row_factory = sqlite3.Row
            sa1 = int(raw.execute("SELECT stock FROM products WHERE id=?", (p1,)).fetchone()["stock"])
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
                    idempotency_key="os-2",
                )
            print("second_checkout_UNEXPECTED_success")
        except ValueError as e:
            print("second_checkout_ValueError", repr(str(e)))
        with sqlite3.connect(db_path) as raw:
            raw.row_factory = sqlite3.Row
            sa2 = int(raw.execute("SELECT stock FROM products WHERE id=?", (p1,)).fetchone()["stock"])
        print("stock_after_second_attempt", sa2)
    finally:
        c.pop()

    # --- 10 executemany tenant_id (SQLite path) ---
    p("10_EXECUTEMANY_TENANT")
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        sid_sample = q(conn, "SELECT id FROM sales ORDER BY id DESC LIMIT 1")[0][0]
        rows = q(
            conn,
            "SELECT tenant_id, sale_id, product_name FROM sale_items WHERE sale_id=? ORDER BY id",
            (sid_sample,),
        )
        print("sale_items_tenant_ids", [r["tenant_id"] for r in rows])
        bad = [r for r in rows if r["tenant_id"] != 1]
        print("any_non_tenant_1", len(bad) > 0, "count_rows", len(rows))

    print("\nDONE_DB", db_path)


if __name__ == "__main__":
    main()
