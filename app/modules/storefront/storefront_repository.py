from __future__ import annotations

from typing import Any

from app.core.db import get_connection


class StorefrontRepository:
    def get_tenant(self, tenant_id: int) -> dict[str, Any] | None:
        with get_connection() as connection:
            return connection.execute(
                "SELECT id, name, subdomain, storefront_template, storefront_url FROM tenants WHERE id = ?",
                (tenant_id,),
            ).fetchone()

    def update_storefront_metadata(self, tenant_id: int, storefront_template: str, storefront_url: str) -> None:
        with get_connection() as connection:
            connection.execute(
                """
                UPDATE tenants
                SET storefront_enabled = 1,
                    auto_storefront_enabled = 1,
                    storefront_template = ?,
                    storefront_url = ?
                WHERE id = ?
                """,
                (storefront_template, storefront_url, tenant_id),
            )

    def get_storefront_template(self, tenant_id: int) -> str | None:
        with get_connection() as connection:
            row = connection.execute(
                "SELECT storefront_template FROM tenants WHERE id = ?",
                (tenant_id,),
            ).fetchone()
        return row["storefront_template"] if row else None

    def get_storefront_products(self, tenant_id: int) -> list[dict[str, Any]]:
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT
                    p.id,
                    p.name,
                    p.sku,
                    p.price,
                    p.stock,
                    p.status,
                    p.image_path,
                    c.name AS category_name,
                    u.symbol AS unit_symbol
                FROM products p
                LEFT JOIN categories c ON c.id = p.category_id AND c.tenant_id = ?
                LEFT JOIN units u ON u.id = p.unit_id AND u.tenant_id = ?
                WHERE p.tenant_id = ? AND p.is_public = 1 AND p.status IN ('active', 'out_of_stock')
                ORDER BY p.sort_order ASC, p.id ASC
                """,
                (tenant_id, tenant_id, tenant_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_storefront_categories(self, tenant_id: int) -> list[dict[str, Any]]:
        with get_connection() as connection:
            rows = connection.execute(
                "SELECT id, name, sort_order FROM categories WHERE tenant_id = ? ORDER BY sort_order ASC, id ASC",
                (tenant_id,),
            ).fetchall()
        return [dict(row) for row in rows]
