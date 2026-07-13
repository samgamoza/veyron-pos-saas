from __future__ import annotations

from flask import Blueprint, request

from app.core.api import api_super_admin_required, err_json, ok_json
from app.modules.api.services.superadmin_api_service import superadmin_api_service

admin_api_bp = Blueprint("admin_api", __name__, url_prefix="/api/admin")


@admin_api_bp.get("/tenants")
@api_super_admin_required
def admin_list_tenants():
    return ok_json({"tenants": superadmin_api_service.list_tenants()})


@admin_api_bp.get("/products")
@api_super_admin_required
def admin_list_products():
    limit = request.args.get("limit", 500, type=int)
    rows = superadmin_api_service.list_all_products(limit=limit or 500)
    return ok_json({"products": rows})


@admin_api_bp.get("/subscriptions")
@api_super_admin_required
def admin_list_subscriptions():
    limit = request.args.get("limit", 500, type=int)
    rows = superadmin_api_service.list_subscriptions(limit=limit or 500)
    return ok_json({"subscriptions": rows})


@admin_api_bp.get("/plans")
@api_super_admin_required
def admin_list_plans():
    return ok_json({"plans": superadmin_api_service.list_plans()})
