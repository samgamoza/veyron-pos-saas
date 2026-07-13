from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DeliveryOrder:
    id: int
    order_id: int
    tenant_id: int
    rider_id: int | None
    address: str
    instructions: str
    delivery_fee: float
    status: str
    assigned_at: str | None
    picked_up_at: str | None
    delivered_at: str | None
    created_at: str
    updated_at: str


@dataclass
class Rider:
    id: int
    tenant_id: int
    name: str
    phone: str
    vehicle: str
    is_active: bool
    created_at: str
    updated_at: str
