from __future__ import annotations

from flask import Flask, render_template

from app.core.auth import login_required
from app.modules.users.decorators import roles_required
from app.modules.web import queries as web_queries



def register_dashboards_routes(app: Flask) -> None:
    @app.route("/admin")
    @roles_required("tenant_admin")
    def admin_dashboard() -> str:
        return render_template("admin_dashboard.html", **web_queries.fetch_admin_context())


    @app.route("/inventory")
    @roles_required("tenant_admin")
    def inventory_dashboard() -> str:
        return render_template("inventory_dashboard.html", **web_queries.fetch_inventory_context())


    @app.route("/owner")
    @login_required("owner")
    def owner_dashboard() -> str:
        return render_template("owner_dashboard.html", **web_queries.fetch_owner_context())


