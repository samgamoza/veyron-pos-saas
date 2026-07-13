from __future__ import annotations

from flask import Blueprint, g, request

from app.core.api import err_json, ok_json, register_tenant_before_request
from app.modules.api.services.storefront_api_service import storefront_api_service

store_api_bp = Blueprint("store_api", __name__, url_prefix="/api/store")
register_tenant_before_request(store_api_bp)


@store_api_bp.get("/products")
def list_products():
    if not getattr(g, "tenant_id", None):
        return err_json("Tenant required for storefront.", code="tenant_required", status=400)
    limit = request.args.get("limit", 100, type=int)
    rows = storefront_api_service.list_public_products(int(g.tenant_id), limit=limit or 100)
    return ok_json({"products": rows})
