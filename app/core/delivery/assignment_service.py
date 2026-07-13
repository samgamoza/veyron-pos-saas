from __future__ import annotations

from typing import Any

from app.core.delivery.repository import DeliveryRepository


class DeliveryAssignmentService:
    def __init__(self) -> None:
        self.repository = DeliveryRepository()

    def manual_assign(self, tenant_id: int, delivery_order_id: int, rider_id: int) -> None:
        rider = self.repository.get_rider(tenant_id, rider_id)
        if rider is None:
            raise ValueError("Rider not found for this tenant.")
        self.repository.assign_rider(tenant_id, delivery_order_id, rider_id)

    def auto_assign(self, tenant_id: int, delivery_order_id: int) -> int:
        riders = self.repository.list_riders(tenant_id, only_active=True)
        if not riders:
            raise ValueError("No active riders available for auto assignment.")

        best_rider = self._select_best_rider(tenant_id, riders)
        if best_rider is None:
            raise ValueError("Unable to select a rider for auto assignment.")

        self.repository.assign_rider(tenant_id, delivery_order_id, best_rider["id"])
        return best_rider["id"]

    def _select_best_rider(self, tenant_id: int, riders: list[dict[str, Any]]) -> dict[str, Any] | None:
        scored_riders: list[tuple[int, str, dict[str, Any]]] = []
        for rider in riders:
            active_assignments = self.repository.count_active_delivery_assignments(tenant_id, rider["id"])
            score = active_assignments
            if rider.get("last_known_latitude") is not None and rider.get("last_known_longitude") is not None:
                score -= 1
            last_seen_at = rider.get("last_seen_at") or ""
            scored_riders.append((score, last_seen_at, rider))

        scored_riders.sort(key=lambda item: (item[0], item[1]))
        return scored_riders[0][2] if scored_riders else None
