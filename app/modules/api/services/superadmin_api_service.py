from __future__ import annotations

from typing import Any

from app.admin.service import AdminService
from app.core.db import get_connection
from app.core.subscription.service import SubscriptionService


class SuperadminApiService:
    def list_tenants(self) -> list[dict[str, Any]]:
        return AdminService().list_tenants()

    def list_all_products(self, limit: int = 500) -> list[dict[str, Any]]:
        lim = max(1, min(limit, 2000))
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT id, tenant_id, name, sku, price, stock, status, category_id, brand_id
                FROM products
                ORDER BY tenant_id ASC, id ASC
                LIMIT ?
                """,
                (lim,),
            ).fetchall()
        return [dict(r) for r in rows]

    def list_subscriptions(self, limit: int = 500) -> list[dict[str, Any]]:
        lim = max(1, min(limit, 2000))
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT id, tenant_id, plan_id, status, trial_end, started_at, cancelled_at, cancel_at_period_end
                FROM subscriptions
                ORDER BY started_at DESC, id DESC
                LIMIT ?
                """,
                (lim,),
            ).fetchall()
        return [dict(r) for r in rows]

    def list_plans(self) -> list[dict[str, Any]]:
        with get_connection() as connection:
            plans = SubscriptionService(connection).list_plans(active_only=True)
        return [
            {
                "id": p.id,
                "name": p.name,
                "price": p.price,
                "features": p.features,
                "is_active": p.is_active,
            }
            for p in plans
        ]


superadmin_api_service = SuperadminApiService()
