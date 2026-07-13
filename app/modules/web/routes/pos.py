from __future__ import annotations

import json

from flask import Flask, flash, g, redirect, render_template, request, session, url_for

from app.core.auth import get_current_user, require_recent_reauth, session_user_id
from app.core.db import get_connection
from app.core.db_integrity import DB_INTEGRITY_ERRORS
from app.core.delivery.integration_service import DeliveryIntegrationService
from app.core.subscription.entitlements import assert_demo_allows_checkout
from app.core.flask_config import BUSINESS_NAME, CURRENCY_CODE, DISCOUNT_PRESETS, VAT_RATE
from app.core.pos_payment_model import compute_shift_tender_totals
from app.core.pos_finalize import finalize_pos_sale, find_sale_by_idempotency_key
from app.core.pos_profiles import get_profile, normalize_pos_profile_id
from app.core.helpers import peso
from app.modules.storefront.storefront_service import StorefrontService
from app.modules.users.decorators import roles_required
from app.modules.web import queries as web_queries



def register_pos_routes(app: Flask, *, storefront_service: StorefrontService) -> None:
    @app.route("/")
    def pos() -> str:
        user = get_current_user()
        if g.tenant is not None and user is None:
            return storefront_service.render_storefront(g.tenant.id, request.args.get("theme", ""))

        if user is None:
            flash("Log in first to continue.", "error")
            return redirect(url_for("login", next=request.path))

        if user["role"] not in {"owner", "admin", "cashier", "super_admin"}:
            flash("You do not have permission to access that page.", "error")
            return redirect(url_for("login"))

        products = web_queries.fetch_pos_products()
        variants_map = web_queries.fetch_variants_by_product()
        low_stock_count = sum(1 for product in products if product["stock"] <= product["reorder_level"])
        tid = session.get("tenant_id")
        pos_profile_meta: dict | None = None
        with get_connection() as conn:
            today_stats = conn.execute(
                f"SELECT COALESCE(SUM(total), 0) AS revenue, COUNT(*) AS orders "
                f"FROM sales WHERE {web_queries.sql_date_column_eq_today('created_at')} AND status = 'completed'"
            ).fetchone()
            if tid:
                prow = conn.execute("SELECT pos_profile FROM tenants WHERE id = ?", (tid,)).fetchone()
                if prow is not None:
                    pos_profile_meta = get_profile(prow.get("pos_profile"))
        store_display_name = g.tenant.name if g.tenant is not None else BUSINESS_NAME
        pos_display_channel = f"veyron_pos_dual_t{session.get('tenant_id') or 0}_u{session.get('user_id') or 0}"
        return render_template(
            "pos.html",
            products=products,
            product_categories=web_queries.build_pos_categories(products),
            variants_map=variants_map,
            today_revenue=today_stats["revenue"],
            today_orders=today_stats["orders"],
            low_stock_count=low_stock_count,
            upcoming_products=web_queries.fetch_upcoming_products(),
            quick_picks=web_queries.fetch_quick_pick_products(),
            open_cash_shift=web_queries.fetch_open_cash_shift(),
            recent_cash_shifts=web_queries.fetch_recent_cash_shifts(),
            settings=web_queries.fetch_app_settings(),
            store_display_name=store_display_name,
            pos_display_channel=pos_display_channel,
            pos_profile_meta=pos_profile_meta,
        )


    @app.route("/pos/customer-display")
    def pos_customer_display() -> str:
        """Second monitor: line items and totals only (synced from cashier POS in the same browser)."""
        user = get_current_user()
        if user is None:
            flash("Log in first to continue.", "error")
            return redirect(url_for("login", next=request.path))
        if user["role"] not in {"owner", "admin", "cashier", "super_admin"}:
            flash("You do not have permission to access that page.", "error")
            return redirect(url_for("login"))
        store_display_name = g.tenant.name if g.tenant is not None else BUSINESS_NAME
        pos_display_channel = f"veyron_pos_dual_t{session.get('tenant_id') or 0}_u{session.get('user_id') or 0}"
        return render_template(
            "pos_customer_display.html",
            store_display_name=store_display_name,
            pos_display_channel=pos_display_channel,
        )


    @app.route("/pos/shift/open", methods=["POST"])
    @roles_required("tenant_admin", "staff")
    def open_cash_shift():
        opening_cash_raw = request.form.get("opening_cash", "0").strip()
        try:
            opening_cash = round(float(opening_cash_raw or 0), 2)
        except ValueError:
            flash("Opening cash must be a valid amount.", "error")
            return redirect(url_for("pos"))
        if opening_cash < 0:
            flash("Opening cash cannot be negative.", "error")
            return redirect(url_for("pos"))

        tenant_id = session.get("tenant_id")
        user_id = session_user_id()
        if not tenant_id or not user_id:
            flash("Unable to open shift. Please sign in again.", "error")
            return redirect(url_for("login"))

        with get_connection() as connection:
            existing = connection.execute(
                "SELECT id FROM cash_register_shifts WHERE tenant_id = ? AND cashier_user_id = ? AND status = 'open' LIMIT 1",
                (tenant_id, user_id),
            ).fetchone()
            if existing is not None:
                flash("You already have an open cash shift.", "warning")
                return redirect(url_for("pos"))
            shift_row = connection.execute(
                """
                INSERT INTO cash_register_shifts (tenant_id, cashier_user_id, opening_cash, expected_cash, status)
                VALUES (?, ?, ?, ?, 'open')
                RETURNING id
                """,
                (tenant_id, user_id, opening_cash, opening_cash),
            ).fetchone()
            web_queries.log_audit(connection, "open_shift", "cash_shift", shift_row["id"], f"Cash shift opened with PHP {opening_cash:.2f}")

        flash("Cash shift opened.", "success")
        return redirect(url_for("pos"))


    @app.route("/pos/shift/close", methods=["POST"])
    @roles_required("tenant_admin", "staff")
    @require_recent_reauth()
    def close_cash_shift():
        shift_id_raw = request.form.get("shift_id", "").strip()
        actual_cash_raw = request.form.get("actual_cash", "").strip()
        closing_note = request.form.get("closing_note", "").strip()
        try:
            shift_id = int(shift_id_raw)
            actual_cash = round(float(actual_cash_raw or 0), 2)
        except ValueError:
            flash("Invalid shift or cash amount.", "error")
            return redirect(url_for("pos"))
        if actual_cash < 0:
            flash("Actual cash cannot be negative.", "error")
            return redirect(url_for("pos"))

        tenant_id = session.get("tenant_id")
        user_id = session_user_id()
        if not tenant_id or not user_id:
            flash("Unable to close shift. Please sign in again.", "error")
            return redirect(url_for("login"))

        with get_connection() as connection:
            shift = connection.execute(
                """
                SELECT id, opening_cash, opened_at, status
                FROM cash_register_shifts
                WHERE id = ? AND tenant_id = ? AND cashier_user_id = ?
                """,
                (shift_id, tenant_id, user_id),
            ).fetchone()
            if shift is None or shift["status"] != "open":
                flash("Open cash shift not found.", "error")
                return redirect(url_for("pos"))

            tenders = compute_shift_tender_totals(
                connection,
                shift_id=shift_id,
                tenant_id=int(tenant_id),
                cashier_user_id=int(user_id),
            )
            opening = round(float(shift["opening_cash"]), 2)
            expected_cash = round(opening + tenders["cash"], 2)
            variance = round(actual_cash - expected_cash, 2)
            breakdown_json = json.dumps(tenders["breakdown"], sort_keys=True)

            connection.execute(
                """
                UPDATE cash_register_shifts
                SET expected_cash = ?, actual_cash = ?, variance = ?, status = 'closed',
                    closed_at = CURRENT_TIMESTAMP, closing_note = ?,
                    expected_card_total = ?, expected_wallet_total = ?, expected_other_total = ?,
                    tender_breakdown_json = ?
                WHERE id = ?
                """,
                (
                    expected_cash,
                    actual_cash,
                    variance,
                    closing_note,
                    tenders["card"],
                    tenders["wallet"],
                    tenders["other"],
                    breakdown_json,
                    shift_id,
                ),
            )
            web_queries.log_audit(
                connection,
                "close_shift",
                "cash_shift",
                shift_id,
                "Shift closed: cash_expected={:.2f}, actual={:.2f}, cash_var={:.2f}; "
                "card={:.2f}, wallet={:.2f}, other={:.2f}; breakdown={}".format(
                    expected_cash,
                    actual_cash,
                    variance,
                    tenders["card"],
                    tenders["wallet"],
                    tenders["other"],
                    breakdown_json,
                ),
            )

        if variance != 0:
            flash(f"Shift closed with variance: {peso(variance)}", "warning")
        else:
            flash("Shift closed and reconciled.", "success")
        return redirect(url_for("pos"))


    @app.route("/checkout", methods=["POST"])
    @roles_required("tenant_admin", "staff")
    def checkout():
        product_ids = request.form.getlist("product_id")
        variant_ids = request.form.getlist("variant_id")
        quantities = request.form.getlist("quantity")
        payment_method = request.form.get("payment_method", "Card")
        discount_type = request.form.get("discount_type", "none").strip().lower()
        discount_note = request.form.get("discount_note", "").strip()
        raw_custom_discount_rate = request.form.get("custom_discount_rate", "0").strip()
        raw_service_reference = (request.form.get("service_reference") or "").strip()[:120]
        idem_raw = (request.form.get("idempotency_key") or "").strip()[:120] or None

        tenant_id_check = session.get("tenant_id")
        if not tenant_id_check:
            flash("Unable to checkout without a store context.", "error")
            return redirect(url_for("login"))

        cart: list[dict[str, object]] = []
        total = 0.0
        tenant_delivery_on = False
        delivery_request = request.form.get("delivery_order") == "1"
        delivery_address = request.form.get("delivery_address", "").strip()
        delivery_instructions = request.form.get("delivery_instructions", "").strip()

        try:
            with get_connection() as connection:
                tenant_pos_profile = "cafe_bakery"
                tp_row = connection.execute(
                    "SELECT pos_profile FROM tenants WHERE id = ?",
                    (int(tenant_id_check),),
                ).fetchone()
                if tp_row is not None:
                    tenant_pos_profile = normalize_pos_profile_id(tp_row.get("pos_profile"))
                try:
                    assert_demo_allows_checkout(connection, int(tenant_id_check))
                except ValueError as exc:
                    flash(str(exc), "error")
                    return redirect(url_for("pos"))
                service_reference = (
                    raw_service_reference if tenant_pos_profile == "restaurant_table_service" else ""
                )

                for idx, (raw_product_id, raw_quantity) in enumerate(zip(product_ids, quantities)):
                    quantity = int(raw_quantity or 0)
                    if quantity <= 0:
                        continue

                    raw_variant_id = variant_ids[idx] if idx < len(variant_ids) else ""
                    variant_id = int(raw_variant_id) if raw_variant_id else None

                    product = connection.execute(
                        """
                        SELECT id, name, sku, price, stock, status
                        FROM products
                        WHERE id = ?
                        """,
                        (raw_product_id,),
                    ).fetchone()

                    if product is None or product["status"] != "active":
                        continue

                    if variant_id:
                        variant = connection.execute(
                            "SELECT id, name, sku_suffix, price, stock FROM product_variants WHERE id = ? AND product_id = ? AND is_active = 1",
                            (variant_id, product["id"]),
                        ).fetchone()
                        if variant is None:
                            continue
                        effective_price = variant["price"]
                        effective_stock = variant["stock"]
                        display_name = f"{product['name']} ({variant['name']})"
                        effective_sku = f"{product['sku']}{variant['sku_suffix']}" if variant["sku_suffix"] else product["sku"]
                    else:
                        effective_price = product["price"]
                        effective_stock = product["stock"]
                        display_name = product["name"]
                        effective_sku = product["sku"]

                    if quantity > effective_stock:
                        flash(f"Only {effective_stock} units of {display_name} are available.", "error")
                        return redirect(url_for("pos"))

                    line_total = round(effective_price * quantity, 2)
                    cart.append(
                        {
                            "id": product["id"],
                            "variant_id": variant_id,
                            "name": display_name,
                            "sku": effective_sku,
                            "quantity": quantity,
                            "unit_price": effective_price,
                            "line_total": line_total,
                        }
                    )

                if not cart:
                    flash("Choose at least one product before checkout.", "error")
                    return redirect(url_for("pos"))

                subtotal = round(sum(item["line_total"] for item in cart), 2)
                if discount_type not in DISCOUNT_PRESETS:
                    flash("Choose a valid discount type.", "error")
                    return redirect(url_for("pos"))

                if discount_type == "custom":
                    try:
                        discount_rate = round(float(raw_custom_discount_rate) / 100, 4)
                    except ValueError:
                        flash("Custom discount must be a valid percentage.", "error")
                        return redirect(url_for("pos"))
                else:
                    discount_rate = DISCOUNT_PRESETS[discount_type] or 0.0

                if discount_rate < 0 or discount_rate > 1:
                    flash("Discount must be between 0% and 100%.", "error")
                    return redirect(url_for("pos"))

                discount_amount = round(subtotal * discount_rate, 2)
                discounted_subtotal = round(subtotal - discount_amount, 2)
                tax = round(discounted_subtotal * VAT_RATE, 2)
                total = round(discounted_subtotal + tax, 2)

                open_shift = web_queries.fetch_open_cash_shift()
                cash_shift_id = open_shift["id"] if open_shift else None

                sale_id, _created = finalize_pos_sale(
                    connection,
                    tenant_id=int(tenant_id_check),
                    cashier_user_id=session_user_id(),
                    cart=cart,
                    subtotal=subtotal,
                    discount_type=discount_type,
                    discount_rate=discount_rate,
                    discount_amount=discount_amount,
                    discount_note=discount_note,
                    service_reference=service_reference,
                    tax=tax,
                    total=total,
                    payment_method=payment_method,
                    cash_shift_id=cash_shift_id,
                    idempotency_key=idem_raw,
                )

                if tenant_id_check:
                    trow = connection.execute(
                        "SELECT delivery_enabled FROM tenants WHERE id = ?",
                        (tenant_id_check,),
                    ).fetchone()
                    tenant_delivery_on = bool(trow and trow["delivery_enabled"])
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("pos"))
        except DB_INTEGRITY_ERRORS:
            if idem_raw:
                with get_connection() as c2:
                    dup_id = find_sale_by_idempotency_key(c2, int(tenant_id_check), idem_raw)
                if dup_id is not None:
                    return redirect(url_for("receipt", sale_id=dup_id))
            flash("Checkout could not be completed.", "error")
            return redirect(url_for("pos"))

        if delivery_request and tenant_delivery_on:
            try:
                delivery_integration = DeliveryIntegrationService()
                delivery_integration.create_delivery_order_if_requested(
                    tenant_id=tenant_id_check,
                    order_id=sale_id,
                    delivery_enabled=True,
                    delivery_address=delivery_address,
                    delivery_instructions=delivery_instructions,
                    order_total=total,
                )
            except Exception as exc:
                flash(f"Delivery order not created: {str(exc)}", "warning")
        elif delivery_request and not tenant_delivery_on:
            flash("Delivery is not enabled for this tenant.", "warning")

        return redirect(url_for("receipt", sale_id=sale_id))


    @app.route("/receipt/<int:sale_id>")
    @roles_required("tenant_admin", "staff")
    def receipt(sale_id: int) -> str:
        with get_connection() as connection:
            sale = connection.execute(
                """
                SELECT id, created_at, subtotal, discount_type, discount_rate, discount_amount, discount_note,
                    service_reference, tax, total, payment_method, status
                FROM sales WHERE id = ?
                """,
                (sale_id,),
            ).fetchone()
            items = connection.execute(
                """
                SELECT
                    COALESCE(NULLIF(trim(si.product_name), ''), p.name, '') AS name,
                    COALESCE(NULLIF(trim(si.sku), ''), p.sku, '') AS sku,
                    si.quantity,
                    si.unit_price,
                    si.line_total,
                    NULL AS variant_name,
                    NULL AS variant_sku_suffix
                FROM sale_items si
                LEFT JOIN products p ON p.id = si.product_id AND p.tenant_id = si.tenant_id
                WHERE si.sale_id = ?
                ORDER BY si.id ASC
                """,
                (sale_id,),
            ).fetchall()

        if sale is None:
            flash("Receipt not found.", "error")
            return redirect(url_for("pos"))

        sale_dict = dict(sale)
        line_dicts = [dict(row) for row in items]
        tenant_id = session.get("tenant_id")
        display_business = BUSINESS_NAME
        if tenant_id:
            with get_connection() as connection:
                trow = connection.execute("SELECT name FROM tenants WHERE id = ?", (tenant_id,)).fetchone()
                if trow is not None and trow["name"]:
                    display_business = trow["name"]

        receipt_payload = {
            "schema": "veyron.receipt/1",
            "business_name": display_business,
            "currency": CURRENCY_CODE,
            "sale": sale_dict,
            "lines": line_dicts,
        }

        return render_template(
            "receipt.html",
            sale=sale,
            items=items,
            settings=web_queries.fetch_app_settings(),
            receipt_payload=receipt_payload,
        )


