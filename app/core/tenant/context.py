from __future__ import annotations

import re
from typing import Optional

from flask import g, request, session

from .model import Tenant

TENANT_HEADER = "X-Tenant-ID"
HOST_WHITELIST = {"localhost", "127.0.0.1", "0.0.0.0"}


def current_tenant_id() -> int | None:
    return getattr(g, "tenant_id", None)


def sync_tenant_context_from_session() -> None:
    """Refresh ``g.tenant_id`` from the session mid-request.

    ``g.tenant_id`` is normally set once by the ``before_request`` hook, read
    from the session as it stood at the *start* of the request. Login and
    signup mutate ``session["tenant_id"]`` partway through handling their own
    request, so without this, any DB write later in that same request (e.g.
    an audit log insert) still carries the pre-login tenant context and gets
    rejected by RLS. Call this immediately after establishing a new
    session identity, before any further ``get_connection()`` use.
    """
    g.tenant_id = session.get("tenant_id")


def current_tenant() -> Tenant | None:
    return getattr(g, "tenant", None)


def is_super_admin() -> bool:
    return bool(session.get("is_super_admin", False))


def parse_subdomain(host: str) -> Optional[str]:
    if not host:
        return None

    host = host.split(":")[0].strip().lower()
    if host in HOST_WHITELIST:
        return None

    parts = host.split(".")
    if len(parts) >= 3:
        return parts[0]
    if len(parts) == 2 and parts[1] == "localhost":
        return parts[0]
    return None


def row_to_tenant(row: dict | None) -> Tenant | None:
    if row is None:
        return None
    r = dict(row) if hasattr(row, "keys") and not hasattr(row, "get") else row
    return Tenant(
        id=r["id"],
        name=r["name"],
        subdomain=r.get("subdomain") or "",
        status=r.get("status") or "active",
        created_at=r["created_at"],
        is_active=bool(r.get("is_active", 1)),
    )


def load_tenant_by_header(connection, tenant_id_value: str) -> Tenant | None:
    if not tenant_id_value:
        return None

    row = connection.execute(
        "SELECT id, name, subdomain, status, created_at, is_active FROM tenants WHERE id = ? AND status = 'active'",
        (tenant_id_value,),
    ).fetchone()
    return row_to_tenant(row)


def load_tenant_by_id(connection, tenant_id_value: int | str) -> Tenant | None:
    if not tenant_id_value:
        return None

    row = connection.execute(
        "SELECT id, name, subdomain, status, created_at, is_active FROM tenants WHERE id = ? AND status = 'active'",
        (tenant_id_value,),
    ).fetchone()
    return row_to_tenant(row)


def load_tenant_by_subdomain(connection, subdomain: str) -> Tenant | None:
    if not subdomain:
        return None

    row = connection.execute(
        "SELECT id, name, subdomain, status, created_at, is_active FROM tenants WHERE subdomain = ? AND status = 'active'",
        (subdomain,),
    ).fetchone()
    return row_to_tenant(row)


def slugify_subdomain(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "tenant"


def build_tenant_subdomain(connection, tenant_name: str) -> str:
    base_subdomain = slugify_subdomain(tenant_name)
    candidate = base_subdomain
    counter = 1
    while connection.execute(
        "SELECT 1 FROM tenants WHERE subdomain = ?",
        (candidate,),
    ).fetchone():
        counter += 1
        candidate = f"{base_subdomain}-{counter}"
    return candidate
