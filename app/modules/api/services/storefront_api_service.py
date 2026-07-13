from __future__ import annotations

from typing import Any

from app.core.db import get_connection


class StorefrontApiService:
    def list_public_products(self, tenant_id: int, limit: int = 100) -> list[dict[str, Any]]:
        lim = max(1, min(limit, 300))
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT p.id, p.name, p.sku, p.price, p.stock, p.status, c.name AS category_name
                FROM products p
                LEFT JOIN categories c ON c.id = p.category_id AND c.tenant_id = p.tenant_id
                WHERE p.tenant_id = ? AND p.status = 'active'
                ORDER BY p.sort_order ASC, p.name ASC
                LIMIT ?
                """,
                (tenant_id, lim),
            ).fetchall()
        return [dict(r) for r in rows]


storefront_api_service = StorefrontApiService()
