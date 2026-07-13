from __future__ import annotations

import logging
from typing import Any

from app.core.db import get_connection
from app.core.subscription.entitlements import DEMO_PLAN_NAME
from app.core.subscription.service import SubscriptionService
from app.core.tenant.model import Tenant
from app.core.tenant.tenant_service import register_tenant_hook
from app.modules.storefront.storefront_service import StorefrontService

logger = logging.getLogger(__name__)


def register_hooks() -> None:
    register_tenant_hook("tenant_created", _on_tenant_created)


def _on_tenant_created(tenant: Tenant, payload: dict[str, Any]) -> None:
    plan_name = str(payload.get("plan_name") or "starter").strip()

    with get_connection() as connection:
        row = connection.execute(
            "SELECT storefront_enabled, auto_storefront_enabled FROM tenants WHERE id = ?",
            (tenant.id,),
        ).fetchone()

    if row and (int(row["storefront_enabled"] or 0) or int(row["auto_storefront_enabled"] or 0)):
        try:
            StorefrontService().create_storefront_for_tenant(int(tenant.id))
        except Exception:
            logger.exception("Storefront auto-setup failed for tenant %s", tenant.id)

    with get_connection() as connection:
        sub = SubscriptionService(connection)
        plan = sub.get_plan_by_name(plan_name)
        if plan is None:
            plans = sub.list_plans(active_only=True)
            plan = plans[0] if plans else None
        if plan is not None:
            try:
                if plan_name.strip().lower() == DEMO_PLAN_NAME:
                    sub.create_subscription(int(tenant.id), int(plan.id), trial_days=None)
                else:
                    sub.start_trial(int(tenant.id), int(plan.id), trial_days=14)
            except ValueError:
                pass
