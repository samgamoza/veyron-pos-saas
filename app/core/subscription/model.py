from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Plan:
    id: int
    name: str
    price: float
    features: dict[str, Any]
    created_at: str
    is_active: bool = True

    def has_feature(self, feature_name: str) -> bool:
        return bool(self.features.get(feature_name))


@dataclass
class Subscription:
    id: int
    tenant_id: int
    plan_id: int
    status: str
    trial_end: str | None
    started_at: str
    cancelled_at: str | None
    cancel_at_period_end: bool

    @property
    def is_trialing(self) -> bool:
        return self.status == "trialing"

    @property
    def is_active(self) -> bool:
        return self.status == "active" or self.status == "trialing"

    @property
    def is_cancelled(self) -> bool:
        return self.status == "canceled"
