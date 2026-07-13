from __future__ import annotations

from typing import Any

from flask import has_request_context

from app.core.constants import ALLOWED_INVENTORY_REASONS
from app.core.tenant.context import current_tenant_id


def log_stock_movement(
    connection: Any,
    product_id: int,
    quantity_change: int,
    reason: str,
    variant_id: int | None = None,
    *,
    tenant_id: int | None = None,
) -> None:
    if quantity_change == 0 or reason not in ALLOWED_INVENTORY_REASONS:
        return

    tid = tenant_id
    if tid is None and has_request_context():
        tid = current_tenant_id()
    if tid is None:
        raise RuntimeError("log_stock_movement requires tenant_id or active tenant request context.")

    connection.execute(
        """
        INSERT INTO stock_movements (tenant_id, product_id, variant_id, quantity_change, reason)
        VALUES (?, ?, ?, ?, ?)
        """,
        (tid, product_id, variant_id, quantity_change, reason),
    )
