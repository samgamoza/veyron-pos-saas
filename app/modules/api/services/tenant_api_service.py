from __future__ import annotations

from typing import Any

from flask import session

from app.admin.service import AdminService
from app.core.db import get_connection
from app.core.tenant.context import current_tenant
from app.core.user_service import user_service


class TenantApiService:
    def current(self) -> dict[str, Any] | None:
        tenant = current_tenant()
        if tenant is None:
            return None
        return {
            "id": tenant.id,
            "name": tenant.name,
            "subdomain": tenant.subdomain,
            "status": tenant.status,
        }

    def switch_tenant(self, target_tenant_id: int, acting_user_id: int, is_super_admin: bool) -> dict[str, Any]:
        if is_super_admin:
            with get_connection() as connection:
                row = connection.execute(
                    "SELECT id, name, subdomain, status FROM tenants WHERE id = ? AND is_active = 1",
                    (target_tenant_id,),
                ).fetchone()
            if row is None:
                raise ValueError("Tenant not found.")
            session["tenant_id"] = int(row["id"])
            return {"tenant_id": int(row["id"]), "name": row["name"], "subdomain": row["subdomain"]}

        user = user_service.get_by_id(acting_user_id, tenant_id=None)
        if user is None or int(user.get("tenant_id") or 0) != int(target_tenant_id):
            raise ValueError("You do not belong to that tenant.")
        session["tenant_id"] = int(target_tenant_id)
        return {"tenant_id": int(target_tenant_id)}

    def create_tenant(self, payload: dict[str, Any]) -> int:
        name = (payload.get("name") or "").strip()
        if not name:
            raise ValueError("name is required.")
        admin = AdminService()
        return admin.create_tenant(
            name=name,
            plan_name=str(payload.get("plan_name", "starter")).strip().lower() or "starter",
            subscription_status=str(payload.get("subscription_status", "trial")).strip().lower() or "trial",
            monthly_fee=float(payload.get("monthly_fee", 0) or 0),
            contact_email=str(payload.get("contact_email", "")).strip(),
            language=str(payload.get("language", "en")).strip().lower() or "en",
            billing_currency=str(payload.get("billing_currency", "PHP")).strip().upper() or "PHP",
            billing_cycle=str(payload.get("billing_cycle", "monthly")).strip().lower() or "monthly",
            payment_gateway=str(payload.get("payment_gateway", "")).strip(),
            payment_gateway_mode=str(payload.get("payment_gateway_mode", "test")).strip().lower() or "test",
            delivery_enabled=1 if payload.get("delivery_enabled") else 0,
            delivery_provider=str(payload.get("delivery_provider", "")).strip(),
            storefront_enabled=1 if payload.get("storefront_enabled", True) else 0,
            storefront_url=str(payload.get("storefront_url", "")).strip(),
            auto_storefront_enabled=1 if payload.get("auto_storefront_enabled", True) else 0,
            storefront_template=str(payload.get("storefront_template", "default")).strip() or "default",
            pos_profile=str(payload.get("pos_profile", "")).strip() or None,
        )


tenant_api_service = TenantApiService()
