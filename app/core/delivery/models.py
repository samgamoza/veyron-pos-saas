from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DeliverySettings:
    delivery_enabled: bool
    delivery_provider: str
    delivery_fee_base: float = 0.0
    delivery_fee_per_km: float = 0.0
    delivery_fee_free_threshold: float = 0.0
    delivery_zones: str = ""

    @classmethod
    def from_dict(cls, raw: dict[str, object]) -> "DeliverySettings":
        return cls(
            delivery_enabled=bool(raw.get("delivery_enabled")),
            delivery_provider=str(raw.get("delivery_provider") or ""),
            delivery_fee_base=cls._to_float(raw.get("delivery_fee_base")),
            delivery_fee_per_km=cls._to_float(raw.get("delivery_fee_per_km")),
            delivery_fee_free_threshold=cls._to_float(raw.get("delivery_fee_free_threshold")),
            delivery_zones=str(raw.get("delivery_zones") or ""),
        )

    @staticmethod
    def _to_float(value: object | None) -> float:
        if value is None:
            return 0.0
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0
