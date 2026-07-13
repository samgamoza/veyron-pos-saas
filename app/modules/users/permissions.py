from __future__ import annotations

from typing import Iterable, Set

ROLE_SUPER_ADMIN = "super_admin"
ROLE_TENANT_ADMIN = "tenant_admin"
ROLE_STAFF = "staff"

ROLE_ALIASES: dict[str, set[str]] = {
    ROLE_SUPER_ADMIN: {"super_admin"},
    ROLE_TENANT_ADMIN: {"owner", "admin"},
    ROLE_STAFF: {"cashier"},
}

ROLE_PERMISSIONS: dict[str, set[str]] = {
    "manage_system": {"super_admin"},
    "manage_tenants": {"super_admin"},
    "manage_users": {"tenant_admin"},
    "manage_inventory": {"tenant_admin"},
    "process_sales": {"tenant_admin", "staff"},
    "manage_delivery": {"tenant_admin"},
}

Permission = str


def resolve_roles(roles: Iterable[str]) -> set[str]:
    allowed: set[str] = set()
    for role in roles:
        normalized = role.strip().lower()
        allowed.update(ROLE_ALIASES.get(normalized, {normalized}))
    return allowed


def resolve_permission_roles(permission: str) -> set[str]:
    return resolve_roles(ROLE_PERMISSIONS.get(permission, set()))
