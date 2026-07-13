from __future__ import annotations

from typing import Any

from app.core.db import get_connection
from app.core.subscription.service import SubscriptionService


def _sub_to_dict(sub: Any) -> dict[str, Any]:
    return {
        "id": sub.id,
        "tenant_id": sub.tenant_id,
        "plan_id": sub.plan_id,
        "status": sub.status,
        "trial_end": sub.trial_end,
        "started_at": sub.started_at,
        "cancelled_at": sub.cancelled_at,
        "cancel_at_period_end": sub.cancel_at_period_end,
    }


class SubscriptionApiService:
    def current(self, tenant_id: int) -> dict[str, Any] | None:
        with get_connection() as connection:
            sub = SubscriptionService(connection).get_subscription_by_tenant(tenant_id)
        return _sub_to_dict(sub) if sub else None

    def upgrade(self, tenant_id: int, plan_id: int) -> dict[str, Any]:
        with get_connection() as connection:
            svc = SubscriptionService(connection)
            sub = svc.upgrade_subscription(tenant_id, plan_id)
        return _sub_to_dict(sub)


subscription_api_service = SubscriptionApiService()
