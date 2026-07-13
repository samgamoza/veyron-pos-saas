"""Split app/modules/web/routes.py into package app/modules/web/routes/. Run from repo root."""
from __future__ import annotations

from pathlib import Path
from textwrap import dedent

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "app" / "modules" / "web" / "routes.py"
PKG = ROOT / "app" / "modules" / "web" / "routes"
lines = SRC.read_text(encoding="utf-8").splitlines()


def indent_body(start: int, end: int) -> str:
    """Body lines already use one indent level (4 spaces); keep as-is inside register_*."""
    return "\n".join(lines[start:end])


# (filename_without_py, slice_start, slice_end_exclusive, func_name, signature, header)
MODULES: list[tuple[str, int, int, str, str, str]] = [
    (
        "misc",
        55,
        72,
        "misc",
        "app: Flask",
        dedent(
            '''
            from __future__ import annotations

            from flask import Flask

            from app.core.flask_config import APP_ENV
            '''
        ).strip()
        + "\n",
    ),
    (
        "auth",
        72,
        256,
        "auth",
        "app: Flask",
        dedent(
            '''
            from __future__ import annotations

            import time

            from flask import Flask, flash, redirect, render_template, request, session, url_for
            from werkzeug.security import check_password_hash, generate_password_hash

            from app.core.auth import get_current_user, login_required
            from app.core.db import get_connection, get_raw_connection
            from app.core.db_integrity import DB_INTEGRITY_ERRORS
            from app.core.helpers import normalize_lookup_name
            from app.core.localization import localization_service
            from app.core.tenant.tenant_service import TenantService
            from app.core.user_service import user_service
            from app.modules.web import queries as web_queries
            '''
        ).strip()
        + "\n",
    ),
    (
        "pos",
        256,
        619,
        "pos",
        "app: Flask, *, storefront_service: StorefrontService",
        dedent(
            '''
            from __future__ import annotations

            from flask import Flask, flash, g, redirect, render_template, request, session, url_for

            from app.core.auth import get_current_user, require_recent_reauth, session_user_id
            from app.core.db import get_connection
            from app.core.delivery.integration_service import DeliveryIntegrationService
            from app.core.flask_config import DISCOUNT_PRESETS, VAT_RATE
            from app.core.helpers import peso
            from app.core.inventory import log_stock_movement
            from app.modules.storefront.storefront_service import StorefrontService
            from app.modules.users.decorators import roles_required
            from app.modules.web import queries as web_queries
            '''
        ).strip()
        + "\n",
    ),
    (
        "dashboards",
        619,
        637,
        "dashboards",
        "app: Flask",
        dedent(
            '''
            from __future__ import annotations

            from flask import Flask, render_template

            from app.core.auth import login_required
            from app.modules.users.decorators import roles_required
            from app.modules.web import queries as web_queries
            '''
        ).strip()
        + "\n",
    ),
    (
        "admin",
        637,
        1460,
        "admin",
        "app: Flask, *, product_service: ProductService",
        dedent(
            '''
            from __future__ import annotations

            from flask import Flask, flash, request
            from werkzeug.security import generate_password_hash

            from app.core.auth import login_required, require_recent_reauth
            from app.core.constants import ALLOWED_PRODUCT_STATUSES
            from app.core.db import get_connection
            from app.core.db_integrity import DB_INTEGRITY_ERRORS
            from app.core.flask_config import ALLOWED_USER_ROLES
            from app.core.helpers import normalize_lookup_name
            from app.modules.products.product_service import ProductService
            from app.modules.users.decorators import permission_required, roles_required
            from app.modules.web import queries as web_queries
            '''
        ).strip()
        + "\n",
    ),
    (
        "inventory",
        1460,
        1828,
        "inventory",
        "app: Flask",
        dedent(
            '''
            from __future__ import annotations

            from flask import Flask, flash, jsonify, request

            from app.core.auth import require_recent_reauth, session_user_id
            from app.core.constants import ALLOWED_INVENTORY_REASONS
            from app.core.db import get_connection
            from app.core.db_integrity import DB_INTEGRITY_ERRORS
            from app.core.helpers import normalize_lookup_name
            from app.core.inventory import log_stock_movement
            from app.modules.users.decorators import roles_required
            from app.modules.web import queries as web_queries
            '''
        ).strip()
        + "\n",
    ),
    (
        "owner",
        1828,
        len(lines),
        "owner",
        "app: Flask",
        dedent(
            '''
            from __future__ import annotations

            import shutil
            from datetime import datetime
            from pathlib import Path

            from flask import Flask, flash, redirect, request, send_file, session, url_for

            from app.core.auth import login_required
            from app.core.constants import ALLOWED_IMAGE_EXTENSIONS
            from app.core.db import get_connection
            from app.core.flask_config import BACKUP_DIR, DATABASE, DATABASE_ENGINE, DEFAULT_APP_SETTINGS
            from app.modules.users.decorators import roles_required
            from app.modules.web import queries as web_queries
            from app.modules.web.routes.branding import BRAND_LOGO_DIR, THEME_PRESETS
            '''
        ).strip()
        + "\n",
    ),
]

PKG.mkdir(parents=True, exist_ok=True)

branding = "\n".join(lines[38:48])  # BRAND_LOGO_DIR + THEME_PRESETS through closing `}`
(PKG / "branding.py").write_text(
    "from __future__ import annotations\n\nfrom app.core.flask_config import BASE_DIR\n\n" + branding + "\n",
    encoding="utf-8",
)

for fname, a, b, reg, sig, header in MODULES:
    body = indent_body(a, b)
    text = f"{header}\n\n\ndef register_{reg}_routes({sig}) -> None:\n{body}\n"
    (PKG / f"{fname}.py").write_text(text, encoding="utf-8")

init = dedent(
    '''
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
    from app.modules.web.routes.pos import register_pos_routes


    def register_web_routes(
        app: Flask,
        *,
        product_service: ProductService,
        storefront_service: StorefrontService,
    ) -> None:
        register_misc_routes(app)
        register_auth_routes(app)
        register_pos_routes(app, storefront_service=storefront_service)
        register_dashboards_routes(app)
        register_admin_routes(app, product_service=product_service)
        register_inventory_routes(app)
        register_owner_routes(app)
    '''
).strip() + "\n"
(PKG / "__init__.py").write_text(init, encoding="utf-8")

SRC.unlink()
print("Created package", PKG, "removed", SRC)
