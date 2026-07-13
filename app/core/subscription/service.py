from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Iterable

from .model import Plan, Subscription

STATUS_ACTIVE = "active"
STATUS_TRIALING = "trialing"
STATUS_CANCELED = "canceled"


class SubscriptionService:
    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def create_plan(self, name: str, price: float, features: dict[str, Any], is_active: bool = True) -> Plan:
        name = name.strip()
        if not name:
            raise ValueError("Plan name is required.")

        row = self.connection.execute(
            """
            INSERT INTO plans (name, price, features, is_active)
            VALUES (?, ?, ?, ?)
            RETURNING id, name, price, features, created_at, is_active
            """,
            (name, price, json.dumps(features or {}), int(bool(is_active))),
        ).fetchone()
        return self._row_to_plan(row)

    def get_plan_by_id(self, plan_id: int) -> Plan | None:
        row = self.connection.execute(
            "SELECT id, name, price, features, created_at, is_active FROM plans WHERE id = ?",
            (plan_id,),
        ).fetchone()
        return self._row_to_plan(row)

    def get_plan_by_name(self, name: str) -> Plan | None:
        row = self.connection.execute(
            "SELECT id, name, price, features, created_at, is_active FROM plans WHERE name = ?",
            (name.strip(),),
        ).fetchone()
        return self._row_to_plan(row)

    def list_plans(self, active_only: bool = True) -> list[Plan]:
        sql = "SELECT id, name, price, features, created_at, is_active FROM plans"
        params: tuple[Any, ...] = ()
        if active_only:
            sql += " WHERE is_active = 1"
        rows = self.connection.execute(sql, params).fetchall()
        return [self._row_to_plan(row) for row in rows]

    def create_subscription(
        self,
        tenant_id: int,
        plan_id: int,
        trial_days: int | None = None,
    ) -> Subscription:
        status = STATUS_ACTIVE
        trial_end = None
        if trial_days and trial_days > 0:
            status = STATUS_TRIALING
            trial_end = (datetime.utcnow() + timedelta(days=trial_days)).isoformat()

        row = self.connection.execute(
            """
            INSERT INTO subscriptions (
                tenant_id,
                plan_id,
                status,
                trial_end,
                started_at,
                cancelled_at,
                cancel_at_period_end
            ) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, NULL, 0)
            RETURNING id, tenant_id, plan_id, status, trial_end, started_at, cancelled_at, cancel_at_period_end
            """,
            (tenant_id, plan_id, status, trial_end),
        ).fetchone()
        return self._row_to_subscription(row)

    def get_subscription_by_tenant(self, tenant_id: int) -> Subscription | None:
        row = self.connection.execute(
            "SELECT id, tenant_id, plan_id, status, trial_end, started_at, cancelled_at, cancel_at_period_end FROM subscriptions WHERE tenant_id = ? ORDER BY started_at DESC LIMIT 1",
            (tenant_id,),
        ).fetchone()
        return self._row_to_subscription(row)

    def start_trial(self, tenant_id: int, plan_id: int, trial_days: int = 14) -> Subscription:
        subscription = self.get_subscription_by_tenant(tenant_id)
        if subscription and subscription.is_active and not subscription.is_cancelled:
            raise ValueError("Tenant already has an active subscription.")
        return self.create_subscription(tenant_id, plan_id, trial_days)

    def upgrade_subscription(self, tenant_id: int, new_plan_id: int) -> Subscription:
        return self._change_plan(tenant_id, new_plan_id, "upgrade")

    def downgrade_subscription(self, tenant_id: int, new_plan_id: int) -> Subscription:
        return self._change_plan(tenant_id, new_plan_id, "downgrade")

    def cancel_subscription(self, tenant_id: int, at_period_end: bool = False) -> Subscription:
        subscription = self.get_subscription_by_tenant(tenant_id)
        if subscription is None:
            raise ValueError("No subscription exists for this tenant.")
        if subscription.is_cancelled:
            return subscription

        self.connection.execute(
            """
            UPDATE subscriptions
            SET status = ?, cancelled_at = CURRENT_TIMESTAMP, cancel_at_period_end = ?
            WHERE id = ?
            """,
            (STATUS_CANCELED, int(bool(at_period_end)), subscription.id),
        )
        return self.get_subscription_by_tenant(tenant_id)

    def reactivate_subscription(self, tenant_id: int) -> Subscription:
        subscription = self.get_subscription_by_tenant(tenant_id)
        if subscription is None:
            raise ValueError("No subscription exists for this tenant.")
        if subscription.is_active and not subscription.is_cancelled:
            return subscription

        self.connection.execute(
            """
            UPDATE subscriptions
            SET status = ?, cancelled_at = NULL, cancel_at_period_end = 0
            WHERE id = ?
            """,
            (STATUS_ACTIVE, subscription.id),
        )
        return self.get_subscription_by_tenant(tenant_id)

    def _change_plan(self, tenant_id: int, new_plan_id: int, action: str) -> Subscription:
        subscription = self.get_subscription_by_tenant(tenant_id)
        if subscription is None:
            raise ValueError("No subscription exists for this tenant.")
        if subscription.plan_id == new_plan_id:
            return subscription

        status = subscription.status
        if subscription.is_cancelled:
            status = STATUS_ACTIVE

        self.connection.execute(
            """
            UPDATE subscriptions
            SET plan_id = ?, status = ?, cancel_at_period_end = 0
            WHERE id = ?
            """,
            (new_plan_id, status, subscription.id),
        )
        return self.get_subscription_by_tenant(tenant_id)

    def _row_to_plan(self, row: Any) -> Plan | None:
        if row is None:
            return None
        return Plan(
            id=row["id"],
            name=row["name"],
            price=float(row["price"]),
            features=json.loads(row["features"] or "{}"),
            created_at=row["created_at"],
            is_active=bool(row["is_active"]),
        )

    def _row_to_subscription(self, row: Any) -> Subscription | None:
        if row is None:
            return None
        return Subscription(
            id=row["id"],
            tenant_id=row["tenant_id"],
            plan_id=row["plan_id"],
            status=row["status"],
            trial_end=row["trial_end"],
            started_at=row["started_at"],
            cancelled_at=row["cancelled_at"],
            cancel_at_period_end=bool(row["cancel_at_period_end"]),
        )
