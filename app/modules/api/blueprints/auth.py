from __future__ import annotations

from flask import Blueprint, request

from app.core.api import err_json, ok_json
from app.modules.api.services.auth_api_service import auth_api_service

auth_api_bp = Blueprint("auth_api", __name__, url_prefix="/api/auth")


@auth_api_bp.post("/login")
def login():
    body = request.get_json(silent=True) or {}
    try:
        username = str(body.get("username", ""))
        pin = str(body.get("pin", ""))
        tenant_raw = body.get("tenant_id")
        tenant_id = int(tenant_raw) if tenant_raw not in (None, "") else None
        data = auth_api_service.login(username, pin, tenant_id)
        return ok_json(data, message="Logged in.")
    except ValueError as exc:
        return err_json(str(exc), code="login_failed", status=401)


@auth_api_bp.post("/logout")
def logout():
    auth_api_service.logout()
    return ok_json(message="Logged out.")


@auth_api_bp.get("/me")
def me():
    from app.core.auth import get_current_user

    user = get_current_user()
    if user is None:
        return err_json("Not authenticated.", code="auth_required", status=401)
    return ok_json(
        {
            "user_id": user["id"],
            "username": user["username"],
            "role": user["role"],
            "full_name": user["full_name"],
            "tenant_id": user.get("tenant_id"),
        }
    )
