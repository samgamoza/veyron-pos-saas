from __future__ import annotations

from flask import Blueprint, g, request

from app.core.api import api_roles_required, err_json, ok_json, register_tenant_before_request
from app.modules.api.services.products_api_service import products_api_service

products_api_bp = Blueprint("products_api", __name__, url_prefix="/api/products")
register_tenant_before_request(products_api_bp)


@products_api_bp.get("/", strict_slashes=False)
@api_roles_required("tenant_admin", "staff")
def list_products():
    limit = request.args.get("limit", 200, type=int)
    rows = products_api_service.list_products(int(g.tenant_id), limit=limit or 200)
    return ok_json({"products": rows})


@products_api_bp.get("/<int:product_id>")
@api_roles_required("tenant_admin", "staff")
def get_product(product_id: int):
    row = products_api_service.get_product(int(g.tenant_id), product_id)
    if row is None:
        return err_json("Product not found.", code="not_found", status=404)
    return ok_json({"product": row})


@products_api_bp.post("/", strict_slashes=False)
@api_roles_required("tenant_admin")
def create_product():
    body = request.get_json(silent=True) or {}
    try:
        pid = products_api_service.create_product(int(g.tenant_id), body)
        return ok_json({"id": pid}, message="Product created.", status=201)
    except (ValueError, KeyError, TypeError) as exc:
        return err_json(str(exc), code="validation_error", status=400)


@products_api_bp.patch("/<int:product_id>")
@api_roles_required("tenant_admin")
def patch_product(product_id: int):
    body = request.get_json(silent=True) or {}
    try:
        products_api_service.update_product(int(g.tenant_id), product_id, body)
        return ok_json(message="Product updated.")
    except ValueError as exc:
        return err_json(str(exc), code="update_failed", status=400)


@products_api_bp.delete("/<int:product_id>")
@api_roles_required("tenant_admin")
def delete_product(product_id: int):
    hard = request.args.get("hard", "0") == "1"
    try:
        products_api_service.delete_product(int(g.tenant_id), product_id, hard=hard)
        return ok_json(message="Product removed or archived.")
    except ValueError as exc:
        return err_json(str(exc), code="delete_failed", status=400)
