"""Owner-initiated plan upgrades with super-admin approval."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.subscription.entitlements import DEMO_PLAN_NAME, normalize_plan_name
from app.core.subscription.service import SubscriptionService
from app.modules.web import queries as web_queries

SUBSCRIPTION_PENDING = "pending"


def _restore_subscription_status(plan_name: str, monthly_fee: float) -> str:
    plan = normalize_plan_name(plan_name)
    if plan == DEMO_PLAN_NAME:
        return "demo"
    if monthly_fee and float(monthly_fee) > 0:
        return "active"
    return "trial"


def notify_platform_plan_upgrade_request(
    connection: Any,
    *,
    tenant_id: int,
    tenant_name: str,
    current_plan: str,
    requested_plan: str,
    requested_fee: float,
    billing_currency: str,
) -> None:
    details = (
        f"Tenant '{tenant_name}' (#{tenant_id}) requested upgrade from "
        f"{current_plan} to {requested_plan} ({billing_currency} {requested_fee:.2f}/mo). "
        "Approve in Super Admin → Health & risk."
    )
    web_queries.log_audit(
        connection,
        "plan_upgrade_requested",
        "tenant",
        tenant_id,
        details,
        tenant_id=tenant_id,
    )
    web_queries.send_email_alert(
        subject=f"[Veyron POS] Plan upgrade pending approval — {tenant_name}",
        body=(
            f"A tenant requested a paid plan upgrade.\n\n"
            f"Tenant: {tenant_name} (ID {tenant_id})\n"
            f"Current plan: {current_plan}\n"
            f"Requested plan: {requested_plan}\n"
            f"Monthly fee: {billing_currency} {requested_fee:.2f}\n\n"
            f"Review and approve in the Super Admin dashboard under Health & risk.\n"
            f"Dashboard: /superadmin/#health"
        ),
    )


def request_plan_upgrade(connection: Any, tenant_id: int, plan_id: int) -> dict[str, object]:
    plan = connection.execute(
        "SELECT id, name, price FROM plans WHERE id = ? AND is_active = 1",
        (plan_id,),
    ).fetchone()
    if plan is None:
        raise ValueError("That plan is not available.")

    requested_plan = normalize_plan_name(plan["name"])
    if requested_plan == DEMO_PLAN_NAME:
        raise ValueError("Choose a paid plan to upgrade.")

    tenant = connection.execute(
        """
        SELECT id, name, plan_name, monthly_fee, subscription_status, billing_currency,
               pending_plan_name
        FROM tenants WHERE id = ?
        """,
        (tenant_id,),
    ).fetchone()
    if tenant is None:
        raise ValueError("Tenant not found.")

    current_plan = normalize_plan_name(tenant["plan_name"])
    if current_plan == requested_plan and (tenant["subscription_status"] or "").lower() == "active":
        raise ValueError("You are already on this plan.")

    requested_fee = float(plan["price"] or 0)
    currency = str(tenant["billing_currency"] or "PHP")
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    connection.execute(
        """
        UPDATE tenants
        SET pending_plan_name = ?,
            pending_plan_fee = ?,
            subscription_status = ?,
            plan_upgrade_requested_at = ?
        WHERE id = ?
        """,
        (requested_plan, requested_fee, SUBSCRIPTION_PENDING, now, tenant_id),
    )

    notify_platform_plan_upgrade_request(
        connection,
        tenant_id=int(tenant_id),
        tenant_name=str(tenant["name"]),
        current_plan=current_plan,
        requested_plan=requested_plan,
        requested_fee=requested_fee,
        billing_currency=currency,
    )

    return {
        "tenant_id": tenant_id,
        "current_plan": current_plan,
        "requested_plan": requested_plan,
        "requested_fee": requested_fee,
        "billing_currency": currency,
    }


def approve_plan_upgrade(connection: Any, tenant_id: int, *, actor_label: str = "Super Admin") -> None:
    tenant = connection.execute(
        """
        SELECT id, name, plan_name, monthly_fee, subscription_status, billing_currency,
               pending_plan_name, pending_plan_fee
        FROM tenants WHERE id = ?
        """,
        (tenant_id,),
    ).fetchone()
    if tenant is None:
        raise ValueError("Tenant not found.")

    pending_plan = normalize_plan_name(tenant["pending_plan_name"])
    if not pending_plan or (tenant["subscription_status"] or "").lower() != SUBSCRIPTION_PENDING:
        raise ValueError("This tenant has no pending plan upgrade.")

    pending_fee = float(tenant["pending_plan_fee"] or 0)
    connection.execute(
        """
        UPDATE tenants
        SET plan_name = ?,
            monthly_fee = ?,
            subscription_status = 'active',
            pending_plan_name = '',
            pending_plan_fee = 0,
            plan_upgrade_requested_at = NULL,
            last_payment_date = ?
        WHERE id = ?
        """,
        (pending_plan, pending_fee, datetime.now(timezone.utc).date().isoformat(), tenant_id),
    )

    plan_row = connection.execute(
        "SELECT id FROM plans WHERE lower(name) = lower(?) AND is_active = 1",
        (pending_plan,),
    ).fetchone()
    if plan_row is not None:
        sub = SubscriptionService(connection)
        existing = sub.get_subscription_by_tenant(int(tenant_id))
        if existing is None:
            sub.create_subscription(int(tenant_id), int(plan_row["id"]), trial_days=None)
        else:
            sub.upgrade_subscription(int(tenant_id), int(plan_row["id"]))

    web_queries.log_audit(
        connection,
        "plan_upgrade_approved",
        "tenant",
        tenant_id,
        f"{actor_label} approved upgrade to {pending_plan} ({tenant['billing_currency']} {pending_fee:.2f}/mo).",
        tenant_id=tenant_id,
    )
    web_queries.create_owner_alert(
        connection,
        "plan_upgrade",
        "info",
        "Plan upgrade approved",
        f"Your upgrade to {pending_plan.title()} is now active. Full platform limits are unlocked.",
        "tenant",
        tenant_id,
        tenant_id=int(tenant_id),
    )


def reject_plan_upgrade(
    connection: Any,
    tenant_id: int,
    *,
    actor_label: str = "Super Admin",
    reason: str = "",
) -> None:
    tenant = connection.execute(
        """
        SELECT id, name, plan_name, monthly_fee, subscription_status,
               pending_plan_name, pending_plan_fee
        FROM tenants WHERE id = ?
        """,
        (tenant_id,),
    ).fetchone()
    if tenant is None:
        raise ValueError("Tenant not found.")

    pending_plan = normalize_plan_name(tenant["pending_plan_name"])
    if not pending_plan or (tenant["subscription_status"] or "").lower() != SUBSCRIPTION_PENDING:
        raise ValueError("This tenant has no pending plan upgrade.")

    restored_status = _restore_subscription_status(tenant["plan_name"], float(tenant["monthly_fee"] or 0))
    connection.execute(
        """
        UPDATE tenants
        SET subscription_status = ?,
            pending_plan_name = '',
            pending_plan_fee = 0,
            plan_upgrade_requested_at = NULL
        WHERE id = ?
        """,
        (restored_status, tenant_id),
    )

    detail = f"{actor_label} rejected upgrade to {pending_plan}."
    if reason.strip():
        detail += f" Reason: {reason.strip()}"
    web_queries.log_audit(
        connection,
        "plan_upgrade_rejected",
        "tenant",
        tenant_id,
        detail,
        tenant_id=tenant_id,
    )
    message = f"Your request to upgrade to {pending_plan.title()} was not approved."
    if reason.strip():
        message += f" Note: {reason.strip()}"
    else:
        message += " Contact platform support if you have questions."
    web_queries.create_owner_alert(
        connection,
        "plan_upgrade",
        "warning",
        "Plan upgrade not approved",
        message,
        "tenant",
        tenant_id,
        tenant_id=int(tenant_id),
    )


def list_pending_plan_upgrades(connection: Any) -> list[dict[str, object]]:
    rows = connection.execute(
        """
        SELECT id, name, plan_name, subscription_status, billing_currency,
               pending_plan_name, pending_plan_fee, plan_upgrade_requested_at, contact_email
        FROM tenants
        WHERE lower(subscription_status) = ?
          AND pending_plan_name IS NOT NULL
          AND trim(pending_plan_name) <> ''
        ORDER BY plan_upgrade_requested_at ASC, id ASC
        """,
        (SUBSCRIPTION_PENDING,),
    ).fetchall()
    return [dict(row) for row in rows]


def clear_pending_plan_fields(connection: Any, tenant_id: int) -> None:
    connection.execute(
        """
        UPDATE tenants
        SET pending_plan_name = '',
            pending_plan_fee = 0,
            plan_upgrade_requested_at = NULL
        WHERE id = ?
        """,
        (tenant_id,),
    )
