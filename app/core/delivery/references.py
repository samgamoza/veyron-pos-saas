"""Polymorphic references for delivery_orders.order_id."""

from __future__ import annotations

REFERENCE_SALE = "sale"
REFERENCE_ONLINE_ORDER = "online_order"

ALL_REFERENCE_TYPES = {REFERENCE_SALE, REFERENCE_ONLINE_ORDER}


def format_delivery_address(
    line1: str,
    *,
    line2: str = "",
    city: str = "",
) -> str:
    parts = [part.strip() for part in (line1, line2, city) if part and part.strip()]
    return ", ".join(parts)
