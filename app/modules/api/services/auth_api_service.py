from __future__ import annotations

import time
from typing import Any

from flask import session
from werkzeug.security import check_password_hash

from app.core.user_service import user_service


class AuthApiService:
    def login(self, username: str, pin: str, tenant_id: int | None) -> dict[str, Any]:
        username = username.strip().lower()
        if not username or not pin:
            raise ValueError("username and pin are required.")

        if tenant_id is not None:
            user = user_service.authenticate(username, pin, tenant_id=tenant_id, require_role=None)
        else:
            row = user_service.get_by_username(username, tenant_id=None)
            if (
                row is None
                or not row["is_active"]
                or not check_password_hash(row["pin_hash"], pin)
            ):
                user = None
            else:
                user = dict(row)

        if user is None:
            raise ValueError("Invalid credentials.")

        if user["role"] == "super_admin":
            session["user_id"] = user["id"]
            session["tenant_id"] = None
            session["is_super_admin"] = True
            session["reauth_at"] = time.time()
            session["reauth_user_id"] = user["id"]
        else:
            tid = user.get("tenant_id")
            if tid is None:
                tid = 1
            session["user_id"] = user["id"]
            session["tenant_id"] = int(tid)
            session["is_super_admin"] = False
            session["reauth_at"] = time.time()
            session["reauth_user_id"] = user["id"]

        session.permanent = True
        return {
            "user_id": user["id"],
            "username": user["username"],
            "role": user["role"],
            "full_name": user["full_name"],
            "tenant_id": session.get("tenant_id"),
            "is_super_admin": bool(session.get("is_super_admin")),
        }

    def logout(self) -> None:
        session.clear()


auth_api_service = AuthApiService()
