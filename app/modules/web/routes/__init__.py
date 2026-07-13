from __future__ import annotations

from flask import Flask

from app.modules.products.product_service import ProductService
from app.modules.storefront.storefront_service import StorefrontService

from app.modules.web.routes.admin import register_admin_routes
from app.modules.web.routes.auth import register_auth_routes
from app.modules.web.routes.dashboards import register_dashboards_routes
from app.modules.web.routes.inventory import register_inventory_routes
from app.modules.web.routes.misc import register_misc_routes
from app.modules.web.routes.owner import register_owner_routes
from app.modules.web.routes.onboarding import register_onboarding_routes, register_subscription_pages_routes
from app.modules.web.routes.pos import register_pos_routes


def register_web_routes(
    app: Flask,
    *,
    product_service: ProductService,
    storefront_service: StorefrontService,
) -> None:
    register_misc_routes(app)
    register_auth_routes(app)
    register_onboarding_routes(app)
    register_subscription_pages_routes(app)
    register_pos_routes(app, storefront_service=storefront_service)
    register_dashboards_routes(app)
    register_admin_routes(app, product_service=product_service)
    register_inventory_routes(app)
    register_owner_routes(app)
