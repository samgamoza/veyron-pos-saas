"""Public scan-to-order surface.

The target of a merchant's QR code. Works for ANY active merchant (not just
marketplace opt-ins) when qr_ordering_enabled is on. Reuses the tenant-scoped
MarketplaceOrderService so orders, customers, VAT, and loyalty all flow through
one code path.
"""

from __future__ import annotations

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from app.core.db import get_connection
from app.core.delivery.integration_service import DeliveryIntegrationService
from app.core.payments.paymongo_gateway import PayMongoError
from app.core.payments.registry import gateway_enabled_for_platform
from app.core.rate_limit import limiter
from app.modules.api.services.payments_api_service import payments_api_service
from app.modules.etown.order_service import MarketplaceOrderService, OrderLineIn

order_bp = Blueprint("order", __name__, url_prefix="/order")
_service = MarketplaceOrderService()
_delivery_integration = DeliveryIntegrationService()


def _parse_lines(form) -> list[OrderLineIn]:
    lines: list[OrderLineIn] = []
    for key in form.keys():
        if not key.startswith("qty_"):
            continue
        try:
            product_id = int(key.removeprefix("qty_").split("_")[0])
        except (TypeError, ValueError):
            continue
        try:
            qty = int(form.get(key, 0) or 0)
        except (TypeError, ValueError):
            continue
        if qty <= 0:
            continue
        variant_raw = (form.get(f"variant_{product_id}", "") or "").strip()
        variant_id = int(variant_raw) if variant_raw.isdigit() else None
        lines.append(OrderLineIn(product_id=product_id, quantity=qty, variant_id=variant_id))
    return lines


def _dispatch_online_delivery(
    tenant_id: int,
    order_id: int,
    *,
    delivery_requested: bool,
    delivery_line1: str,
    delivery_line2: str,
    delivery_city: str,
    delivery_notes: str,
    order_total: float,
) -> None:
    if not delivery_requested:
        return
    try:
        _delivery_integration.create_online_delivery_if_requested(
            tenant_id,
            order_id,
            delivery_requested=True,
            delivery_line1=delivery_line1,
            delivery_line2=delivery_line2,
            delivery_city=delivery_city,
            delivery_notes=delivery_notes,
            order_total=order_total,
        )
    except Exception as exc:
        flash(f"Order placed, but rider delivery could not be scheduled: {exc}", "warning")


@order_bp.route("/<int:tenant_id>")
def order_page(tenant_id: int):
    tenant = _service.get_orderable_tenant(tenant_id)
    if tenant is None:
        abort(404)
    products = _service.list_public_products(tenant_id)
    variants_by_product = _service.list_public_variants_by_product(tenant_id)
    table = (request.args.get("table", "") or "").strip()[:40]
    delivery_enabled = _service.tenant_delivery_enabled(tenant_id)
    return render_template(
        "order/order.html",
        tenant=tenant,
        products=products,
        variants_by_product=variants_by_product,
        table=table,
        delivery_enabled=delivery_enabled,
    )


@order_bp.route("/<int:tenant_id>", methods=["POST"])
@limiter.limit("20 per minute; 100 per hour", methods=["POST"])
def submit_order(tenant_id: int):
    tenant = _service.get_orderable_tenant(tenant_id)
    if tenant is None:
        abort(404)

    table = (request.form.get("table", "") or "").strip()[:40]
    fulfillment = (request.form.get("fulfillment", "pickup") or "pickup").strip().lower()
    delivery_requested = fulfillment == "delivery"
    delivery_line1 = (request.form.get("delivery_line1", "") or "").strip()[:200]
    delivery_line2 = (request.form.get("delivery_line2", "") or "").strip()[:200]
    delivery_city = (request.form.get("delivery_city", "") or "").strip()[:120]
    delivery_notes = (request.form.get("delivery_notes", "") or "").strip()[:500]

    if delivery_requested:
        if not _service.tenant_delivery_enabled(tenant_id):
            flash("Delivery is not available from this store.", "error")
            return redirect(url_for("order.order_page", tenant_id=tenant_id, table=table))
        if not delivery_line1 or not delivery_city:
            flash("Street address and city are required for delivery.", "error")
            return redirect(url_for("order.order_page", tenant_id=tenant_id, table=table))

    lines = _parse_lines(request.form)
    base_notes = (request.form.get("notes", "") or "").strip()[:500]
    context_parts: list[str] = []
    if table:
        context_parts.append(f"Table {table}")
    if delivery_requested:
        context_parts.append("Delivery")
    context = " · ".join(context_parts)
    notes = " · ".join(part for part in (context, base_notes) if part)

    try:
        order_id = _service.create_order(
            tenant_id,
            lines,
            guest_name=request.form.get("guest_name", ""),
            guest_phone=request.form.get("guest_phone", ""),
            notes=notes,
            delivery_line1=delivery_line1 if delivery_requested else "",
            delivery_line2=delivery_line2 if delivery_requested else "",
            delivery_city=delivery_city if delivery_requested else "",
            delivery_notes=delivery_notes if delivery_requested else "",
            require_marketplace=False,
        )
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("order.order_page", tenant_id=tenant_id, table=table))

    order = _service.get_order_public(tenant_id, order_id)
    order_total = float(order["total"]) if order else 0.0
    _dispatch_online_delivery(
        tenant_id,
        order_id,
        delivery_requested=delivery_requested,
        delivery_line1=delivery_line1,
        delivery_line2=delivery_line2,
        delivery_city=delivery_city,
        delivery_notes=delivery_notes,
        order_total=order_total,
    )

    return redirect(url_for("order.order_confirmation", tenant_id=tenant_id, order_id=order_id, table=table or None))


@order_bp.route("/<int:tenant_id>/confirmation/<int:order_id>")
def order_confirmation(tenant_id: int, order_id: int):
    tenant = _service.get_orderable_tenant(tenant_id)
    if tenant is None:
        abort(404)
    order = _service.get_order_public(tenant_id, order_id)
    if order is None:
        abort(404)
    table = (request.args.get("table", "") or "").strip()[:40]
    delivery = _service.get_online_delivery(tenant_id, order_id)

    show_pay_button = False
    if order["payment_status"] == "unpaid" and float(order["total"]) > 0:
        with get_connection() as connection:
            show_pay_button = gateway_enabled_for_platform(connection, "paymongo")

    return render_template(
        "order/confirmation.html",
        tenant=tenant,
        order=order,
        table=table,
        delivery=delivery,
        show_pay_button=show_pay_button,
    )


@order_bp.route("/<int:tenant_id>/<int:order_id>/pay", methods=["POST"])
@limiter.limit("20 per minute; 100 per hour", methods=["POST"])
def pay_order(tenant_id: int, order_id: int):
    """Pay-now is optional: an order left unpaid here is simply settled at the counter."""
    tenant = _service.get_orderable_tenant(tenant_id)
    if tenant is None:
        abort(404)
    table = (request.form.get("table", "") or "").strip()[:40]

    confirm_url = url_for(
        "order.order_confirmation",
        tenant_id=tenant_id,
        order_id=order_id,
        table=table or None,
    )

    try:
        data = payments_api_service.create_checkout_for_order(
            tenant_id,
            order_id,
            success_url=url_for(
                "order.order_confirmation",
                tenant_id=tenant_id,
                order_id=order_id,
                table=table or None,
                _external=True,
            ),
            cancel_url=url_for(
                "order.order_confirmation",
                tenant_id=tenant_id,
                order_id=order_id,
                table=table or None,
                _external=True,
            ),
        )
    except ValueError as exc:
        flash(str(exc), "error")
        if _service.get_order_public(tenant_id, order_id) is None:
            return redirect(url_for("order.order_page", tenant_id=tenant_id, table=table or None))
        return redirect(confirm_url)
    except PayMongoError:
        flash("Payment provider is unavailable right now. You can still pay at the counter.", "error")
        return redirect(confirm_url)

    checkout_url = data.get("checkout_url") if isinstance(data, dict) else None
    if not checkout_url:
        flash("Payment checkout could not be started. You can still pay at the counter.", "error")
        return redirect(confirm_url)

    return redirect(checkout_url)
