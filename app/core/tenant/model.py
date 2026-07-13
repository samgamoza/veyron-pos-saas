from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Tenant:
    id: int
    name: str
    subdomain: str | None
    status: str
    created_at: str
    is_active: bool = True

    @property
    def active(self) -> bool:
        return self.is_active and self.status == "active"
