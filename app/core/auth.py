from __future__ import annotations

import time
from functools import wraps
from typing import Any

from flask import flash, redirect, request, session, url_for

from app.core.db import get_connection
from app.core.tenant.context import is_super_admin
from app.core.user_service import user_service
from app.modules.users.permissions import resolve_roles

REAUTH_TTL_SECONDS = 600


def post_login_redirect_for_user(user: dict[str, object]) -> str:
    """Default landing page after login, signup, or re-auth (when no explicit next URL)."""
    from app.modules.web import queries as web_queries

    role = user.get("role")
    if role == "super_admin":
        return url_for("superadmin.super_admin_dashboard")
    if role == "owner":
        if session.get("tenant_id") and web_queries.get_setting("onboarding_completed", "0") != "1":
            return url_for("onboarding_home")
        return url_for("owner_dashboard")
    if role == "admin":
        return url_for("admin_dashboard")
    return url_for("pos")


def get_current_user() -> dict[str, object] | None:
    user_id = session.get("user_id")
    tenant_id = session.get("tenant_id")
    if not user_id:
        return None

    user = user_service.get_by_id(user_id, tenant_id=tenant_id)

    if user is None or not user["is_active"]:
        session.clear()
        return None

    session["is_super_admin"] = user["role"] == "super_admin"
    return dict(user)


def login_required(*roles: str):
    def decorator(view):
        @wraps(view)
        def wrapped_view(*args, **kwargs):
            user = get_current_user()
            if user is None:
                flash("Log in first to continue.", "error")
                return redirect(url_for("login", next=request.path))

            if user["role"] == "super_admin":
                return view(*args, **kwargs)

            allowed_roles = resolve_roles(roles)
            if roles and user["role"] not in allowed_roles:
                flash("You do not have permission to access that page.", "error")
                if user["role"] == "owner":
                    return redirect(url_for("owner_dashboard"))
                if user["role"] == "admin":
                    return redirect(url_for("admin_dashboard"))
                return redirect(url_for("pos"))

            return view(*args, **kwargs)

        return wrapped_view

    return decorator


def require_recent_reauth(max_age_seconds: int = REAUTH_TTL_SECONDS):
    def decorator(view):
        @wraps(view)
        def wrapped_view(*args, **kwargs):
            user = get_current_user()
            if user is None:
                flash("Log in first to continue.", "error")
                return redirect(url_for("login", next=request.path))

            last_reauth = session.get("reauth_at")
            reauth_user_id = session.get("reauth_user_id")
            is_fresh = (
                isinstance(last_reauth, (int, float))
                and reauth_user_id == user["id"]
                and (time.time() - float(last_reauth)) <= max_age_seconds
            )
            if not is_fresh:
                if request.method == "GET":
                    next_target = request.path
                else:
                    next_target = request.referrer
                    if not next_target:
                        if request.path.startswith("/superadmin"):
                            next_target = url_for("superadmin.super_admin_dashboard")
                        elif request.path.startswith("/admin"):
                            next_target = url_for("admin_dashboard")
                        elif request.path.startswith("/inventory"):
                            next_target = url_for("inventory_dashboard")
                        else:
                            next_target = post_login_redirect_for_user(user)
                flash("Please confirm your PIN to continue.", "warning")
                return redirect(url_for("reauth", next=next_target))
            return view(*args, **kwargs)

        return wrapped_view

    return decorator


def session_user_id() -> int | None:
    user_id = session.get("user_id")
    return int(user_id) if user_id else None
