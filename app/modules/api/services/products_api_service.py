from __future__ import annotations

from typing import Any

from app.core.constants import ALLOWED_PRODUCT_STATUSES
from app.core.db import get_connection
from app.core.subscription.entitlements import assert_demo_allows_new_product


class ProductsApiService:
    def list_products(self, tenant_id: int, limit: int = 200) -> list[dict[str, Any]]:
        lim = max(1, min(limit, 500))
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT id, name, sku, price, cost, stock, reorder_level, category_id, brand_id, unit_id, status, sort_order
                FROM products
                WHERE tenant_id = ?
                ORDER BY sort_order ASC, name ASC
                LIMIT ?
                """,
                (tenant_id, lim),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_product(self, tenant_id: int, product_id: int) -> dict[str, Any] | None:
        with get_connection() as connection:
            row = connection.execute(
                """
                SELECT id, name, sku, price, cost, stock, reorder_level, category_id, brand_id, unit_id, status, sort_order
                FROM products
                WHERE tenant_id = ? AND id = ?
                """,
                (tenant_id, product_id),
            ).fetchone()
        return dict(row) if row else None

    def _next_sku(self, connection: Any, tenant_id: int, name: str, category_id: int) -> str:
        cat = connection.execute("SELECT name FROM categories WHERE id = ? AND tenant_id = ?", (category_id, tenant_id)).fetchone()
        prefix_source = cat["name"] if cat else name
        prefix = "".join(c for c in prefix_source.upper() if c.isalnum())[:3] or "PRD"
        attempt = 101
        while True:
            sku = f"{prefix}-{attempt}"
            exists = connection.execute(
                "SELECT 1 FROM products WHERE tenant_id = ? AND sku = ?",
                (tenant_id, sku),
            ).fetchone()
            if exists is None:
                return sku
            attempt += 1

    def create_product(self, tenant_id: int, payload: dict[str, Any]) -> int:
        name = (payload.get("name") or "").strip()
        if not name:
            raise ValueError("name is required.")
        status = str(payload.get("status", "active")).strip().lower()
        if status not in ALLOWED_PRODUCT_STATUSES:
            raise ValueError("Invalid status.")
        price = float(payload.get("price", 0) or 0)
        cost = float(payload.get("cost", 0) or 0)
        stock = int(payload.get("stock", 0) or 0)
        reorder_level = int(payload.get("reorder_level", 5) or 5)
        category_id = int(payload["category_id"])
        brand_id = int(payload["brand_id"])
        unit_id = int(payload["unit_id"])

        with get_connection() as connection:
            assert_demo_allows_new_product(connection, tenant_id)
            for table, pk in (
                ("categories", category_id),
                ("brands", brand_id),
                ("units", unit_id),
            ):
                ok = connection.execute(
                    f"SELECT 1 FROM {table} WHERE id = ? AND tenant_id = ?",
                    (pk, tenant_id),
                ).fetchone()
                if ok is None:
                    raise ValueError(f"Invalid {table[:-1]} id for this tenant: {pk}.")

            sku = self._next_sku(connection, tenant_id, name, category_id)
            sort_row = connection.execute(
                "SELECT COALESCE(MAX(sort_order), 0) + 1 AS n FROM products WHERE tenant_id = ?",
                (tenant_id,),
            ).fetchone()
            sort_order = int(sort_row["n"])

            row = connection.execute(
                """
                INSERT INTO products (
                    tenant_id, name, sku, price, cost, stock, reorder_level,
                    category_id, brand_id, unit_id, status, sort_order,
                    last_restocked, last_synced_at
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    CASE WHEN ? > 0 THEN CURRENT_TIMESTAMP ELSE NULL END,
                    CURRENT_TIMESTAMP
                )
                RETURNING id
                """,
                (
                    tenant_id,
                    name,
                    sku,
                    price,
                    cost,
                    stock,
                    reorder_level,
                    category_id,
                    brand_id,
                    unit_id,
                    status,
                    sort_order,
                    stock,
                ),
            ).fetchone()
            pid = int(row["id"])
            connection.execute(
                """
                INSERT INTO stock_movements (tenant_id, product_id, variant_id, quantity_change, reason)
                VALUES (?, ?, NULL, ?, ?)
                """,
                (tenant_id, pid, stock, "opening_balance"),
            )
        return pid

    def update_product(self, tenant_id: int, product_id: int, payload: dict[str, Any]) -> None:
        current = self.get_product(tenant_id, product_id)
        if current is None:
            raise ValueError("Product not found.")

        name = (payload.get("name") or current["name"]).strip()
        status = str(payload.get("status", current["status"])).strip().lower()
        if status not in ALLOWED_PRODUCT_STATUSES:
            raise ValueError("Invalid status.")
        price = float(payload.get("price", current["price"]))
        cost = float(payload.get("cost", current["cost"]))
        reorder_level = int(payload.get("reorder_level", current["reorder_level"]))
        category_id = int(payload.get("category_id", current["category_id"]))
        brand_id = int(payload.get("brand_id", current["brand_id"]))
        unit_id = int(payload.get("unit_id", current["unit_id"]))

        with get_connection() as connection:
            connection.execute(
                """
                UPDATE products
                SET name = ?, price = ?, cost = ?, reorder_level = ?,
                    category_id = ?, brand_id = ?, unit_id = ?, status = ?,
                    last_synced_at = CURRENT_TIMESTAMP
                WHERE tenant_id = ? AND id = ?
                """,
                (
                    name,
                    price,
                    cost,
                    reorder_level,
                    category_id,
                    brand_id,
                    unit_id,
                    status,
                    tenant_id,
                    product_id,
                ),
            )

    def delete_product(self, tenant_id: int, product_id: int, hard: bool = False) -> None:
        with get_connection() as connection:
            row = connection.execute(
                "SELECT id FROM products WHERE tenant_id = ? AND id = ?",
                (tenant_id, product_id),
            ).fetchone()
            if row is None:
                raise ValueError("Product not found.")
            if hard:
                connection.execute("DELETE FROM stock_movements WHERE product_id = ?", (product_id,))
                connection.execute("DELETE FROM products WHERE tenant_id = ? AND id = ?", (tenant_id, product_id))
            else:
                connection.execute(
                    "UPDATE products SET status = 'inactive', last_synced_at = CURRENT_TIMESTAMP WHERE tenant_id = ? AND id = ?",
                    (tenant_id, product_id),
                )


products_api_service = ProductsApiService()
