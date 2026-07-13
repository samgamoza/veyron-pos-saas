from __future__ import annotations

from typing import Any

from app.core.delivery.service import DeliveryService

_delivery = DeliveryService()


class DeliveryApiService:
    def create(self, tenant_id: int, order_id: int, address: str, instructions: str, delivery_fee: float) -> int:
        return _delivery.create_delivery_order(
            tenant_id=tenant_id,
            order_id=order_id,
            address=address,
            instructions=instructions or "",
            delivery_fee=float(delivery_fee or 0),
        )

    def assign_rider(self, tenant_id: int, delivery_order_id: int, rider_id: int) -> None:
        _delivery.assign_rider(tenant_id, delivery_order_id, rider_id)

    def update_status(self, tenant_id: int, delivery_order_id: int, status: str) -> None:
        _delivery.update_delivery_status(tenant_id, delivery_order_id, status)

    def list_orders(self, tenant_id: int, status: str | None = None) -> list[dict[str, Any]]:
        return _delivery.list_delivery_orders(tenant_id, status)


delivery_api_service = DeliveryApiService()
