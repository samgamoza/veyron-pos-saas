#!/usr/bin/env python3
"""
Postgres POS smoke test: proves multi-row sale_items get correct tenant_id (executemany + inject path).

Requires:
  - DATABASE_URL=postgresql://... (not sqlite) in the environment or .env
  - psycopg installed (requirements.txt)
  - PYTHONPATH=repo root (or run from repo with python -m after pip install -e .)

Skips with exit 0 if DATABASE_URL is unset or sqlite.

Creates rows tagged idempotency_key='pg-smoke-executemany' and product SKUs pg-smoke-a / pg-smoke-b;
removes them in a finally block.

Usage:
  set DATABASE_URL=postgresql://user:pass@localhost:5432/veyron
  set PYTHONPATH=c:\\path\\to\\veyron-pos-saas
  python tools/pos_postgres_smoke.py
"""

from __future__ import annotations

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
    print("SKIP_POSTGRES: DATABASE_URL empty or sqlite — set postgresql://... to run this smoke test.")
    raise SystemExit(0)

# Import app stack only after env is known (first import wins for DATABASE_ENGINE).
from app.core.db import DATABASE_ENGINE, get_raw_connection  # noqa: E402

if DATABASE_ENGINE != "postgres":
    print("SKIP_POSTGRES: DATABASE_ENGINE=", DATABASE_ENGINE, "(expected postgres)")
    raise SystemExit(0)

try:
    from psycopg import connect as _pg_connect  # noqa: F401
except ImportError:
    print("SKIP_POSTGRES: psycopg not installed")
    raise SystemExit(0)


def _cleanup(sale_id: int | None) -> None:
    with get_raw_connection() as conn:
        if sale_id:
            conn.execute(
                "DELETE FROM payments WHERE sale_id = %s",
                (sale_id,),
            )
            conn.execute(
                "DELETE FROM sale_items WHERE sale_id = %s",
                (sale_id,),
            )
            conn.execute(
                "DELETE FROM sales WHERE id = %s",
                (sale_id,),
            )
        conn.execute(
            "DELETE FROM stock_movements WHERE product_id IN (SELECT id FROM products WHERE sku IN ('pg-smoke-a', 'pg-smoke-b') AND tenant_id = 1)"
        )
        conn.execute("DELETE FROM products WHERE sku IN ('pg-smoke-a', 'pg-smoke-b') AND tenant_id = 1")


def main() -> int:
    from flask import g, session  # noqa: E402

    from app.core.flask_app import create_flask_application  # noqa: E402
    from app.core.pos_finalize import finalize_pos_sale  # noqa: E402

    app = create_flask_application()
    app.config["TESTING"] = True

    with get_raw_connection() as conn:
        row = conn.execute("SELECT 1 AS ok").fetchone()
        print("CONNECT_OK", dict(row))

    sale_id: int | None = None
    try:
        with get_raw_connection() as conn:
            u = conn.execute(
                "SELECT id FROM users WHERE tenant_id = 1 AND username = 'cashier' LIMIT 1"
            ).fetchone()
            if u is None:
                print("FAIL: no cashier user for tenant_id=1 — seed DB first")
                return 1
            cashier_id = int(u["id"])

            conn.execute(
                "DELETE FROM payments WHERE sale_id IN (SELECT id FROM sales WHERE tenant_id = 1 AND idempotency_key = %s)",
                ("pg-smoke-executemany",),
            )
            conn.execute(
                "DELETE FROM sale_items WHERE sale_id IN (SELECT id FROM sales WHERE tenant_id = 1 AND idempotency_key = %s)",
                ("pg-smoke-executemany",),
            )
            conn.execute(
                "DELETE FROM sales WHERE tenant_id = 1 AND idempotency_key = %s",
                ("pg-smoke-executemany",),
            )
            conn.execute(
                "DELETE FROM products WHERE sku IN ('pg-smoke-a', 'pg-smoke-b') AND tenant_id = 1",
            )
            conn.execute(
                """
                INSERT INTO products (tenant_id, name, sku, price, stock, reorder_level, cost, status, sort_order)
                VALUES (1, 'PG Smoke A', 'pg-smoke-a', 1.0, 50, 5, 0, 'active', 0)
                """
            )
            conn.execute(
                """
                INSERT INTO products (tenant_id, name, sku, price, stock, reorder_level, cost, status, sort_order)
                VALUES (1, 'PG Smoke B', 'pg-smoke-b', 2.0, 50, 5, 0, 'active', 0)
                """
            )
            pa = conn.execute("SELECT id FROM products WHERE sku = 'pg-smoke-a' AND tenant_id = 1").fetchone()
            pb = conn.execute("SELECT id FROM products WHERE sku = 'pg-smoke-b' AND tenant_id = 1").fetchone()
            if pa is None or pb is None:
                print("FAIL: could not resolve pg-smoke product ids after insert")
                return 1
            pid_a = int(pa["id"])
            pid_b = int(pb["id"])

        cart = [
            {
                "id": pid_a,
                "variant_id": None,
                "name": "PG Smoke A",
                "sku": "pg-smoke-a",
                "quantity": 1,
                "unit_price": 1.0,
                "line_total": 1.0,
            },
            {
                "id": pid_b,
                "variant_id": None,
                "name": "PG Smoke B",
                "sku": "pg-smoke-b",
                "quantity": 1,
                "unit_price": 2.0,
                "line_total": 2.0,
            },
        ]

        ctx = app.test_request_context()
        ctx.push()
        try:
            session["user_id"] = cashier_id
            session["tenant_id"] = 1
            g.tenant_id = 1

            from app.core.db import get_connection  # noqa: E402

            with get_connection() as c:
                sid, created = finalize_pos_sale(
                    c,
                    tenant_id=1,
                    cashier_user_id=cashier_id,
                    cart=cart,
                    subtotal=3.0,
                    discount_type="none",
                    discount_rate=0.0,
                    discount_amount=0.0,
                    discount_note="",
                    service_reference="",
                    tax=0.0,
                    total=3.0,
                    payment_method="Cash",
                    cash_shift_id=None,
                    idempotency_key="pg-smoke-executemany",
                    payment_lines=[
                        {"amount": 3.0, "method": "Cash", "status": "completed", "reference": "pg-smoke"},
                    ],
                )
            sale_id = sid
            print("FINALIZE_OK sale_id=", sid, "created=", created)
        finally:
            ctx.pop()

        if sale_id is None:
            print("FAIL: finalize_pos_sale did not assign sale_id")
            return 1

        with get_raw_connection() as conn:
            rows = conn.execute(
                """
                SELECT id, tenant_id, product_id, product_name
                FROM sale_items
                WHERE sale_id = %s
                ORDER BY id
                """,
                (sale_id,),
            ).fetchall()
            print("SALE_ITEMS_ROWS", [dict(r) for r in rows])
            bad = [r for r in rows if int(r["tenant_id"]) != 1]
            if len(rows) != 2:
                print("FAIL: expected 2 sale_items, got", len(rows))
                return 1
            if bad:
                print("FAIL: tenant_id not 1 on rows", [dict(r) for r in bad])
                return 1

        print("POSTGRES_EXECUTEMANY_TENANT_OK: both sale_items.tenant_id == 1")
        return 0
    finally:
        _cleanup(sale_id)
        print("CLEANUP_DONE sale_id=", sale_id)


if __name__ == "__main__":
    raise SystemExit(main())
