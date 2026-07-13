from __future__ import annotations

from typing import Any

from app.core.db import get_raw_connection


class GlobalProductAdminService:
    def list_products(self, tenant_id: int | None = None, category_id: int | None = None) -> list[dict[str, Any]]:
        sql = (
            "SELECT p.id, p.name, p.sku, p.price, p.stock, p.status, p.category_id, p.tenant_id, "
            "t.name AS tenant_name, c.name AS category_name "
            "FROM products p "
            "JOIN tenants t ON p.tenant_id = t.id "
            "LEFT JOIN categories c ON p.category_id = c.id"
        )
        params: list[Any] = []
        if tenant_id is not None:
            sql += " WHERE p.tenant_id = ?"
            params.append(tenant_id)
        if category_id is not None:
            sql += " AND p.category_id = ?" if params else " WHERE p.category_id = ?"
            params.append(category_id)
        sql += " ORDER BY t.name, p.name"

        with get_raw_connection() as connection:
            rows = connection.execute(sql, tuple(params)).fetchall()
        return [dict(row) for row in rows]

    def list_tenants(self) -> list[dict[str, Any]]:
        with get_raw_connection() as connection:
            rows = connection.execute(
                "SELECT id, name, is_active FROM tenants ORDER BY name"
            ).fetchall()
        return [dict(row) for row in rows]

    def list_categories(self) -> list[dict[str, Any]]:
        with get_raw_connection() as connection:
            rows = connection.execute(
                "SELECT id, name, tenant_id FROM categories ORDER BY name"
            ).fetchall()
        return [dict(row) for row in rows]
