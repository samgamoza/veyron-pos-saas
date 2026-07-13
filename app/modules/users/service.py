from __future__ import annotations

from typing import Optional

from app.core.auth import get_current_user


class UserService:
    @staticmethod
    def current_user() -> dict[str, object] | None:
        return get_current_user()

    @staticmethod
    def current_role() -> Optional[str]:
        user = UserService.current_user()
        return user["role"] if user is not None else None

    @staticmethod
    def is_super_admin() -> bool:
        return UserService.current_role() == "super_admin"

    @staticmethod
    def is_tenant_admin() -> bool:
        return UserService.current_role() in {"owner", "admin"}

    @staticmethod
    def is_staff() -> bool:
        return UserService.current_role() == "cashier"
