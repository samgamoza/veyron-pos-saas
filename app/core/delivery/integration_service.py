from __future__ import annotations

from typing import Optional

from app.core.audit import audit_service
from app.core.delivery.service import DeliveryService


class DeliveryIntegrationService:
    def __init__(self) -> None:
        self.delivery_service = DeliveryService()

    def create_delivery_order_if_requested(
        self,
        tenant_id: int,
        order_id: int,
        delivery_enabled: bool,
        delivery_address: str,
        delivery_instructions: str,
        order_total: float,
        distance_km: float = 0.0,
    ) -> Optional[int]:
        if not delivery_enabled:
            return None

        address = delivery_address.strip()
        if not address:
            raise ValueError("Delivery address is required when delivery is requested.")

        delivery_fee = self.delivery_service.calculate_delivery_fee(
            tenant_id=tenant_id,
            order_total=order_total,
            distance_km=distance_km,
        )

        delivery_order_id = self.delivery_service.create_delivery_order(
            tenant_id=tenant_id,
            order_id=order_id,
            address=address,
            instructions=delivery_instructions.strip(),
            delivery_fee=delivery_fee,
        )
        audit_service.log(
            action="create",
            entity_type="delivery_order",
            entity_id=delivery_order_id,
            details=f"Delivery order created for sale {order_id} with fee {delivery_fee:.2f}",
            tenant_id=tenant_id,
        )
        return delivery_order_id
