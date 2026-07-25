"""Multi-branch locations.

A tenant may run several branches (locations); each sale is attributed to one.
Every query is explicitly tenant-scoped (belt-and-suspenders alongside RLS).

Per-location stock is intentionally out of scope here — products carry a single
stock figure today, and splitting it per location is a separate inventory change.
"""

from __future__ import annotations

from typing import Any

DEFAULT_LOCATION_NAME = "Main Branch"


class LocationService:
    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def list_locations(self, tenant_id: int, *, include_inactive: bool = False) -> list[dict[str, Any]]:
        sql = (
            "SELECT id, name, code, address, phone, is_default, is_active, created_at "
            "FROM locations WHERE tenant_id = ?"
        )
        params: tuple[Any, ...] = (tenant_id,)
        if not include_inactive:
            sql += " AND is_active = 1"
        sql += " ORDER BY is_default DESC, name"
        rows = self.connection.execute(sql, params).fetchall()
        return [dict(r) for r in rows or []]

    def get_location(self, tenant_id: int, location_id: int) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT id, name, code, address, phone, is_default, is_active, created_at "
            "FROM locations WHERE tenant_id = ? AND id = ?",
            (tenant_id, location_id),
        ).fetchone()
        return dict(row) if row else None

    def get_default(self, tenant_id: int) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT id, name, code, address, phone, is_default, is_active, created_at "
            "FROM locations WHERE tenant_id = ? AND is_default = 1 AND is_active = 1 "
            "ORDER BY id LIMIT 1",
            (tenant_id,),
        ).fetchone()
        if row:
            return dict(row)
        # Fall back to the first active location if no explicit default is set.
        row = self.connection.execute(
            "SELECT id, name, code, address, phone, is_default, is_active, created_at "
            "FROM locations WHERE tenant_id = ? AND is_active = 1 ORDER BY id LIMIT 1",
            (tenant_id,),
        ).fetchone()
        return dict(row) if row else None

    def ensure_default(self, tenant_id: int, name: str = DEFAULT_LOCATION_NAME) -> int:
        """Guarantee the tenant has at least one (default) branch; return its id."""
        existing = self.get_default(tenant_id)
        if existing is not None:
            return int(existing["id"])
        return self.create_location(tenant_id, name, code="MAIN", is_default=True)

    def create_location(
        self,
        tenant_id: int,
        name: str,
        *,
        code: str = "",
        address: str = "",
        phone: str = "",
        is_default: bool = False,
    ) -> int:
        name = (name or "").strip()
        if not name:
            raise ValueError("Branch name is required.")

        has_any = self.connection.execute(
            "SELECT 1 FROM locations WHERE tenant_id = ? LIMIT 1", (tenant_id,)
        ).fetchone()
        # The first branch is always the default; otherwise honour the flag.
        make_default = is_default or has_any is None
        if make_default:
            self.connection.execute(
                "UPDATE locations SET is_default = 0 WHERE tenant_id = ?", (tenant_id,)
            )

        row = self.connection.execute(
            "INSERT INTO locations (tenant_id, name, code, address, phone, is_default, is_active) "
            "VALUES (?, ?, ?, ?, ?, ?, 1) RETURNING id",
            (tenant_id, name, code.strip(), address.strip(), phone.strip(), 1 if make_default else 0),
        ).fetchone()
        return int(row["id"])

    def update_location(
        self,
        tenant_id: int,
        location_id: int,
        *,
        name: str | None = None,
        code: str | None = None,
        address: str | None = None,
        phone: str | None = None,
        is_active: bool | None = None,
    ) -> None:
        current = self.get_location(tenant_id, location_id)
        if current is None:
            raise ValueError("Branch not found.")
        if is_active is False and current["is_default"]:
            raise ValueError("Cannot deactivate the default branch. Set another branch as default first.")

        fields: list[str] = []
        params: list[Any] = []
        for column, value in (
            ("name", None if name is None else name.strip()),
            ("code", None if code is None else code.strip()),
            ("address", None if address is None else address.strip()),
            ("phone", None if phone is None else phone.strip()),
            ("is_active", None if is_active is None else (1 if is_active else 0)),
        ):
            if value is not None:
                fields.append(f"{column} = ?")
                params.append(value)
        if not fields:
            return
        params.extend([tenant_id, location_id])
        self.connection.execute(
            f"UPDATE locations SET {', '.join(fields)} WHERE tenant_id = ? AND id = ?",
            tuple(params),
        )

    def set_default(self, tenant_id: int, location_id: int) -> None:
        location = self.get_location(tenant_id, location_id)
        if location is None or not location["is_active"]:
            raise ValueError("Branch not found or inactive.")
        self.connection.execute(
            "UPDATE locations SET is_default = 0 WHERE tenant_id = ?", (tenant_id,)
        )
        self.connection.execute(
            "UPDATE locations SET is_default = 1 WHERE tenant_id = ? AND id = ?",
            (tenant_id, location_id),
        )

    def resolve_active_location_id(self, tenant_id: int, requested: Any = None) -> int | None:
        """Resolve the branch a sale belongs to: a valid requested id, else the default."""
        if requested:
            try:
                requested_id = int(requested)
            except (TypeError, ValueError):
                requested_id = None
            if requested_id is not None:
                location = self.get_location(tenant_id, requested_id)
                if location is not None and location["is_active"]:
                    return requested_id
        default = self.get_default(tenant_id)
        return int(default["id"]) if default else None
