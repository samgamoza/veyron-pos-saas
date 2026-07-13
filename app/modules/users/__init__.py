from .permissions import Permission, ROLE_ALIASES, ROLE_PERMISSIONS, resolve_roles, resolve_permission_roles


def permission_required(*permissions):
    from .decorators import permission_required as _permission_required
    return _permission_required(*permissions)


def roles_required(*roles):
    from .decorators import roles_required as _roles_required
    return _roles_required(*roles)

__all__ = [
    "permission_required",
    "roles_required",
    "Permission",
    "ROLE_ALIASES",
    "ROLE_PERMISSIONS",
    "resolve_roles",
    "resolve_permission_roles",
]
