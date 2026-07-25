from __future__ import annotations

import os
from datetime import timedelta

import html
import json

from flask import Flask, Response, flash, g, redirect, request, session, url_for
from flask_wtf.csrf import CSRFError, CSRFProtect, generate_csrf
from markupsafe import Markup
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import generate_password_hash

from app.admin import superadmin_bp, product_admin_bp
from app.core.auth import get_current_user
from app.core.flask_config import (
    BACKUP_DIR,
    BASE_DIR,
    BUSINESS_NAME,
    CURRENCY_CODE,
    DEFAULT_APP_SETTINGS,
    DEFAULT_BRANDS,
    DEFAULT_CATEGORIES,
    DEFAULT_PLACEHOLDER,
    DEFAULT_UNITS,
    DEFAULT_UNITS_DATA,
    DEFAULT_USERS,
    FALLBACK_LOGO,
    INSTANCE_DIR,
    IS_PRODUCTION,
    PLACEHOLDER_MAP,
    PRODUCT_IMAGES_DIR,
    PUBLIC_LOGO,
    VAT_RATE,
)
from app.core.db import get_connection
from app.core.helpers import peso
from app.core.rate_limit import limiter
from app.core.tax import vat_config_from_settings
from app.core.tenant.context import (
    is_super_admin,
    load_tenant_by_header,
    load_tenant_by_id,
    load_tenant_by_subdomain,
    parse_subdomain,
)
from app.core.localization import localization_service
from app.modules.products.product_service import ProductService
from app.modules.api import register_api_blueprints
from app.modules.etown import etown_bp
from app.modules.storefront import storefront_bp
from app.modules.storefront.storefront_service import StorefrontService
from app.modules.web import queries as web_queries
from app.modules.web.routes import register_web_routes


def create_flask_application():
    app = Flask(
        __name__,
        instance_path=str(INSTANCE_DIR),
        instance_relative_config=False,
        template_folder=str(BASE_DIR / "templates"),
        static_folder=str(BASE_DIR / "static"),
    )
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    product_service = ProductService()
    storefront_service = StorefrontService()

    # Refuse to boot in production with a weak/absent SECRET_KEY. A predictable
    # key lets an attacker forge session cookies (including is_super_admin), which
    # would bypass every tenant-isolation check.
    DEV_FALLBACK_SECRET = "veyron-pos-dev-key"
    secret_key = os.getenv("SECRET_KEY", "").strip()
    if IS_PRODUCTION and (not secret_key or secret_key == DEV_FALLBACK_SECRET):
        raise RuntimeError(
            "SECRET_KEY must be set to a strong random value when APP_ENV=production. "
            "Refusing to start with a missing or development fallback key."
        )
    if not secret_key:
        secret_key = DEV_FALLBACK_SECRET

    app.config.update(
        SECRET_KEY=secret_key,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=IS_PRODUCTION,
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
        MAX_CONTENT_LENGTH=5 * 1024 * 1024,
    )

    limiter.init_app(app)

    # CSRF protects all server-rendered POST forms. JSON API blueprints (/api/*)
    # authenticate via their own guards and are exempted below.
    csrf = CSRFProtect(app)

    app.register_blueprint(superadmin_bp)
    app.register_blueprint(product_admin_bp)
    app.register_blueprint(storefront_bp)
    app.register_blueprint(etown_bp)
    register_api_blueprints(app, csrf=csrf)

    # Public JSON order endpoint is consumed programmatically (application/json,
    # no browser form/session), so form-based CSRF does not apply; exempt it.
    _json_order_view = app.view_functions.get("etown.place_order_json")
    if _json_order_view is not None:
        csrf.exempt(_json_order_view)


    @app.before_request
    def tenant_context() -> None:
        g.tenant_id = session.get("tenant_id")
        g.tenant = None
        g.tenant_bypass = is_super_admin()

        with get_connection() as connection:
            tenant = None
            header_tenant_id = request.headers.get("X-Tenant-ID")
            if header_tenant_id:
                tenant = load_tenant_by_header(connection, header_tenant_id)

            if tenant is None:
                subdomain = parse_subdomain(request.host)
                if subdomain:
                    tenant = load_tenant_by_subdomain(connection, subdomain)

            if tenant is None and g.tenant_id:
                tenant = load_tenant_by_id(connection, g.tenant_id)

            if tenant is not None:
                g.tenant = tenant
                g.tenant_id = tenant.id
                session["tenant_id"] = tenant.id
            else:
                # Public eTown marketplace pages are anonymous but still operate inside
                # one tenant's context (tenant id comes from the URL). Without this,
                # Postgres RLS fails closed and public shop pages return nothing.
                endpoint = request.endpoint or ""
                if endpoint.startswith("etown."):
                    view_tenant_id = (request.view_args or {}).get("tenant_id")
                    if view_tenant_id:
                        public_tenant = load_tenant_by_id(connection, view_tenant_id)
                        if public_tenant is not None:
                            g.tenant = public_tenant
                            g.tenant_id = public_tenant.id
                            # Deliberately NOT persisted to session: anonymous browsing
                            # must never bind a visitor's session to a tenant.


    app.jinja_env.globals["csrf_field"] = lambda: Markup(
        f'<input type="hidden" name="csrf_token" value="{generate_csrf()}">'
    )
    app.jinja_env.filters["php"] = peso
    app.jinja_env.globals["get_product_image_url"] = lambda img, cat=None: web_queries.get_product_image_url(img, cat)
    app.jinja_env.globals["_"] = localization_service.translate
    app.jinja_env.globals["locales"] = lambda: localization_service.supported_locales


    def get_logo_path() -> str:
        custom = web_queries.get_setting("brand_logo_path", "")
        if custom and (BASE_DIR / "static" / custom).exists():
            return custom
        return "public/logo.png" if PUBLIC_LOGO.exists() else FALLBACK_LOGO


    @app.context_processor
    def inject_template_globals() -> dict[str, object]:
        settings = web_queries.fetch_app_settings()
        return {
            "business_name": BUSINESS_NAME,
            "currency_code": CURRENCY_CODE,
            "logo_path": get_logo_path(),
            # Per-tenant VAT rate (0 when the tenant is not VAT-registered).
            "vat_rate": vat_config_from_settings(settings).rate if settings.get("vat_registered", "1") != "0" else 0.0,
            "current_user": get_current_user(),
            "brand_primary": settings.get("brand_primary_color", "#0f6a5d"),
            "brand_accent": settings.get("brand_accent_color", "#b54a2f"),
            "brand_theme": settings.get("brand_theme_mode", "warm"),
            "current_locale": localization_service.get_locale(),
            "supported_locales": localization_service.supported_locales,
        }


    def init_db() -> None:
        INSTANCE_DIR.mkdir(parents=True, exist_ok=True)
        with get_connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS tenants (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    subdomain TEXT UNIQUE,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    is_active INTEGER NOT NULL DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id INTEGER,
                    full_name TEXT NOT NULL,
                    username TEXT NOT NULL,
                    role TEXT NOT NULL,
                    pin_hash TEXT NOT NULL,
                    language_override TEXT,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    last_login TEXT,
                    FOREIGN KEY (tenant_id) REFERENCES tenants (id),
                    UNIQUE (tenant_id, username)
                );

                CREATE TABLE IF NOT EXISTS audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id INTEGER NOT NULL,
                    user_id INTEGER,
                    action TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    entity_id INTEGER,
                    details TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (tenant_id) REFERENCES tenants (id),
                    FOREIGN KEY (user_id) REFERENCES users (id)
                );

                CREATE TABLE IF NOT EXISTS owner_alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id INTEGER NOT NULL,
                    alert_type TEXT NOT NULL,
                    severity TEXT NOT NULL DEFAULT 'info',
                    title TEXT NOT NULL,
                    message TEXT NOT NULL,
                    related_entity_type TEXT,
                    related_entity_id INTEGER,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (tenant_id) REFERENCES tenants (id)
                );

                CREATE TABLE IF NOT EXISTS app_settings (
                    tenant_id INTEGER NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, key),
                    FOREIGN KEY (tenant_id) REFERENCES tenants (id)
                );

                CREATE TABLE IF NOT EXISTS plans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    price REAL NOT NULL DEFAULT 0,
                    features TEXT NOT NULL DEFAULT '{}',
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS subscriptions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id INTEGER NOT NULL,
                    plan_id INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    trial_end TEXT,
                    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    cancelled_at TEXT,
                    cancel_at_period_end INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY (tenant_id) REFERENCES tenants (id),
                    FOREIGN KEY (plan_id) REFERENCES plans (id)
                );

                CREATE TABLE IF NOT EXISTS invoices (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id INTEGER NOT NULL,
                    subscription_id INTEGER NOT NULL,
                    plan_id INTEGER NOT NULL,
                    amount REAL NOT NULL DEFAULT 0,
                    tax_rate REAL NOT NULL DEFAULT 0,
                    tax_amount REAL NOT NULL DEFAULT 0,
                    total_amount REAL NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'pending',
                    issued_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    due_at TEXT,
                    paid_at TEXT,
                    payment_reference TEXT,
                    FOREIGN KEY (tenant_id) REFERENCES tenants (id),
                    FOREIGN KEY (subscription_id) REFERENCES subscriptions (id),
                    FOREIGN KEY (plan_id) REFERENCES plans (id)
                );

                CREATE TABLE IF NOT EXISTS payment_credentials (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id INTEGER NOT NULL,
                    gateway_name TEXT NOT NULL,
                    encrypted_config TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (tenant_id) REFERENCES tenants (id),
                    UNIQUE (tenant_id, gateway_name)
                );

                CREATE TABLE IF NOT EXISTS categories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY (tenant_id) REFERENCES tenants (id),
                    UNIQUE (tenant_id, name)
                );

                CREATE TABLE IF NOT EXISTS brands (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    FOREIGN KEY (tenant_id) REFERENCES tenants (id),
                    UNIQUE (tenant_id, name)
                );

                CREATE TABLE IF NOT EXISTS units (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    symbol TEXT NOT NULL DEFAULT '',
                    type TEXT NOT NULL DEFAULT 'count',
                    conversion REAL NOT NULL DEFAULT 1,
                    FOREIGN KEY (tenant_id) REFERENCES tenants (id),
                    UNIQUE (tenant_id, name)
                );

                CREATE TABLE IF NOT EXISTS products (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    sku TEXT NOT NULL,
                    price REAL NOT NULL,
                    stock INTEGER NOT NULL DEFAULT 0,
                    category_id INTEGER REFERENCES categories (id),
                    brand_id INTEGER REFERENCES brands (id),
                    unit_id INTEGER REFERENCES units (id),
                    reorder_level INTEGER NOT NULL DEFAULT 5,
                    cost REAL NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'active',
                    last_restocked TEXT,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    image_path TEXT,
                    FOREIGN KEY (tenant_id) REFERENCES tenants (id),
                    UNIQUE (tenant_id, sku)
                );

                CREATE TABLE IF NOT EXISTS product_variants (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id INTEGER NOT NULL,
                    product_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    sku_suffix TEXT NOT NULL DEFAULT '',
                    price REAL NOT NULL,
                    cost REAL NOT NULL DEFAULT 0,
                    stock INTEGER NOT NULL DEFAULT 0,
                    reorder_level INTEGER NOT NULL DEFAULT 5,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    FOREIGN KEY (tenant_id) REFERENCES tenants (id),
                    FOREIGN KEY (product_id) REFERENCES products (id)
                );

                CREATE TABLE IF NOT EXISTS riders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    phone TEXT NOT NULL,
                    vehicle TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    last_known_latitude REAL,
                    last_known_longitude REAL,
                    last_seen_at TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (tenant_id) REFERENCES tenants (id)
                );

                CREATE TABLE IF NOT EXISTS sales (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    subtotal REAL NOT NULL,
                    discount_type TEXT NOT NULL DEFAULT 'none',
                    discount_rate REAL NOT NULL DEFAULT 0,
                    discount_amount REAL NOT NULL DEFAULT 0,
                    discount_note TEXT NOT NULL DEFAULT '',
                    service_reference TEXT NOT NULL DEFAULT '',
                    tax REAL NOT NULL,
                    total REAL NOT NULL,
                    payment_method TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'completed',
                    original_sale_id INTEGER,
                    cashier_user_id INTEGER,
                    idempotency_key TEXT,
                    FOREIGN KEY (tenant_id) REFERENCES tenants (id),
                    FOREIGN KEY (original_sale_id) REFERENCES sales (id),
                    FOREIGN KEY (cashier_user_id) REFERENCES users (id)
                );

                CREATE TABLE IF NOT EXISTS sale_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id INTEGER NOT NULL,
                    sale_id INTEGER NOT NULL,
                    product_id INTEGER NOT NULL,
                    variant_id INTEGER,
                    quantity INTEGER NOT NULL,
                    unit_price REAL NOT NULL,
                    line_total REAL NOT NULL,
                    product_name TEXT NOT NULL DEFAULT '',
                    sku TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY (tenant_id) REFERENCES tenants (id),
                    FOREIGN KEY (sale_id) REFERENCES sales (id),
                    FOREIGN KEY (product_id) REFERENCES products (id),
                    FOREIGN KEY (variant_id) REFERENCES product_variants (id)
                );

                CREATE TABLE IF NOT EXISTS payments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id INTEGER NOT NULL,
                    sale_id INTEGER NOT NULL,
                    amount REAL NOT NULL,
                    method TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'completed',
                    reference TEXT NOT NULL DEFAULT '',
                    reverses_payment_id INTEGER,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (tenant_id) REFERENCES tenants (id),
                    FOREIGN KEY (sale_id) REFERENCES sales (id),
                    FOREIGN KEY (reverses_payment_id) REFERENCES payments (id)
                );

                CREATE TABLE IF NOT EXISTS stock_movements (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id INTEGER NOT NULL,
                    product_id INTEGER NOT NULL,
                    variant_id INTEGER,
                    quantity_change INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (tenant_id) REFERENCES tenants (id),
                    FOREIGN KEY (product_id) REFERENCES products (id),
                    FOREIGN KEY (variant_id) REFERENCES product_variants (id)
                );

                CREATE TABLE IF NOT EXISTS delivery_orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id INTEGER NOT NULL,
                    order_id INTEGER NOT NULL,
                    rider_id INTEGER,
                    address TEXT NOT NULL,
                    instructions TEXT NOT NULL DEFAULT '',
                    delivery_fee REAL NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'pending',
                    assigned_at TEXT,
                    picked_up_at TEXT,
                    delivered_at TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (tenant_id) REFERENCES tenants (id),
                    FOREIGN KEY (rider_id) REFERENCES riders (id),
                    FOREIGN KEY (order_id) REFERENCES sales (id)
                );

                CREATE TABLE IF NOT EXISTS suppliers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    contact_person TEXT NOT NULL DEFAULT '',
                    phone TEXT NOT NULL DEFAULT '',
                    email TEXT NOT NULL DEFAULT '',
                    notes TEXT NOT NULL DEFAULT '',
                    is_active INTEGER NOT NULL DEFAULT 1,
                    FOREIGN KEY (tenant_id) REFERENCES tenants (id),
                    UNIQUE (tenant_id, name)
                );

                CREATE TABLE IF NOT EXISTS purchase_orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id INTEGER NOT NULL,
                    supplier_id INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'draft',
                    notes TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    received_at TEXT,
                    created_by INTEGER,
                    FOREIGN KEY (tenant_id) REFERENCES tenants (id),
                    FOREIGN KEY (supplier_id) REFERENCES suppliers (id),
                    FOREIGN KEY (created_by) REFERENCES users (id)
                );

                CREATE TABLE IF NOT EXISTS purchase_order_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id INTEGER NOT NULL,
                    purchase_order_id INTEGER NOT NULL,
                    product_id INTEGER NOT NULL,
                    ordered_quantity INTEGER NOT NULL,
                    received_quantity INTEGER NOT NULL DEFAULT 0,
                    unit_cost REAL NOT NULL DEFAULT 0,
                    FOREIGN KEY (tenant_id) REFERENCES tenants (id),
                    FOREIGN KEY (purchase_order_id) REFERENCES purchase_orders (id),
                    FOREIGN KEY (product_id) REFERENCES products (id)
                );

                CREATE TABLE IF NOT EXISTS stock_counts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'open',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    completed_at TEXT,
                    created_by INTEGER,
                    FOREIGN KEY (tenant_id) REFERENCES tenants (id),
                    FOREIGN KEY (created_by) REFERENCES users (id)
                );

                CREATE TABLE IF NOT EXISTS stock_count_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id INTEGER NOT NULL,
                    stock_count_id INTEGER NOT NULL,
                    product_id INTEGER NOT NULL,
                    system_stock INTEGER NOT NULL,
                    counted_stock INTEGER,
                    variance INTEGER,
                    FOREIGN KEY (tenant_id) REFERENCES tenants (id),
                    FOREIGN KEY (stock_count_id) REFERENCES stock_counts (id),
                    FOREIGN KEY (product_id) REFERENCES products (id)
                );

                CREATE TABLE IF NOT EXISTS daily_inventory_shifts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id INTEGER NOT NULL,
                    shift_date TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'open',
                    opened_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    closed_at TEXT,
                    opened_by INTEGER,
                    closed_by INTEGER,
                    opening_units INTEGER NOT NULL DEFAULT 0,
                    closing_units INTEGER,
                    units_sold INTEGER DEFAULT 0,
                    units_received INTEGER DEFAULT 0,
                    units_adjusted INTEGER DEFAULT 0,
                    notes TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY (tenant_id) REFERENCES tenants (id),
                    FOREIGN KEY (opened_by) REFERENCES users (id),
                    FOREIGN KEY (closed_by) REFERENCES users (id)
                );

                CREATE TABLE IF NOT EXISTS daily_shift_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id INTEGER NOT NULL,
                    shift_id INTEGER NOT NULL,
                    product_id INTEGER NOT NULL,
                    opening_stock INTEGER NOT NULL,
                    closing_stock INTEGER,
                    FOREIGN KEY (tenant_id) REFERENCES tenants (id),
                    FOREIGN KEY (shift_id) REFERENCES daily_inventory_shifts (id),
                    FOREIGN KEY (product_id) REFERENCES products (id)
                );

                CREATE TABLE IF NOT EXISTS customers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id INTEGER,
                    phone TEXT NOT NULL,
                    full_name TEXT NOT NULL DEFAULT '',
                    email TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (tenant_id) REFERENCES tenants (id),
                    UNIQUE (tenant_id, phone)
                );

                CREATE TABLE IF NOT EXISTS customer_addresses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id INTEGER,
                    customer_id INTEGER NOT NULL,
                    label TEXT NOT NULL DEFAULT '',
                    line1 TEXT NOT NULL,
                    line2 TEXT NOT NULL DEFAULT '',
                    barangay TEXT NOT NULL DEFAULT '',
                    city TEXT NOT NULL DEFAULT '',
                    province TEXT NOT NULL DEFAULT '',
                    postal_code TEXT NOT NULL DEFAULT '',
                    notes TEXT NOT NULL DEFAULT '',
                    is_default INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (customer_id) REFERENCES customers (id)
                );

                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id INTEGER NOT NULL,
                    customer_id INTEGER,
                    customer_address_id INTEGER,
                    guest_name TEXT NOT NULL DEFAULT '',
                    guest_phone TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending',
                    subtotal REAL NOT NULL,
                    tax REAL NOT NULL DEFAULT 0,
                    total REAL NOT NULL,
                    notes TEXT NOT NULL DEFAULT '',
                    delivery_line1 TEXT NOT NULL DEFAULT '',
                    delivery_line2 TEXT NOT NULL DEFAULT '',
                    delivery_city TEXT NOT NULL DEFAULT '',
                    delivery_notes TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (tenant_id) REFERENCES tenants (id),
                    FOREIGN KEY (customer_id) REFERENCES customers (id),
                    FOREIGN KEY (customer_address_id) REFERENCES customer_addresses (id)
                );

                CREATE TABLE IF NOT EXISTS order_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id INTEGER NOT NULL,
                    order_id INTEGER NOT NULL,
                    product_id INTEGER NOT NULL,
                    variant_id INTEGER,
                    product_name TEXT NOT NULL DEFAULT '',
                    quantity INTEGER NOT NULL,
                    unit_price REAL NOT NULL,
                    line_total REAL NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (tenant_id) REFERENCES tenants (id),
                    FOREIGN KEY (order_id) REFERENCES orders (id),
                    FOREIGN KEY (product_id) REFERENCES products (id),
                    FOREIGN KEY (variant_id) REFERENCES product_variants (id)
                );

                CREATE INDEX IF NOT EXISTS idx_orders_tenant_status_created
                    ON orders (tenant_id, status, created_at);
                CREATE INDEX IF NOT EXISTS idx_order_items_order_id ON order_items (order_id);
                CREATE INDEX IF NOT EXISTS idx_order_items_tenant_order ON order_items (tenant_id, order_id);
                """
            )

            web_queries.ensure_column(connection, "products", "category_id", "INTEGER REFERENCES categories (id)")
            web_queries.ensure_column(connection, "products", "brand_id", "INTEGER REFERENCES brands (id)")
            web_queries.ensure_column(connection, "products", "unit_id", "INTEGER REFERENCES units (id)")
            web_queries.ensure_column(connection, "users", "tenant_id", "INTEGER REFERENCES tenants (id)")
            web_queries.ensure_column(connection, "users", "language_override", "TEXT")
            web_queries.ensure_column(connection, "tenants", "subdomain", "TEXT UNIQUE")
            web_queries.ensure_column(connection, "tenants", "status", "TEXT NOT NULL DEFAULT 'active'")
            web_queries.ensure_column(connection, "tenants", "plan_name", "TEXT NOT NULL DEFAULT 'starter'")
            web_queries.ensure_column(connection, "tenants", "subscription_status", "TEXT NOT NULL DEFAULT 'trial'")
            web_queries.ensure_column(connection, "tenants", "monthly_fee", "REAL NOT NULL DEFAULT 0")
            web_queries.ensure_column(connection, "tenants", "contact_email", "TEXT NOT NULL DEFAULT ''")
            web_queries.ensure_column(connection, "tenants", "language", "TEXT NOT NULL DEFAULT 'en'")
            web_queries.ensure_column(connection, "tenants", "billing_currency", "TEXT NOT NULL DEFAULT 'PHP'")
            web_queries.ensure_column(connection, "tenants", "billing_cycle", "TEXT NOT NULL DEFAULT 'monthly'")
            web_queries.ensure_column(connection, "tenants", "next_billing_date", "TEXT")
            web_queries.ensure_column(connection, "tenants", "last_payment_date", "TEXT")
            web_queries.ensure_column(connection, "tenants", "payment_gateway", "TEXT NOT NULL DEFAULT ''")
            web_queries.ensure_column(connection, "tenants", "payment_gateway_mode", "TEXT NOT NULL DEFAULT 'test'")
            web_queries.ensure_column(connection, "tenants", "delivery_enabled", "INTEGER NOT NULL DEFAULT 0")
            web_queries.ensure_column(connection, "tenants", "delivery_provider", "TEXT NOT NULL DEFAULT ''")
            web_queries.ensure_column(connection, "tenants", "delivery_api_key", "TEXT NOT NULL DEFAULT ''")
            web_queries.ensure_column(connection, "tenants", "delivery_callback_url", "TEXT NOT NULL DEFAULT ''")
            web_queries.ensure_column(connection, "tenants", "storefront_enabled", "INTEGER NOT NULL DEFAULT 0")
            web_queries.ensure_column(connection, "tenants", "storefront_url", "TEXT NOT NULL DEFAULT ''")
            web_queries.ensure_column(connection, "tenants", "auto_storefront_enabled", "INTEGER NOT NULL DEFAULT 0")
            web_queries.ensure_column(connection, "tenants", "auto_publish_products", "INTEGER NOT NULL DEFAULT 1")
            web_queries.ensure_column(connection, "tenants", "storefront_template", "TEXT NOT NULL DEFAULT 'default'")
            web_queries.ensure_column(connection, "tenants", "parent_tenant_id", "INTEGER")
            web_queries.ensure_column(connection, "tenants", "org_slug", "TEXT NOT NULL DEFAULT ''")
            web_queries.ensure_column(connection, "tenants", "outlet_code", "TEXT NOT NULL DEFAULT ''")
            web_queries.ensure_column(connection, "tenants", "bir_tin", "TEXT NOT NULL DEFAULT ''")
            web_queries.ensure_column(connection, "tenants", "bir_vat_registered", "INTEGER NOT NULL DEFAULT 0")
            web_queries.ensure_column(connection, "tenants", "compliance_notes", "TEXT NOT NULL DEFAULT ''")
            web_queries.ensure_column(connection, "tenants", "bir_last_report_generated", "TEXT")
            web_queries.ensure_column(connection, "tenants", "feature_flags_json", "TEXT NOT NULL DEFAULT '{}'")
            web_queries.ensure_column(connection, "tenants", "pos_profile", "TEXT NOT NULL DEFAULT 'cafe_bakery'")
            web_queries.ensure_column(connection, "tenants", "marketplace_enabled", "INTEGER NOT NULL DEFAULT 0")
            web_queries.ensure_column(connection, "tenants", "public_business_name", "TEXT")
            web_queries.ensure_column(connection, "tenants", "public_subtitle", "TEXT")
            web_queries.ensure_column(connection, "tenants", "public_description", "TEXT")
            web_queries.ensure_column(connection, "tenants", "public_address", "TEXT")
            web_queries.ensure_column(connection, "tenants", "public_phone", "TEXT")
            web_queries.ensure_column(connection, "tenants", "public_cover_image", "TEXT")
            web_queries.ensure_column(connection, "tenants", "public_logo_image", "TEXT")
            web_queries.ensure_column(connection, "tenants", "public_opening_hours", "TEXT")
            web_queries.ensure_column(connection, "tenants", "province", "TEXT")
            web_queries.ensure_column(connection, "tenants", "municipality", "TEXT")
            web_queries.ensure_column(connection, "tenants", "barangay", "TEXT")
            web_queries.ensure_column(connection, "tenants", "storefront_visibility", "TEXT NOT NULL DEFAULT 'hidden'")
            web_queries.ensure_column(connection, "tenants", "updated_at", "TEXT")
            web_queries.ensure_column(connection, "products", "is_public", "INTEGER NOT NULL DEFAULT 1")
            web_queries.ensure_column(connection, "products", "marketplace_description", "TEXT")
            web_queries.ensure_column(connection, "products", "marketplace_image_path", "TEXT")
            web_queries.ensure_column(connection, "products", "marketplace_sort_order", "INTEGER NOT NULL DEFAULT 0")
            web_queries.ensure_column(connection, "products", "marketplace_published_at", "TEXT")
            web_queries.ensure_column(connection, "products", "slug", "TEXT")
            web_queries.ensure_column(connection, "customers", "updated_at", "TEXT")
            web_queries.ensure_column(connection, "order_items", "product_name", "TEXT NOT NULL DEFAULT ''")
            web_queries.ensure_column(connection, "order_items", "created_at", "TEXT NOT NULL DEFAULT ''")
            web_queries.ensure_column(connection, "riders", "last_known_latitude", "REAL")
            web_queries.ensure_column(connection, "riders", "last_known_longitude", "REAL")
            web_queries.ensure_column(connection, "riders", "last_seen_at", "TEXT")
            web_queries.ensure_column(connection, "categories", "tenant_id", "INTEGER NOT NULL DEFAULT 1")
            web_queries.ensure_column(connection, "brands", "tenant_id", "INTEGER NOT NULL DEFAULT 1")
            web_queries.ensure_column(connection, "units", "tenant_id", "INTEGER NOT NULL DEFAULT 1")
            web_queries.ensure_column(connection, "products", "tenant_id", "INTEGER NOT NULL DEFAULT 1")
            web_queries.ensure_column(connection, "sales", "tenant_id", "INTEGER NOT NULL DEFAULT 1")
            web_queries.ensure_column(connection, "sale_items", "tenant_id", "INTEGER NOT NULL DEFAULT 1")
            web_queries.ensure_column(connection, "stock_movements", "tenant_id", "INTEGER NOT NULL DEFAULT 1")
            web_queries.ensure_column(connection, "suppliers", "tenant_id", "INTEGER NOT NULL DEFAULT 1")
            web_queries.ensure_column(connection, "purchase_orders", "tenant_id", "INTEGER NOT NULL DEFAULT 1")
            web_queries.ensure_column(connection, "purchase_order_items", "tenant_id", "INTEGER NOT NULL DEFAULT 1")
            web_queries.ensure_column(connection, "product_variants", "tenant_id", "INTEGER NOT NULL DEFAULT 1")
            web_queries.ensure_column(connection, "stock_counts", "tenant_id", "INTEGER NOT NULL DEFAULT 1")
            web_queries.ensure_column(connection, "stock_count_items", "tenant_id", "INTEGER NOT NULL DEFAULT 1")
            web_queries.ensure_column(connection, "products", "reorder_level", "INTEGER NOT NULL DEFAULT 5")
            web_queries.ensure_column(connection, "products", "cost", "REAL NOT NULL DEFAULT 0")
            web_queries.ensure_column(connection, "products", "status", "TEXT NOT NULL DEFAULT 'active'")
            web_queries.ensure_column(connection, "products", "last_restocked", "TEXT")
            web_queries.ensure_column(connection, "products", "sort_order", "INTEGER NOT NULL DEFAULT 0")
            web_queries.ensure_column(connection, "categories", "sort_order", "INTEGER NOT NULL DEFAULT 0")
            web_queries.ensure_column(connection, "sales", "discount_type", "TEXT NOT NULL DEFAULT 'none'")
            web_queries.ensure_column(connection, "sales", "discount_rate", "REAL NOT NULL DEFAULT 0")
            web_queries.ensure_column(connection, "sales", "discount_amount", "REAL NOT NULL DEFAULT 0")
            web_queries.ensure_column(connection, "sales", "discount_note", "TEXT NOT NULL DEFAULT ''")
            web_queries.ensure_column(connection, "sales", "status", "TEXT NOT NULL DEFAULT 'completed'")
            web_queries.ensure_column(connection, "sales", "original_sale_id", "INTEGER")
            web_queries.ensure_column(connection, "sales", "cashier_user_id", "INTEGER")
            web_queries.ensure_column(connection, "sales", "cash_shift_id", "INTEGER")
            web_queries.ensure_column(connection, "sales", "service_reference", "TEXT NOT NULL DEFAULT ''")
            web_queries.ensure_column(connection, "sales", "idempotency_key", "TEXT")
            web_queries.ensure_column(connection, "sale_items", "product_name", "TEXT NOT NULL DEFAULT ''")
            web_queries.ensure_column(connection, "sale_items", "sku", "TEXT NOT NULL DEFAULT ''")
            web_queries.ensure_column(connection, "payments", "reverses_payment_id", "INTEGER")

            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS uq_sales_tenant_idempotency
                ON sales (tenant_id, idempotency_key)
                WHERE idempotency_key IS NOT NULL AND trim(idempotency_key) != ''
                """
            )

            web_queries.ensure_column(connection, "units", "symbol", "TEXT NOT NULL DEFAULT ''")
            web_queries.ensure_column(connection, "units", "type", "TEXT NOT NULL DEFAULT 'count'")
            web_queries.ensure_column(connection, "units", "conversion", "REAL NOT NULL DEFAULT 1")
            web_queries.ensure_column(connection, "daily_inventory_shifts", "manual_opening_units", "INTEGER")
            web_queries.ensure_column(connection, "daily_inventory_shifts", "manual_closing_units", "INTEGER")
            web_queries.ensure_column(connection, "daily_inventory_shifts", "damaged_units", "INTEGER NOT NULL DEFAULT 0")
            web_queries.ensure_column(connection, "daily_inventory_shifts", "wastage_units", "INTEGER NOT NULL DEFAULT 0")
            web_queries.ensure_column(connection, "daily_inventory_shifts", "expected_closing_units", "INTEGER")
            web_queries.ensure_column(connection, "daily_inventory_shifts", "variance_units", "INTEGER")

            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS cash_register_shifts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id INTEGER NOT NULL DEFAULT 1,
                    cashier_user_id INTEGER NOT NULL,
                    opened_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    closed_at TEXT,
                    opening_cash REAL NOT NULL DEFAULT 0,
                    expected_cash REAL NOT NULL DEFAULT 0,
                    actual_cash REAL,
                    variance REAL,
                    expected_card_total REAL NOT NULL DEFAULT 0,
                    expected_wallet_total REAL NOT NULL DEFAULT 0,
                    expected_other_total REAL NOT NULL DEFAULT 0,
                    tender_breakdown_json TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL DEFAULT 'open',
                    closing_note TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY (tenant_id) REFERENCES tenants (id),
                    FOREIGN KEY (cashier_user_id) REFERENCES users (id)
                )
                """
            )
            web_queries.ensure_column(connection, "cash_register_shifts", "expected_card_total", "REAL NOT NULL DEFAULT 0")
            web_queries.ensure_column(connection, "cash_register_shifts", "expected_wallet_total", "REAL NOT NULL DEFAULT 0")
            web_queries.ensure_column(connection, "cash_register_shifts", "expected_other_total", "REAL NOT NULL DEFAULT 0")
            web_queries.ensure_column(connection, "cash_register_shifts", "tender_breakdown_json", "TEXT NOT NULL DEFAULT '{}'")

            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS payment_gateways (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id INTEGER NOT NULL,
                    provider TEXT NOT NULL,
                    mode TEXT NOT NULL DEFAULT 'test',
                    api_key TEXT NOT NULL DEFAULT '',
                    secret_key TEXT NOT NULL DEFAULT '',
                    webhook_url TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (tenant_id) REFERENCES tenants (id)
                )
                """
            )

            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS billing_invoices (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id INTEGER NOT NULL,
                    invoice_number TEXT NOT NULL,
                    amount REAL NOT NULL,
                    currency TEXT NOT NULL DEFAULT 'PHP',
                    status TEXT NOT NULL DEFAULT 'pending',
                    issued_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    due_date TEXT,
                    paid_at TEXT,
                    description TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY (tenant_id) REFERENCES tenants (id)
                )
                """
            )

            # Ensure default tenant exists and legacy rows are assigned to it.
            connection.execute(
                "INSERT INTO tenants (id, name, is_active) VALUES (1, 'Default Tenant', 1) ON CONFLICT(id) DO NOTHING"
            )
            # Customer tables predate tenant scoping; add the column before backfilling.
            web_queries.ensure_column(connection, "customers", "tenant_id", "INTEGER")
            web_queries.ensure_column(connection, "customer_addresses", "tenant_id", "INTEGER")

            for table_name in [
                "customers",
                "customer_addresses",
                "users",
                "categories",
                "brands",
                "units",
                "products",
                "sales",
                "sale_items",
                "stock_movements",
                "suppliers",
                "purchase_orders",
                "purchase_order_items",
                "product_variants",
                "stock_counts",
                "stock_count_items",
            ]:
                connection.execute(f"UPDATE {table_name} SET tenant_id = 1 WHERE tenant_id IS NULL")
            connection.execute("UPDATE tenants SET plan_name = 'starter' WHERE plan_name IS NULL OR trim(plan_name) = ''")
            connection.execute("UPDATE tenants SET subscription_status = 'trial' WHERE subscription_status IS NULL OR trim(subscription_status) = ''")
            connection.execute("UPDATE tenants SET monthly_fee = 0 WHERE monthly_fee IS NULL")

            for key, value in DEFAULT_APP_SETTINGS.items():
                connection.execute(
                    """
                    INSERT INTO app_settings (tenant_id, key, value) VALUES (1, ?, ?)
                    ON CONFLICT(tenant_id, key) DO NOTHING
                    """,
                    (key, value),
                )

            # Account seeding.
            # Production MUST NOT ship the weak demo owner/admin/cashier PINs. We only
            # ensure a single platform super-admin exists, sourced from the environment,
            # so first boot is reachable without hardcoding known credentials.
            if IS_PRODUCTION:
                existing_superadmin = connection.execute(
                    "SELECT id FROM users WHERE role = 'super_admin'"
                ).fetchone()
                if existing_superadmin is None:
                    seed_username = os.getenv("SEED_SUPERADMIN_USERNAME", "").strip().lower()
                    seed_pin = os.getenv("SEED_SUPERADMIN_PIN", "")
                    if not seed_username or len(seed_pin) < 8:
                        raise RuntimeError(
                            "No super-admin account exists and SEED_SUPERADMIN_USERNAME / "
                            "SEED_SUPERADMIN_PIN (minimum 8 characters) are not set. Provide "
                            "them in the environment to create the initial platform super-admin."
                        )
                    connection.execute(
                        "INSERT INTO users (tenant_id, full_name, username, role, pin_hash) "
                        "VALUES (NULL, 'Platform Super Admin', ?, 'super_admin', ?)",
                        (seed_username, generate_password_hash(seed_pin)),
                    )
            else:
                for user in DEFAULT_USERS:
                    exists = connection.execute(
                        "SELECT id FROM users WHERE tenant_id = 1 AND username = ?",
                        (user["username"],),
                    ).fetchone()
                    if exists is None:
                        connection.execute(
                            "INSERT INTO users (tenant_id, full_name, username, role, pin_hash) VALUES (1, ?, ?, ?, ?)",
                            (
                                user["full_name"],
                                user["username"],
                                user["role"],
                                generate_password_hash(user["pin"]),
                            ),
                        )

            # Tenant-scoped hot paths. Every read is filtered by tenant_id (app-layer
            # scoper + Postgres RLS), so tenant_id leads each composite index —
            # without these, tenant-filtered queries degrade into full table scans.
            connection.executescript(
                """
                CREATE INDEX IF NOT EXISTS idx_products_tenant ON products (tenant_id);
                CREATE INDEX IF NOT EXISTS idx_products_tenant_category ON products (tenant_id, category_id);
                CREATE INDEX IF NOT EXISTS idx_products_tenant_brand ON products (tenant_id, brand_id);
                CREATE INDEX IF NOT EXISTS idx_product_variants_tenant_product ON product_variants (tenant_id, product_id);
                CREATE INDEX IF NOT EXISTS idx_sales_tenant_created ON sales (tenant_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_sales_tenant_status_created ON sales (tenant_id, status, created_at);
                CREATE INDEX IF NOT EXISTS idx_sales_tenant_cashier ON sales (tenant_id, cashier_user_id);
                CREATE INDEX IF NOT EXISTS idx_sale_items_tenant_sale ON sale_items (tenant_id, sale_id);
                CREATE INDEX IF NOT EXISTS idx_sale_items_tenant_product ON sale_items (tenant_id, product_id);
                CREATE INDEX IF NOT EXISTS idx_stock_movements_tenant_product ON stock_movements (tenant_id, product_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_payments_tenant_sale ON payments (tenant_id, sale_id);
                CREATE INDEX IF NOT EXISTS idx_audit_logs_tenant_created ON audit_logs (tenant_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_owner_alerts_tenant_created ON owner_alerts (tenant_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_customers_tenant ON customers (tenant_id);
                CREATE INDEX IF NOT EXISTS idx_customer_addresses_tenant_customer ON customer_addresses (tenant_id, customer_id);
                CREATE INDEX IF NOT EXISTS idx_users_tenant ON users (tenant_id);
                CREATE INDEX IF NOT EXISTS idx_categories_tenant ON categories (tenant_id);
                CREATE INDEX IF NOT EXISTS idx_brands_tenant ON brands (tenant_id);
                CREATE INDEX IF NOT EXISTS idx_units_tenant ON units (tenant_id);
                """
            )

            web_queries.seed_lookup_table(connection, "categories", DEFAULT_CATEGORIES)
            web_queries.seed_lookup_table(connection, "brands", DEFAULT_BRANDS)
            web_queries.seed_lookup_table(connection, "units", DEFAULT_UNITS)
            web_queries.seed_units_data(connection)
            web_queries.resequence_category_order(connection)

            import json

            plan_count_row = connection.execute("SELECT COUNT(*) AS c FROM plans").fetchone()
            if plan_count_row and int(plan_count_row["c"] or 0) == 0:
                seed_plans = (
                    ("demo", 0.0, {"headline": "Explore the product", "limits": "capped catalog & monthly sales"}),
                    ("starter", 49.0, {"headline": "Single location", "pos": True, "inventory": True}),
                    ("growth", 99.0, {"headline": "Growing teams", "pos": True, "inventory": True, "reports": True}),
                    ("enterprise", 299.0, {"headline": "Multi-outlet & priority support", "pos": True, "inventory": True}),
                )
                for pname, price, feats in seed_plans:
                    connection.execute(
                        "INSERT INTO plans (name, price, features, is_active) VALUES (?, ?, ?, 1)",
                        (pname, price, json.dumps(feats)),
                    )
            demo_row = connection.execute("SELECT 1 FROM plans WHERE lower(name) = 'demo' LIMIT 1").fetchone()
            if demo_row is None:
                connection.execute(
                    "INSERT INTO plans (name, price, features, is_active) VALUES (?, ?, ?, 1)",
                    ("demo", 0.0, json.dumps({"headline": "Explore the product", "limits": "capped catalog & monthly sales"})),
                )

        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        PRODUCT_IMAGES_DIR.mkdir(parents=True, exist_ok=True)



    register_web_routes(
        app,
        product_service=product_service,
        storefront_service=storefront_service,
    )


    init_db()
    from app.core.tenant import lifecycle as tenant_lifecycle

    tenant_lifecycle.register_hooks()

    @app.before_request
    def platform_maintenance_guard() -> Response | None:
        if web_queries.get_platform_setting("platform_maintenance_mode", "0") != "1":
            return None
        ep = request.endpoint
        if ep in (None, "static"):
            return None
        if ep in ("login", "superadmin_login", "logout"):
            return None
        if ep and ep.startswith("superadmin."):
            return None
        if session.get("is_super_admin"):
            return None
        msg = web_queries.get_platform_setting("maintenance_message") or (
            "We are performing scheduled maintenance. Please try again shortly."
        )
        if request.path.startswith("/api/"):
            body = json.dumps({"error": "maintenance", "message": msg})
            return Response(body, status=503, mimetype="application/json; charset=utf-8")
        page = (
            "<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            "<title>Maintenance</title></head><body style=\"font-family:system-ui,sans-serif;"
            "padding:2rem;max-width:36rem;line-height:1.5\">"
            "<h1 style=\"margin-top:0\">Maintenance</h1>"
            f"<p>{html.escape(msg)}</p>"
            "<p class=\"text-muted\" style=\"opacity:.75;font-size:.9rem\">Super admins can sign in at "
            "<code>/superadmin/login</code> to disable maintenance mode.</p>"
            "</body></html>"
        )
        return Response(page, status=503, mimetype="text/html; charset=utf-8")

    @app.before_request
    def owner_onboarding_gate() -> Response | None:
        if request.endpoint in (None, "static"):
            return None
        ep = request.endpoint or ""
        if ep in ("login", "logout", "signup", "superadmin_login"):
            return None
        if ep in ("onboarding_home", "onboarding_complete", "subscription_plans", "subscription_select_plan"):
            return None
        if ep.startswith("superadmin."):
            return None
        if request.path.startswith("/api/"):
            return None
        if not session.get("user_id"):
            return None
        user = get_current_user()
        if not user or user.get("role") != "owner":
            return None
        if web_queries.get_setting("onboarding_completed", "0") == "1":
            return None
        return redirect(url_for("onboarding_home"))

    @app.errorhandler(429)
    def rate_limit_exceeded(exc):
        if request.path.startswith("/api/"):
            body = json.dumps(
                {"error": "rate_limited", "message": "Too many requests. Please slow down and try again shortly."}
            )
            return Response(body, status=429, mimetype="application/json; charset=utf-8")
        flash("Too many attempts. Please wait a moment and try again.", "error")
        return redirect(request.referrer or url_for("login")), 302

    @app.errorhandler(CSRFError)
    def handle_csrf_error(exc):
        if request.path.startswith("/api/"):
            body = json.dumps({"error": "csrf_failed", "message": exc.description})
            return Response(body, status=400, mimetype="application/json; charset=utf-8")
        flash("Your session expired or the form was invalid. Please try again.", "error")
        return redirect(request.referrer or url_for("login")), 302

    return app
