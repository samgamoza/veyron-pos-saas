from __future__ import annotations

import time

from flask import Flask, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from app.core.auth import get_current_user, login_required, post_login_redirect_for_user
from app.core.db import get_connection, get_raw_connection
from app.core.db_integrity import DB_INTEGRITY_ERRORS
from app.core.helpers import normalize_lookup_name
from app.core.localization import localization_service
from app.core.tenant.tenant_service import TenantService
from app.core.user_service import user_service
from app.modules.web import queries as web_queries



def register_auth_routes(app: Flask) -> None:
    @app.route("/login", methods=["GET", "POST"])
    def login() -> str:
        if request.method == "POST":
            username = request.form.get("username", "").strip().lower()
            pin = request.form.get("pin", "").strip()
            next_url = request.form.get("next", "").strip()

            user = user_service.authenticate(username, pin, force_global_username=True)
            if user:
                session["user_id"] = user["id"]
                session["tenant_id"] = user["tenant_id"] if user["tenant_id"] else (None if user["role"] == "super_admin" else 1)
                session["is_super_admin"] = user["role"] == "super_admin"
                session.pop("reauth_at", None)
                session.pop("reauth_user_id", None)
                language = user["language_override"]
                if not language and session.get("tenant_id"):
                    with get_connection() as connection:
                        tenant_row = connection.execute(
                            "SELECT language FROM tenants WHERE id = ?",
                            (session["tenant_id"],),
                        ).fetchone()
                        language = tenant_row["language"] if tenant_row is not None else None
                if not language:
                    language = web_queries.get_platform_setting("platform_default_language")
                session["language"] = localization_service.normalize_locale(language)
                with get_connection() as connection:
                    connection.execute(
                        "UPDATE users SET last_login = CURRENT_TIMESTAMP WHERE id = ?",
                        (user["id"],),
                    )
                    web_queries.log_audit(connection, "login", "user", user["id"], f"{user['username']} signed in.")
                return redirect(next_url or post_login_redirect_for_user(dict(user)))

            flash("Invalid username or PIN.", "error")

        return render_template("login.html", next=request.args.get("next") or request.form.get("next", ""))


    @app.route("/signup", methods=["GET", "POST"])
    def signup() -> str:
        if web_queries.get_platform_setting("allow_public_signup", "1") != "1":
            flash("New tenant registration is temporarily closed. Contact support if you need access.", "error")
            return redirect(url_for("login"))

        if request.method == "POST":
            business_name = normalize_lookup_name(request.form.get("business_name", ""))
            owner_name = normalize_lookup_name(request.form.get("owner_name", ""))
            username = request.form.get("username", "").strip().lower()
            pin = request.form.get("pin", "").strip()
            plan_name = "demo"
            contact_email = request.form.get("contact_email", "").strip()

            if not all([business_name, owner_name, username, pin]):
                flash("Business name, owner name, username, and PIN are required.", "error")
                return render_template("signup.html")
            if len(pin) < 4:
                flash("PIN must be at least 4 characters.", "error")
                return render_template("signup.html")

            try:
                with get_raw_connection() as connection:
                    existing_tenant = connection.execute("SELECT id FROM tenants WHERE name = ?", (business_name,)).fetchone()
                    if existing_tenant is not None:
                        flash("Business name already exists. Please choose a different name.", "error")
                        return render_template("signup.html")

                    existing_username = connection.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
                    if existing_username is not None:
                        flash("Username already exists. Please choose a different username.", "error")
                        return render_template("signup.html")

                with get_connection() as connection:
                    tenant = TenantService(connection).create_tenant(
                        name=business_name,
                        contact_email=contact_email,
                        plan_name=plan_name,
                        subscription_status="demo",
                        monthly_fee=0.0,
                        billing_currency="PHP",
                        billing_cycle="monthly",
                        storefront_enabled=1,
                        storefront_url="",
                        auto_storefront_enabled=1,
                        storefront_template="default",
                    )
                    tenant_id = tenant.id

                with get_connection() as connection:
                    user_row = connection.execute(
                        """
                        INSERT INTO users (tenant_id, full_name, username, role, pin_hash, is_active)
                        VALUES (?, ?, ?, 'owner', ?, 1)
                        RETURNING id
                        """,
                        (tenant_id, owner_name, username, generate_password_hash(pin)),
                    ).fetchone()

                    web_queries.log_audit(
                        connection,
                        "signup",
                        "tenant",
                        tenant_id,
                        f"Self-serve signup created tenant '{business_name}' with owner '{username}' on plan '{plan_name}'.",
                    )

                session["user_id"] = user_row["id"]
                session["tenant_id"] = tenant_id
                session["is_super_admin"] = False
                plat_lang = web_queries.get_platform_setting("platform_default_language")
                session["language"] = localization_service.normalize_locale(plat_lang)
                session.pop("reauth_at", None)
                session.pop("reauth_user_id", None)
                user = get_current_user()
                flash("Welcome! Your free demo workspace is ready — follow the quick tour next.", "success")
                return redirect(
                    post_login_redirect_for_user(user) if user else url_for("onboarding_home")
                )
            except DB_INTEGRITY_ERRORS:
                flash("Unable to complete signup right now. Please try again.", "error")

        return render_template("signup.html")


    @app.route("/superadmin/login", methods=["GET", "POST"])
    def superadmin_login() -> str:
        next_url = request.args.get("next") or request.form.get("next", "")
        if request.method == "POST":
            username = request.form.get("username", "").strip().lower()
            pin = request.form.get("pin", "").strip()
            user = user_service.authenticate(
                username, pin, tenant_id=None, require_role="super_admin", force_global_username=True
            )
            if user:
                session["user_id"] = user["id"]
                session["tenant_id"] = None
                session["is_super_admin"] = True
                session.pop("reauth_at", None)
                session.pop("reauth_user_id", None)
                language = user["language_override"] or web_queries.get_platform_setting("platform_default_language")
                session["language"] = localization_service.normalize_locale(language)
                with get_connection() as connection:
                    connection.execute(
                        "UPDATE users SET last_login = CURRENT_TIMESTAMP WHERE id = ?",
                        (user["id"],),
                    )
                    web_queries.log_audit(connection, "login", "user", user["id"], f"{user['username']} signed in via super admin portal.")
                return redirect(next_url or url_for("superadmin.super_admin_dashboard"))
            flash("Invalid Super Admin credentials.", "error")

        return render_template("super_admin_login.html", next=next_url)


    @app.route("/reauth", methods=["GET", "POST"])
    @login_required("owner", "admin", "cashier", "super_admin")
    def reauth() -> str:
        user = get_current_user()
        if user is None:
            return redirect(url_for("login", next=request.args.get("next", "")))

        next_url = request.args.get("next") or request.form.get("next", "")
        if request.method == "POST":
            pin = request.form.get("pin", "").strip()
            with get_connection() as connection:
                db_user = connection.execute(
                    "SELECT id, username, pin_hash FROM users WHERE id = ?",
                    (user["id"],),
                ).fetchone()
                if db_user and check_password_hash(db_user["pin_hash"], pin):
                    session["reauth_at"] = time.time()
                    session["reauth_user_id"] = user["id"]
                    web_queries.log_audit(connection, "reauth", "user", db_user["id"], f"{db_user['username']} re-authenticated.")
                    if user["role"] == "super_admin":
                        return redirect(next_url or url_for("superadmin.super_admin_dashboard"))
                    return redirect(next_url or post_login_redirect_for_user(user))
            flash("Invalid PIN. Please try again.", "error")

        return render_template("reauth.html", next=next_url, auth_user=user)


    @app.route("/logout")
    def logout():
        user = get_current_user()
        if user is not None:
            with get_connection() as connection:
                web_queries.log_audit(connection, "logout", "user", user["id"], f"{user['username']} signed out.")
        session.clear()
        flash("Signed out.", "success")
        return redirect(url_for("login"))


