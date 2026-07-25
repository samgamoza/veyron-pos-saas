from __future__ import annotations

from flask import Blueprint, g, request

from app.core.api import api_roles_required, err_json, ok_json, register_tenant_before_request
from app.core.payments.paymongo_gateway import PayMongoError
from app.modules.api.services.payments_api_service import (
    WebhookVerificationError,
    payments_api_service,
)

payments_api_bp = Blueprint("payments_api", __name__, url_prefix="/api/payments")

# The webhook is called by PayMongo, which has no session, tenant header, or subdomain.
# Its trust boundary is the HMAC signature, not tenant context — so exempt it here.
register_tenant_before_request(
    payments_api_bp,
    exempt_endpoints={"payments_api.paymongo_webhook"},
)


@payments_api_bp.post("/checkout")
@api_roles_required("tenant_admin", "staff")
def create_checkout():
    """Open a PayMongo hosted checkout (GCash/Maya/QRPH/card) for a finalized sale."""
    body = request.get_json(silent=True) or {}
    try:
        sale_id = int(body["sale_id"])
    except (KeyError, TypeError, ValueError):
        return err_json("sale_id is required.", code="invalid_request", status=400)

    methods = body.get("methods")
    if methods is not None and not isinstance(methods, list):
        return err_json("methods must be a list.", code="invalid_request", status=400)

    try:
        data = payments_api_service.create_checkout(
            int(g.tenant_id),
            sale_id,
            methods=methods,
            success_url=body.get("success_url"),
            cancel_url=body.get("cancel_url"),
        )
    except ValueError as exc:
        return err_json(str(exc), code="checkout_failed", status=400)
    except PayMongoError as exc:
        # Upstream/provider failure — surface as a gateway error, not a client error.
        return err_json(str(exc), code="gateway_error", status=502)

    return ok_json(data, message="Checkout session created.")


@payments_api_bp.post("/webhook/paymongo")
def paymongo_webhook():
    """Receive PayMongo payment events. Signature-verified and idempotent."""
    try:
        result = payments_api_service.handle_paymongo_webhook(
            request.get_data(),
            dict(request.headers),
        )
    except WebhookVerificationError as exc:
        return err_json(str(exc), code="webhook_unverified", status=400)

    # Always 200 for verified events (even unmatched) so PayMongo stops retrying.
    return ok_json(result)
