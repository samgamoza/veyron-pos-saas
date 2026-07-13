from __future__ import annotations

from typing import Any

from flask import session

from app.core.auth import session_user_id
from app.core.db import get_connection, get_raw_connection
from app.core.tenant.context import current_tenant_id


class AuditService:
    def _resolve_tenant_id(self, user_id: int | None = None) -> int:
        tenant_id = session.get("tenant_id")
        if tenant_id:
            return int(tenant_id)

        if user_id is not None:
            with get_raw_connection() as connection:
                row = connection.execute(
                    "SELECT tenant_id FROM users WHERE id = ?",
                    (user_id,),
                ).fetchone()
                if row is not None and row["tenant_id"]:
                    return int(row["tenant_id"])

        with get_raw_connection() as connection:
            row = connection.execute("SELECT id FROM tenants ORDER BY id ASC LIMIT 1").fetchone()
            if row is not None:
                return int(row["id"])

        raise RuntimeError("Unable to resolve tenant for audit log entry.")

    def log(
        self,
        action: str,
        entity_type: str,
        entity_id: int | None = None,
        details: str = "",
        connection: Any | None = None,
        user_id: int | None = None,
        tenant_id: int | None = None,
    ) -> None:
        if connection is None:
            connection = get_connection()
            close_after = True
        else:
            close_after = False

        if tenant_id is None:
            tenant_id = current_tenant_id() or self._resolve_tenant_id(user_id or session_user_id())

        if user_id is None:
            user_id = session_user_id()

        try:
            connection.execute(
                "INSERT INTO audit_logs (tenant_id, user_id, action, entity_type, entity_id, details) VALUES (?, ?, ?, ?, ?, ?)",
                (tenant_id, user_id, action, entity_type, entity_id, details),
            )
        finally:
            if close_after:
                connection.close()


audit_service = AuditService()
