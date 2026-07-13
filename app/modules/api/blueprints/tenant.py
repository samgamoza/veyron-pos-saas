from __future__ import annotations

from flask import Blueprint, g, request, session

from app.core.api import api_super_admin_required, err_json, ok_json, require_api_user
from app.modules.api.services.tenant_api_service import tenant_api_service

tenant_api_bp = Blueprint("tenant_api", __name__, url_prefix="/api/tenant")


@tenant_api_bp.get("/current")
def tenant_current():
    data = tenant_api_service.current()
    return ok_json(data)


@tenant_api_bp.post("/switch")
def tenant_switch():
    denied = require_api_user()
    if denied:
        return denied
    body = request.get_json(silent=True) or {}
    try:
        tid = int(body.get("tenant_id"))
    except (TypeError, ValueError):
        return err_json("tenant_id is required.", code="validation_error", status=400)
    user = g.api_user
    try:
        data = tenant_api_service.switch_tenant(
            target_tenant_id=tid,
            acting_user_id=int(user["id"]),
            is_super_admin=bool(session.get("is_super_admin")),
        )
        return ok_json(data, message="Tenant context updated.")
    except ValueError as exc:
        return err_json(str(exc), code="switch_failed", status=403)


@tenant_api_bp.post("/create")
@api_super_admin_required
def tenant_create():
    body = request.get_json(silent=True) or {}
    try:
        tid = tenant_api_service.create_tenant(body)
        return ok_json({"id": tid}, message="Tenant created.", status=201)
    except ValueError as exc:
        return err_json(str(exc), code="validation_error", status=400)
