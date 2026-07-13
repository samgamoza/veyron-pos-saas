from __future__ import annotations

import os
import re
import shutil
import smtplib
import sqlite3
import time
from contextlib import suppress
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from flask import redirect, session, url_for
from werkzeug.security import generate_password_hash

from app.core.audit import audit_service
from app.core.auth import session_user_id
from app.core.constants import ALLOWED_INVENTORY_REASONS
from app.core.db import DATABASE_ENGINE, get_connection, get_raw_connection
from app.core.flask_config import (
    BACKUP_DIR,
    DATABASE,
    DEFAULT_APP_SETTINGS,
    DEFAULT_PLACEHOLDER,
    DEFAULT_UNITS_DATA,
    PLACEHOLDER_MAP,
    PRODUCT_IMAGES_DIR,
)

def get_platform_setting(key: str, fallback: str | None = None) -> str | None:
    """SaaS-wide settings stored under `tenant_id = 1` (super admin platform settings)."""
    with get_connection() as connection:
        row = connection.execute(
            "SELECT value FROM app_settings WHERE tenant_id = 1 AND key = ?",
            (key,),
        ).fetchone()
    if row is None or row["value"] is None or str(row["value"]).strip() == "":
        return fallback
    return row["value"]


def get_setting(key: str, fallback: str | None = None) -> str | None:
    tenant_id = session.get("tenant_id") or 1
    with get_connection() as connection:
        row = connection.execute(
            "SELECT value FROM app_settings WHERE tenant_id = ? AND key = ?",
            (tenant_id, key),
        ).fetchone()
    if row is None:
        return DEFAULT_APP_SETTINGS.get(key, fallback)
    return row["value"]


def fetch_app_settings() -> dict[str, str]:
    tenant_id = session.get("tenant_id") or 1
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT key, value FROM app_settings WHERE tenant_id = ?",
            (tenant_id,),
        ).fetchall()
        settings = dict(DEFAULT_APP_SETTINGS)
        settings.update({row["key"]: row["value"] for row in rows})

        tenant_id = session.get("tenant_id")
        if tenant_id:
            tenant_row = connection.execute(
                "SELECT delivery_enabled FROM tenants WHERE id = ?",
                (tenant_id,),
            ).fetchone()
            if tenant_row is not None:
                settings["delivery_enabled"] = str(tenant_row["delivery_enabled"])

    return settings


def log_audit(
    connection: sqlite3.Connection,
    action: str,
    entity_type: str,
    entity_id: int | None = None,
    details: str = "",
) -> None:
    audit_service.log(
        connection=connection,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        details=details,
    )


def send_email_alert(subject: str, body: str) -> bool:
    smtp_host = os.getenv("SMTP_HOST", "").strip()
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_username = os.getenv("SMTP_USERNAME", "").strip()
    smtp_password = os.getenv("SMTP_PASSWORD", "").strip()
    alert_from_email = os.getenv("ALERT_FROM_EMAIL", smtp_username).strip()
    alert_to_email = os.getenv("ALERT_TO_EMAIL", "").strip()

    if not smtp_host or not alert_from_email or not alert_to_email:
        return False

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = alert_from_email
    message["To"] = alert_to_email
    message.set_content(body)

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as smtp:
            with suppress(Exception):
                smtp.starttls()
            if smtp_username and smtp_password:
                smtp.login(smtp_username, smtp_password)
            smtp.send_message(message)
    except Exception:
        return False
    return True


def should_email_alert(connection, setting_key: str) -> bool:
    tenant_id = session.get("tenant_id") or 1
    row = connection.execute(
        "SELECT value FROM app_settings WHERE tenant_id = ? AND key = ?",
        (tenant_id, setting_key),
    ).fetchone()
    value = row["value"] if row is not None else DEFAULT_APP_SETTINGS.get(setting_key, "0")
    return value == "1"


def create_owner_alert(
    connection,
    alert_type: str,
    severity: str,
    title: str,
    message: str,
    related_entity_type: str | None = None,
    related_entity_id: int | None = None,
    email_setting_key: str | None = None,
) -> None:
    from flask import has_request_context

    from app.core.tenant.context import current_tenant_id

    tid = session.get("tenant_id") if has_request_context() else None
    if tid is None and has_request_context():
        ct = current_tenant_id()
        tid = ct if ct is not None else 1
    if tid is None:
        tid = 1

    connection.execute(
        """
        INSERT INTO owner_alerts (
            tenant_id, alert_type, severity, title, message, related_entity_type, related_entity_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (tid, alert_type, severity, title, message, related_entity_type, related_entity_id),
    )
    if email_setting_key and should_email_alert(connection, email_setting_key):
        send_email_alert(f"[Veyron POS] {title}", message)


def maybe_create_low_stock_alert(connection, product_id: int, source: str) -> None:
    product = connection.execute(
        "SELECT id, name, stock, reorder_level FROM products WHERE id = ?",
        (product_id,),
    ).fetchone()
    if product is None or product["stock"] > product["reorder_level"]:
        return

    create_owner_alert(
        connection,
        "low_stock",
        "warning" if product["stock"] > 0 else "critical",
        f"Low stock: {product['name']}",
        f"{product['name']} is now at {product['stock']} unit(s), at or below its reorder level of {product['reorder_level']} after {source}.",
        "product",
        product["id"],
        "alert_low_stock_email",
    )


def maybe_create_adjustment_alert(connection, product, quantity_change: int, reason: str) -> None:
    threshold = max(5, int(product["reorder_level"] or 0), int(abs(product["stock"]) * 0.25))
    suspicious_reasons = {"manual_count", "damaged", "wastage"}
    if reason not in suspicious_reasons and abs(quantity_change) < threshold:
        return

    severity = "critical" if abs(quantity_change) >= max(10, threshold * 2) else "warning"
    create_owner_alert(
        connection,
        "inventory_adjustment",
        severity,
        f"Inventory adjustment: {product['name']}",
        f"{product['name']} was adjusted by {quantity_change} unit(s) for reason '{reason}'. New stock is {product['stock'] + quantity_change}.",
        "product",
        product["id"],
        "alert_variance_email",
    )


def copy_database(target_path: Path) -> None:
    if DATABASE_ENGINE == "postgres":
        raise RuntimeError("Use managed PostgreSQL backups or pg_dump for production database backups.")
    target_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DATABASE) as source_connection:
        with sqlite3.connect(target_path) as target_connection:
            source_connection.backup(target_connection)


def fetch_backup_rows() -> list[dict[str, str]]:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backups = []
    for file_path in sorted(BACKUP_DIR.glob("veyron-pos-backup-*.db"), reverse=True):
        backups.append(
            {
                "name": file_path.name,
                "size": f"{file_path.stat().st_size / 1024:.1f} KB",
                "modified": datetime.fromtimestamp(file_path.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
            }
        )
    return backups


def fetch_users() -> list[sqlite3.Row]:
    tenant_id = session.get("tenant_id")
    if not tenant_id:
        return []
    with get_connection() as connection:
        return connection.execute(
            "SELECT id, full_name, username, role, is_active, last_login, language_override FROM users WHERE tenant_id = ? ORDER BY role, username",
            (tenant_id,)
        ).fetchall()


def fetch_suppliers() -> list[sqlite3.Row]:
    tenant_id = session.get("tenant_id")
    if not tenant_id:
        return []
    with get_connection() as connection:
        return connection.execute(
            "SELECT id, name, contact_person, phone, email, notes, is_active FROM suppliers WHERE tenant_id = ? ORDER BY is_active DESC, name ASC",
            (tenant_id,)
        ).fetchall()


def fetch_purchase_orders() -> list[sqlite3.Row]:
    tenant_id = session.get("tenant_id")
    if not tenant_id:
        return []
    with get_connection() as connection:
        return connection.execute(
            """
            SELECT
                po.id,
                po.status,
                po.notes,
                po.created_at,
                po.received_at,
                s.name AS supplier_name,
                p.name AS product_name,
                poi.ordered_quantity,
                poi.received_quantity,
                poi.unit_cost
            FROM purchase_orders po
            JOIN suppliers s ON s.id = po.supplier_id AND s.tenant_id = ?
            JOIN purchase_order_items poi ON poi.purchase_order_id = po.id AND poi.tenant_id = ?
            JOIN products p ON p.id = poi.product_id AND p.tenant_id = ?
            WHERE po.tenant_id = ?
            ORDER BY po.created_at DESC, po.id DESC
            LIMIT 20
            """,
            (tenant_id, tenant_id, tenant_id, tenant_id)
        ).fetchall()


def fetch_open_stock_count() -> tuple[sqlite3.Row | None, list[sqlite3.Row]]:
    tenant_id = session.get("tenant_id")
    if not tenant_id:
        return None, []
    with get_connection() as connection:
        stock_count = connection.execute(
            """
            SELECT id, title, status, created_at, completed_at
            FROM stock_counts
            WHERE tenant_id = ? AND status = 'open'
            ORDER BY created_at DESC, id DESC LIMIT 1
            """,
            (tenant_id,),
        ).fetchone()
        if stock_count is None:
            return None, []
        items = connection.execute(
            """
            SELECT
                sci.id,
                sci.product_id,
                sci.system_stock,
                sci.counted_stock,
                sci.variance,
                p.name,
                p.sku,
                c.name AS category_name
            FROM stock_count_items sci
            JOIN products p ON p.id = sci.product_id AND p.tenant_id = sci.tenant_id
            LEFT JOIN categories c ON c.id = p.category_id AND c.tenant_id = p.tenant_id
            WHERE sci.stock_count_id = ? AND sci.tenant_id = ?
            ORDER BY c.sort_order ASC, p.sort_order ASC, p.id ASC
            """,
            (stock_count["id"], tenant_id),
        ).fetchall()
    return stock_count, items


def fetch_recent_audit_logs(limit: int = 20) -> list[sqlite3.Row]:
    tenant_id = session.get("tenant_id")
    if not tenant_id:
        return []
    with get_connection() as connection:
        return connection.execute(
            """
            SELECT
                al.created_at,
                al.action,
                al.entity_type,
                al.entity_id,
                al.details,
                COALESCE(u.full_name, 'System') AS actor_name,
                COALESCE(u.role, 'system') AS actor_role
            FROM audit_logs al
            LEFT JOIN users u ON u.id = al.user_id
            WHERE al.tenant_id = ?
            ORDER BY al.created_at DESC, al.id DESC
            LIMIT ?
            """,
            (tenant_id, limit),
        ).fetchall()


def fetch_sales_for_control(limit: int = 20) -> list[sqlite3.Row]:
    tenant_id = session.get("tenant_id")
    if not tenant_id:
        return []
    with get_connection() as connection:
        return connection.execute(
            """
            SELECT
                s.id,
                s.created_at,
                s.subtotal,
                s.discount_type,
                s.discount_amount,
                s.tax,
                s.total,
                s.payment_method,
                s.status,
                COALESCE(u.full_name, 'Unknown') AS cashier_name
            FROM sales s
            LEFT JOIN users u ON u.id = s.cashier_user_id AND u.tenant_id = s.tenant_id
            WHERE s.tenant_id = ?
            ORDER BY s.created_at DESC, s.id DESC
            LIMIT ?
            """,
            (tenant_id, limit),
        ).fetchall()


def fetch_owner_alerts(limit: int = 20) -> list[sqlite3.Row]:
    tenant_id = session.get("tenant_id")
    if not tenant_id:
        return []
    with get_connection() as connection:
        return connection.execute(
            """
            SELECT id, alert_type, severity, title, message, related_entity_type, related_entity_id, created_at
            FROM owner_alerts
            WHERE tenant_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (tenant_id, limit),
        ).fetchall()


def build_sale_stock_return(connection, sale_id: int, reason: str) -> None:
    sale_items = connection.execute(
        "SELECT product_id, variant_id, quantity FROM sale_items WHERE sale_id = ?",
        (sale_id,),
    ).fetchall()
    for item in sale_items:
        if item["variant_id"]:
            connection.execute(
                "UPDATE product_variants SET stock = stock + ? WHERE id = ?",
                (item["quantity"], item["variant_id"]),
            )
        else:
            connection.execute(
                "UPDATE products SET stock = stock + ? WHERE id = ?",
                (item["quantity"], item["product_id"]),
            )
        log_stock_movement(connection, item["product_id"], item["quantity"], reason, item["variant_id"])


def ensure_column(connection, table_name: str, column_name: str, definition: str) -> None:
    if DATABASE_ENGINE == "postgres":
        columns = {
            row["column_name"]
            for row in connection.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = %s
                """,
                (table_name,),
            ).fetchall()
        }
    else:
        columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()}
    if column_name not in columns:
        if DATABASE_ENGINE != "postgres" and "UNIQUE" in definition.upper():
            definition = re.sub(r"\bUNIQUE\b", "", definition, flags=re.IGNORECASE).strip()
        connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")


def seed_lookup_table(connection, table_name: str, values: list[str]) -> None:
    if DATABASE_ENGINE == "postgres":
        connection.executemany(
            f"INSERT INTO {table_name} (tenant_id, name) VALUES (1, %s) ON CONFLICT (tenant_id, name) DO NOTHING",
            [(value,) for value in values],
        )
        return

    connection.executemany(
        f"INSERT INTO {table_name} (tenant_id, name) VALUES (1, ?) ON CONFLICT(tenant_id, name) DO NOTHING",
        [(value,) for value in values],
    )


def seed_units_data(connection) -> None:
    """Seed extended unit data (symbol, type, conversion) for measurement system."""
    for name, symbol, utype, conversion in DEFAULT_UNITS_DATA:
        if DATABASE_ENGINE == "postgres":
            connection.execute(
                "INSERT INTO units (tenant_id, name, symbol, type, conversion) VALUES (1, %s, %s, %s, %s) "
                "ON CONFLICT (tenant_id, name) DO UPDATE SET symbol=EXCLUDED.symbol, type=EXCLUDED.type, conversion=EXCLUDED.conversion",
                (name, symbol, utype, conversion),
            )
        else:
            connection.execute(
                "INSERT INTO units (tenant_id, name, symbol, type, conversion) VALUES (1, ?, ?, ?, ?) "
                "ON CONFLICT(tenant_id, name) DO UPDATE SET symbol=excluded.symbol, type=excluded.type, conversion=excluded.conversion",
                (name, symbol, utype, conversion),
            )


def fetch_lookup_ids(connection: sqlite3.Connection, table_name: str) -> dict[str, int]:
    return {row["name"]: row["id"] for row in connection.execute(f"SELECT id, name FROM {table_name}").fetchall()}


def resequence_category_order(connection: sqlite3.Connection) -> None:
    ordered_ids = [
        row["id"]
        for row in connection.execute(
            "SELECT id FROM categories ORDER BY sort_order ASC, id ASC"
        ).fetchall()
    ]
    connection.executemany(
        "UPDATE categories SET sort_order = ? WHERE id = ?",
        [(position, category_id) for position, category_id in enumerate(ordered_ids, start=1)],
    )


def move_category_to_position(connection: sqlite3.Connection, category_id: int, requested_position: int) -> int:
    ordered_ids = [
        row["id"]
        for row in connection.execute(
            "SELECT id FROM categories ORDER BY sort_order ASC, id ASC"
        ).fetchall()
    ]
    if category_id not in ordered_ids:
        raise ValueError("Category not found in sort order list")

    ordered_ids.remove(category_id)
    target_index = max(0, min(requested_position - 1, len(ordered_ids)))
    ordered_ids.insert(target_index, category_id)
    connection.executemany(
        "UPDATE categories SET sort_order = ? WHERE id = ?",
        [(position, row_category_id) for position, row_category_id in enumerate(ordered_ids, start=1)],
    )
    return target_index + 1


def resequence_product_order(connection: sqlite3.Connection) -> None:
    ordered_ids = [
        row["id"]
        for row in connection.execute(
            "SELECT id FROM products ORDER BY sort_order ASC, id ASC"
        ).fetchall()
    ]
    connection.executemany(
        "UPDATE products SET sort_order = ? WHERE id = ?",
        [(position, product_id) for position, product_id in enumerate(ordered_ids, start=1)],
    )


def log_stock_movement(
    connection: sqlite3.Connection,
    product_id: int,
    quantity_change: int,
    reason: str,
    variant_id: int | None = None,
) -> None:
    from app.core.inventory import log_stock_movement as _core_log

    _core_log(connection, product_id, quantity_change, reason, variant_id)


def redirect_to_admin(section: str):
    return redirect(f"{url_for('admin_dashboard')}#{section}")


def redirect_to_inventory(anchor: str | None = None):
    destination = url_for("inventory_dashboard")
    if anchor:
        destination = f"{destination}#{anchor}"
    return redirect(destination)

def get_product_image_url(image_path: str | None, category_name: str | None = None) -> str:
    if image_path:
        return image_path
    if category_name:
        return PLACEHOLDER_MAP.get(category_name.lower(), DEFAULT_PLACEHOLDER)
    return DEFAULT_PLACEHOLDER


def fetch_pos_products() -> list[sqlite3.Row]:
    tenant_id = session.get("tenant_id")
    if not tenant_id:
        return []
    with get_connection() as connection:
        return connection.execute(
            """
            SELECT
                p.id,
                p.sort_order,
                p.name,
                p.sku,
                p.price,
                p.stock,
                p.reorder_level,
                p.image_path,
                c.id AS category_id,
                c.sort_order AS category_sort_order,
                c.name AS category_name,
                u.name AS unit_name
            FROM products p
            LEFT JOIN categories c ON c.id = p.category_id AND c.tenant_id = ?
            LEFT JOIN units u ON u.id = p.unit_id AND u.tenant_id = ?
            WHERE p.tenant_id = ? AND p.status IN ('active', 'out_of_stock')
            ORDER BY c.sort_order ASC, c.id ASC, p.sort_order ASC, p.id ASC
            """,
            (tenant_id, tenant_id, tenant_id)
        ).fetchall()


def fetch_quick_pick_products(limit: int = 8) -> list[sqlite3.Row]:
    tenant_id = session.get("tenant_id")
    if not tenant_id:
        return []
    with get_connection() as connection:
        return connection.execute(
            f"""
            SELECT
                p.id,
                p.name,
                p.sku,
                p.price,
                COALESCE(SUM(CASE WHEN s.id IS NOT NULL THEN si.quantity ELSE 0 END), 0) AS sold_qty
            FROM products p
            LEFT JOIN sale_items si ON si.product_id = p.id AND si.tenant_id = ?
            LEFT JOIN sales s ON s.id = si.sale_id AND s.tenant_id = ? AND s.status = 'completed'
                AND {sql_date_column_gte_days_ago_local("s.created_at", 30)}
            WHERE p.tenant_id = ? AND p.status = 'active'
            GROUP BY p.id, p.name, p.sku, p.price, p.sort_order
            ORDER BY sold_qty DESC, p.sort_order ASC, p.id ASC
            LIMIT ?
            """,
            (tenant_id, tenant_id, tenant_id, limit),
        ).fetchall()


def fetch_product_variants(product_id: int | None = None) -> list[sqlite3.Row]:
    with get_connection() as connection:
        if product_id is not None:
            return connection.execute(
                """
                SELECT id, product_id, name, sku_suffix, price, cost, stock, reorder_level, sort_order, is_active
                FROM product_variants
                WHERE product_id = ?
                ORDER BY sort_order ASC, id ASC
                """,
                (product_id,),
            ).fetchall()
        return connection.execute(
            """
            SELECT id, product_id, name, sku_suffix, price, cost, stock, reorder_level, sort_order, is_active
            FROM product_variants
            WHERE is_active = 1
            ORDER BY product_id ASC, sort_order ASC, id ASC
            """
        ).fetchall()


def fetch_variants_by_product() -> dict[int, list[dict]]:
    variants = fetch_product_variants()
    grouped: dict[int, list[dict]] = {}
    for v in variants:
        pid = v["product_id"]
        if pid not in grouped:
            grouped[pid] = []
        grouped[pid].append(dict(v))
    return grouped


def fetch_lookup_rows(table_name: str) -> list[sqlite3.Row]:
    if table_name not in {"categories", "brands", "units"}:
        raise ValueError(f"Unsupported lookup table: {table_name}")

    with get_connection() as connection:
        if table_name == "categories":
            return connection.execute(
                "SELECT id, name, sort_order FROM categories ORDER BY sort_order ASC, id ASC"
            ).fetchall()
        if table_name == "units":
            return connection.execute(
                "SELECT id, name, symbol, type, conversion FROM units ORDER BY type, name"
            ).fetchall()
        return connection.execute(f"SELECT id, name FROM {table_name} ORDER BY name").fetchall()


def fetch_admin_products() -> list[sqlite3.Row]:
    tenant_id = session.get("tenant_id")
    if not tenant_id:
        return []
    with get_connection() as connection:
        return connection.execute(
            """
            SELECT
                p.id,
                p.sort_order,
                p.name,
                p.sku,
                p.price,
                p.cost,
                p.stock,
                p.reorder_level,
                p.status,
                p.image_path,
                p.category_id,
                p.brand_id,
                p.unit_id,
                c.sort_order AS category_sort_order,
                COALESCE(c.name, 'Unassigned') AS category_name,
                COALESCE(b.name, 'Unassigned') AS brand_name,
                COALESCE(u.name, 'Unassigned') AS unit_name,
                COALESCE(u.symbol, '') AS unit_symbol,
                p.last_restocked,
                (p.stock * p.cost) AS stock_cost_value,
                (p.stock * p.price) AS stock_retail_value
            FROM products p
            LEFT JOIN categories c ON c.id = p.category_id AND c.tenant_id = p.tenant_id
            LEFT JOIN brands b ON b.id = p.brand_id AND b.tenant_id = p.tenant_id
            LEFT JOIN units u ON u.id = p.unit_id AND u.tenant_id = p.tenant_id
            WHERE p.tenant_id = ?
            ORDER BY p.sort_order ASC, p.id ASC
            """,
            (tenant_id,),
        ).fetchall()


def fetch_inventory_watchlist() -> list[sqlite3.Row]:
    tenant_id = session.get("tenant_id")
    if not tenant_id:
        return []
    with get_connection() as connection:
        return connection.execute(
            """
            SELECT
                p.id,
                p.name,
                p.sku,
                p.stock,
                p.reorder_level,
                p.status,
                c.name AS category_name,
                b.name AS brand_name
            FROM products p
            LEFT JOIN categories c ON c.id = p.category_id AND c.tenant_id = ?
            LEFT JOIN brands b ON b.id = p.brand_id AND b.tenant_id = ?
            WHERE p.tenant_id = ? AND p.status = 'active' AND p.stock <= p.reorder_level
            ORDER BY p.stock ASC, p.name ASC
            LIMIT 12
            """,
            (tenant_id, tenant_id, tenant_id)
        ).fetchall()


def fetch_recent_stock_movements() -> list[sqlite3.Row]:
    tenant_id = session.get("tenant_id")
    if not tenant_id:
        return []
    with get_connection() as connection:
        return connection.execute(
            """
            SELECT
                sm.created_at,
                sm.quantity_change,
                sm.reason,
                p.name,
                p.sku,
                p.stock
            FROM stock_movements sm
            JOIN products p ON p.id = sm.product_id AND p.tenant_id = ?
            WHERE sm.tenant_id = ?
            ORDER BY sm.created_at DESC, sm.id DESC
            LIMIT 12
            """,
            (tenant_id, tenant_id)
        ).fetchall()


def fetch_upcoming_products() -> list[sqlite3.Row]:
    tenant_id = session.get("tenant_id")
    if not tenant_id:
        return []
    with get_connection() as connection:
        return connection.execute(
            """
            SELECT p.name, p.sku, c.name AS category_name
            FROM products p
            LEFT JOIN categories c ON c.id = p.category_id AND c.tenant_id = p.tenant_id
            WHERE p.status = 'upcoming' AND p.tenant_id = ?
            ORDER BY p.name ASC
            LIMIT 6
            """,
            (tenant_id,),
        ).fetchall()


def fetch_dashboard_metrics() -> dict[str, object]:
    tenant_id = session.get("tenant_id")
    if not tenant_id:
        return {}
    with get_connection() as connection:
        sales_summary = connection.execute(
            """
            SELECT
                COUNT(*) AS receipt_count,
                COALESCE(SUM(total), 0) AS revenue,
                COALESCE(AVG(total), 0) AS average_ticket
            FROM sales
            WHERE status = 'completed' AND tenant_id = ?
            """,
            (tenant_id,)
        ).fetchone()
        inventory_summary = connection.execute(
            """
            SELECT
                COUNT(*) AS item_count,
                COALESCE(SUM(CASE WHEN status = 'active' THEN stock ELSE 0 END), 0) AS units_in_stock,
                COALESCE(SUM(CASE WHEN status = 'active' AND stock > 0 AND stock <= reorder_level THEN 1 ELSE 0 END), 0) AS low_stock_count,
                COALESCE(SUM(CASE WHEN status = 'active' AND stock = 0 THEN 1 ELSE 0 END), 0) AS out_of_stock_count,
                COALESCE(SUM(CASE WHEN status = 'active' THEN stock * cost ELSE 0 END), 0) AS inventory_cost_value,
                COALESCE(SUM(CASE WHEN status = 'active' THEN stock * price ELSE 0 END), 0) AS inventory_retail_value,
                COALESCE(SUM(CASE WHEN status = 'upcoming' THEN 1 ELSE 0 END), 0) AS upcoming_count
            FROM products
            WHERE tenant_id = ?
            """,
            (tenant_id,)
        ).fetchone()
        recent_sales = connection.execute(
            """
            SELECT id, created_at, total, payment_method, status, discount_amount
            FROM sales
            WHERE tenant_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 8
            """,
            (tenant_id,)
        ).fetchall()
        inventory = connection.execute(
            """
            SELECT
                p.name,
                p.sku,
                p.price,
                p.cost,
                p.stock,
                p.reorder_level,
                c.name AS category_name,
                p.status
            FROM products p
            LEFT JOIN categories c ON c.id = p.category_id AND c.tenant_id = ?
            WHERE p.tenant_id = ?
            ORDER BY CASE p.status WHEN 'active' THEN 0 WHEN 'upcoming' THEN 1 ELSE 2 END, p.stock ASC, p.name ASC
            LIMIT 14
            """,
            (tenant_id, tenant_id)
        ).fetchall()
        category_mix = connection.execute(
            """
            SELECT c.name, COUNT(p.id) AS product_count
            FROM categories c
            LEFT JOIN products p ON p.category_id = c.id AND p.tenant_id = ?
            WHERE c.tenant_id = ?
            GROUP BY c.id, c.name
            ORDER BY product_count DESC, c.name ASC
            """,
            (tenant_id, tenant_id)
        ).fetchall()
        # Chart data: sales by day (last 30 days)
        sales_by_day = connection.execute(
            """
            SELECT substr(created_at, 1, 10) AS day, SUM(total) AS revenue, COUNT(*) AS receipts
            FROM sales
            WHERE tenant_id = ? AND status = 'completed'
            GROUP BY day
            ORDER BY day DESC
            LIMIT 30
            """,
            (tenant_id,)
        ).fetchall()
        # Top products by sales
        top_products = connection.execute(
            """
            SELECT p.name, SUM(si.quantity) AS qty, SUM(si.line_total) AS total
            FROM sale_items si
            JOIN products p ON p.id = si.product_id AND p.tenant_id = ?
            WHERE si.tenant_id = ?
            GROUP BY p.id, p.name
            ORDER BY total DESC
            LIMIT 8
            """,
            (tenant_id, tenant_id)
        ).fetchall()
        payment_methods = connection.execute(
            f"""
            SELECT payment_method, COUNT(*) AS count, SUM(total) AS total
            FROM sales
            WHERE tenant_id = ? AND status = 'completed' AND {sql_timestamp_since_days_ago("created_at", 30)}
            GROUP BY payment_method
            ORDER BY total DESC
            """,
            (tenant_id,)
        ).fetchall()

    return {
        "receipt_count": sales_summary["receipt_count"],
        "revenue": sales_summary["revenue"],
        "average_ticket": sales_summary["average_ticket"],
        "item_count": inventory_summary["item_count"],
        "units_in_stock": inventory_summary["units_in_stock"],
        "low_stock_count": inventory_summary["low_stock_count"],
        "out_of_stock_count": inventory_summary["out_of_stock_count"],
        "inventory_cost_value": inventory_summary["inventory_cost_value"],
        "inventory_retail_value": inventory_summary["inventory_retail_value"],
        "upcoming_count": inventory_summary["upcoming_count"],
        "recent_sales": recent_sales,
        "inventory": inventory,
        "category_mix": category_mix,
        "sales_by_day": [dict(row) for row in sales_by_day],
        "top_products": [dict(row) for row in top_products],
        "payment_methods": [dict(row) for row in payment_methods],
    }


def sql_now() -> str:
    if DATABASE_ENGINE == "postgres":
        return "CURRENT_TIMESTAMP"
    return "datetime('now','localtime')"


def sql_today() -> str:
    if DATABASE_ENGINE == "postgres":
        return "CURRENT_DATE"
    return "date('now','localtime')"


def sql_timestamp_since_days_ago(column: str, days: int) -> str:
    """WHERE fragment: column within the last `days` days (SQLite vs Postgres)."""
    d = int(days)
    if DATABASE_ENGINE == "postgres":
        return f"({column})::timestamp >= NOW() - INTERVAL '{d} days'"
    return f"{column} >= date('now', '-{d} days')"


def sql_date_column_eq_today(column: str) -> str:
    if DATABASE_ENGINE == "postgres":
        return f"({column})::date = CURRENT_DATE"
    return f"DATE({column}) = DATE('now', 'localtime')"


def sql_date_column_gte_days_ago_local(column: str, days: int) -> str:
    d = int(days)
    if DATABASE_ENGINE == "postgres":
        return f"({column})::date >= (CURRENT_DATE - {d})"
    return f"DATE({column}) >= DATE('now', 'localtime', '-{d} days')"


def sql_date_bucket(column: str) -> str:
    if DATABASE_ENGINE == "postgres":
        return f"({column})::date"
    return f"date({column})"


def sql_month_bucket(expr: str) -> str:
    if DATABASE_ENGINE == "postgres":
        return f"to_char(({expr})::timestamp, 'YYYY-MM')"
    return f"strftime('%Y-%m', {expr})"


def sql_week_bucket(column: str) -> str:
    if DATABASE_ENGINE == "postgres":
        return f"to_char(({column})::timestamp, 'IYYY-IW')"
    return f"(strftime('%G', date({column})) || '-' || strftime('%V', date({column})))"


def sql_year_bucket(column: str) -> str:
    if DATABASE_ENGINE == "postgres":
        return f"to_char(({column})::timestamp, 'YYYY')"
    return f"strftime('%Y', date({column}))"


def build_period_report_rows(
    connection: sqlite3.Connection,
    sales_group_sql: str,
    movement_group_sql: str,
    limit: int,
    tenant_id: int,
) -> list[dict[str, object]]:
    sales_rows = connection.execute(
        f"""
        SELECT
            {sales_group_sql} AS period_key,
            COUNT(DISTINCT s.id) AS receipt_count,
            COALESCE(SUM(si.quantity), 0) AS units_sold,
            COALESCE(SUM(si.line_total), 0) AS sales_value,
            COALESCE(SUM(si.quantity * p.cost), 0) AS cogs
        FROM sale_items si
        JOIN sales s ON s.id = si.sale_id AND s.tenant_id = si.tenant_id
        JOIN products p ON p.id = si.product_id AND p.tenant_id = si.tenant_id
        WHERE s.status = 'completed' AND si.tenant_id = ?
        GROUP BY period_key
        ORDER BY period_key DESC
        LIMIT ?
        """,
        (tenant_id, limit),
    ).fetchall()

    movement_rows = connection.execute(
        f"""
        SELECT
            {movement_group_sql} AS period_key,
            COALESCE(SUM(CASE WHEN reason IN ('restock', 'opening_balance', 'purchase_receive', 'refund', 'void') AND quantity_change > 0 THEN quantity_change ELSE 0 END), 0) AS stock_in,
            COALESCE(SUM(CASE WHEN reason = 'manual_count' AND quantity_change > 0 THEN quantity_change ELSE 0 END), 0) AS count_gain,
            COALESCE(SUM(CASE WHEN reason = 'manual_count' AND quantity_change < 0 THEN ABS(quantity_change) ELSE 0 END), 0) AS count_loss,
            COALESCE(SUM(CASE WHEN reason IN ('damaged', 'wastage') AND quantity_change < 0 THEN ABS(quantity_change) ELSE 0 END), 0) AS write_off_units
        FROM stock_movements sm
        WHERE sm.tenant_id = ?
        GROUP BY period_key
        ORDER BY period_key DESC
        LIMIT ?
        """,
        (tenant_id, limit),
    ).fetchall()

    period_map: dict[str, dict[str, object]] = {}
    for row in sales_rows:
        period_map[row["period_key"]] = {
            "period_key": row["period_key"],
            "receipt_count": row["receipt_count"],
            "units_sold": row["units_sold"],
            "sales_value": row["sales_value"],
            "cogs": row["cogs"],
            "stock_in": 0,
            "count_gain": 0,
            "count_loss": 0,
            "write_off_units": 0,
        }

    for row in movement_rows:
        bucket = period_map.setdefault(
            row["period_key"],
            {
                "period_key": row["period_key"],
                "receipt_count": 0,
                "units_sold": 0,
                "sales_value": 0,
                "cogs": 0,
                "stock_in": 0,
                "count_gain": 0,
                "count_loss": 0,
                "write_off_units": 0,
            },
        )
        bucket["stock_in"] = row["stock_in"]
        bucket["count_gain"] = row["count_gain"]
        bucket["count_loss"] = row["count_loss"]
        bucket["write_off_units"] = row["write_off_units"]

    rows = sorted(period_map.values(), key=lambda item: item["period_key"], reverse=True)[:limit]
    for row in rows:
        row["gross_profit"] = row["sales_value"] - row["cogs"]
        row["stock_out_total"] = row["units_sold"] + row["count_loss"] + row["write_off_units"]
        row["net_stock_movement"] = row["stock_in"] + row["count_gain"] - row["stock_out_total"]
    return rows


def build_current_period_summary(
    connection: sqlite3.Connection,
    sales_where_sql: str,
    movement_where_sql: str,
    label_sql: str,
    title: str,
    tenant_id: int,
) -> dict[str, object]:
    sales_row = connection.execute(
        f"""
        SELECT
            COUNT(DISTINCT s.id) AS receipt_count,
            COALESCE(SUM(si.quantity), 0) AS units_sold,
            COALESCE(SUM(si.line_total), 0) AS sales_value,
            COALESCE(SUM(si.quantity * p.cost), 0) AS cogs
        FROM sale_items si
        JOIN sales s ON s.id = si.sale_id AND s.tenant_id = si.tenant_id
        JOIN products p ON p.id = si.product_id AND p.tenant_id = si.tenant_id
        WHERE s.status = 'completed' AND {sales_where_sql} AND si.tenant_id = ?
        """,
        (tenant_id,),
    ).fetchone()
    movement_row = connection.execute(
        f"""
        SELECT
            COALESCE(SUM(CASE WHEN reason IN ('restock', 'opening_balance', 'purchase_receive', 'refund', 'void') AND quantity_change > 0 THEN quantity_change ELSE 0 END), 0) AS stock_in,
            COALESCE(SUM(CASE WHEN reason = 'manual_count' AND quantity_change > 0 THEN quantity_change ELSE 0 END), 0) AS count_gain,
            COALESCE(SUM(CASE WHEN reason = 'manual_count' AND quantity_change < 0 THEN ABS(quantity_change) ELSE 0 END), 0) AS count_loss,
            COALESCE(SUM(CASE WHEN reason IN ('damaged', 'wastage') AND quantity_change < 0 THEN ABS(quantity_change) ELSE 0 END), 0) AS write_off_units
        FROM stock_movements sm
        WHERE {movement_where_sql} AND sm.tenant_id = ?
        """,
        (tenant_id,),
    ).fetchone()
    label = connection.execute(f"SELECT {label_sql} AS label").fetchone()["label"]

    sales_value = sales_row["sales_value"] or 0
    cogs = sales_row["cogs"] or 0
    units_sold = sales_row["units_sold"] or 0
    stock_in = movement_row["stock_in"] or 0
    count_gain = movement_row["count_gain"] or 0
    count_loss = movement_row["count_loss"] or 0
    write_off_units = movement_row["write_off_units"] or 0

    return {
        "title": title,
        "label": label,
        "receipt_count": sales_row["receipt_count"] or 0,
        "units_sold": units_sold,
        "sales_value": sales_value,
        "cogs": cogs,
        "gross_profit": sales_value - cogs,
        "stock_in": stock_in,
        "count_gain": count_gain,
        "count_loss": count_loss,
        "write_off_units": write_off_units,
        "stock_out_total": units_sold + count_loss + write_off_units,
        "net_stock_movement": stock_in + count_gain - (units_sold + count_loss + write_off_units),
    }


def fetch_reports_context() -> dict[str, object]:
    tenant_id = session.get("tenant_id")
    if not tenant_id:
        empty_summary = {"today_revenue": 0, "today_receipts": 0, "month_revenue": 0, "month_receipts": 0}
        return {
            "summary": empty_summary,
            "daily_sales": [],
            "top_sellers": [],
            "current_periods": [],
            "daily_inventory": [],
            "weekly_inventory": [],
            "monthly_inventory": [],
            "yearly_inventory": [],
        }

    sales_date_bucket = sql_date_bucket("s.created_at")
    sales_month_bucket = sql_month_bucket("s.created_at")
    today_value_sql = sql_today()
    current_month_bucket_sql = sql_month_bucket(sql_now())
    sales_day_group_sql = sql_date_bucket("s.created_at")
    movement_day_group_sql = sql_date_bucket("sm.created_at")
    sales_week_group_sql = sql_week_bucket("s.created_at")
    movement_week_group_sql = sql_week_bucket("sm.created_at")
    sales_month_group_sql = sql_month_bucket("s.created_at")
    movement_month_group_sql = sql_month_bucket("sm.created_at")
    sales_year_group_sql = sql_year_bucket("s.created_at")
    movement_year_group_sql = sql_year_bucket("sm.created_at")

    with get_connection() as connection:
        report_summary = connection.execute(
            f"""
            SELECT
                COALESCE(SUM(CASE WHEN {sales_date_bucket} = {today_value_sql} THEN s.total ELSE 0 END), 0) AS today_revenue,
                COALESCE(SUM(CASE WHEN {sales_date_bucket} = {today_value_sql} THEN 1 ELSE 0 END), 0) AS today_receipts,
                COALESCE(SUM(CASE WHEN {sales_month_bucket} = {current_month_bucket_sql} THEN s.total ELSE 0 END), 0) AS month_revenue,
                COALESCE(SUM(CASE WHEN {sales_month_bucket} = {current_month_bucket_sql} THEN 1 ELSE 0 END), 0) AS month_receipts
            FROM sales s
            WHERE s.status = 'completed' AND s.tenant_id = ?
            """,
            (tenant_id,),
        ).fetchone()
        daily_sales = connection.execute(
            f"""
            SELECT
                {sales_date_bucket} AS sale_date,
                COUNT(*) AS receipt_count,
                COALESCE(SUM(s.total), 0) AS revenue,
                COALESCE(AVG(s.total), 0) AS average_ticket
            FROM sales s
            WHERE s.status = 'completed' AND s.tenant_id = ?
            GROUP BY sale_date
            ORDER BY sale_date DESC
            LIMIT 14
            """,
            (tenant_id,),
        ).fetchall()
        top_sellers = connection.execute(
            """
            SELECT
                p.name,
                p.sku,
                COALESCE(SUM(si.quantity), 0) AS quantity_sold,
                COALESCE(SUM(si.line_total), 0) AS sales_value
            FROM sale_items si
            JOIN products p ON p.id = si.product_id AND p.tenant_id = si.tenant_id
            JOIN sales s ON s.id = si.sale_id AND s.tenant_id = si.tenant_id
            WHERE s.status = 'completed' AND si.tenant_id = ?
            GROUP BY p.id, p.name, p.sku
            ORDER BY quantity_sold DESC, sales_value DESC, p.name ASC
            LIMIT 10
            """,
            (tenant_id,),
        ).fetchall()

        daily_inventory = build_period_report_rows(
            connection,
            sales_day_group_sql,
            movement_day_group_sql,
            14,
            tenant_id,
        )
        weekly_inventory = build_period_report_rows(
            connection,
            sales_week_group_sql,
            movement_week_group_sql,
            12,
            tenant_id,
        )
        monthly_inventory = build_period_report_rows(
            connection,
            sales_month_group_sql,
            movement_month_group_sql,
            12,
            tenant_id,
        )
        yearly_inventory = build_period_report_rows(
            connection,
            sales_year_group_sql,
            movement_year_group_sql,
            6,
            tenant_id,
        )

        current_periods = [
            build_current_period_summary(
                connection,
                f"{sales_day_group_sql} = {today_value_sql}",
                f"{movement_day_group_sql} = {today_value_sql}",
                today_value_sql,
                "Daily",
                tenant_id,
            ),
            build_current_period_summary(
                connection,
                f"{sales_week_group_sql} = {sql_week_bucket(sql_now())}",
                f"{movement_week_group_sql} = {sql_week_bucket(sql_now())}",
                sql_week_bucket(sql_now()),
                "Weekly",
                tenant_id,
            ),
            build_current_period_summary(
                connection,
                f"{sales_month_group_sql} = {sql_month_bucket(sql_now())}",
                f"{movement_month_group_sql} = {sql_month_bucket(sql_now())}",
                sql_month_bucket(sql_now()),
                "Monthly",
                tenant_id,
            ),
            build_current_period_summary(
                connection,
                f"{sales_year_group_sql} = {sql_year_bucket(sql_now())}",
                f"{movement_year_group_sql} = {sql_year_bucket(sql_now())}",
                sql_year_bucket(sql_now()),
                "Yearly",
                tenant_id,
            ),
        ]

    return {
        "summary": report_summary,
        "daily_sales": daily_sales,
        "top_sellers": top_sellers,
        "current_periods": current_periods,
        "daily_inventory": daily_inventory,
        "weekly_inventory": weekly_inventory,
        "monthly_inventory": monthly_inventory,
        "yearly_inventory": yearly_inventory,
    }


def fetch_admin_context() -> dict[str, object]:
    products = fetch_admin_products()
    return {
        "metrics": fetch_dashboard_metrics(),
        "cash_reads": fetch_xz_read_context(),
        "today_shift": fetch_today_shift(),
        "recent_shifts": fetch_recent_shifts(),
        "products": products,
        "variants_map": fetch_variants_by_product(),
        "all_variants": fetch_product_variants(),
        "categories": fetch_lookup_rows("categories"),
        "brands": fetch_lookup_rows("brands"),
        "units": fetch_lookup_rows("units"),
        "users": fetch_users(),
        "audit_logs": fetch_recent_audit_logs(12),
        "backups": fetch_backup_rows(),
        "settings": fetch_app_settings(),
    }


def fetch_today_shift() -> dict | None:
    from datetime import date as _date

    tenant_id = session.get("tenant_id")
    if not tenant_id:
        return None
    today = _date.today().isoformat()
    with get_connection() as connection:
        row = connection.execute(
            """SELECT dis.*, u1.full_name AS opened_by_name, u2.full_name AS closed_by_name
               FROM daily_inventory_shifts dis
               LEFT JOIN users u1 ON dis.opened_by = u1.id AND u1.tenant_id = dis.tenant_id
               LEFT JOIN users u2 ON dis.closed_by = u2.id AND u2.tenant_id = dis.tenant_id
               WHERE dis.shift_date = ? AND dis.tenant_id = ?""",
            (today, tenant_id),
        ).fetchone()
    return dict(row) if row else None


def fetch_recent_shifts(limit: int = 7) -> list[dict]:
    tenant_id = session.get("tenant_id")
    if not tenant_id:
        return []
    with get_connection() as connection:
        rows = connection.execute(
            """SELECT dis.*, u1.full_name AS opened_by_name, u2.full_name AS closed_by_name
               FROM daily_inventory_shifts dis
               LEFT JOIN users u1 ON dis.opened_by = u1.id AND u1.tenant_id = dis.tenant_id
               LEFT JOIN users u2 ON dis.closed_by = u2.id AND u2.tenant_id = dis.tenant_id
               WHERE dis.tenant_id = ?
               ORDER BY dis.shift_date DESC LIMIT ?""",
            (tenant_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def fetch_inventory_context() -> dict[str, object]:
    products = fetch_admin_products()
    active_products = [product for product in products if product["status"] == "active"]
    open_stock_count, stock_count_items = fetch_open_stock_count()
    today_shift = fetch_today_shift()
    recent_shifts = fetch_recent_shifts()
    return {
        "metrics": fetch_dashboard_metrics(),
        "reports": fetch_reports_context(),
        "active_products": active_products,
        "variants_map": fetch_variants_by_product(),
        "inventory_watchlist": fetch_inventory_watchlist(),
        "recent_movements": fetch_recent_stock_movements(),
        "suppliers": fetch_suppliers(),
        "purchase_orders": fetch_purchase_orders(),
        "open_stock_count": open_stock_count,
        "stock_count_items": stock_count_items,
        "sales_controls": fetch_sales_for_control(),
        "today_shift": today_shift,
        "recent_shifts": recent_shifts,
        "units": fetch_lookup_rows("units"),
    }


def fetch_owner_context() -> dict[str, object]:
    from app.core.subscription import entitlements as sub_entitlements

    tenant_id = session.get("tenant_id")
    tenant_plan: dict[str, object] = {}
    demo_limits: dict[str, object] | None = None
    if tenant_id:
        with get_connection() as connection:
            row = connection.execute(
                """
                SELECT plan_name, subscription_status, monthly_fee, billing_currency
                FROM tenants WHERE id = ?
                """,
                (tenant_id,),
            ).fetchone()
            if row is not None:
                tenant_plan = dict(row)
            demo_limits = sub_entitlements.demo_limits_summary(connection, int(tenant_id))

    return {
        "metrics": fetch_dashboard_metrics(),
        "cash_reads": fetch_xz_read_context(),
        "reports": fetch_reports_context(),
        "audit_logs": fetch_recent_audit_logs(20),
        "owner_alerts": fetch_owner_alerts(20),
        "backups": fetch_backup_rows(),
        "settings": fetch_app_settings(),
        "inventory_watchlist": fetch_inventory_watchlist(),
        "sales_controls": fetch_sales_for_control(12),
        "recent_shifts": fetch_recent_shifts(),
        "tenant_plan": tenant_plan,
        "demo_limits": demo_limits,
    }


def fetch_open_cash_shift() -> dict | None:
    tenant_id = session.get("tenant_id")
    user_id = session_user_id()
    if not tenant_id or not user_id:
        return None
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT crs.*, u.full_name AS cashier_name
            FROM cash_register_shifts crs
            LEFT JOIN users u ON u.id = crs.cashier_user_id AND u.tenant_id = crs.tenant_id
            WHERE crs.tenant_id = ? AND crs.cashier_user_id = ? AND crs.status = 'open'
            ORDER BY crs.id DESC
            LIMIT 1
            """,
            (tenant_id, user_id),
        ).fetchone()
    return dict(row) if row else None


def fetch_recent_cash_shifts(limit: int = 5) -> list[dict]:
    tenant_id = session.get("tenant_id")
    user_id = session_user_id()
    if not tenant_id or not user_id:
        return []
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT id, opened_at, closed_at, opening_cash, expected_cash, actual_cash, variance, status
            FROM cash_register_shifts
            WHERE tenant_id = ? AND cashier_user_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (tenant_id, user_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def fetch_xz_read_context() -> dict[str, object]:
    tenant_id = session.get("tenant_id")
    if not tenant_id:
        return {
            "x_read": {
                "receipt_count": 0,
                "gross_sales": 0.0,
                "discounts": 0.0,
                "vat": 0.0,
                "net_sales": 0.0,
                "cash_sales": 0.0,
                "card_sales": 0.0,
                "wallet_sales": 0.0,
            },
            "z_read": {"closed_shift_count": 0, "expected_cash": 0.0, "actual_cash": 0.0, "variance": 0.0},
            "recent_shift_rows": [],
        }

    with get_connection() as connection:
        x_row = connection.execute(
            f"""
            SELECT
                COUNT(*) AS receipt_count,
                COALESCE(SUM(subtotal), 0) AS gross_sales,
                COALESCE(SUM(discount_amount), 0) AS discounts,
                COALESCE(SUM(tax), 0) AS vat,
                COALESCE(SUM(total), 0) AS net_sales,
                COALESCE(SUM(CASE WHEN payment_method = 'Cash' THEN total ELSE 0 END), 0) AS cash_sales,
                COALESCE(SUM(CASE WHEN payment_method = 'Card' THEN total ELSE 0 END), 0) AS card_sales,
                COALESCE(SUM(CASE WHEN payment_method IN ('Mobile Wallet', 'GCash') THEN total ELSE 0 END), 0) AS wallet_sales
            FROM sales
            WHERE tenant_id = ? AND status = 'completed' AND {sql_date_column_eq_today("created_at")}
            """,
            (tenant_id,),
        ).fetchone()

        z_row = connection.execute(
            f"""
            SELECT
                COUNT(*) AS closed_shift_count,
                COALESCE(SUM(expected_cash), 0) AS expected_cash,
                COALESCE(SUM(actual_cash), 0) AS actual_cash,
                COALESCE(SUM(variance), 0) AS variance
            FROM cash_register_shifts
            WHERE tenant_id = ? AND status = 'closed' AND {sql_date_column_eq_today("closed_at")}
            """,
            (tenant_id,),
        ).fetchone()

        shift_rows = connection.execute(
            """
            SELECT id, cashier_user_id, opened_at, closed_at, opening_cash, expected_cash, actual_cash, variance, status
            FROM cash_register_shifts
            WHERE tenant_id = ?
            ORDER BY id DESC
            LIMIT 12
            """,
            (tenant_id,),
        ).fetchall()

    return {
        "x_read": dict(x_row),
        "z_read": dict(z_row),
        "recent_shift_rows": [dict(r) for r in shift_rows],
    }


def build_pos_categories(products: list[sqlite3.Row]) -> list[dict[str, object]]:
    categories: list[dict[str, object]] = []
    current_category_id: object = object()
    current_bucket: dict[str, object] | None = None

    for product in products:
        if product["category_id"] != current_category_id:
            current_category_id = product["category_id"]
            current_bucket = {
                "id": product["category_id"],
                "name": product["category_name"],
                "products": [],
            }
            categories.append(current_bucket)

        current_bucket["products"].append(product)

    return categories
