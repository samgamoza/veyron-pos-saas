from __future__ import annotations

from flask import Blueprint, g, request

from app.core.api import api_roles_required, err_json, ok_json, register_tenant_before_request
from app.modules.api.services.subscription_api_service import subscription_api_service

subscription_api_bp = Blueprint("subscription_api", __name__, url_prefix="/api/subscription")
register_tenant_before_request(subscription_api_bp)


@subscription_api_bp.get("/current")
@api_roles_required("tenant_admin")
def subscription_current():
    data = subscription_api_service.current(int(g.tenant_id))
    return ok_json({"subscription": data})


@subscription_api_bp.post("/upgrade")
@api_roles_required("tenant_admin")
def subscription_upgrade():
    body = request.get_json(silent=True) or {}
    try:
        plan_id = int(body["plan_id"])
        data = subscription_api_service.upgrade(int(g.tenant_id), plan_id)
        return ok_json({"subscription": data}, message="Plan updated.")
    except (ValueError, KeyError, TypeError) as exc:
        return err_json(str(exc), code="upgrade_failed", status=400)
