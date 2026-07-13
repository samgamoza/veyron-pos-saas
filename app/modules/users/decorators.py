from __future__ import annotations

from functools import wraps
from typing import Callable

from flask import flash, redirect, request, url_for

from app.core.auth import get_current_user
from app.modules.users.permissions import Permission, resolve_permission_roles, resolve_roles


def roles_required(*roles: str) -> Callable[[Callable], Callable]:
    def decorator(view: Callable) -> Callable:
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
                return redirect(url_for("pos"))

            return view(*args, **kwargs)

        return wrapped_view

    return decorator


def permission_required(permission: Permission) -> Callable[[Callable], Callable]:
    def decorator(view: Callable) -> Callable:
        @wraps(view)
        def wrapped_view(*args, **kwargs):
            user = get_current_user()
            if user is None:
                flash("Log in first to continue.", "error")
                return redirect(url_for("login", next=request.path))

            if user["role"] == "super_admin":
                return view(*args, **kwargs)

            allowed_roles = resolve_permission_roles(permission)
            if user["role"] not in allowed_roles:
                flash("You do not have permission to access that page.", "error")
                return redirect(url_for("pos"))

            return view(*args, **kwargs)

        return wrapped_view

    return decorator
