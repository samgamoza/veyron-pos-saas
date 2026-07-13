from __future__ import annotations

from flask import Blueprint, g, request

from app.core.api import api_roles_required, err_json, ok_json, register_tenant_before_request
from app.modules.api.services.delivery_api_service import delivery_api_service

delivery_api_bp = Blueprint("delivery_api", __name__, url_prefix="/api/delivery")
register_tenant_before_request(delivery_api_bp)


@delivery_api_bp.get("/orders")
@api_roles_required("tenant_admin", "staff")
def list_delivery_orders():
    status = request.args.get("status", type=str)
    try:
        rows = delivery_api_service.list_orders(int(g.tenant_id), status=status)
        return ok_json({"delivery_orders": rows})
    except ValueError as exc:
        return err_json(str(exc), code="delivery_disabled", status=400)


@delivery_api_bp.post("/orders")
@api_roles_required("tenant_admin", "staff")
def create_delivery_order():
    body = request.get_json(silent=True) or {}
    try:
        oid = int(body["order_id"])
        address = str(body.get("address", "")).strip()
        if not address:
            raise ValueError("address is required.")
        did = delivery_api_service.create(
            tenant_id=int(g.tenant_id),
            order_id=oid,
            address=address,
            instructions=str(body.get("instructions", "")),
            delivery_fee=float(body.get("delivery_fee", 0) or 0),
        )
        return ok_json({"delivery_order_id": did}, message="Delivery order created.", status=201)
    except (ValueError, KeyError, TypeError) as exc:
        return err_json(str(exc), code="delivery_error", status=400)


@delivery_api_bp.post("/orders/<int:delivery_order_id>/assign")
@api_roles_required("tenant_admin")
def assign_rider(delivery_order_id: int):
    body = request.get_json(silent=True) or {}
    try:
        rider_id = int(body["rider_id"])
        delivery_api_service.assign_rider(int(g.tenant_id), delivery_order_id, rider_id)
        return ok_json(message="Rider assigned.")
    except (ValueError, KeyError, TypeError) as exc:
        return err_json(str(exc), code="assign_failed", status=400)


@delivery_api_bp.patch("/orders/<int:delivery_order_id>/status")
@api_roles_required("tenant_admin", "staff")
def patch_status(delivery_order_id: int):
    body = request.get_json(silent=True) or {}
    try:
        status = str(body["status"])
        delivery_api_service.update_status(int(g.tenant_id), delivery_order_id, status)
        return ok_json(message="Status updated.")
    except (ValueError, KeyError, TypeError) as exc:
        return err_json(str(exc), code="status_failed", status=400)
