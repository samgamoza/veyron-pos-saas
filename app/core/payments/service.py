from __future__ import annotations

from typing import Any

from .credentials import PaymentCredentialStore
from .gateway import PaymentGateway
from .paypal_gateway import PayPalGateway
from .registry import gateway_enabled_for_platform


class PaymentService:
    GATEWAY_CLASSES: dict[str, type[PaymentGateway]] = {
        PayPalGateway.gateway_name(): PayPalGateway,
    }

    def __init__(self, connection: Any) -> None:
        self.connection = connection
        self.credentials_store = PaymentCredentialStore(connection)

    def configure_gateway(self, tenant_id: int, gateway_name: str, config: dict[str, Any]) -> None:
        if gateway_name not in self.GATEWAY_CLASSES:
            raise ValueError(f"Unsupported gateway: {gateway_name}")
        if not gateway_enabled_for_platform(self.connection, gateway_name):
            raise ValueError(
                f"Gateway '{gateway_name}' is disabled at platform level. "
                "Enable it under Super Admin → Payment gateways."
            )
        self.credentials_store.save_credentials(tenant_id, gateway_name, config)

    def build_gateway(self, tenant_id: int, gateway_name: str) -> PaymentGateway:
        if not gateway_enabled_for_platform(self.connection, gateway_name):
            raise ValueError(
                f"Gateway '{gateway_name}' is disabled at platform level. "
                "Enable it under Super Admin → Payment gateways."
            )
        config = self.credentials_store.load_credentials(tenant_id, gateway_name)
        if config is None:
            raise ValueError(f"Gateway configuration not found for tenant {tenant_id} and gateway {gateway_name}.")
        gateway_cls = self.GATEWAY_CLASSES.get(gateway_name)
        if gateway_cls is None:
            raise ValueError(f"Unsupported gateway: {gateway_name}")
        return gateway_cls(config)

    def charge(self, tenant_id: int, gateway_name: str, amount: float, currency: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        gateway = self.build_gateway(tenant_id, gateway_name)
        return gateway.charge(amount, currency, metadata)

    def refund(self, tenant_id: int, gateway_name: str, payment_id: str, amount: float | None = None) -> dict[str, Any]:
        gateway = self.build_gateway(tenant_id, gateway_name)
        return gateway.refund(payment_id, amount)

    def handle_webhook(self, gateway_name: str, payload: dict[str, Any], headers: dict[str, Any]) -> dict[str, Any]:
        if not gateway_enabled_for_platform(self.connection, gateway_name):
            raise ValueError(f"Gateway '{gateway_name}' is disabled at platform level.")
        gateway_cls = self.GATEWAY_CLASSES.get(gateway_name)
        if gateway_cls is None:
            raise ValueError(f"Unsupported gateway: {gateway_name}")
        gateway = gateway_cls({})
        return gateway.handle_webhook(payload, headers)

    def supported_gateways(self) -> list[str]:
        return [
            name
            for name in self.GATEWAY_CLASSES
            if gateway_enabled_for_platform(self.connection, name)
        ]
