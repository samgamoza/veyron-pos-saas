from __future__ import annotations

from typing import Any

from flask import g
from werkzeug.security import check_password_hash

from app.core.db import get_raw_connection
from app.core.tenant.context import current_tenant_id


class UserService:
    def get_by_username(
        self,
        username: str,
        tenant_id: int | None = None,
        *,
        global_username: bool = False,
    ) -> dict[str, Any] | None:
        """If global_username is True, match username across all tenants (e.g. /login before session has tenant_id)."""
        if global_username:
            with get_raw_connection() as connection:
                row = connection.execute(
                    """
                    SELECT id, tenant_id, full_name, username, role, pin_hash, is_active, language_override
                    FROM users WHERE lower(trim(username)) = lower(trim(?))
                    """,
                    (username,),
                ).fetchone()
            return row

        if tenant_id is None:
            tenant_id = current_tenant_id()

        with get_raw_connection() as connection:
            if tenant_id is not None:
                row = connection.execute(
                    "SELECT id, tenant_id, full_name, username, role, pin_hash, is_active, language_override FROM users WHERE username = ? AND tenant_id = ?",
                    (username, tenant_id),
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT id, tenant_id, full_name, username, role, pin_hash, is_active, language_override FROM users WHERE username = ?",
                    (username,),
                ).fetchone()
        return row

    def get_by_id(self, user_id: int, tenant_id: int | None = None) -> dict[str, Any] | None:
        if tenant_id is None:
            tenant_id = current_tenant_id()

        with get_raw_connection() as connection:
            if tenant_id is not None:
                row = connection.execute(
                    "SELECT id, tenant_id, full_name, username, role, is_active FROM users WHERE id = ? AND tenant_id = ?",
                    (user_id, tenant_id),
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT id, tenant_id, full_name, username, role, is_active FROM users WHERE id = ?",
                    (user_id,),
                ).fetchone()
        return row

    def authenticate(
        self,
        username: str,
        pin: str,
        tenant_id: int | None = None,
        require_role: str | None = None,
        *,
        force_global_username: bool = False,
    ) -> dict[str, Any] | None:
        if force_global_username:
            user = self.get_by_username(username, global_username=True)
        elif tenant_id is not None:
            user = self.get_by_username(username, tenant_id=tenant_id)
        else:
            resolved = current_tenant_id()
            if resolved is not None:
                user = self.get_by_username(username, tenant_id=resolved)
            else:
                user = self.get_by_username(username, global_username=True)
        if user is None:
            return None

        if require_role is not None and user["role"] != require_role:
            return None

        if not user["is_active"]:
            return None

        if not check_password_hash(user["pin_hash"], pin):
            return None

        return user


user_service = UserService()
