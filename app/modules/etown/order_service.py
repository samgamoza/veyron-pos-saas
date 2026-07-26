"""
eTown marketplace reads/writes the **same** database as Veyron POS (``get_raw_connection`` /
``DATABASE_URL`` or SQLite path). Marketplace rows reference ``tenants`` and ``products``;
they do not duplicate catalog tables or use a second DSN.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.core.db import DATABASE_ENGINE, get_raw_connection
from app.core.tax import compute_sale_totals, load_vat_config


@dataclass
class OrderLineIn:
    product_id: int
    quantity: int
    variant_id: int | None = None


class MarketplaceOrderService:
    """Creates eTown marketplace orders (not POS sales). Uses raw DB connection to avoid tenant-scope issues."""

    def list_marketplace_tenants(self) -> list[dict[str, Any]]:
        with get_raw_connection() as connection:
            rows = connection.execute(
                """
                SELECT id, name, subdomain, storefront_url, storefront_template
                FROM tenants
                WHERE is_active = 1 AND marketplace_enabled = 1
                ORDER BY name ASC
                """
            ).fetchall()
        return [dict(r) for r in rows]

    def get_tenant_public(self, tenant_id: int) -> dict[str, Any] | None:
        with get_raw_connection() as connection:
            row = connection.execute(
                """
                SELECT id, name, subdomain, storefront_url, storefront_template, marketplace_enabled
                FROM tenants
                WHERE id = ? AND is_active = 1 AND marketplace_enabled = 1
                """,
                (tenant_id,),
            ).fetchone()
        return dict(row) if row else None

    def get_orderable_tenant(self, tenant_id: int) -> dict[str, Any] | None:
        """Tenant lookup for scan-to-order — works for ANY active merchant.

        Unlike get_tenant_public this does not require marketplace opt-in; the
        merchant's own QR ordering page is gated only by being active and having
        the ``qr_ordering_enabled`` setting on (default on).
        """
        with get_raw_connection() as connection:
            row = connection.execute(
                "SELECT id, name, subdomain FROM tenants WHERE id = ? AND is_active = 1",
                (tenant_id,),
            ).fetchone()
            if row is None:
                return None
            setting = connection.execute(
                "SELECT value FROM app_settings WHERE tenant_id = ? AND key = 'qr_ordering_enabled'",
                (tenant_id,),
            ).fetchone()
        if setting is not None and str(setting["value"]).strip() == "0":
            return None
        return dict(row)

    def list_public_products(self, tenant_id: int) -> list[dict[str, Any]]:
        with get_raw_connection() as connection:
            rows = connection.execute(
                """
                SELECT
                    p.id,
                    p.name,
                    p.sku,
                    p.price,
                    p.stock,
                    p.status,
                    p.image_path,
                    p.is_public,
                    c.name AS category_name,
                    u.symbol AS unit_symbol
                FROM products p
                LEFT JOIN categories c ON c.id = p.category_id AND c.tenant_id = p.tenant_id
                LEFT JOIN units u ON u.id = p.unit_id AND u.tenant_id = p.tenant_id
                WHERE p.tenant_id = ?
                  AND p.is_public = 1
                  AND p.status = 'active'
                ORDER BY p.sort_order ASC, p.id ASC
                """,
                (tenant_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def list_public_variants_by_product(self, tenant_id: int) -> dict[int, list[dict[str, Any]]]:
        with get_raw_connection() as connection:
            rows = connection.execute(
                """
                SELECT
                    pv.id,
                    pv.product_id,
                    pv.name,
                    pv.sku_suffix,
                    pv.price,
                    pv.stock
                FROM product_variants pv
                JOIN products p ON p.id = pv.product_id AND p.tenant_id = pv.tenant_id
                WHERE pv.tenant_id = ?
                  AND pv.is_active = 1
                  AND p.is_public = 1
                  AND p.status = 'active'
                ORDER BY pv.sort_order ASC, pv.id ASC
                """,
                (tenant_id,),
            ).fetchall()
        out: dict[int, list[dict[str, Any]]] = {}
        for r in rows:
            d = dict(r)
            pid = int(d["product_id"])
            out.setdefault(pid, []).append(d)
        return out

    def _ensure_customer(self, connection: Any, tenant_id: int, phone: str, full_name: str) -> int | None:
        """Look up or create a customer WITHIN the given tenant.

        Customers are tenant-scoped: the same phone number may exist under many
        merchants, and one merchant must never read or overwrite another's record.
        """
        phone = phone.strip()
        if not phone:
            return None
        full_name = (full_name or "").strip()
        row = connection.execute(
            "SELECT id FROM customers WHERE tenant_id = ? AND phone = ?",
            (tenant_id, phone),
        ).fetchone()
        if row:
            cid = int(row["id"])
            if full_name:
                connection.execute(
                    "UPDATE customers SET full_name = ? WHERE tenant_id = ? AND id = ?",
                    (full_name, tenant_id, cid),
                )
            return cid
        if DATABASE_ENGINE == "postgres":
            ins = connection.execute(
                "INSERT INTO customers (tenant_id, phone, full_name) VALUES (?, ?, ?) RETURNING id",
                (tenant_id, phone, full_name),
            ).fetchone()
            return int(ins["id"]) if ins else None
        connection.execute(
            "INSERT INTO customers (tenant_id, phone, full_name) VALUES (?, ?, ?)",
            (tenant_id, phone, full_name),
        )
        ins = connection.execute("SELECT last_insert_rowid() AS id").fetchone()
        return int(ins["id"]) if ins else None

    def create_order(
        self,
        tenant_id: int,
        lines: list[OrderLineIn],
        *,
        guest_name: str,
        guest_phone: str,
        notes: str = "",
        delivery_line1: str = "",
        delivery_line2: str = "",
        delivery_city: str = "",
        delivery_notes: str = "",
        customer_phone: str | None = None,
        customer_full_name: str | None = None,
        require_marketplace: bool = True,
    ) -> int:
        if not lines:
            raise ValueError("Cart is empty.")
        guest_name = (guest_name or "").strip()
        guest_phone = (guest_phone or "").strip()
        if not guest_name or not guest_phone:
            raise ValueError("Customer name and phone are required.")

        conn = get_raw_connection()
        try:
            if DATABASE_ENGINE != "postgres":
                conn.execute("BEGIN IMMEDIATE")
            else:
                conn.execute("BEGIN")

            tenant = conn.execute(
                "SELECT id, marketplace_enabled FROM tenants WHERE id = ? AND is_active = 1",
                (tenant_id,),
            ).fetchone()
            if tenant is None:
                raise ValueError("Store not found.")
            # eTown marketplace requires opt-in; a merchant's own QR order page does not.
            if require_marketplace and not int(tenant["marketplace_enabled"] or 0):
                raise ValueError("This store is not on the marketplace.")

            resolved: list[dict[str, Any]] = []
            subtotal = 0.0

            for line in lines:
                if line.quantity <= 0:
                    raise ValueError("Invalid quantity.")

                vcount = conn.execute(
                    """
                    SELECT COUNT(*) AS c FROM product_variants
                    WHERE product_id = ? AND tenant_id = ? AND is_active = 1
                    """,
                    (line.product_id, tenant_id),
                ).fetchone()
                has_variants = vcount and int(vcount["c"] or 0) > 0

                if line.variant_id:
                    row = conn.execute(
                        """
                        SELECT pv.id, pv.product_id, pv.name AS variant_name, pv.price, pv.stock, pv.is_active,
                               p.tenant_id, p.status, p.is_public, p.name AS product_name
                        FROM product_variants pv
                        JOIN products p ON p.id = pv.product_id AND p.tenant_id = pv.tenant_id
                        WHERE pv.id = ? AND pv.product_id = ? AND pv.tenant_id = ?
                        """,
                        (line.variant_id, line.product_id, tenant_id),
                    ).fetchone()
                    if row is None or not row["is_active"]:
                        raise ValueError("Invalid product variant.")
                    if int(row["is_public"] or 0) != 1 or row["status"] != "active":
                        raise ValueError("Product is not available online.")
                    if line.quantity > int(row["stock"]):
                        raise ValueError("Not enough stock for this variant.")
                    unit_price = float(row["price"])
                    line_total = unit_price * line.quantity
                    subtotal += line_total
                    pname = row["product_name"] or ""
                    vname = row["variant_name"] or ""
                    snapshot = f"{pname} ({vname})" if vname else pname
                    resolved.append(
                        {
                            "product_id": line.product_id,
                            "variant_id": line.variant_id,
                            "quantity": line.quantity,
                            "unit_price": unit_price,
                            "line_total": line_total,
                            "product_name": snapshot,
                        }
                    )
                else:
                    if has_variants:
                        raise ValueError("This product has options — please choose a variant.")
                    row = conn.execute(
                        """
                        SELECT id, tenant_id, name, price, stock, status, is_public
                        FROM products
                        WHERE id = ? AND tenant_id = ?
                        """,
                        (line.product_id, tenant_id),
                    ).fetchone()
                    if row is None:
                        raise ValueError("Product not found.")
                    if int(row["is_public"] or 0) != 1 or row["status"] != "active":
                        raise ValueError("Product is not available online.")
                    if line.quantity > int(row["stock"]):
                        raise ValueError("Not enough stock for this product.")
                    unit_price = float(row["price"])
                    line_total = unit_price * line.quantity
                    subtotal += line_total
                    resolved.append(
                        {
                            "product_id": line.product_id,
                            "variant_id": None,
                            "quantity": line.quantity,
                            "unit_price": unit_price,
                            "line_total": line_total,
                            "product_name": (row["name"] or "")[:500],
                        }
                    )

            vat_totals = compute_sale_totals(
                subtotal, config=load_vat_config(conn, tenant_id), discount_type="none"
            )
            tax = vat_totals.vat_amount
            total = vat_totals.total

            phone_for_customer = (customer_phone or guest_phone).strip()
            name_for_customer = (customer_full_name or guest_name).strip()
            customer_id = self._ensure_customer(conn, tenant_id, phone_for_customer, name_for_customer)
            customer_address_id = None

            if DATABASE_ENGINE == "postgres":
                orow = conn.execute(
                    """
                    INSERT INTO orders (
                        tenant_id, customer_id, customer_address_id,
                        guest_name, guest_phone, status,
                        subtotal, tax, total, notes,
                        delivery_line1, delivery_line2, delivery_city, delivery_notes
                    ) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?)
                    RETURNING id
                    """,
                    (
                        tenant_id,
                        customer_id,
                        customer_address_id,
                        guest_name,
                        guest_phone,
                        subtotal,
                        tax,
                        total,
                        notes.strip(),
                        delivery_line1.strip(),
                        delivery_line2.strip(),
                        delivery_city.strip(),
                        delivery_notes.strip(),
                    ),
                ).fetchone()
                order_id = int(orow["id"]) if orow else 0
            else:
                conn.execute(
                    """
                    INSERT INTO orders (
                        tenant_id, customer_id, customer_address_id,
                        guest_name, guest_phone, status,
                        subtotal, tax, total, notes,
                        delivery_line1, delivery_line2, delivery_city, delivery_notes
                    ) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        tenant_id,
                        customer_id,
                        customer_address_id,
                        guest_name,
                        guest_phone,
                        subtotal,
                        tax,
                        total,
                        notes.strip(),
                        delivery_line1.strip(),
                        delivery_line2.strip(),
                        delivery_city.strip(),
                        delivery_notes.strip(),
                    ),
                )
                lid = conn.execute("SELECT last_insert_rowid() AS id").fetchone()
                order_id = int(lid["id"]) if lid else 0

            line_created = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            for rline in resolved:
                conn.execute(
                    """
                    INSERT INTO order_items (
                        tenant_id, order_id, product_id, variant_id, product_name,
                        quantity, unit_price, line_total, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        tenant_id,
                        order_id,
                        rline["product_id"],
                        rline["variant_id"],
                        (rline.get("product_name") or "")[:500],
                        rline["quantity"],
                        rline["unit_price"],
                        rline["line_total"],
                        line_created,
                    ),
                )

            # Accrue loyalty points for the ordering customer (no-op if disabled).
            if customer_id:
                from app.core.loyalty import LoyaltyService

                LoyaltyService(conn).earn_for_sale(tenant_id, int(customer_id), None, float(total))

            if DATABASE_ENGINE == "postgres":
                conn.connection.commit()
            else:
                conn.commit()
            return order_id
        except Exception:
            if DATABASE_ENGINE == "postgres":
                conn.connection.rollback()
            else:
                conn.rollback()
            raise
        finally:
            if DATABASE_ENGINE == "postgres":
                conn.connection.close()
            else:
                conn.close()
