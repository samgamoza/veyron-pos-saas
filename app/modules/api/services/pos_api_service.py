from __future__ import annotations

from typing import Any

from app.core.db import get_connection
from app.core.db_integrity import DB_INTEGRITY_ERRORS
from app.core.pos_finalize import finalize_pos_sale, find_sale_by_idempotency_key
from app.core.pos_profiles import normalize_pos_profile_id
from app.core.tax import compute_sale_totals, load_vat_config
from app.modules.web import queries as web_queries
DISCOUNT_PRESETS: dict[str, float | None] = {
    "none": 0.0,
    "senior": 0.20,
    "pwd": 0.20,
    "custom": None,
}


class PosApiService:
    @staticmethod
    def parse_optional_payment_lines(body: dict[str, Any]) -> list[dict[str, Any]] | None:
        raw = body.get("payments")
        if raw is None:
            return None
        if not isinstance(raw, list):
            raise ValueError("payments must be an array.")
        out: list[dict[str, Any]] = []
        for p in raw:
            if not isinstance(p, dict):
                raise ValueError("Each payment must be an object.")
            out.append(
                {
                    "amount": float(p["amount"]),
                    "method": str(p.get("method", "cash")),
                    "status": str(p.get("status", "completed")),
                    "reference": str(p.get("reference", "") or ""),
                }
            )
        return out

    def list_orders(self, tenant_id: int, limit: int = 50) -> list[dict[str, Any]]:
        lim = max(1, min(limit, 200))
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT id, created_at, subtotal, total, payment_method, status, cashier_user_id
                FROM sales
                WHERE tenant_id = ?
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (tenant_id, lim),
            ).fetchall()
        return [dict(r) for r in rows]

    def create_order(
        self,
        tenant_id: int,
        cashier_user_id: int | None,
        lines: list[dict[str, Any]],
        payment_method: str,
        discount_type: str,
        discount_note: str,
        custom_discount_rate: float,
        service_reference: str = "",
        idempotency_key: str | None = None,
        payment_lines: list[dict[str, Any]] | None = None,
    ) -> tuple[int, bool]:
        if not lines:
            raise ValueError("At least one line item is required.")

        discount_type = (discount_type or "none").strip().lower()
        if discount_type not in DISCOUNT_PRESETS:
            raise ValueError("Invalid discount_type.")

        if discount_type == "custom":
            discount_rate = max(0.0, min(1.0, float(custom_discount_rate)))
        else:
            dr = DISCOUNT_PRESETS[discount_type]
            discount_rate = float(dr or 0.0)

        idem = (idempotency_key or "").strip()[:120] or None

        cart: list[dict[str, Any]] = []
        try:
            with get_connection() as connection:
                tprof = connection.execute(
                    "SELECT pos_profile FROM tenants WHERE id = ?",
                    (tenant_id,),
                ).fetchone()
                profile_slug = normalize_pos_profile_id(tprof["pos_profile"] if tprof else None)
                ref_stored = (
                    (service_reference or "").strip()[:120] if profile_slug == "restaurant_table_service" else ""
                )

                for raw in lines:
                    product_id = int(raw["product_id"])
                    quantity = int(raw["quantity"])
                    if quantity <= 0:
                        continue
                    variant_id = int(raw["variant_id"]) if raw.get("variant_id") not in (None, "", 0) else None

                    product = connection.execute(
                        """
                        SELECT id, name, sku, price, stock, status
                        FROM products
                        WHERE id = ? AND tenant_id = ?
                        """,
                        (product_id, tenant_id),
                    ).fetchone()
                    if product is None or product["status"] != "active":
                        raise ValueError(f"Product {product_id} not available.")

                    if variant_id:
                        variant = connection.execute(
                            """
                            SELECT id, name, sku_suffix, price, stock
                            FROM product_variants
                            WHERE id = ? AND product_id = ? AND tenant_id = ? AND is_active = 1
                            """,
                            (variant_id, product["id"], tenant_id),
                        ).fetchone()
                        if variant is None:
                            raise ValueError(f"Variant {variant_id} not found.")
                        effective_price = float(variant["price"])
                        effective_stock = int(variant["stock"])
                        display_name = f"{product['name']} ({variant['name']})"
                        effective_sku = (
                            f"{product['sku']}{variant['sku_suffix']}" if variant["sku_suffix"] else product["sku"]
                        )
                    else:
                        effective_price = float(product["price"])
                        effective_stock = int(product["stock"])
                        display_name = product["name"]
                        effective_sku = product["sku"]

                    if quantity > effective_stock:
                        raise ValueError(f"Insufficient stock for {display_name}.")

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
                    raise ValueError("No valid line items.")

                subtotal = round(sum(item["line_total"] for item in cart), 2)
                # VAT per tenant settings (senior/PWD are VAT-exempt; see app/core/tax.py).
                vat_totals = compute_sale_totals(
                    subtotal,
                    discount_rate,
                    config=load_vat_config(connection, tenant_id),
                    discount_type=discount_type,
                )
                discount_amount = vat_totals.discount_amount
                tax = vat_totals.vat_amount
                total = vat_totals.total

                open_shift = web_queries.fetch_open_cash_shift()
                cash_shift_id = int(open_shift["id"]) if open_shift else None

                return finalize_pos_sale(
                    connection,
                    tenant_id=tenant_id,
                    cashier_user_id=cashier_user_id,
                    cart=cart,
                    subtotal=subtotal,
                    discount_type=discount_type,
                    discount_rate=discount_rate,
                    discount_amount=discount_amount,
                    discount_note=discount_note or "",
                    service_reference=ref_stored,
                    tax=tax,
                    total=total,
                    payment_method=payment_method,
                    cash_shift_id=cash_shift_id,
                    idempotency_key=idem,
                    payment_lines=payment_lines,
                )
        except DB_INTEGRITY_ERRORS:
            if idem:
                with get_connection() as c2:
                    dup = find_sale_by_idempotency_key(c2, tenant_id, idem)
                if dup is not None:
                    return dup, False
            raise


pos_api_service = PosApiService()
