from __future__ import annotations

from functools import wraps

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from .service import GlobalProductAdminService

product_admin_bp = Blueprint("product_admin", __name__, url_prefix="/admin/products")
service = GlobalProductAdminService()


def super_admin_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if not session.get("user_id") or not session.get("is_super_admin"):
            flash("Super admin access required.", "error")
            return redirect(url_for("superadmin_login"))
        return view(*args, **kwargs)

    return wrapped_view


@product_admin_bp.route("/")
@super_admin_required
def index() -> str:
    tenant_id = request.args.get("tenant_id")
    category_id = request.args.get("category_id")
    try:
        tenant_filter = int(tenant_id) if tenant_id else None
    except ValueError:
        tenant_filter = None
    try:
        category_filter = int(category_id) if category_id else None
    except ValueError:
        category_filter = None

    products = service.list_products(tenant_filter, category_filter)
    tenants = service.list_tenants()
    categories = service.list_categories()
    return render_template(
        "admin_products.html",
        products=products,
        tenants=tenants,
        categories=categories,
        selected_tenant_id=tenant_filter,
        selected_category_id=category_filter,
    )
