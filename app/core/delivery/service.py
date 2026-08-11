from __future__ import annotations

from typing import Any

from app.core.audit import audit_service
from app.core.delivery.models import DeliverySettings
from app.core.delivery.repository import DeliveryRepository
from app.core.delivery.references import REFERENCE_SALE


class DeliveryStatus:
    PENDING = "pending"
    ASSIGNED = "assigned"
    IN_TRANSIT = "in_transit"
    DELIVERED = "delivered"

    ALL_STATUSES = {PENDING, ASSIGNED, IN_TRANSIT, DELIVERED}


class DeliveryService:
    def __init__(self) -> None:
        self.repository = DeliveryRepository()

    def ensure_delivery_enabled(self, tenant_id: int) -> DeliverySettings:
        settings = self.repository.get_tenant_delivery_settings(tenant_id)
        if settings is None or not settings.delivery_enabled:
            raise ValueError("Delivery is not enabled for this tenant.")
        return settings

    def calculate_delivery_fee(self, tenant_id: int, order_total: float, distance_km: float = 0.0) -> float:
        settings = self.ensure_delivery_enabled(tenant_id)
        if settings.delivery_fee_free_threshold > 0 and order_total >= settings.delivery_fee_free_threshold:
            return 0.0
        fee_value = settings.delivery_fee_base + settings.delivery_fee_per_km * max(0.0, distance_km)
        return round(max(0.0, fee_value), 2)

    def create_rider(self, tenant_id: int, name: str, phone: str, vehicle: str) -> int:
        self.ensure_delivery_enabled(tenant_id)
        rider_id = self.repository.create_rider(tenant_id, name, phone, vehicle)
        audit_service.log(
            action="create",
            entity_type="delivery_rider",
            entity_id=rider_id,
            details=f"Delivery rider created: {name}",
            tenant_id=tenant_id,
        )
        return rider_id

    def list_riders(self, tenant_id: int) -> list[dict[str, Any]]:
        self.ensure_delivery_enabled(tenant_id)
        return self.repository.list_riders(tenant_id)

    def create_delivery_order(
        self,
        tenant_id: int,
        order_id: int,
        address: str,
        instructions: str = "",
        delivery_fee: float = 0.0,
        *,
        reference_type: str = REFERENCE_SALE,
    ) -> int:
        self.ensure_delivery_enabled(tenant_id)
        return self.repository.create_delivery_order(
            tenant_id=tenant_id,
            order_id=order_id,
            address=address,
            instructions=instructions,
            delivery_fee=delivery_fee,
            reference_type=reference_type,
        )

    def get_delivery_order(
        self,
        tenant_id: int,
        order_id: int,
        *,
        reference_type: str = REFERENCE_SALE,
    ) -> dict[str, Any] | None:
        self.ensure_delivery_enabled(tenant_id)
        return self.repository.get_delivery_order(
            tenant_id, order_id, reference_type=reference_type
        )

    def list_delivery_orders(self, tenant_id: int, status: str | None = None) -> list[dict[str, Any]]:
        self.ensure_delivery_enabled(tenant_id)
        if status is not None and status not in DeliveryStatus.ALL_STATUSES:
            raise ValueError(f"Invalid delivery status: {status}")
        return self.repository.list_delivery_orders(tenant_id, status)

    def assign_rider(self, tenant_id: int, delivery_order_id: int, rider_id: int) -> None:
        self.ensure_delivery_enabled(tenant_id)
        rider = self.repository.get_rider(tenant_id, rider_id)
        if rider is None:
            raise ValueError("Rider not found for this tenant.")
        self.repository.assign_rider(tenant_id, delivery_order_id, rider_id)
        audit_service.log(
            action="assign",
            entity_type="delivery_order",
            entity_id=delivery_order_id,
            details=f"Delivery order assigned to rider {rider_id}",
            tenant_id=tenant_id,
        )

    def update_delivery_status(self, tenant_id: int, delivery_order_id: int, status: str) -> None:
        self.ensure_delivery_enabled(tenant_id)
        if status not in DeliveryStatus.ALL_STATUSES:
            raise ValueError(f"Invalid delivery status: {status}")
        self.repository.update_delivery_status(tenant_id, delivery_order_id, status)
        audit_service.log(
            action="update",
            entity_type="delivery_order",
            entity_id=delivery_order_id,
            details=f"Delivery order status updated to {status}",
            tenant_id=tenant_id,
        )
