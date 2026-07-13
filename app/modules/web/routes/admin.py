from __future__ import annotations

from flask import Flask, abort, flash, render_template, request, session
from werkzeug.security import generate_password_hash

from app.core.auth import login_required, require_recent_reauth
from app.core.constants import ALLOWED_PRODUCT_STATUSES
from app.core.db import get_connection
from app.core.db_integrity import DB_INTEGRITY_ERRORS
from app.core.flask_config import ALLOWED_USER_ROLES
from app.core.helpers import normalize_lookup_name
from app.modules.products.product_service import ProductService
from app.modules.users.decorators import permission_required, roles_required
from app.modules.web import queries as web_queries



def register_admin_routes(app: Flask, *, product_service: ProductService) -> None:
    @app.route("/admin/categories/add", methods=["POST"])
    @roles_required("tenant_admin")
    def add_category():
        name = normalize_lookup_name(request.form.get("name", ""))
        if not name:
            flash("Category name is required.", "error")
            return web_queries.redirect_to_admin("categories")

        try:
            with get_connection() as connection:
                next_sort_order = connection.execute(
                    "SELECT COALESCE(MAX(sort_order), 0) + 1 AS next_value FROM categories"
                ).fetchone()["next_value"]
                category_row = connection.execute(
                    "INSERT INTO categories (name, sort_order) VALUES (?, ?) RETURNING id",
                    (name, next_sort_order),
                ).fetchone()
                web_queries.log_audit(connection, "create", "category", category_row["id"], f"Category created: {name}")
            flash("Category added.", "success")
        except DB_INTEGRITY_ERRORS:
            flash("That category already exists.", "error")
        return web_queries.redirect_to_admin("categories")


    @app.route("/admin/categories/move", methods=["POST"])
    @roles_required("tenant_admin")
    def move_category():
        category_id = request.form.get("category_id", "").strip()
        move_to = request.form.get("move_to", "").strip()
        if not category_id or not move_to:
            flash("Select a category and a destination number.", "error")
            return web_queries.redirect_to_admin("categories")

        try:
            category_id_int = int(category_id)
            move_to_int = int(move_to)
        except ValueError:
            flash("Category move position must be a valid whole number.", "error")
            return web_queries.redirect_to_admin("categories")

        if move_to_int <= 0:
            flash("Category move position must be 1 or greater.", "error")
            return web_queries.redirect_to_admin("categories")

        with get_connection() as connection:
            category = connection.execute(
                "SELECT id, name FROM categories WHERE id = ?",
                (category_id_int,),
            ).fetchone()
            if category is None:
                flash("Category not found.", "error")
                return web_queries.redirect_to_admin("categories")

            final_position = web_queries.move_category_to_position(connection, category_id_int, move_to_int)
            web_queries.log_audit(connection, "move", "category", category_id_int, f"Moved category to no. {final_position}")

        flash(f"{category['name']} moved to no. {final_position}.", "success")
        return web_queries.redirect_to_admin("categories")


    @app.route("/admin/brands/add", methods=["POST"])
    @roles_required("tenant_admin")
    def add_brand():
        name = normalize_lookup_name(request.form.get("name", ""))
        if not name:
            flash("Brand name is required.", "error")
            return web_queries.redirect_to_admin("brands")

        try:
            with get_connection() as connection:
                brand_row = connection.execute("INSERT INTO brands (name) VALUES (?) RETURNING id", (name,)).fetchone()
                web_queries.log_audit(connection, "create", "brand", brand_row["id"], f"Brand created: {name}")
            flash("Brand added.", "success")
        except DB_INTEGRITY_ERRORS:
            flash("That brand already exists.", "error")
        return web_queries.redirect_to_admin("brands")


    @app.route("/admin/units/add", methods=["POST"])
    @roles_required("tenant_admin")
    def add_unit():
        name = normalize_lookup_name(request.form.get("name", ""))
        if not name:
            flash("Unit name is required.", "error")
            return web_queries.redirect_to_admin("units")

        try:
            with get_connection() as connection:
                unit_row = connection.execute("INSERT INTO units (name) VALUES (?) RETURNING id", (name,)).fetchone()
                web_queries.log_audit(connection, "create", "unit", unit_row["id"], f"Unit created: {name}")
            flash("Unit added.", "success")
        except DB_INTEGRITY_ERRORS:
            flash("That unit already exists.", "error")
        return web_queries.redirect_to_admin("units")


    @app.route("/admin/products/add", methods=["POST"])
    @roles_required("tenant_admin")
    def add_product():
        name = normalize_lookup_name(request.form.get("name", ""))
        raw_price = request.form.get("price", "").strip()
        raw_cost = request.form.get("cost", "").strip()
        raw_stock = request.form.get("stock", "").strip()
        raw_reorder_level = request.form.get("reorder_level", "").strip()
        category_id = request.form.get("category_id", "").strip()
        brand_id = request.form.get("brand_id", "").strip()
        unit_id = request.form.get("unit_id", "").strip()
        status = request.form.get("status", "active").strip().lower()
        image_file = request.files.get("image")

        if not all([name, raw_price, raw_cost, raw_stock, raw_reorder_level, category_id, brand_id, unit_id]):
            flash("Complete all product fields before saving.", "error")
            return web_queries.redirect_to_admin("products")

        try:
            price = round(float(raw_price), 2)
            cost = round(float(raw_cost), 2)
            stock = int(raw_stock)
            reorder_level = int(raw_reorder_level)
            category_id_int = int(category_id)
            brand_id_int = int(brand_id)
            unit_id_int = int(unit_id)
        except ValueError:
            flash("Price, cost, stock, and reorder level must be valid values.", "error")
            return web_queries.redirect_to_admin("products")

        if status not in ALLOWED_PRODUCT_STATUSES:
            flash("Choose a valid product status.", "error")
            return web_queries.redirect_to_admin("products")
        if price < 0 or cost < 0 or stock < 0 or reorder_level < 0:
            flash("Price, cost, stock, and reorder level must be zero or greater.", "error")
            return web_queries.redirect_to_admin("products")

        try:
            product_id = product_service.add_product(
                name=name,
                price=price,
                cost=cost,
                stock=stock,
                reorder_level=reorder_level,
                category_id=category_id_int,
                brand_id=brand_id_int,
                unit_id=unit_id_int,
                status=status,
                image_file=image_file,
            )
        except ValueError as exc:
            flash(str(exc), "error")
            return web_queries.redirect_to_admin("products")
        except Exception:
            flash("Unable to save product. Please review input values and try again.", "error")
            return web_queries.redirect_to_admin("products")

        with get_connection() as connection:
            web_queries.log_audit(connection, "create", "product", product_id, f"Product created: {name}")

        flash("Product added.", "success")
        return web_queries.redirect_to_admin("products")


    @app.route("/admin/products/update", methods=["POST"])
    @roles_required("tenant_admin")
    def update_product():
        product_id = request.form.get("product_id", "").strip()
        name = normalize_lookup_name(request.form.get("name", ""))
        raw_price = request.form.get("price", "").strip()
        raw_cost = request.form.get("cost", "").strip()
        raw_reorder_level = request.form.get("reorder_level", "").strip()
        category_id = request.form.get("category_id", "").strip()
        brand_id = request.form.get("brand_id", "").strip()
        unit_id = request.form.get("unit_id", "").strip()
        status = request.form.get("status", "active").strip().lower()
        image_file = request.files.get("image")

        if not all([product_id, name, raw_price, raw_cost, raw_reorder_level, category_id, brand_id, unit_id]):
            flash("Complete all product fields before updating.", "error")
            return web_queries.redirect_to_admin("products")

        try:
            product_id_int = int(product_id)
            price = round(float(raw_price), 2)
            cost = round(float(raw_cost), 2)
            reorder_level = int(raw_reorder_level)
            category_id_int = int(category_id)
            brand_id_int = int(brand_id)
            unit_id_int = int(unit_id)
        except ValueError:
            flash("Product update fields contain invalid values.", "error")
            return web_queries.redirect_to_admin("products")

        if status not in ALLOWED_PRODUCT_STATUSES:
            flash("Choose a valid product status.", "error")
            return web_queries.redirect_to_admin("products")
        if price < 0 or cost < 0 or reorder_level < 0:
            flash("Price, cost, and reorder level must be zero or greater.", "error")
            return web_queries.redirect_to_admin("products")

        try:
            product_service.update_product(
                product_id=product_id_int,
                name=name,
                price=price,
                cost=cost,
                reorder_level=reorder_level,
                category_id=category_id_int,
                brand_id=brand_id_int,
                unit_id=unit_id_int,
                status=status,
                image_file=image_file,
            )
        except ValueError as exc:
            flash(str(exc), "error")
            return web_queries.redirect_to_admin("products")
        except Exception:
            flash("Unable to update product. Please review input values and try again.", "error")
            return web_queries.redirect_to_admin("products")

        with get_connection() as connection:
            web_queries.log_audit(connection, "update", "product", product_id_int, f"Product updated: {name}")

        flash("Product updated.", "success")
        return web_queries.redirect_to_admin("products")


    @app.route("/admin/products/move", methods=["POST"])
    @roles_required("tenant_admin")
    def move_product():
        product_id = request.form.get("product_id", "").strip()
        move_to = request.form.get("move_to", "").strip()
        if not product_id or not move_to:
            flash("Select a product and a destination number.", "error")
            return web_queries.redirect_to_admin("products")

        try:
            product_id_int = int(product_id)
            move_to_int = int(move_to)
        except ValueError:
            flash("Move position must be a valid whole number.", "error")
            return web_queries.redirect_to_admin("products")

        if move_to_int <= 0:
            flash("Move position must be 1 or greater.", "error")
            return web_queries.redirect_to_admin("products")

        try:
            final_position = product_service.move_product(product_id_int, move_to_int)
        except ValueError as exc:
            flash(str(exc), "error")
            return web_queries.redirect_to_admin("products")
        except Exception:
            flash("Unable to move product. Please try again.", "error")
            return web_queries.redirect_to_admin("products")

        with get_connection() as connection:
            product = connection.execute(
                "SELECT id, name FROM products WHERE id = ?",
                (product_id_int,),
            ).fetchone()
            web_queries.log_audit(connection, "move", "product", product_id_int, f"Moved product to no. {final_position}")

        flash(f"{product['name']} moved to no. {final_position}.", "success")
        return web_queries.redirect_to_admin("products")


    @app.route("/admin/products/remove", methods=["POST"])
    @roles_required("tenant_admin")
    def remove_product():
        product_id = request.form.get("product_id", "").strip()
        if not product_id:
            flash("Select a product to remove.", "error")
            return web_queries.redirect_to_admin("products")

        try:
            product_id_int = int(product_id)
        except ValueError:
            flash("Invalid product selection.", "error")
            return web_queries.redirect_to_admin("products")

        try:
            message, was_archived = product_service.remove_product(product_id_int)
        except ValueError as exc:
            flash(str(exc), "error")
            return web_queries.redirect_to_admin("products")
        except Exception:
            flash("Unable to remove product. Please try again.", "error")
            return web_queries.redirect_to_admin("products")

        with get_connection() as connection:
            if was_archived:
                web_queries.log_audit(connection, "archive", "product", product_id_int, "Product archived instead of deleted")
            else:
                web_queries.log_audit(connection, "delete", "product", product_id_int, "Product permanently deleted")

        flash(message, "success")
        return web_queries.redirect_to_admin("products")


    @app.route("/admin/variants/add", methods=["POST"])
    @roles_required("tenant_admin")
    def add_variant():
        product_id = request.form.get("product_id", "").strip()
        name = normalize_lookup_name(request.form.get("variant_name", ""))
        raw_price = request.form.get("variant_price", "").strip()
        raw_cost = request.form.get("variant_cost", "").strip()
        raw_stock = request.form.get("variant_stock", "").strip()
        raw_reorder = request.form.get("variant_reorder_level", "").strip()
        sku_suffix = request.form.get("variant_sku_suffix", "").strip()

        if not all([product_id, name, raw_price, raw_cost, raw_stock, raw_reorder]):
            flash("Complete all variant fields before saving.", "error")
            return web_queries.redirect_to_admin("products")

        try:
            product_id_int = int(product_id)
            price = round(float(raw_price), 2)
            cost = round(float(raw_cost), 2)
            stock = int(raw_stock)
            reorder_level = int(raw_reorder)
        except ValueError:
            flash("Variant fields contain invalid values.", "error")
            return web_queries.redirect_to_admin("products")

        if price < 0 or cost < 0 or stock < 0 or reorder_level < 0:
            flash("Variant price, cost, stock, and reorder level must be zero or greater.", "error")
            return web_queries.redirect_to_admin("products")

        with get_connection() as connection:
            product = connection.execute("SELECT id, name FROM products WHERE id = ?", (product_id_int,)).fetchone()
            if product is None:
                flash("Product not found.", "error")
                return web_queries.redirect_to_admin("products")

            next_sort = connection.execute(
                "SELECT COALESCE(MAX(sort_order), 0) + 1 AS next_value FROM product_variants WHERE product_id = ?",
                (product_id_int,),
            ).fetchone()["next_value"]

            variant_row = connection.execute(
                """
                INSERT INTO product_variants (product_id, name, sku_suffix, price, cost, stock, reorder_level, sort_order)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                RETURNING id
                """,
                (product_id_int, name, sku_suffix, price, cost, stock, reorder_level, next_sort),
            ).fetchone()
            web_queries.log_stock_movement(connection, product_id_int, stock, "opening_balance", variant_row["id"])
            web_queries.log_audit(connection, "create", "variant", variant_row["id"], f"Variant '{name}' added to {product['name']}")

        flash(f"Variant '{name}' added.", "success")
        return web_queries.redirect_to_admin("products")


    @app.route("/admin/variants/update", methods=["POST"])
    @roles_required("tenant_admin")
    def update_variant():
        variant_id = request.form.get("variant_id", "").strip()
        name = normalize_lookup_name(request.form.get("variant_name", ""))
        raw_price = request.form.get("variant_price", "").strip()
        raw_cost = request.form.get("variant_cost", "").strip()
        raw_reorder = request.form.get("variant_reorder_level", "").strip()
        sku_suffix = request.form.get("variant_sku_suffix", "").strip()
        is_active = request.form.get("variant_is_active", "1").strip()

        if not all([variant_id, name, raw_price, raw_cost, raw_reorder]):
            flash("Complete all variant fields before updating.", "error")
            return web_queries.redirect_to_admin("products")

        try:
            variant_id_int = int(variant_id)
            price = round(float(raw_price), 2)
            cost = round(float(raw_cost), 2)
            reorder_level = int(raw_reorder)
            is_active_int = int(is_active)
        except ValueError:
            flash("Variant fields contain invalid values.", "error")
            return web_queries.redirect_to_admin("products")

        if price < 0 or cost < 0 or reorder_level < 0:
            flash("Variant price, cost, and reorder level must be zero or greater.", "error")
            return web_queries.redirect_to_admin("products")

        with get_connection() as connection:
            variant = connection.execute("SELECT id, product_id FROM product_variants WHERE id = ?", (variant_id_int,)).fetchone()
            if variant is None:
                flash("Variant not found.", "error")
                return web_queries.redirect_to_admin("products")

            connection.execute(
                """
                UPDATE product_variants
                SET name = ?, sku_suffix = ?, price = ?, cost = ?, reorder_level = ?, is_active = ?
                WHERE id = ?
                """,
                (name, sku_suffix, price, cost, reorder_level, is_active_int, variant_id_int),
            )
            web_queries.log_audit(connection, "update", "variant", variant_id_int, f"Variant updated: {name}")

        flash("Variant updated.", "success")
        return web_queries.redirect_to_admin("products")


    @app.route("/admin/variants/remove", methods=["POST"])
    @roles_required("tenant_admin")
    def remove_variant():
        variant_id = request.form.get("variant_id", "").strip()
        if not variant_id:
            flash("Select a variant to remove.", "error")
            return web_queries.redirect_to_admin("products")

        try:
            variant_id_int = int(variant_id)
        except ValueError:
            flash("Invalid variant selection.", "error")
            return web_queries.redirect_to_admin("products")

        with get_connection() as connection:
            variant = connection.execute(
                "SELECT pv.id, pv.name, pv.product_id, pv.stock FROM product_variants pv WHERE pv.id = ?",
                (variant_id_int,),
            ).fetchone()
            if variant is None:
                flash("Variant not found.", "error")
                return web_queries.redirect_to_admin("products")

            sales_count = connection.execute(
                "SELECT COUNT(*) AS count FROM sale_items WHERE variant_id = ?",
                (variant_id_int,),
            ).fetchone()["count"]

            if variant["stock"] > 0 or sales_count > 0:
                connection.execute("UPDATE product_variants SET is_active = 0 WHERE id = ?", (variant_id_int,))
                web_queries.log_audit(connection, "archive", "variant", variant_id_int, f"Variant '{variant['name']}' archived")
                flash("Variant archived instead of deleted because it has stock or sales history.", "success")
            else:
                connection.execute("DELETE FROM stock_movements WHERE variant_id = ?", (variant_id_int,))
                connection.execute("DELETE FROM product_variants WHERE id = ?", (variant_id_int,))
                web_queries.log_audit(connection, "delete", "variant", variant_id_int, f"Variant '{variant['name']}' permanently deleted")
                flash("Variant removed.", "success")

        return web_queries.redirect_to_admin("products")


    @app.route("/admin/inventory/open-day", methods=["POST"])
    @roles_required("tenant_admin")
    def open_inventory_day():
        from datetime import date as _date

        today = _date.today().isoformat()
        opening_units_raw = request.form.get("opening_units", "").strip()
        with get_connection() as connection:
            existing = connection.execute(
                "SELECT id, status FROM daily_inventory_shifts WHERE shift_date = ?",
                (today,),
            ).fetchone()
            if existing:
                flash(f"Today's inventory shift is already {'open' if existing['status'] == 'open' else 'closed'}.", "error")
                return web_queries.redirect_to_inventory("stock-control")

            total_units = connection.execute("SELECT COALESCE(SUM(stock), 0) AS total FROM products WHERE status = 'active'").fetchone()["total"]
            if opening_units_raw:
                try:
                    manual_opening_units = int(opening_units_raw)
                except ValueError:
                    flash("Opening count must be a whole number.", "error")
                    return web_queries.redirect_to_inventory("stock-control")
                if manual_opening_units < 0:
                    flash("Opening count cannot be negative.", "error")
                    return web_queries.redirect_to_inventory("stock-control")
            else:
                manual_opening_units = int(total_units)
            shift = connection.execute(
                """
                INSERT INTO daily_inventory_shifts (shift_date, status, opened_by, opening_units, manual_opening_units)
                VALUES (?, 'open', ?, ?, ?)
                RETURNING id
                """,
                (today, session.get("user_id"), total_units, manual_opening_units),
            ).fetchone()
            shift_id = shift["id"]

            products = connection.execute("SELECT id, stock FROM products WHERE status = 'active'").fetchall()
            for p in products:
                connection.execute(
                    "INSERT INTO daily_shift_snapshots (shift_id, product_id, opening_stock) VALUES (?, ?, ?)",
                    (shift_id, p["id"], p["stock"]),
                )

            web_queries.log_audit(
                connection,
                "create",
                "daily_shift",
                shift_id,
                f"Opened inventory day for {today}: system_open={total_units}, manual_open={manual_opening_units}",
            )

        flash(
            f"Inventory day opened for {today}. System opening: {total_units}, manual opening: {manual_opening_units}.",
            "success",
        )
        return web_queries.redirect_to_inventory("stock-control")


    @app.route("/admin/inventory/close-day", methods=["POST"])
    @roles_required("tenant_admin")
    def close_inventory_day():
        shift_id_raw = request.form.get("shift_id", "").strip()
        notes = request.form.get("closing_notes", "").strip()[:300]
        manual_closing_raw = request.form.get("manual_closing_units", "").strip()
        damaged_raw = request.form.get("damaged_units", "").strip() or "0"
        wastage_raw = request.form.get("wastage_units", "").strip() or "0"

        try:
            shift_id_int = int(shift_id_raw)
            manual_closing_units = int(manual_closing_raw)
            damaged_units = int(damaged_raw)
            wastage_units = int(wastage_raw)
        except (ValueError, TypeError):
            flash("Invalid shift or inventory closing counts.", "error")
            return web_queries.redirect_to_inventory("stock-control")
        if manual_closing_units < 0 or damaged_units < 0 or wastage_units < 0:
            flash("Closing, damaged, and wastage counts must be zero or greater.", "error")
            return web_queries.redirect_to_inventory("stock-control")

        with get_connection() as connection:
            shift = connection.execute(
                "SELECT id, shift_date, opening_units FROM daily_inventory_shifts WHERE id = ? AND status = 'open'",
                (shift_id_int,),
            ).fetchone()
            if shift is None:
                flash("No open shift found to close.", "error")
                return web_queries.redirect_to_inventory("stock-control")

            closing_units = connection.execute("SELECT COALESCE(SUM(stock), 0) AS total FROM products WHERE status = 'active'").fetchone()["total"]

            products = connection.execute("SELECT id, stock FROM products WHERE status = 'active'").fetchall()
            for p in products:
                connection.execute(
                    "UPDATE daily_shift_snapshots SET closing_stock = ? WHERE shift_id = ? AND product_id = ?",
                    (p["stock"], shift_id_int, p["id"]),
                )

            units_sold = connection.execute(
                "SELECT COALESCE(SUM(si.quantity), 0) AS total FROM sale_items si JOIN sales s ON si.sale_id = s.id WHERE DATE(s.created_at) = ? AND s.status = 'completed'",
                (shift["shift_date"],),
            ).fetchone()["total"]

            units_received = connection.execute(
                "SELECT COALESCE(SUM(quantity_change), 0) AS total FROM stock_movements WHERE DATE(created_at) = ? AND quantity_change > 0 AND reason != 'sale'",
                (shift["shift_date"],),
            ).fetchone()["total"]

            units_adjusted = connection.execute(
                "SELECT COALESCE(SUM(quantity_change), 0) AS total FROM stock_movements WHERE DATE(created_at) = ? AND quantity_change < 0 AND reason != 'sale'",
                (shift["shift_date"],),
            ).fetchone()["total"]

            expected_closing_units = int(
                shift["opening_units"] + units_received + units_adjusted - units_sold - damaged_units - wastage_units
            )
            variance_units = manual_closing_units - expected_closing_units

            connection.execute(
                """UPDATE daily_inventory_shifts
                   SET status = 'closed', closed_at = CURRENT_TIMESTAMP, closed_by = ?,
                       closing_units = ?, manual_closing_units = ?, damaged_units = ?, wastage_units = ?,
                       expected_closing_units = ?, variance_units = ?,
                       units_sold = ?, units_received = ?, units_adjusted = ?, notes = ?
                   WHERE id = ?""",
                (
                    session.get("user_id"),
                    closing_units,
                    manual_closing_units,
                    damaged_units,
                    wastage_units,
                    expected_closing_units,
                    variance_units,
                    units_sold,
                    units_received,
                    units_adjusted,
                    notes,
                    shift_id_int,
                ),
            )

            web_queries.log_audit(connection, "update", "daily_shift", shift_id_int,
                      f"Closed day {shift['shift_date']}: system_close={closing_units}, manual_close={manual_closing_units}, expected={expected_closing_units}, variance={variance_units}, sold={units_sold}, damaged={damaged_units}, wastage={wastage_units}")

        if variance_units == 0:
            flash(
                f"Inventory day closed and tallied. Expected {expected_closing_units}, manual closing {manual_closing_units}.",
                "success",
            )
        else:
            flash(
                f"Inventory day closed with variance of {variance_units} unit(s). Expected {expected_closing_units}, manual closing {manual_closing_units}.",
                "warning",
            )
        return web_queries.redirect_to_inventory("stock-control")


    @app.route("/admin/inventory/adjust", methods=["POST"])
    @roles_required("tenant_admin")
    def adjust_inventory():
        product_id = request.form.get("product_id", "").strip()
        variant_id_raw = request.form.get("variant_id", "").strip()
        raw_quantity_change = request.form.get("quantity_change", "").strip()
        reason = request.form.get("reason", "manual_count").strip().lower()

        if not product_id or not raw_quantity_change:
            flash("Select a product and quantity adjustment.", "error")
            return web_queries.redirect_to_inventory("stock-control")

        try:
            product_id_int = int(product_id)
            quantity_change = int(raw_quantity_change)
            variant_id_int = int(variant_id_raw) if variant_id_raw else None
        except ValueError:
            flash("Inventory adjustments must use whole numbers.", "error")
            return web_queries.redirect_to_inventory("stock-control")

        if quantity_change == 0:
            flash("Adjustment quantity cannot be zero.", "error")
            return web_queries.redirect_to_inventory("stock-control")
        if reason not in ALLOWED_INVENTORY_REASONS - {"sale"}:
            flash("Choose a valid inventory reason.", "error")
            return web_queries.redirect_to_inventory("stock-control")

        with get_connection() as connection:
            product = connection.execute(
                "SELECT id, name, stock, reorder_level, status FROM products WHERE id = ?",
                (product_id_int,),
            ).fetchone()
            if product is None:
                flash("Product not found.", "error")
                return web_queries.redirect_to_inventory("stock-control")

            if variant_id_int:
                variant = connection.execute(
                    "SELECT id, name, stock FROM product_variants WHERE id = ? AND product_id = ?",
                    (variant_id_int, product_id_int),
                ).fetchone()
                if variant is None:
                    flash("Variant not found.", "error")
                    return web_queries.redirect_to_inventory("stock-control")
                updated_stock = variant["stock"] + quantity_change
                if updated_stock < 0:
                    flash("Adjustment would result in negative stock.", "error")
                    return web_queries.redirect_to_inventory("stock-control")
                connection.execute("UPDATE product_variants SET stock = ? WHERE id = ?", (updated_stock, variant_id_int))
                web_queries.log_stock_movement(connection, product_id_int, quantity_change, reason, variant_id_int)
                adjust_label = f"{product['name']} ({variant['name']})"
            else:
                updated_stock = product["stock"] + quantity_change
                if updated_stock < 0:
                    flash("Adjustment would result in negative stock.", "error")
                    return web_queries.redirect_to_inventory("stock-control")
                connection.execute(
                    """
                    UPDATE products
                    SET stock = ?,
                        last_restocked = CASE WHEN ? > 0 THEN CURRENT_TIMESTAMP ELSE last_restocked END
                    WHERE id = ?
                    """,
                    (updated_stock, quantity_change, product_id_int),
                )
                web_queries.log_stock_movement(connection, product_id_int, quantity_change, reason)
                adjust_label = product["name"]

            web_queries.maybe_create_adjustment_alert(connection, product, quantity_change, reason)
            web_queries.maybe_create_low_stock_alert(connection, product_id_int, f"inventory adjustment ({reason})")
            web_queries.log_audit(
                connection,
                "adjust",
                "inventory",
                product_id_int,
                f"Inventory adjusted by {quantity_change} for {adjust_label}, reason: {reason}",
            )

        flash("Inventory adjusted.", "success")
        return web_queries.redirect_to_inventory("stock-control")


    @app.route("/admin/inventory/reset-stock", methods=["POST"])
    @roles_required("tenant_admin")
    @require_recent_reauth()
    def reset_inventory_stock():
        with get_connection() as connection:
            product_summary = connection.execute(
                "SELECT COUNT(*) AS affected, COALESCE(SUM(stock), 0) AS units FROM products WHERE stock != 0"
            ).fetchone()
            variant_summary = connection.execute(
                "SELECT COUNT(*) AS affected, COALESCE(SUM(stock), 0) AS units FROM product_variants WHERE stock != 0"
            ).fetchone()

            products_affected = int(product_summary["affected"])
            variants_affected = int(variant_summary["affected"])
            units_cleared = int(product_summary["units"]) + int(variant_summary["units"])

            if products_affected == 0 and variants_affected == 0:
                flash("All stocks are already zero.", "warning")
                return web_queries.redirect_to_admin("products")

            connection.execute("UPDATE products SET stock = 0 WHERE stock != 0")
            connection.execute("UPDATE product_variants SET stock = 0 WHERE stock != 0")
            web_queries.log_audit(
                connection,
                "reset",
                "inventory",
                None,
                f"Bulk stock reset to zero. products={products_affected}, variants={variants_affected}, units_cleared={units_cleared}",
            )

        flash(
            f"Stock reset complete. Cleared {units_cleared} total unit(s) across {products_affected} product(s) and {variants_affected} variant(s).",
            "success",
        )
        return web_queries.redirect_to_admin("products")


    @app.route("/admin/users/add", methods=["POST"])
    @permission_required("manage_users")
    @require_recent_reauth()
    def add_user():
        full_name = normalize_lookup_name(request.form.get("full_name", ""))
        username = request.form.get("username", "").strip().lower()
        role = request.form.get("role", "cashier").strip().lower()
        language_override = request.form.get("language_override", "").strip().lower() or None
        pin = request.form.get("pin", "").strip()

        if not all([full_name, username, role, pin]):
            flash("Complete all user fields.", "error")
            return web_queries.redirect_to_admin("users")
        if role not in ALLOWED_USER_ROLES:
            flash("Choose a valid role.", "error")
            return web_queries.redirect_to_admin("users")

        try:
            with get_connection() as connection:
                user_row = connection.execute(
                    "INSERT INTO users (full_name, username, role, pin_hash, language_override) VALUES (?, ?, ?, ?, ?) RETURNING id",
                    (full_name, username, role, generate_password_hash(pin), language_override),
                ).fetchone()
                web_queries.log_audit(connection, "create", "user", user_row["id"], f"User created: {username}")
            flash("User added.", "success")
        except DB_INTEGRITY_ERRORS:
            flash("That username already exists.", "error")
        return web_queries.redirect_to_admin("users")


    @app.route("/admin/users/edit", methods=["POST"])
    @permission_required("manage_users")
    @require_recent_reauth()
    def edit_user():
        user_id = request.form.get("user_id")
        full_name = normalize_lookup_name(request.form.get("full_name", ""))
        username = request.form.get("username", "").strip().lower()
        role = request.form.get("role", "cashier").strip().lower()
        language_override = request.form.get("language_override", "").strip().lower() or None
        if not all([user_id, full_name, username, role]):
            flash("Complete all user fields.", "error")
            return web_queries.redirect_to_admin("users")
        if role not in ALLOWED_USER_ROLES:
            flash("Choose a valid role.", "error")
            return web_queries.redirect_to_admin("users")
        try:
            with get_connection() as connection:
                connection.execute(
                    "UPDATE users SET full_name = ?, username = ?, role = ?, language_override = ? WHERE id = ?",
                    (full_name, username, role, language_override, user_id),
                )
                web_queries.log_audit(connection, "edit", "user", user_id, f"User edited: {username}")
            flash("User updated.", "success")
        except DB_INTEGRITY_ERRORS:
            flash("That username already exists.", "error")
        return web_queries.redirect_to_admin("users")


    @app.route("/admin/users/deactivate", methods=["POST"])
    @permission_required("manage_users")
    @require_recent_reauth()
    def deactivate_user():
        user_id = request.form.get("user_id")
        if not user_id:
            flash("User not found.", "error")
            return web_queries.redirect_to_admin("users")
        with get_connection() as connection:
            connection.execute("UPDATE users SET is_active = 0 WHERE id = ?", (user_id,))
            web_queries.log_audit(connection, "deactivate", "user", user_id, "User deactivated.")
        flash("User deactivated.", "success")
        return web_queries.redirect_to_admin("users")


    @app.route("/admin/users/reactivate", methods=["POST"])
    @permission_required("manage_users")
    @require_recent_reauth()
    def reactivate_user():
        user_id = request.form.get("user_id")
        if not user_id:
            flash("User not found.", "error")
            return web_queries.redirect_to_admin("users")
        with get_connection() as connection:
            connection.execute("UPDATE users SET is_active = 1 WHERE id = ?", (user_id,))
            web_queries.log_audit(connection, "reactivate", "user", user_id, "User reactivated.")
        flash("User reactivated.", "success")
        return web_queries.redirect_to_admin("users")


    @app.route("/admin/users/reset_pin", methods=["POST"])
    @login_required("owner")
    @require_recent_reauth()
    def reset_user_pin():
        user_id = request.form.get("user_id")
        new_pin = request.form.get("new_pin", "").strip()
        if not user_id or not new_pin:
            flash("User and new PIN required.", "error")
            return web_queries.redirect_to_admin("users")
        with get_connection() as connection:
            connection.execute("UPDATE users SET pin_hash = ? WHERE id = ?", (generate_password_hash(new_pin), user_id))
            web_queries.log_audit(connection, "reset_pin", "user", user_id, "User PIN reset.")
        flash("User PIN reset.", "success")
        return web_queries.redirect_to_admin("users")


    @app.route("/admin/marketplace-orders")
    @roles_required("tenant_admin", "staff")
    def admin_marketplace_orders():
        tenant_id = session.get("tenant_id")
        if not tenant_id:
            abort(404)
        with get_connection() as connection:
            orders = connection.execute(
                """
                SELECT id, guest_name, guest_phone, status, subtotal, tax, total,
                       created_at, delivery_line1, delivery_city, delivery_notes, notes
                FROM orders
                WHERE tenant_id = ?
                ORDER BY created_at DESC
                LIMIT 100
                """,
                (tenant_id,),
            ).fetchall()
            order_list = [dict(row) for row in orders]
            oids = [o["id"] for o in order_list]
            items_by_order: dict[int, list[dict]] = {}
            if oids:
                placeholders = ",".join("?" * len(oids))
                item_rows = connection.execute(
                    f"""
                    SELECT oi.order_id, oi.quantity, oi.unit_price, oi.line_total,
                           p.name AS product_name,
                           COALESCE(pv.name, '') AS variant_name
                    FROM order_items oi
                    JOIN products p ON p.id = oi.product_id AND p.tenant_id = oi.tenant_id
                    LEFT JOIN product_variants pv ON pv.id = oi.variant_id AND pv.tenant_id = oi.tenant_id
                    WHERE oi.tenant_id = ? AND oi.order_id IN ({placeholders})
                    ORDER BY oi.id ASC
                    """,
                    (tenant_id, *oids),
                ).fetchall()
                for ir in item_rows:
                    oid = int(ir["order_id"])
                    items_by_order.setdefault(oid, []).append(dict(ir))
        return render_template(
            "admin_marketplace_orders.html",
            orders=order_list,
            items_by_order=items_by_order,
        )


