from app.modules.storefront.routes import storefront_bp
from app.modules.storefront.storefront_service import StorefrontService
from app.core.tenant.tenant_service import register_tenant_hook


def _create_storefront_hook(tenant, payload):
    StorefrontService().create_storefront_for_tenant(tenant.id)


register_tenant_hook("tenant_created", _create_storefront_hook)
