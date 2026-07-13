from __future__ import annotations

from typing import Any

from app.core.db import get_connection
from app.core.image_store import ImageStore


class ProductRepository:
    def __init__(self, image_store: ImageStore | None = None) -> None:
        self.image_store = image_store or ImageStore()

    def validate_product_lookups(self, category_id: int, brand_id: int, unit_id: int) -> None:
        with get_connection() as connection:
            lookup_counts = connection.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM categories WHERE id = ?) AS category_match,
                    (SELECT COUNT(*) FROM brands WHERE id = ?) AS brand_match,
                    (SELECT COUNT(*) FROM units WHERE id = ?) AS unit_match
                """,
                (category_id, brand_id, unit_id),
            ).fetchone()

            if not all(lookup_counts[key] == 1 for key in lookup_counts.keys()):
                raise ValueError("Choose valid category, brand, and unit values.")

    def get_category_name(self, category_id: int) -> str | None:
        with get_connection() as connection:
            row = connection.execute(
                "SELECT name FROM categories WHERE id = ?",
                (category_id,),
            ).fetchone()
            return row["name"] if row else None

    def get_next_sort_order(self) -> int:
        with get_connection() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(sort_order), 0) + 1 AS next_value FROM products"
            ).fetchone()
            return int(row["next_value"])

    def sku_exists(self, sku: str) -> bool:
        with get_connection() as connection:
            return connection.execute(
                "SELECT 1 FROM products WHERE sku = ?",
                (sku,),
            ).fetchone() is not None

    def generate_sku(self, product_name: str, category_name: str | None = None) -> str:
        prefix_source = category_name or product_name
        prefix = "".join(char for char in prefix_source.upper() if char.isalnum())[:3] or "PRD"
        attempt = 101
        while True:
            sku = f"{prefix}-{attempt}"
            if not self.sku_exists(sku):
                return sku
            attempt += 1

    def insert_product(
        self,
        name: str,
        sku: str,
        price: float,
        cost: float,
        stock: int,
        reorder_level: int,
        category_id: int,
        brand_id: int,
        unit_id: int,
        status: str,
        image_file: Any,
        sort_order: int,
    ) -> int:
        saved_image = self.image_store.save_product_image(image_file)
        with get_connection() as connection:
            row = connection.execute(
                """
                INSERT INTO products (
                    name, sku, price, cost, stock, reorder_level,
                    category_id, brand_id, unit_id, status, last_restocked,
                    sort_order, image_path, last_synced_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CASE WHEN ? > 0 THEN CURRENT_TIMESTAMP ELSE NULL END, ?, ?, CURRENT_TIMESTAMP)
                RETURNING id
                """,
                (
                    name,
                    sku,
                    price,
                    cost,
                    stock,
                    reorder_level,
                    category_id,
                    brand_id,
                    unit_id,
                    status,
                    stock,
                    sort_order,
                    saved_image,
                ),
            ).fetchone()
            return int(row["id"])

    def get_product(self, product_id: int) -> dict | None:
        with get_connection() as connection:
            return connection.execute(
                "SELECT id, name, stock, status FROM products WHERE id = ?",
                (product_id,),
            ).fetchone()

    def get_product_sales_count(self, product_id: int) -> int:
        with get_connection() as connection:
            return int(
                connection.execute(
                    "SELECT COUNT(*) AS count FROM sale_items WHERE product_id = ?",
                    (product_id,),
                ).fetchone()["count"]
            )

    def update_product(
        self,
        product_id: int,
        name: str,
        price: float,
        cost: float,
        reorder_level: int,
        category_id: int,
        brand_id: int,
        unit_id: int,
        status: str,
        image_file: Any,
    ) -> None:
        saved_image = self.image_store.save_product_image(image_file)
        with get_connection() as connection:
            if saved_image:
                connection.execute(
                    """
                    UPDATE products
                    SET name = ?, price = ?, cost = ?, reorder_level = ?, category_id = ?, brand_id = ?, unit_id = ?, status = ?, image_path = ?, last_synced_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (
                        name,
                        price,
                        cost,
                        reorder_level,
                        category_id,
                        brand_id,
                        unit_id,
                        status,
                        saved_image,
                        product_id,
                    ),
                )
            else:
                connection.execute(
                    """
                    UPDATE products
                    SET name = ?, price = ?, cost = ?, reorder_level = ?, category_id = ?, brand_id = ?, unit_id = ?, status = ?, last_synced_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (
                        name,
                        price,
                        cost,
                        reorder_level,
                        category_id,
                        brand_id,
                        unit_id,
                        status,
                        product_id,
                    ),
                )

    def archive_product(self, product_id: int) -> None:
        with get_connection() as connection:
            connection.execute(
                "UPDATE products SET status = 'inactive', last_synced_at = CURRENT_TIMESTAMP WHERE id = ?",
                (product_id,),
            )

    def delete_product(self, product_id: int) -> None:
        with get_connection() as connection:
            connection.execute("DELETE FROM stock_movements WHERE product_id = ?", (product_id,))
            connection.execute("DELETE FROM products WHERE id = ?", (product_id,))

    def reorder_product(self, product_id: int, requested_position: int) -> int:
        with get_connection() as connection:
            ordered_ids = [row["id"] for row in connection.execute("SELECT id FROM products ORDER BY sort_order ASC, id ASC").fetchall()]
            if product_id not in ordered_ids:
                raise ValueError("Product not found.")

            ordered_ids.remove(product_id)
            target_index = max(0, min(requested_position - 1, len(ordered_ids)))
            ordered_ids.insert(target_index, product_id)
            connection.executemany(
                "UPDATE products SET sort_order = ? WHERE id = ?",
                [(position, row_product_id) for position, row_product_id in enumerate(ordered_ids, start=1)],
            )
            return target_index + 1

    def insert_stock_movement(self, product_id: int, quantity_change: int, reason: str, variant_id: int | None = None) -> None:
        with get_connection() as connection:
            connection.execute(
                "INSERT INTO stock_movements (product_id, variant_id, quantity_change, reason) VALUES (?, ?, ?, ?)",
                (product_id, variant_id, quantity_change, reason),
            )
