from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Iterable, List, Optional

from app.core.pos_profiles import normalize_pos_profile_id

from .context import build_tenant_subdomain
from .model import Tenant

TenantHook = Callable[[Tenant, Dict[str, Any]], None]

DEFAULT_TENANT_SETTINGS: dict[str, str] = {
    "onboarding_completed": "0",
    "auto_print_receipt": "0",
    "cash_drawer_enabled": "0",
    "printer_mode": "browser",
    "printer_bridge_url": "http://127.0.0.1:19191/print",
    "drawer_open_note": "Browser mode logs drawer opens but needs a local bridge for real ESC/POS drawer pulses.",
    "alert_low_stock_email": "1",
    "alert_void_refund_email": "1",
    "alert_variance_email": "1",
    "brand_primary_color": "#0f6a5d",
    "brand_accent_color": "#b54a2f",
    "brand_theme_mode": "warm",
    "brand_logo_path": "",
    "delivery_fee_base": "0.00",
    "delivery_fee_per_km": "0.00",
    "delivery_fee_free_threshold": "0.00",
    "delivery_zones": "",
}

_TENANT_HOOKS: dict[str, List[TenantHook]] = {
    "tenant_created": [],
    "tenant_status_changed": [],
    "subscription_assigned": [],
}


def register_tenant_hook(event_name: str, hook: TenantHook) -> None:
    if event_name not in _TENANT_HOOKS:
        raise ValueError(f"Unknown tenant hook event: {event_name}")
    _TENANT_HOOKS[event_name].append(hook)


def trigger_tenant_hooks(tenant: Tenant, event_name: str, payload: Optional[Dict[str, Any]] = None) -> None:
    if event_name not in _TENANT_HOOKS:
        return
    for hook in _TENANT_HOOKS[event_name]:
        try:
            hook(tenant, payload or {})
        except Exception:
            # Hooks are best-effort side effects (e.g. storefront provisioning) that
            # open their own DB connection and so cannot see this tenant's row until
            # the caller's transaction commits. A hook failure must never roll back
            # or crash the tenant/account creation that triggered it.
            logging.getLogger(__name__).exception(
                "Tenant hook '%s' failed for tenant_id=%s", event_name, tenant.id
            )


class TenantService:
    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def create_tenant(
        self,
        name: str,
        contact_email: str = "",
        plan_name: str = "starter",
        subscription_status: str = "trial",
        monthly_fee: float = 0.0,
        billing_currency: str = "PHP",
        billing_cycle: str = "monthly",
        storefront_enabled: int = 1,
        storefront_url: str = "",
        auto_storefront_enabled: int = 1,
        storefront_template: str = "default",
        delivery_enabled: int = 0,
        delivery_provider: str = "",
        delivery_api_key: str = "",
        delivery_callback_url: str = "",
        pos_profile: str | None = None,
    ) -> Tenant:
        name = name.strip()
        if not name:
            raise ValueError("Tenant name is required.")

        profile_slug = normalize_pos_profile_id(pos_profile)
        subdomain = build_tenant_subdomain(self.connection, name)
        row = self.connection.execute(
            """
            INSERT INTO tenants (
                name,
                subdomain,
                status,
                is_active,
                plan_name,
                subscription_status,
                monthly_fee,
                contact_email,
                billing_currency,
                billing_cycle,
                delivery_enabled,
                delivery_provider,
                delivery_api_key,
                delivery_callback_url,
                storefront_enabled,
                storefront_url,
                auto_storefront_enabled,
                storefront_template,
                pos_profile
            ) VALUES (?, ?, 'active', 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING id, name, subdomain, status, created_at, is_active
            """,
            (
                name,
                subdomain,
                plan_name,
                subscription_status,
                monthly_fee,
                contact_email,
                billing_currency,
                billing_cycle,
                delivery_enabled,
                delivery_provider,
                delivery_api_key,
                delivery_callback_url,
                storefront_enabled,
                storefront_url,
                auto_storefront_enabled,
                storefront_template,
                profile_slug,
            ),
        ).fetchone()

        tenant = Tenant(
            id=row["id"],
            name=row["name"],
            subdomain=row["subdomain"],
            status=row["status"],
            created_at=row["created_at"],
            is_active=bool(row["is_active"]),
        )

        self._create_default_settings(tenant.id)
        # Every tenant starts with one default branch.
        from app.core.locations import LocationService

        LocationService(self.connection).ensure_default(tenant.id)
        trigger_tenant_hooks(tenant, "tenant_created", {"plan_name": plan_name})
        return tenant

    def activate_tenant(self, tenant_id: int) -> Tenant:
        self.connection.execute(
            "UPDATE tenants SET status = 'active', is_active = 1 WHERE id = ?",
            (tenant_id,),
        )
        tenant = self.get_tenant_by_id(tenant_id)
        if tenant is None:
            raise ValueError(f"Tenant not found: {tenant_id}")

        trigger_tenant_hooks(tenant, "tenant_status_changed", {"status": "active"})
        return tenant

    def deactivate_tenant(self, tenant_id: int, reason: str = "") -> Tenant:
        self.connection.execute(
            "UPDATE tenants SET status = 'inactive', is_active = 0 WHERE id = ?",
            (tenant_id,),
        )
        tenant = self.get_tenant_by_id(tenant_id)
        if tenant is None:
            raise ValueError(f"Tenant not found: {tenant_id}")

        trigger_tenant_hooks(tenant, "tenant_status_changed", {"status": "inactive", "reason": reason})
        return tenant

    def assign_subscription_plan(
        self,
        tenant_id: int,
        plan_name: str,
        subscription_status: str = "active",
        monthly_fee: float = 0.0,
        billing_currency: str = "PHP",
    ) -> Tenant:
        self.connection.execute(
            "UPDATE tenants SET plan_name = ?, subscription_status = ?, monthly_fee = ?, billing_currency = ? WHERE id = ?",
            (plan_name, subscription_status, monthly_fee, billing_currency, tenant_id),
        )
        tenant = self.get_tenant_by_id(tenant_id)
        if tenant is None:
            raise ValueError(f"Tenant not found: {tenant_id}")

        trigger_tenant_hooks(
            tenant,
            "subscription_assigned",
            {
                "plan_name": plan_name,
                "subscription_status": subscription_status,
                "monthly_fee": monthly_fee,
                "billing_currency": billing_currency,
            },
        )
        return tenant

    def get_tenant_by_id(self, tenant_id: int) -> Optional[Tenant]:
        row = self.connection.execute(
            "SELECT id, name, subdomain, status, created_at, is_active FROM tenants WHERE id = ?",
            (tenant_id,),
        ).fetchone()
        if row is None:
            return None
        return Tenant(
            id=row["id"],
            name=row["name"],
            subdomain=row["subdomain"],
            status=row["status"],
            created_at=row["created_at"],
            is_active=bool(row["is_active"]),
        )

    def _create_default_settings(self, tenant_id: int) -> None:
        for key, value in DEFAULT_TENANT_SETTINGS.items():
            self.connection.execute(
                """
                INSERT INTO app_settings (tenant_id, key, value)
                VALUES (?, ?, ?)
                ON CONFLICT (tenant_id, key) DO UPDATE SET value = excluded.value
                """,
                (tenant_id, key, value),
            )

    def ensure_tenant_settings(self, tenant_id: int, settings: Optional[Dict[str, str]] = None) -> None:
        if settings is None:
            settings = DEFAULT_TENANT_SETTINGS.copy()
        for key, value in settings.items():
            self.connection.execute(
                """
                INSERT INTO app_settings (tenant_id, key, value)
                VALUES (?, ?, ?)
                ON CONFLICT (tenant_id, key) DO UPDATE SET value = excluded.value
                """,
                (tenant_id, key, value),
            )
