from __future__ import annotations

import os
from typing import Any

from flask import abort

from app.modules.storefront.storefront_repository import StorefrontRepository


class StorefrontService:
    DEFAULT_THEME = "default"
    ALLOWED_THEMES = {"default", "modern"}

    def __init__(self) -> None:
        self.repository = StorefrontRepository()

    def create_storefront_for_tenant(self, tenant_id: int) -> None:
        tenant = self.repository.get_tenant(tenant_id)
        if tenant is None:
            raise ValueError(f"Tenant not found: {tenant_id}")

        storefront_url = self.build_storefront_url(tenant["subdomain"])
        self.repository.update_storefront_metadata(tenant_id, self.DEFAULT_THEME, storefront_url)

    def build_storefront_url(self, subdomain: str | None) -> str:
        if not subdomain:
            return ""
        base_host = os.getenv("STORE_FRONT_BASE_HOST", "app.com").strip()
        scheme = "http" if base_host.startswith("localhost") else "https"
        return f"{scheme}://{subdomain}.{base_host}"

    def get_storefront_theme(self, tenant_id: int) -> str:
        theme = self.repository.get_storefront_template(tenant_id)
        if not theme:
            return self.DEFAULT_THEME
        theme = theme.strip().lower()
        return theme if theme in self.ALLOWED_THEMES else self.DEFAULT_THEME

    def get_storefront_context(self, tenant_id: int) -> dict[str, Any]:
        tenant = self.repository.get_tenant(tenant_id)
        if tenant is None:
            abort(404)

        theme = self.get_storefront_theme(tenant_id)
        return {
            "tenant_name": tenant["name"],
            "storefront_url": tenant["storefront_url"] or self.build_storefront_url(tenant["subdomain"]),
            "theme": theme,
            "template": self.get_template_name(theme),
            "products": self.repository.get_storefront_products(tenant_id),
            "categories": self.repository.get_storefront_categories(tenant_id),
        }

    def render_storefront(self, tenant_id: int, theme_override: str | None = None) -> str:
        context = self.get_storefront_context(tenant_id)
        if theme_override:
            override = theme_override.strip().lower()
            if override in self.ALLOWED_THEMES:
                context["theme"] = override
                context["template"] = self.get_template_name(override)
        from flask import render_template

        return render_template(context["template"], **context)

    def get_template_name(self, theme: str) -> str:
        return f"storefront/{theme}.html" if theme in self.ALLOWED_THEMES else f"storefront/{self.DEFAULT_THEME}.html"
