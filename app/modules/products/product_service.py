from __future__ import annotations

from typing import Any

from flask import session

from app.core.constants import ALLOWED_PRODUCT_STATUSES
from app.core.db import get_connection
from app.core.subscription.entitlements import assert_demo_allows_new_product
from app.modules.products.product_repository import ProductRepository
from app.modules.products.sync_service import ProductSyncService


class ProductService:
    def __init__(self) -> None:
        self.repository = ProductRepository()
        self.sync_service = ProductSyncService()

    def add_product(
        self,
        name: str,
        price: float,
        cost: float,
        stock: int,
        reorder_level: int,
        category_id: int,
        brand_id: int,
        unit_id: int,
        status: str,
        image_file: Any,
    ) -> int:
        if status not in ALLOWED_PRODUCT_STATUSES:
            raise ValueError("Invalid product status.")

        tenant_id = session.get("tenant_id")
        if tenant_id:
            with get_connection() as connection:
                assert_demo_allows_new_product(connection, int(tenant_id))

        self.repository.validate_product_lookups(category_id, brand_id, unit_id)
        category_name = self.repository.get_category_name(category_id)
        if category_name is None:
            raise ValueError("Choose valid category, brand, and unit values.")

        sku = self.repository.generate_sku(name, category_name)
        sort_order = self.repository.get_next_sort_order()
        product_id = self.repository.insert_product(
            name=name,
            sku=sku,
            price=price,
            cost=cost,
            stock=stock,
            reorder_level=reorder_level,
            category_id=category_id,
            brand_id=brand_id,
            unit_id=unit_id,
            status=status,
            image_file=image_file,
            sort_order=sort_order,
        )

        self.repository.insert_stock_movement(product_id, stock, "opening_balance")
        self.sync_service.sync_product(product_id)
        return product_id

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
        if status not in ALLOWED_PRODUCT_STATUSES:
            raise ValueError("Invalid product status.")

        product = self.repository.get_product(product_id)
        if product is None:
            raise ValueError("Product not found.")

        self.repository.validate_product_lookups(category_id, brand_id, unit_id)
        self.repository.update_product(
            product_id=product_id,
            name=name,
            price=price,
            cost=cost,
            reorder_level=reorder_level,
            category_id=category_id,
            brand_id=brand_id,
            unit_id=unit_id,
            status=status,
            image_file=image_file,
        )
        self.sync_service.sync_product(product_id)

    def move_product(self, product_id: int, requested_position: int) -> int:
        if self.repository.get_product(product_id) is None:
            raise ValueError("Product not found.")

        final_position = self.repository.reorder_product(product_id, requested_position)
        self.sync_service.sync_product(product_id)
        return final_position

    def remove_product(self, product_id: int) -> tuple[str, bool]:
        product = self.repository.get_product(product_id)
        if product is None:
            raise ValueError("Product not found.")

        sales_count = self.repository.get_product_sales_count(product_id)
        if product["stock"] > 0 or sales_count > 0:
            self.repository.archive_product(product_id)
            self.sync_service.sync_product(product_id)
            if product["stock"] > 0:
                return ("Product archived and removed from cashier view. Existing stock remains recorded until you adjust it.", True)
            return ("Product archived instead of deleted because it has sales history.", True)

        self.repository.delete_product(product_id)
        return ("Product permanently deleted.", False)
