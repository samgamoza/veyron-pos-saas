from __future__ import annotations

from flask import Flask, flash, jsonify, request

from app.core.auth import require_recent_reauth, session_user_id
from app.core.constants import ALLOWED_INVENTORY_REASONS
from app.core.db import get_connection
from app.core.db_integrity import DB_INTEGRITY_ERRORS
from app.core.pos_refund import apply_sale_financial_reversal
from app.core.helpers import normalize_lookup_name
from app.core.inventory import log_stock_movement
from app.modules.users.decorators import roles_required
from app.modules.web import queries as web_queries



def register_inventory_routes(app: Flask) -> None:
    @app.route("/inventory/suppliers/add", methods=["POST"])
    @roles_required("tenant_admin")
    def add_supplier():
        name = normalize_lookup_name(request.form.get("name", ""))
        contact_person = normalize_lookup_name(request.form.get("contact_person", ""))
        phone = request.form.get("phone", "").strip()
        email = request.form.get("email", "").strip()
        notes = request.form.get("notes", "").strip()
        if not name:
            flash("Supplier name is required.", "error")
            return web_queries.redirect_to_inventory("suppliers")

        try:
            with get_connection() as connection:
                supplier_row = connection.execute(
                    """
                    INSERT INTO suppliers (name, contact_person, phone, email, notes)
                    VALUES (?, ?, ?, ?, ?)
                    RETURNING id
                    """,
                    (name, contact_person, phone, email, notes),
                ).fetchone()
                web_queries.log_audit(connection, "create", "supplier", supplier_row["id"], f"Supplier created: {name}")
            flash("Supplier added.", "success")
        except DB_INTEGRITY_ERRORS:
            flash("That supplier already exists.", "error")
        return web_queries.redirect_to_inventory("suppliers")


    @app.route("/inventory/purchase-orders/add", methods=["POST"])
    @roles_required("tenant_admin")
    def add_purchase_order():
        supplier_id = request.form.get("supplier_id", "").strip()
        product_id = request.form.get("product_id", "").strip()
        ordered_quantity = request.form.get("ordered_quantity", "").strip()
        unit_cost = request.form.get("unit_cost", "").strip()
        notes = request.form.get("notes", "").strip()

        try:
            supplier_id_int = int(supplier_id)
            product_id_int = int(product_id)
            ordered_quantity_int = int(ordered_quantity)
            unit_cost_value = round(float(unit_cost), 2)
        except ValueError:
            flash("Purchase order values are invalid.", "error")
            return web_queries.redirect_to_inventory("purchase-orders")

        if ordered_quantity_int <= 0 or unit_cost_value < 0:
            flash("Ordered quantity must be positive and unit cost cannot be negative.", "error")
            return web_queries.redirect_to_inventory("purchase-orders")

        with get_connection() as connection:
            supplier = connection.execute("SELECT id, name FROM suppliers WHERE id = ? AND is_active = 1", (supplier_id_int,)).fetchone()
            product = connection.execute("SELECT id, name FROM products WHERE id = ?", (product_id_int,)).fetchone()
            if supplier is None or product is None:
                flash("Select a valid supplier and product.", "error")
                return web_queries.redirect_to_inventory("purchase-orders")

            purchase_order_row = connection.execute(
                "INSERT INTO purchase_orders (supplier_id, notes, created_by) VALUES (?, ?, ?) RETURNING id",
                (supplier_id_int, notes, session_user_id()),
            ).fetchone()
            po_id = purchase_order_row["id"]
            connection.execute(
                "INSERT INTO purchase_order_items (purchase_order_id, product_id, ordered_quantity, unit_cost) VALUES (?, ?, ?, ?)",
                (po_id, product_id_int, ordered_quantity_int, unit_cost_value),
            )
            web_queries.log_audit(connection, "create", "purchase_order", po_id, f"PO created for {supplier['name']} / {product['name']}")

        flash("Purchase order created.", "success")
        return web_queries.redirect_to_inventory("purchase-orders")


    @app.route("/inventory/purchase-orders/receive", methods=["POST"])
    @roles_required("tenant_admin")
    def receive_purchase_order():
        purchase_order_id = request.form.get("purchase_order_id", "").strip()
        received_quantity = request.form.get("received_quantity", "").strip()

        try:
            purchase_order_id_int = int(purchase_order_id)
            received_quantity_int = int(received_quantity)
        except ValueError:
            flash("Received quantity must be a whole number.", "error")
            return web_queries.redirect_to_inventory("purchase-orders")

        if received_quantity_int <= 0:
            flash("Received quantity must be greater than zero.", "error")
            return web_queries.redirect_to_inventory("purchase-orders")

        with get_connection() as connection:
            order_row = connection.execute(
                """
                SELECT po.id, po.status, poi.id AS item_id, poi.product_id, poi.ordered_quantity, poi.received_quantity, p.name
                FROM purchase_orders po
                JOIN purchase_order_items poi ON poi.purchase_order_id = po.id
                JOIN products p ON p.id = poi.product_id
                WHERE po.id = ?
                """,
                (purchase_order_id_int,),
            ).fetchone()
            if order_row is None:
                flash("Purchase order not found.", "error")
                return web_queries.redirect_to_inventory("purchase-orders")

            remaining_quantity = order_row["ordered_quantity"] - order_row["received_quantity"]
            if received_quantity_int > remaining_quantity:
                flash(f"Only {remaining_quantity} unit(s) remain to be received.", "error")
                return web_queries.redirect_to_inventory("purchase-orders")

            new_received_total = order_row["received_quantity"] + received_quantity_int
            new_status = "received" if new_received_total >= order_row["ordered_quantity"] else "partial"
            connection.execute(
                "UPDATE purchase_order_items SET received_quantity = ? WHERE id = ?",
                (new_received_total, order_row["item_id"]),
            )
            connection.execute(
                "UPDATE purchase_orders SET status = ?, received_at = CURRENT_TIMESTAMP WHERE id = ?",
                (new_status, purchase_order_id_int),
            )
            connection.execute(
                "UPDATE products SET stock = stock + ?, last_restocked = CURRENT_TIMESTAMP WHERE id = ?",
                (received_quantity_int, order_row["product_id"]),
            )
            log_stock_movement(connection, order_row["product_id"], received_quantity_int, "purchase_receive")
            web_queries.log_audit(connection, "receive", "purchase_order", purchase_order_id_int, f"Received {received_quantity_int} unit(s) for {order_row['name']}")

        flash("Purchase order received.", "success")
        return web_queries.redirect_to_inventory("purchase-orders")


    @app.route("/inventory/stock-counts/create", methods=["POST"])
    @roles_required("tenant_admin")
    def create_stock_count():
        title = normalize_lookup_name(request.form.get("title", "")) or "Cycle Count"
        with get_connection() as connection:
            existing = connection.execute("SELECT id FROM stock_counts WHERE status = 'open' LIMIT 1").fetchone()
            if existing is not None:
                flash("Complete the current open stock count first.", "error")
                return web_queries.redirect_to_inventory("stock-counts")

            stock_count_row = connection.execute(
                "INSERT INTO stock_counts (title, created_by) VALUES (?, ?) RETURNING id",
                (title, session_user_id()),
            ).fetchone()
            stock_count_id = stock_count_row["id"]
            products = connection.execute(
                "SELECT id, stock FROM products WHERE status = 'active' ORDER BY sort_order ASC, id ASC"
            ).fetchall()
            connection.executemany(
                "INSERT INTO stock_count_items (stock_count_id, product_id, system_stock) VALUES (?, ?, ?)",
                [(stock_count_id, product["id"], product["stock"]) for product in products],
            )
            web_queries.log_audit(connection, "create", "stock_count", stock_count_id, f"Stock count opened: {title}")

        flash("Stock count started.", "success")
        return web_queries.redirect_to_inventory("stock-counts")


    @app.route("/inventory/stock-counts/complete", methods=["POST"])
    @roles_required("tenant_admin")
    def complete_stock_count():
        stock_count_id = request.form.get("stock_count_id", "").strip()
        try:
            stock_count_id_int = int(stock_count_id)
        except ValueError:
            flash("Invalid stock count selection.", "error")
            return web_queries.redirect_to_inventory("stock-counts")

        with get_connection() as connection:
            count_row = connection.execute(
                "SELECT id, title, status FROM stock_counts WHERE id = ?",
                (stock_count_id_int,),
            ).fetchone()
            if count_row is None or count_row["status"] != "open":
                flash("Open stock count not found.", "error")
                return web_queries.redirect_to_inventory("stock-counts")

            items = connection.execute(
                "SELECT id, product_id, system_stock FROM stock_count_items WHERE stock_count_id = ?",
                (stock_count_id_int,),
            ).fetchall()

            for item in items:
                raw_counted = request.form.get(f"counted_{item['id']}", "").strip()
                try:
                    counted_stock = int(raw_counted)
                except ValueError:
                    flash("All counted quantities must be whole numbers.", "error")
                    return web_queries.redirect_to_inventory("stock-counts")
                if counted_stock < 0:
                    flash("Counted stock cannot be negative.", "error")
                    return web_queries.redirect_to_inventory("stock-counts")

                variance = counted_stock - item["system_stock"]
                connection.execute(
                    "UPDATE stock_count_items SET counted_stock = ?, variance = ? WHERE id = ?",
                    (counted_stock, variance, item["id"]),
                )
                if variance != 0:
                    connection.execute(
                        "UPDATE products SET stock = ?, last_restocked = CASE WHEN ? > 0 THEN CURRENT_TIMESTAMP ELSE last_restocked END WHERE id = ?",
                        (counted_stock, variance, item["product_id"]),
                    )
                    log_stock_movement(connection, item["product_id"], variance, "manual_count")
                    product = connection.execute(
                        "SELECT id, name, stock, reorder_level FROM products WHERE id = ?",
                        (item["product_id"],),
                    ).fetchone()
                    if product is not None:
                        web_queries.maybe_create_adjustment_alert(connection, product, variance, "manual_count")
                        web_queries.maybe_create_low_stock_alert(connection, item["product_id"], "stock count")

            connection.execute(
                "UPDATE stock_counts SET status = 'completed', completed_at = CURRENT_TIMESTAMP WHERE id = ?",
                (stock_count_id_int,),
            )
            web_queries.log_audit(connection, "complete", "stock_count", stock_count_id_int, f"Stock count completed: {count_row['title']}")

        flash("Stock count completed and variances applied.", "success")
        return web_queries.redirect_to_inventory("stock-counts")


    @app.route("/inventory/stock-counts/report")
    @roles_required("tenant_admin")
    def stock_count_report_api():
        from flask import jsonify

        period = request.args.get("period", "daily")
        if period == "weekly":
            group_fmt = "%Y-%W"
        elif period == "monthly":
            group_fmt = "%Y-%m"
        else:
            group_fmt = "%Y-%m-%d"

        with get_connection() as connection:
            rows = connection.execute(
                f"""
                SELECT
                    strftime('{group_fmt}', sc.completed_at) AS period,
                    p.name AS product_name,
                    SUM(sci.system_stock) AS opening,
                    SUM(CASE WHEN sci.variance > 0 THEN sci.variance ELSE 0 END) AS stock_in,
                    SUM(CASE WHEN sci.variance < 0 THEN ABS(sci.variance) ELSE 0 END) AS stock_out,
                    SUM(sci.system_stock) AS closing_system,
                    SUM(sci.counted_stock) AS physical_count,
                    SUM(sci.variance) AS variance
                FROM stock_count_items sci
                JOIN stock_counts sc ON sc.id = sci.stock_count_id
                JOIN products p ON p.id = sci.product_id
                WHERE sc.status = 'completed' AND sc.completed_at IS NOT NULL
                GROUP BY strftime('{group_fmt}', sc.completed_at), sci.product_id
                ORDER BY period DESC, p.name ASC
                """,
            ).fetchall()

            summary = connection.execute(
                f"""
                SELECT
                    COUNT(DISTINCT sc.id) AS total_counts,
                    COALESCE(SUM(sci.system_stock), 0) AS total_opening,
                    COALESCE(SUM(sci.counted_stock), 0) AS total_physical,
                    COALESCE(SUM(sci.variance), 0) AS total_variance,
                    COALESCE(SUM(CASE WHEN sci.variance > 0 THEN sci.variance ELSE 0 END), 0) AS positive_variance,
                    COALESCE(SUM(CASE WHEN sci.variance < 0 THEN ABS(sci.variance) ELSE 0 END), 0) AS negative_variance
                FROM stock_count_items sci
                JOIN stock_counts sc ON sc.id = sci.stock_count_id
                WHERE sc.status = 'completed' AND sc.completed_at IS NOT NULL
                    AND strftime('{group_fmt}', sc.completed_at) = strftime('{group_fmt}', 'now')
                """,
            ).fetchone()

        return jsonify({
            "rows": [
                {
                    "period": row["period"],
                    "product": row["product_name"],
                    "opening": row["opening"],
                    "stock_in": row["stock_in"],
                    "stock_out": row["stock_out"],
                    "closing_system": row["closing_system"],
                    "physical_count": row["physical_count"],
                    "variance": row["variance"],
                }
                for row in rows
            ],
            "summary": {
                "total_counts": summary["total_counts"],
                "total_opening": summary["total_opening"],
                "total_physical": summary["total_physical"],
                "total_variance": summary["total_variance"],
                "positive_variance": summary["positive_variance"],
                "negative_variance": summary["negative_variance"],
            },
        })


    @app.route("/inventory/sales/void", methods=["POST"])
    @roles_required("tenant_admin")
    @require_recent_reauth()
    def void_sale():
        sale_id = request.form.get("sale_id", "").strip()
        try:
            sale_id_int = int(sale_id)
        except ValueError:
            flash("Invalid sale selection.", "error")
            return web_queries.redirect_to_inventory("sales-controls")

        with get_connection() as connection:
            sale = connection.execute(
                "SELECT id, status, tenant_id FROM sales WHERE id = ?", (sale_id_int,)
            ).fetchone()
            if sale is None or sale["status"] != "completed":
                flash("Only completed sales can be voided.", "error")
                return web_queries.redirect_to_inventory("sales-controls")

            apply_sale_financial_reversal(connection, int(sale["tenant_id"]), sale_id_int, "void")
            web_queries.build_sale_stock_return(connection, sale_id_int, "void")
            connection.execute("UPDATE sales SET status = 'voided' WHERE id = ?", (sale_id_int,))
            web_queries.create_owner_alert(
                connection,
                "sale_void",
                "warning",
                f"Sale voided: #{sale_id_int}",
                f"Sale #{sale_id_int} was voided and stock was returned to inventory.",
                "sale",
                sale_id_int,
                "alert_void_refund_email",
            )
            web_queries.log_audit(connection, "void", "sale", sale_id_int, "Sale voided and stock returned")

        flash("Sale voided.", "success")
        return web_queries.redirect_to_inventory("sales-controls")


    @app.route("/inventory/sales/refund", methods=["POST"])
    @roles_required("tenant_admin")
    @require_recent_reauth()
    def refund_sale():
        sale_id = request.form.get("sale_id", "").strip()
        try:
            sale_id_int = int(sale_id)
        except ValueError:
            flash("Invalid sale selection.", "error")
            return web_queries.redirect_to_inventory("sales-controls")

        with get_connection() as connection:
            sale = connection.execute(
                "SELECT id, status, tenant_id FROM sales WHERE id = ?", (sale_id_int,)
            ).fetchone()
            if sale is None or sale["status"] != "completed":
                flash("Only completed sales can be refunded.", "error")
                return web_queries.redirect_to_inventory("sales-controls")

            apply_sale_financial_reversal(connection, int(sale["tenant_id"]), sale_id_int, "refund")
            web_queries.build_sale_stock_return(connection, sale_id_int, "refund")
            connection.execute("UPDATE sales SET status = 'refunded' WHERE id = ?", (sale_id_int,))
            web_queries.create_owner_alert(
                connection,
                "sale_refund",
                "warning",
                f"Sale refunded: #{sale_id_int}",
                f"Sale #{sale_id_int} was refunded and stock was returned to inventory.",
                "sale",
                sale_id_int,
                "alert_void_refund_email",
            )
            web_queries.log_audit(connection, "refund", "sale", sale_id_int, "Sale refunded and stock returned")

        flash("Sale refunded.", "success")
        return web_queries.redirect_to_inventory("sales-controls")


