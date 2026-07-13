from __future__ import annotations

from app.core.db import get_connection


class ProductSyncService:
    def sync_product(self, product_id: int) -> None:
        with get_connection() as connection:
            connection.execute(
                "UPDATE products SET last_synced_at = CURRENT_TIMESTAMP WHERE id = ?",
                (product_id,),
            )
