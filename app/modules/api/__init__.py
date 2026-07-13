from __future__ import annotations

from flask import Flask

from app.modules.api.blueprints import (
    auth_api_bp,
    billing_api_bp,
    delivery_api_bp,
    pos_api_bp,
    products_api_bp,
    store_api_bp,
    subscription_api_bp,
    tenant_api_bp,
)


def register_api_blueprints(app: Flask) -> None:
    """Register JSON API blueprints (service-backed, tenant-aware)."""
    from app.admin.api_blueprint import admin_api_bp

    app.register_blueprint(auth_api_bp)
    app.register_blueprint(tenant_api_bp)
    app.register_blueprint(pos_api_bp)
    app.register_blueprint(products_api_bp)
    app.register_blueprint(store_api_bp)
    app.register_blueprint(delivery_api_bp)
    app.register_blueprint(billing_api_bp)
    app.register_blueprint(subscription_api_bp)
    app.register_blueprint(admin_api_bp)


__all__ = ["register_api_blueprints"]
