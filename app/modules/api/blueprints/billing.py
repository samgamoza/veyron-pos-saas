from __future__ import annotations

from flask import Blueprint, g, request

from app.core.api import api_roles_required, err_json, ok_json, register_tenant_before_request
from app.modules.api.services.billing_api_service import billing_api_service

billing_api_bp = Blueprint("billing_api", __name__, url_prefix="/api/billing")
register_tenant_before_request(billing_api_bp)


@billing_api_bp.get("/invoices")
@api_roles_required("tenant_admin")
def list_invoices():
    status = request.args.get("status", type=str)
    rows = billing_api_service.list_invoices(int(g.tenant_id), status=status)
    return ok_json({"invoices": rows})


@billing_api_bp.post("/payments")
@api_roles_required("tenant_admin")
def record_payment():
    body = request.get_json(silent=True) or {}
    try:
        invoice_id = int(body["invoice_id"])
        ref = body.get("payment_reference")
        data = billing_api_service.record_payment(int(g.tenant_id), invoice_id, str(ref) if ref else None)
        return ok_json({"invoice": data}, message="Payment recorded.")
    except (ValueError, KeyError, TypeError) as exc:
        return err_json(str(exc), code="payment_failed", status=400)
