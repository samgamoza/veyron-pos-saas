from __future__ import annotations

from typing import Any

from app.core.delivery.repository import DeliveryRepository


class RiderService:
    def __init__(self) -> None:
        self.repository = DeliveryRepository()

    def create_rider(self, tenant_id: int, name: str, phone: str, vehicle: str) -> int:
        return self.repository.create_rider(tenant_id, name, phone, vehicle)

    def list_riders(self, tenant_id: int, only_active: bool = True) -> list[dict[str, Any]]:
        return self.repository.list_riders(tenant_id, only_active=only_active)

    def get_rider(self, tenant_id: int, rider_id: int) -> dict[str, Any] | None:
        return self.repository.get_rider(tenant_id, rider_id)
