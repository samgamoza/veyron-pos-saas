from __future__ import annotations

from functools import wraps
from typing import Any, Callable, Iterable

from flask import Response, g, request, session

from app.core.api.responses import err_json
from app.core.auth import get_current_user
from app.modules.users.permissions import resolve_roles


def require_api_tenant() -> tuple[Response, int] | None:
    if not getattr(g, "tenant_id", None):
        return err_json(
            "Tenant context required (session, X-Tenant-ID, or subdomain).",
            code="tenant_required",
            status=401,
        )
    return None


def require_api_user() -> tuple[Response, int] | None:
    user = get_current_user()
    if user is None:
        return err_json("Authentication required.", code="auth_required", status=401)
    g.api_user = user
    return None


def require_api_roles(*roles: str) -> tuple[Response, int] | None:
    denied = require_api_user()
    if denied:
        return denied
    user = g.api_user
    if user["role"] == "super_admin":
        return None
    allowed = resolve_roles(roles)
    if roles and user["role"] not in allowed:
        return err_json("Insufficient permissions for this API.", code="forbidden", status=403)
    return None


def require_super_admin_api() -> tuple[Response, int] | None:
    denied = require_api_user()
    if denied:
        return denied
    if not session.get("is_super_admin"):
        return err_json("Super admin access required.", code="super_admin_required", status=403)
    return None


def api_roles_required(*roles: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def decorator(view: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(view)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            block = require_api_roles(*roles)
            if block:
                return block
            return view(*args, **kwargs)

        return wrapped

    return decorator


def api_super_admin_required(view: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(view)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        block = require_super_admin_api()
        if block:
            return block
        return view(*args, **kwargs)

    return wrapped


def register_tenant_before_request(bp: Any, *, exempt_endpoints: Iterable[str] | None = None) -> None:
    exempt_set = set(exempt_endpoints or ())

    @bp.before_request
    def _tenant_guard() -> tuple[Response, int] | None:
        ep = request.endpoint
        if ep and ep in exempt_set:
            return None
        return require_api_tenant()
