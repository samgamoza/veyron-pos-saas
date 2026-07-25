from __future__ import annotations

from flask import Flask

from app.modules.api.blueprints import (
    auth_api_bp,
    billing_api_bp,
    delivery_api_bp,
    payments_api_bp,
    pos_api_bp,
    products_api_bp,
    store_api_bp,
    subscription_api_bp,
    tenant_api_bp,
)


def register_api_blueprints(app: Flask, csrf: object | None = None) -> None:
    """Register JSON API blueprints (service-backed, tenant-aware).

    JSON APIs are stateless request/response and authenticate via their own guards,
    so they are exempted from form-based CSRF when a CSRFProtect instance is passed.
    """
    from app.admin.api_blueprint import admin_api_bp

    api_blueprints = [
        auth_api_bp,
        tenant_api_bp,
        pos_api_bp,
        products_api_bp,
        store_api_bp,
        delivery_api_bp,
        billing_api_bp,
        subscription_api_bp,
        payments_api_bp,
        admin_api_bp,
    ]
    for bp in api_blueprints:
        app.register_blueprint(bp)
        if csrf is not None:
            csrf.exempt(bp)


__all__ = ["register_api_blueprints"]
