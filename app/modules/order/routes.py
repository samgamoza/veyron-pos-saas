"""Public scan-to-order surface.

The target of a merchant's QR code. Works for ANY active merchant (not just
marketplace opt-ins) when qr_ordering_enabled is on. Reuses the tenant-scoped
MarketplaceOrderService so orders, customers, VAT, and loyalty all flow through
one code path.
"""

from __future__ import annotations

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from app.core.rate_limit import limiter
from app.modules.etown.order_service import MarketplaceOrderService, OrderLineIn

order_bp = Blueprint("order", __name__, url_prefix="/order")
_service = MarketplaceOrderService()


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


@order_bp.route("/<int:tenant_id>")
def order_page(tenant_id: int):
    tenant = _service.get_orderable_tenant(tenant_id)
    if tenant is None:
        abort(404)
    products = _service.list_public_products(tenant_id)
    variants_by_product = _service.list_public_variants_by_product(tenant_id)
    table = (request.args.get("table", "") or "").strip()[:40]
    return render_template(
        "order/order.html",
        tenant=tenant,
        products=products,
        variants_by_product=variants_by_product,
        table=table,
    )


@order_bp.route("/<int:tenant_id>", methods=["POST"])
@limiter.limit("20 per minute; 100 per hour", methods=["POST"])
def submit_order(tenant_id: int):
    tenant = _service.get_orderable_tenant(tenant_id)
    if tenant is None:
        abort(404)

    table = (request.form.get("table", "") or "").strip()[:40]
    lines = _parse_lines(request.form)
    base_notes = (request.form.get("notes", "") or "").strip()[:500]
    context = f"Table {table}" if table else ""
    notes = " · ".join(part for part in (context, base_notes) if part)

    try:
        order_id = _service.create_order(
            tenant_id,
            lines,
            guest_name=request.form.get("guest_name", ""),
            guest_phone=request.form.get("guest_phone", ""),
            notes=notes,
            require_marketplace=False,
        )
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("order.order_page", tenant_id=tenant_id, table=table))

    return render_template("order/confirmation.html", tenant=tenant, order_id=order_id, table=table)
