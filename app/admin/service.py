from __future__ import annotations

import csv
import io
import json
from typing import Any

from app.core.audit import audit_service
from app.core.db import DATABASE_ENGINE, get_connection
from app.core.localization import localization_service
from app.core.pos_profiles import get_profile, iter_profiles_for_admin, normalize_pos_profile_id
from app.core.tenant.tenant_service import TenantService


_DEFAULT_FEATURE_FLAGS = {"api": 1, "pos_advanced": 1, "multi_branch": 0, "bir_exports": 0}


def _sql_scalar(connection: Any, sql: str, params: tuple = ()) -> Any:
    row = connection.execute(sql, params).fetchone()
    if row is None:
        return None
    return next(iter(dict(row).values()))


def _tenant_row_to_dict(row: Any) -> dict[str, Any]:
    d = dict(row)
    raw = d.get("feature_flags_json") or "{}"
    try:
        fj = json.loads(raw) if isinstance(raw, str) else {}
        if not isinstance(fj, dict):
            fj = {}
    except json.JSONDecodeError:
        fj = {}
    for key, default in _DEFAULT_FEATURE_FLAGS.items():
        v = fj.get(key, default)
        d[f"ff_{key}"] = 1 if v in (1, True, "1", "true", "True") else 0
    d["pos_profile"] = normalize_pos_profile_id(d.get("pos_profile") if isinstance(d.get("pos_profile"), str) else None)
    d["pos_profile_label"] = get_profile(d["pos_profile"])["label"]
    return d


class AdminService:
    def list_tenants(self) -> list:
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT id, name, subdomain, is_active, created_at, plan_name, subscription_status, monthly_fee,
                    contact_email, language, billing_currency, billing_cycle, next_billing_date, last_payment_date,
                    payment_gateway, payment_gateway_mode, delivery_enabled, delivery_provider, delivery_api_key,
                    delivery_callback_url,
                    storefront_enabled, storefront_url, auto_storefront_enabled, storefront_template,
                    parent_tenant_id, org_slug, outlet_code, bir_tin, bir_vat_registered, compliance_notes,
                    bir_last_report_generated, feature_flags_json, pos_profile
                FROM tenants ORDER BY created_at DESC
                """
            ).fetchall()
        return [_tenant_row_to_dict(row) for row in rows]

    def list_users(self) -> list:
        with get_connection() as connection:
            rows = connection.execute(
                "SELECT u.id, u.full_name, u.username, u.role, u.is_active, u.tenant_id, t.name as tenant_name, u.last_login FROM users u LEFT JOIN tenants t ON u.tenant_id = t.id ORDER BY u.id DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def list_super_admin_accounts(self) -> list[dict[str, Any]]:
        """Platform operators only (`super_admin`). Tenant staff are managed in each tenant admin."""
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT u.id, u.full_name, u.username, u.role, u.is_active, u.tenant_id, u.last_login
                FROM users u
                WHERE u.role = 'super_admin'
                ORDER BY u.id DESC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def set_super_admin_account_active(self, user_id: int, active: bool, acting_user_id: int) -> None:
        with get_connection() as connection:
            row = connection.execute(
                "SELECT id, role, is_active FROM users WHERE id = ?",
                (user_id,),
            ).fetchone()
            if row is None:
                raise ValueError("User not found.")
            if row["role"] != "super_admin":
                raise ValueError("Only platform super admin accounts can be updated here.")
            if not active:
                if user_id == acting_user_id:
                    raise ValueError("You cannot deactivate your own account.")
                active_count = connection.execute(
                    "SELECT COUNT(*) AS c FROM users WHERE role = 'super_admin' AND is_active = 1"
                ).fetchone()["c"]
                if int(active_count or 0) <= 1 and int(row["is_active"] or 0) == 1:
                    raise ValueError("Cannot deactivate the last active super admin account.")
            connection.execute("UPDATE users SET is_active = ? WHERE id = ?", (1 if active else 0, user_id))
            action = "reactivate" if active else "deactivate"
            self.log_audit(
                connection,
                action,
                "user",
                user_id,
                f"Platform admin account {action}d (super admin console).",
                tenant_id=1,
            )

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        with get_connection() as connection:
            row = connection.execute(
                "SELECT value FROM app_settings WHERE tenant_id = 1 AND key = ?",
                (key,),
            ).fetchone()
        return row["value"] if row is not None else default

    def set_setting(self, key: str, value: str) -> None:
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO app_settings (tenant_id, key, value) VALUES (1, ?, ?)
                ON CONFLICT(tenant_id, key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )

    def save_platform_settings(self, key_values: dict[str, str]) -> None:
        with get_connection() as connection:
            for key, value in key_values.items():
                connection.execute(
                    """
                    INSERT INTO app_settings (tenant_id, key, value) VALUES (1, ?, ?)
                    ON CONFLICT(tenant_id, key) DO UPDATE SET value = excluded.value
                    """,
                    (key, value),
                )
            self.log_audit(
                connection,
                "settings_save",
                "platform",
                None,
                f"Platform settings updated ({', '.join(sorted(key_values.keys()))}).",
                tenant_id=1,
            )

    def log_audit(self, connection: Any, action: str, entity_type: str, entity_id: int | None, details: str, tenant_id: int | None = None) -> None:
        audit_service.log(
            connection=connection,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            details=details,
            user_id=None,
            tenant_id=tenant_id,
        )

    def create_tenant(
        self,
        name: str,
        plan_name: str,
        subscription_status: str,
        monthly_fee: float,
        contact_email: str,
        language: str,
        billing_currency: str,
        billing_cycle: str,
        payment_gateway: str,
        payment_gateway_mode: str,
        delivery_enabled: int,
        delivery_provider: str,
        storefront_enabled: int,
        storefront_url: str,
        auto_storefront_enabled: int,
        storefront_template: str = "default",
        pos_profile: str | None = None,
    ) -> int:
        profile_slug = normalize_pos_profile_id(pos_profile)
        with get_connection() as connection:
            tenant = TenantService(connection).create_tenant(
                name=name,
                contact_email=contact_email,
                plan_name=plan_name,
                subscription_status=subscription_status,
                monthly_fee=monthly_fee,
                billing_currency=billing_currency,
                billing_cycle=billing_cycle,
                delivery_enabled=delivery_enabled,
                delivery_provider=delivery_provider,
                storefront_enabled=storefront_enabled,
                storefront_url=storefront_url,
                auto_storefront_enabled=auto_storefront_enabled,
                storefront_template=storefront_template,
                pos_profile=profile_slug,
            )
            tenant_id = tenant.id
            self.log_audit(
                connection,
                "create",
                "tenant",
                tenant_id,
                f"Tenant created: {name} ({plan_name}, {subscription_status}, {billing_currency} {monthly_fee:.2f}, pos_profile={profile_slug})",
                tenant_id=tenant_id,
            )

        return tenant_id

    def update_tenant(
        self,
        tenant_id: int,
        name: str,
        is_active: int,
        plan_name: str,
        subscription_status: str,
        monthly_fee: float,
        contact_email: str,
        language: str,
        billing_currency: str,
        billing_cycle: str,
        payment_gateway: str,
        payment_gateway_mode: str,
        delivery_enabled: int,
        delivery_provider: str,
        delivery_api_key: str,
        delivery_callback_url: str,
        storefront_enabled: int,
        storefront_url: str,
        auto_storefront_enabled: int,
        next_billing_date: str | None,
        last_payment_date: str | None,
        parent_tenant_id: int | None,
        org_slug: str,
        outlet_code: str,
        bir_tin: str,
        bir_vat_registered: int,
        compliance_notes: str,
        feature_flags_json: str,
        pos_profile: str,
    ) -> None:
        if parent_tenant_id is not None:
            if parent_tenant_id == tenant_id:
                raise ValueError("A tenant cannot be its own parent organization.")
        with get_connection() as connection:
            if parent_tenant_id is not None:
                exists = connection.execute(
                    "SELECT 1 FROM tenants WHERE id = ?",
                    (parent_tenant_id,),
                ).fetchone()
                if exists is None:
                    raise ValueError("Parent tenant not found.")
            connection.execute(
                """
                UPDATE tenants
                SET name = ?, is_active = ?, plan_name = ?, subscription_status = ?, monthly_fee = ?, contact_email = ?,
                    language = ?, billing_currency = ?, billing_cycle = ?, next_billing_date = ?, last_payment_date = ?,
                    payment_gateway = ?, payment_gateway_mode = ?, delivery_enabled = ?, delivery_provider = ?,
                    delivery_api_key = ?, delivery_callback_url = ?, storefront_enabled = ?, storefront_url = ?,
                    auto_storefront_enabled = ?,
                    parent_tenant_id = ?, org_slug = ?, outlet_code = ?, bir_tin = ?, bir_vat_registered = ?,
                    compliance_notes = ?, feature_flags_json = ?, pos_profile = ?
                WHERE id = ?
                """,
                (
                    name,
                    is_active,
                    plan_name,
                    subscription_status,
                    monthly_fee,
                    contact_email,
                    language,
                    billing_currency,
                    billing_cycle,
                    next_billing_date,
                    last_payment_date,
                    payment_gateway,
                    payment_gateway_mode,
                    delivery_enabled,
                    delivery_provider,
                    delivery_api_key,
                    delivery_callback_url,
                    storefront_enabled,
                    storefront_url,
                    auto_storefront_enabled,
                    parent_tenant_id,
                    org_slug.strip(),
                    outlet_code.strip(),
                    bir_tin.strip(),
                    bir_vat_registered,
                    compliance_notes.strip(),
                    feature_flags_json,
                    normalize_pos_profile_id(pos_profile),
                    tenant_id,
                ),
            )
            self.log_audit(
                connection,
                "edit",
                "tenant",
                tenant_id,
                f"Tenant edited: {name}, status={is_active}, plan={plan_name}, subscription={subscription_status}, fee={billing_currency} {monthly_fee:.2f}",
                tenant_id=tenant_id,
            )

    def set_tenant_active(self, tenant_id: int, active: bool) -> None:
        with get_connection() as connection:
            connection.execute("UPDATE tenants SET is_active = ? WHERE id = ?", (1 if active else 0, tenant_id))
            self.log_audit(
                connection,
                "activate" if active else "deactivate",
                "tenant",
                tenant_id,
                f"Tenant {'activated' if active else 'deactivated'}.",
                tenant_id=tenant_id,
            )

    def delete_tenant(self, tenant_id: int) -> None:
        with get_connection() as connection:
            connection.execute("DELETE FROM tenants WHERE id = ?", (tenant_id,))
            self.log_audit(
                connection,
                "delete",
                "tenant",
                tenant_id,
                "Tenant deleted.",
                tenant_id=tenant_id,
            )

    def touch_bir_report_timestamp(self, tenant_id: int) -> None:
        from datetime import datetime, timezone

        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with get_connection() as connection:
            row = connection.execute("SELECT id FROM tenants WHERE id = ?", (tenant_id,)).fetchone()
            if row is None:
                raise ValueError("Tenant not found.")
            connection.execute(
                "UPDATE tenants SET bir_last_report_generated = ? WHERE id = ?",
                (stamp, tenant_id),
            )
            self.log_audit(
                connection,
                "compliance_touch",
                "tenant",
                tenant_id,
                "BIR report placeholder timestamp updated (super admin).",
                tenant_id=tenant_id,
            )

    def stream_audit_logs_csv(self, limit: int = 5000) -> str:
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT al.created_at, al.tenant_id, al.action, al.entity_type, al.entity_id,
                    COALESCE(u.full_name, 'System') AS actor_name, al.details
                FROM audit_logs al
                LEFT JOIN users u ON u.id = al.user_id
                ORDER BY al.created_at DESC, al.id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["created_at", "tenant_id", "action", "entity_type", "entity_id", "actor_name", "details"])
        for row in rows:
            writer.writerow(
                [
                    row["created_at"],
                    row["tenant_id"],
                    row["action"],
                    row["entity_type"],
                    row["entity_id"],
                    row["actor_name"],
                    row["details"],
                ]
            )
        return buf.getvalue()

    def stream_compliance_snapshot_csv(self) -> str:
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT t.id, t.name, t.subdomain, t.bir_tin, t.bir_vat_registered, t.compliance_notes,
                    t.bir_last_report_generated, t.org_slug, t.outlet_code, t.parent_tenant_id,
                    p.name AS parent_name
                FROM tenants t
                LEFT JOIN tenants p ON p.id = t.parent_tenant_id
                ORDER BY t.name
                """
            ).fetchall()
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(
            [
                "tenant_id",
                "name",
                "subdomain",
                "bir_tin",
                "bir_vat_registered",
                "compliance_notes",
                "bir_last_report_generated",
                "org_slug",
                "outlet_code",
                "parent_tenant_id",
                "parent_name",
            ]
        )
        for row in rows:
            writer.writerow(
                [
                    row["id"],
                    row["name"],
                    row["subdomain"],
                    row["bir_tin"],
                    row["bir_vat_registered"],
                    row["compliance_notes"],
                    row["bir_last_report_generated"],
                    row["org_slug"],
                    row["outlet_code"],
                    row["parent_tenant_id"],
                    row["parent_name"],
                ]
            )
        return buf.getvalue()

    def pos_profile_dropdown(self) -> list[dict[str, str]]:
        return [{"id": pid, "label": meta["label"]} for pid, meta in iter_profiles_for_admin()]

    def fetch_pos_profiles_reference_page(self) -> dict[str, Any]:
        rows: list[dict[str, Any]] = []
        for pid, meta in iter_profiles_for_admin():
            rows.append({"id": pid, **meta})
        return {"pos_profile_reference_rows": rows}

    def fetch_super_admin_context(self) -> dict[str, Any]:
        with get_connection() as connection:
            tenants = self.list_tenants()
            all_users = self.list_users()
            platform_admin_users = self.list_super_admin_accounts()
            metrics = {
                "tenant_count": int(_sql_scalar(connection, "SELECT COUNT(*) FROM tenants") or 0),
                "user_count": int(_sql_scalar(connection, "SELECT COUNT(*) FROM users") or 0),
                "active_location_count": int(
                    _sql_scalar(connection, "SELECT COUNT(*) FROM tenants WHERE is_active = 1") or 0
                ),
                "inactive_tenant_count": int(
                    _sql_scalar(connection, "SELECT COUNT(*) FROM tenants WHERE is_active = 0") or 0
                ),
                "active_subscriptions": int(
                    _sql_scalar(
                        connection,
                        "SELECT COUNT(*) FROM tenants WHERE is_active = 1 AND subscription_status IN ('active', 'trial', 'demo', 'trialing')",
                    )
                    or 0
                ),
                "monthly_recurring_revenue": float(
                    _sql_scalar(
                        connection,
                        "SELECT COALESCE(SUM(monthly_fee), 0) FROM tenants WHERE is_active = 1 AND subscription_status = 'active'",
                    )
                    or 0
                ),
                "pending_issues_count": 0,
            }
            if DATABASE_ENGINE == "postgres":
                health_trials_sql = """
                SELECT s.tenant_id, t.name AS tenant_name, s.status, s.trial_end, p.name AS plan_name
                FROM subscriptions s
                JOIN tenants t ON t.id = s.tenant_id
                JOIN plans p ON p.id = s.plan_id
                WHERE s.trial_end IS NOT NULL
                  AND (s.trial_end::date) >= CURRENT_DATE
                  AND (s.trial_end::date) <= CURRENT_DATE + INTERVAL '7 days'
                ORDER BY s.trial_end ASC, s.id ASC
                LIMIT 50
                """
                health_overdue_invoices_sql = """
                SELECT i.id, i.tenant_id, t.name AS tenant_name, i.total_amount, i.status, i.due_at,
                    t.billing_currency
                FROM invoices i
                JOIN tenants t ON t.id = i.tenant_id
                WHERE lower(i.status) != 'paid'
                  AND i.due_at IS NOT NULL
                  AND (i.due_at::date) < CURRENT_DATE
                ORDER BY i.due_at ASC, i.id ASC
                LIMIT 50
                """
            else:
                health_trials_sql = """
                SELECT s.tenant_id, t.name AS tenant_name, s.status, s.trial_end, p.name AS plan_name
                FROM subscriptions s
                JOIN tenants t ON t.id = s.tenant_id
                JOIN plans p ON p.id = s.plan_id
                WHERE s.trial_end IS NOT NULL
                  AND date(s.trial_end) >= date('now')
                  AND date(s.trial_end) <= date('now', '+7 days')
                ORDER BY s.trial_end ASC, s.id ASC
                LIMIT 50
                """
                health_overdue_invoices_sql = """
                SELECT i.id, i.tenant_id, t.name AS tenant_name, i.total_amount, i.status, i.due_at,
                    t.billing_currency
                FROM invoices i
                JOIN tenants t ON t.id = i.tenant_id
                WHERE lower(i.status) != 'paid'
                  AND i.due_at IS NOT NULL
                  AND date(i.due_at) < date('now')
                ORDER BY i.due_at ASC, i.id ASC
                LIMIT 50
                """
            health_trials_ending = connection.execute(health_trials_sql).fetchall()
            health_overdue_invoices = connection.execute(health_overdue_invoices_sql).fetchall()
            health_subscription_overdue = connection.execute(
                """
                SELECT id, name, subscription_status, monthly_fee, billing_currency, contact_email
                FROM tenants
                WHERE is_active = 1 AND lower(subscription_status) = 'overdue'
                ORDER BY name
                LIMIT 50
                """
            ).fetchall()
            if DATABASE_ENGINE == "postgres":
                health_delivery_sql = """
                SELECT d.id, d.tenant_id, t.name AS tenant_name, d.order_id, d.status, d.created_at
                FROM delivery_orders d
                JOIN tenants t ON t.id = d.tenant_id
                WHERE d.status IN ('failed', 'cancelled')
                   OR (d.status = 'pending' AND d.created_at::timestamp < NOW() - INTERVAL '1 day')
                ORDER BY d.created_at DESC, d.id DESC
                LIMIT 50
                """
                organization_rollups_sql = """
                SELECT x.org_root_id, org.name AS org_name, x.outlet_count,
                    (
                        SELECT COALESCE(SUM(s.total), 0)
                        FROM sales s
                        JOIN tenants tt ON tt.id = s.tenant_id
                        WHERE COALESCE(tt.parent_tenant_id, tt.id) = x.org_root_id
                          AND s.created_at::timestamp >= NOW() - INTERVAL '30 days'
                    ) AS gmv_30d
                FROM (
                    SELECT COALESCE(t.parent_tenant_id, t.id) AS org_root_id,
                        COUNT(*) AS outlet_count
                    FROM tenants t
                    GROUP BY COALESCE(t.parent_tenant_id, t.id)
                ) x
                JOIN tenants org ON org.id = x.org_root_id
                ORDER BY org.name
                """
            else:
                health_delivery_sql = """
                SELECT d.id, d.tenant_id, t.name AS tenant_name, d.order_id, d.status, d.created_at
                FROM delivery_orders d
                JOIN tenants t ON t.id = d.tenant_id
                WHERE d.status IN ('failed', 'cancelled')
                   OR (d.status = 'pending' AND datetime(d.created_at) < datetime('now', '-1 day'))
                ORDER BY d.created_at DESC, d.id DESC
                LIMIT 50
                """
                organization_rollups_sql = """
                SELECT x.org_root_id, org.name AS org_name, x.outlet_count,
                    (
                        SELECT COALESCE(SUM(s.total), 0)
                        FROM sales s
                        JOIN tenants tt ON tt.id = s.tenant_id
                        WHERE COALESCE(tt.parent_tenant_id, tt.id) = x.org_root_id
                          AND datetime(s.created_at) >= datetime('now', '-30 days')
                    ) AS gmv_30d
                FROM (
                    SELECT COALESCE(t.parent_tenant_id, t.id) AS org_root_id,
                        COUNT(*) AS outlet_count
                    FROM tenants t
                    GROUP BY COALESCE(t.parent_tenant_id, t.id)
                ) x
                JOIN tenants org ON org.id = x.org_root_id
                ORDER BY org.name
                """
            health_delivery_attention = connection.execute(health_delivery_sql).fetchall()
            organization_rollups = connection.execute(organization_rollups_sql).fetchall()
            metrics["pending_issues_count"] = (
                len(health_trials_ending)
                + len(health_overdue_invoices)
                + len(health_subscription_overdue)
                + len(health_delivery_attention)
            )
            saas_metrics = {
                "db_subscriptions": int(
                    _sql_scalar(
                        connection,
                        "SELECT COUNT(*) FROM subscriptions WHERE status IN ('active', 'trialing')",
                    )
                    or 0
                ),
                "tenants_delivery_on": int(
                    _sql_scalar(
                        connection,
                        "SELECT COUNT(*) FROM tenants WHERE is_active = 1 AND delivery_enabled = 1",
                    )
                    or 0
                ),
                "tenants_storefront_on": int(
                    _sql_scalar(
                        connection,
                        "SELECT COUNT(*) FROM tenants WHERE is_active = 1 AND storefront_enabled = 1",
                    )
                    or 0
                ),
                "delivery_orders_open": int(
                    _sql_scalar(
                        connection,
                        """
                        SELECT COUNT(*) FROM delivery_orders
                        WHERE status IN ('pending', 'assigned', 'in_transit')
                        """,
                    )
                    or 0
                ),
                "riders_active": int(
                    _sql_scalar(connection, "SELECT COUNT(*) FROM riders WHERE is_active = 1") or 0
                ),
                "active_plans": int(
                    _sql_scalar(connection, "SELECT COUNT(*) FROM plans WHERE is_active = 1") or 0
                ),
            }
            settings = {
                "support_email": self.get_setting("support_email", "") or "",
                "company_name": self.get_setting("company_name", "") or "",
                "support_phone": self.get_setting("support_phone", "") or "",
                "support_url": self.get_setting("support_url", "") or "",
                "platform_default_language": self.get_setting("platform_default_language", "en") or "en",
                "default_timezone": self.get_setting("default_timezone", "Asia/Manila") or "Asia/Manila",
                "date_format": self.get_setting("date_format", "Y-m-d") or "Y-m-d",
                "allow_public_signup": self.get_setting("allow_public_signup", "1") or "1",
                "platform_maintenance_mode": self.get_setting("platform_maintenance_mode", "0") or "0",
                "maintenance_message": self.get_setting(
                    "maintenance_message",
                    "We are performing scheduled maintenance. Please try again shortly.",
                )
                or "We are performing scheduled maintenance. Please try again shortly.",
            }
            settings["platform_locale_options"] = [
                {
                    "code": code,
                    "label": localization_service.LOCALE_DISPLAY_NAMES.get(code, code.upper()),
                }
                for code in localization_service.supported_locales
            ]
            audit_logs = connection.execute(
                """
                SELECT al.created_at, al.action, al.entity_type, al.entity_id,
                    COALESCE(u.full_name, 'System') AS actor_name, al.details, al.tenant_id
                FROM audit_logs al
                LEFT JOIN users u ON u.id = al.user_id
                ORDER BY al.created_at DESC, al.id DESC LIMIT 200
                """
            ).fetchall()
            tenant_products = connection.execute(
                """
                SELECT p.id, p.name, p.sku, p.price, p.status, p.tenant_id, t.name AS tenant_name,
                    t.auto_publish_products, t.storefront_url
                FROM products p
                JOIN tenants t ON p.tenant_id = t.id
                ORDER BY t.name, p.name
                """
            ).fetchall()
            billing_invoices = connection.execute(
                """
                SELECT i.id, i.amount, i.tax_rate, i.total_amount, i.status, i.issued_at, i.due_at, i.paid_at,
                    i.payment_reference, t.name AS tenant_name, t.billing_currency
                FROM invoices i
                JOIN tenants t ON i.tenant_id = t.id
                ORDER BY i.issued_at DESC, i.id DESC LIMIT 50
                """
            ).fetchall()
            subscription_overview = connection.execute(
                """
                SELECT s.id, s.tenant_id, t.name AS tenant_name, s.status, s.trial_end, s.started_at,
                    p.name AS plan_name, p.price AS plan_price
                FROM subscriptions s
                JOIN tenants t ON t.id = s.tenant_id
                JOIN plans p ON p.id = s.plan_id
                ORDER BY s.started_at DESC, s.id DESC
                LIMIT 40
                """
            ).fetchall()
            delivery_orders_recent = connection.execute(
                """
                SELECT d.id, d.tenant_id, t.name AS tenant_name, d.order_id, d.status, d.delivery_fee,
                    d.address, d.created_at
                FROM delivery_orders d
                JOIN tenants t ON t.id = d.tenant_id
                ORDER BY d.created_at DESC, d.id DESC
                LIMIT 30
                """
            ).fetchall()
            plans_catalog = connection.execute(
                """
                SELECT id, name, price, features, is_active, created_at
                FROM plans ORDER BY price ASC, name ASC
                """
            ).fetchall()

        return {
            "pos_profile_options": self.pos_profile_dropdown(),
            "tenants": tenants,
            "all_users": all_users,
            "platform_admin_users": platform_admin_users,
            "tenant_products": [dict(row) for row in tenant_products],
            "metrics": metrics,
            "saas_metrics": saas_metrics,
            "settings": settings,
            "audit_logs": audit_logs,
            "billing_invoices": [dict(row) for row in billing_invoices],
            "subscription_overview": [dict(row) for row in subscription_overview],
            "delivery_orders_recent": [dict(row) for row in delivery_orders_recent],
            "plans_catalog": [dict(row) for row in plans_catalog],
            "health_trials_ending": [dict(row) for row in health_trials_ending],
            "health_overdue_invoices": [dict(row) for row in health_overdue_invoices],
            "health_subscription_overdue": [dict(row) for row in health_subscription_overdue],
            "health_delivery_attention": [dict(row) for row in health_delivery_attention],
            "organization_rollups": [dict(row) for row in organization_rollups],
        }

    def fetch_payment_gateways_admin_page(self) -> dict[str, Any]:
        from app.core.payments.registry import catalog_rows_for_admin

        with get_connection() as connection:
            rows = catalog_rows_for_admin(connection)
        return {"payment_gateway_catalog_rows": rows}

    def save_payment_gateway_platform_toggles(self, enabled_gateway_ids: set[str]) -> None:
        from app.core.payments import registry as pay_registry

        catalog = pay_registry.catalog_ids()
        allowed_enabled = {g for g in enabled_gateway_ids if g in catalog}
        flags = {gid: {"enabled": gid in allowed_enabled} for gid in catalog}
        payload = json.dumps(flags, sort_keys=True)
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO app_settings (tenant_id, key, value) VALUES (1, ?, ?)
                ON CONFLICT(tenant_id, key) DO UPDATE SET value = excluded.value
                """,
                (pay_registry.PLATFORM_GATEWAYS_SETTING_KEY, payload),
            )
            self.log_audit(
                connection,
                "payment_gateways_save",
                "platform",
                None,
                f"Platform payment gateways toggled; enabled={sorted(allowed_enabled)}",
                tenant_id=1,
            )
