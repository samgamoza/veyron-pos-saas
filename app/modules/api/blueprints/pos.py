from __future__ import annotations

from flask import Blueprint, g, request

from app.core.api import api_roles_required, err_json, ok_json, register_tenant_before_request
from app.core.auth import session_user_id
from app.modules.api.services.pos_api_service import PosApiService, pos_api_service

pos_api_bp = Blueprint("pos_api", __name__, url_prefix="/api/pos")
register_tenant_before_request(pos_api_bp)


@pos_api_bp.get("/orders")
@api_roles_required("tenant_admin", "staff")
def list_orders():
    limit = request.args.get("limit", 50, type=int)
    try:
        rows = pos_api_service.list_orders(int(g.tenant_id), limit=limit or 50)
        return ok_json({"orders": rows})
    except Exception as exc:
        return err_json(str(exc), code="pos_error", status=400)


@pos_api_bp.post("/orders")
@api_roles_required("tenant_admin", "staff")
def create_order():
    body = request.get_json(silent=True) or {}
    lines = body.get("lines") or body.get("items") or []
    if not isinstance(lines, list):
        return err_json("lines must be an array.", code="validation_error", status=400)
    try:
        pay_lines = PosApiService.parse_optional_payment_lines(body)
    except ValueError as exc:
        return err_json(str(exc), code="validation_error", status=400)
    try:
        sale_id, created = pos_api_service.create_order(
            tenant_id=int(g.tenant_id),
            cashier_user_id=session_user_id(),
            lines=lines,
            payment_method=str(body.get("payment_method", "cash")),
            discount_type=str(body.get("discount_type", "none")),
            discount_note=str(body.get("discount_note", "")),
            custom_discount_rate=float(body.get("custom_discount_rate", 0) or 0),
            service_reference=str(body.get("service_reference", "") or ""),
            idempotency_key=str(body.get("idempotency_key", "") or "").strip() or None,
            payment_lines=pay_lines,
            location_id=body.get("location_id"),
            promo_code=str(body.get("promo_code", "") or ""),
        )
        msg = "Order created." if created else "Idempotent replay: existing sale."
        return ok_json({"sale_id": sale_id, "created": created}, message=msg, status=201 if created else 200)
    except ValueError as exc:
        return err_json(str(exc), code="checkout_failed", status=400)
