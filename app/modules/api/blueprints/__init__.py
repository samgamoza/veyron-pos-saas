from __future__ import annotations

from app.modules.api.blueprints.auth import auth_api_bp
from app.modules.api.blueprints.billing import billing_api_bp
from app.modules.api.blueprints.delivery import delivery_api_bp
from app.modules.api.blueprints.payments import payments_api_bp
from app.modules.api.blueprints.pos import pos_api_bp
from app.modules.api.blueprints.products import products_api_bp
from app.modules.api.blueprints.store import store_api_bp
from app.modules.api.blueprints.subscription import subscription_api_bp
from app.modules.api.blueprints.tenant import tenant_api_bp

__all__ = [
    "auth_api_bp",
    "tenant_api_bp",
    "pos_api_bp",
    "products_api_bp",
    "store_api_bp",
    "delivery_api_bp",
    "billing_api_bp",
    "subscription_api_bp",
    "payments_api_bp",
]
