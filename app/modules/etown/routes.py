"""
Public eTown HTTP surface. Uses the shared app database (same env as POS); no separate DB.
"""

from __future__ import annotations

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for

from app.modules.etown.order_service import MarketplaceOrderService, OrderLineIn

etown_bp = Blueprint("etown", __name__, url_prefix="/etown")
_service = MarketplaceOrderService()


def _parse_order_lines(form) -> list[OrderLineIn]:
    lines: list[OrderLineIn] = []
    for key in form.keys():
        if not key.startswith("qty_"):
            continue
        suffix = key.removeprefix("qty_")
        try:
            product_id = int(suffix.split("_")[0])
        except (TypeError, ValueError):
            continue
        try:
            qty = int(form.get(key, 0) or 0)
        except (TypeError, ValueError):
            continue
        if qty <= 0:
            continue
        variant_raw = form.get(f"variant_{product_id}", "").strip()
        variant_id = int(variant_raw) if variant_raw else None
        lines.append(OrderLineIn(product_id=product_id, quantity=qty, variant_id=variant_id))
    return lines


@etown_bp.route("/")
def hub():
    tenants = _service.list_marketplace_tenants()
    return render_template("etown/hub.html", tenants=tenants)


@etown_bp.route("/shop/<int:tenant_id>")
def shop(tenant_id: int):
    tenant = _service.get_tenant_public(tenant_id)
    if tenant is None:
        abort(404)
    products = _service.list_public_products(tenant_id)
    variants_by_product = _service.list_public_variants_by_product(tenant_id)
    return render_template(
        "etown/shop.html",
        tenant=tenant,
        products=products,
        variants_by_product=variants_by_product,
    )


@etown_bp.route("/shop/<int:tenant_id>/order", methods=["POST"])
def place_order(tenant_id: int):
    tenant = _service.get_tenant_public(tenant_id)
    if tenant is None:
        abort(404)
    lines = _parse_order_lines(request.form)
    try:
        order_id = _service.create_order(
            tenant_id,
            lines,
            guest_name=request.form.get("guest_name", ""),
            guest_phone=request.form.get("guest_phone", ""),
            notes=request.form.get("notes", ""),
            delivery_line1=request.form.get("delivery_line1", ""),
            delivery_line2=request.form.get("delivery_line2", ""),
            delivery_city=request.form.get("delivery_city", ""),
            delivery_notes=request.form.get("delivery_notes", ""),
        )
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("etown.shop", tenant_id=tenant_id))
    flash(f"Order placed. Reference #{order_id}. The store will contact you to confirm.", "success")
    return redirect(url_for("etown.shop", tenant_id=tenant_id))


@etown_bp.route("/shop/<int:tenant_id>/order.json", methods=["POST"])
def place_order_json(tenant_id: int):
    tenant = _service.get_tenant_public(tenant_id)
    if tenant is None:
        return jsonify(error="Store not found"), 404
    payload = request.get_json(silent=True) or {}
    raw_items = payload.get("items") or []
    lines: list[OrderLineIn] = []
    for item in raw_items:
        try:
            pid = int(item.get("product_id"))
            qty = int(item.get("quantity", 0))
            vid = item.get("variant_id")
            variant_id = int(vid) if vid is not None and str(vid).strip() != "" else None
        except (TypeError, ValueError):
            continue
        lines.append(OrderLineIn(product_id=pid, quantity=qty, variant_id=variant_id))
    try:
        order_id = _service.create_order(
            tenant_id,
            lines,
            guest_name=str(payload.get("guest_name", "")),
            guest_phone=str(payload.get("guest_phone", "")),
            notes=str(payload.get("notes", "")),
            delivery_line1=str(payload.get("delivery_line1", "")),
            delivery_line2=str(payload.get("delivery_line2", "")),
            delivery_city=str(payload.get("delivery_city", "")),
            delivery_notes=str(payload.get("delivery_notes", "")),
        )
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    return jsonify(ok=True, order_id=order_id), 201
