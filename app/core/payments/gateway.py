from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class PaymentGateway(ABC):
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    @abstractmethod
    def charge(self, amount: float, currency: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def refund(self, payment_id: str, amount: float | None = None) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def handle_webhook(self, payload: dict[str, Any], headers: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def gateway_name(cls) -> str:
        raise NotImplementedError
