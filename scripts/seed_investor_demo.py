#!/usr/bin/env python3
"""Seed tenant 5 (Sample Bakery) for live QR / investor demos.

Usage (inside app container or local with DATABASE_URL set):
    python scripts/seed_investor_demo.py
    python scripts/seed_investor_demo.py --tenant-id 5 --plan growth
"""
from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core.db import get_raw_connection  # noqa: E402

DEMO_PRODUCTS = [
    ("Pan de Sal", "BAKE-PDS", "Pastries", 8.0, 200),
    ("Ensaymada", "BAKE-ENS", "Pastries", 45.0, 80),
    ("Ube Cheese Pandesal", "BAKE-UBE", "Pastries", 55.0, 60),
    ("Spanish Bread", "BAKE-SPB", "Pastries", 35.0, 70),
    ("Hot Brew Coffee", "BAKE-COF", "Beverages", 65.0, 100),
    ("Iced Latte", "BAKE-LAT", "Beverages", 95.0, 100),
]


def _ensure_lookup(connection, tenant_id: int, table: str, name: str) -> int:
    row = connection.execute(
        f"SELECT id FROM {table} WHERE tenant_id = ? AND name = ?",
        (tenant_id, name),
    ).fetchone()
    if row:
        return int(row["id"])
    ins = connection.execute(
        f"INSERT INTO {table} (tenant_id, name) VALUES (?, ?) RETURNING id",
        (tenant_id, name),
    ).fetchone()
    if ins is None:
        connection.execute(
            f"INSERT INTO {table} (tenant_id, name) VALUES (?, ?)",
            (tenant_id, name),
        )
        ins = connection.execute(
            f"SELECT id FROM {table} WHERE tenant_id = ? AND name = ?",
            (tenant_id, name),
        ).fetchone()
    return int(ins["id"])


def _ensure_unit(connection, tenant_id: int, name: str, symbol: str) -> int:
    row = connection.execute(
        "SELECT id FROM units WHERE tenant_id = ? AND symbol = ?",
        (tenant_id, symbol),
    ).fetchone()
    if row:
        return int(row["id"])
    ins = connection.execute(
        """
        INSERT INTO units (tenant_id, name, symbol, unit_type, base_factor)
        VALUES (?, ?, ?, 'count', 1)
        RETURNING id
        """,
        (tenant_id, name, symbol),
    ).fetchone()
    if ins is None:
        connection.execute(
            """
            INSERT INTO units (tenant_id, name, symbol, unit_type, base_factor)
            VALUES (?, ?, ?, 'count', 1)
            """,
            (tenant_id, name, symbol),
        )
        ins = connection.execute(
            "SELECT id FROM units WHERE tenant_id = ? AND symbol = ?",
            (tenant_id, symbol),
        ).fetchone()
    return int(ins["id"])


def seed_tenant(tenant_id: int, plan: str) -> None:
    with get_raw_connection() as conn:
        tenant = conn.execute(
            "SELECT id, name FROM tenants WHERE id = ?",
            (tenant_id,),
        ).fetchone()
        if tenant is None:
            raise SystemExit(f"Tenant {tenant_id} not found.")

        conn.execute(
            """
            UPDATE tenants
            SET plan_name = ?,
                subscription_status = 'active',
                delivery_enabled = 1,
                marketplace_enabled = COALESCE(marketplace_enabled, 0),
                qr_ordering_override = '',
                is_active = 1
            WHERE id = ?
            """,
            (plan, tenant_id),
        )

        conn.execute(
            """
            INSERT INTO app_settings (tenant_id, key, value)
            VALUES (?, 'qr_ordering_enabled', '1')
            ON CONFLICT(tenant_id, key) DO UPDATE SET value = '1'
            """,
            (tenant_id,),
        )
        conn.execute(
            """
            INSERT INTO app_settings (tenant_id, key, value)
            VALUES (?, 'onboarding_completed', '1')
            ON CONFLICT(tenant_id, key) DO UPDATE SET value = '1'
            """,
            (tenant_id,),
        )

        unit_id = _ensure_unit(conn, tenant_id, "Piece", "pcs")
        created = 0
        for pname, sku, category_name, price, stock in DEMO_PRODUCTS:
            existing = conn.execute(
                "SELECT id FROM products WHERE tenant_id = ? AND sku = ?",
                (tenant_id, sku),
            ).fetchone()
            if existing:
                conn.execute(
                    """
                    UPDATE products
                    SET is_public = 1, status = 'active', price = ?, stock = ?
                    WHERE id = ?
                    """,
                    (price, stock, int(existing["id"])),
                )
                continue
            category_id = _ensure_lookup(conn, tenant_id, "categories", category_name)
            brand_id = _ensure_lookup(conn, tenant_id, "brands", "House Brand")
            conn.execute(
                """
                INSERT INTO products (
                    tenant_id, name, sku, price, stock, reorder_level, cost,
                    status, sort_order, is_public, category_id, brand_id, unit_id
                )
                VALUES (?, ?, ?, ?, ?, 10, ?, 'active', ?, 1, ?, ?, ?)
                """,
                (
                    tenant_id,
                    pname,
                    sku,
                    price,
                    stock,
                    round(price * 0.35, 2),
                    created,
                    category_id,
                    brand_id,
                    unit_id,
                ),
            )
            created += 1

        public_count = conn.execute(
            "SELECT COUNT(*) AS c FROM products WHERE tenant_id = ? AND is_public = 1 AND status = 'active'",
            (tenant_id,),
        ).fetchone()
        print(
            f"Seeded {tenant['name']} (id={tenant_id}): plan={plan}, "
            f"public products={int(public_count['c'])}, new SKUs={created}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed investor demo tenant for QR ordering.")
    parser.add_argument("--tenant-id", type=int, default=int(os.getenv("DEMO_TENANT_ID", "5")))
    parser.add_argument("--plan", default=os.getenv("DEMO_PLAN", "growth"))
    args = parser.parse_args()
    seed_tenant(args.tenant_id, args.plan.strip().lower())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
