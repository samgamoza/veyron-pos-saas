from __future__ import annotations

from typing import Any

from app.core.db import get_connection
from app.core.delivery.models import DeliverySettings
from app.core.delivery.references import REFERENCE_SALE


RIDER_COLUMNS = (
    "id, tenant_id, name, phone, vehicle, is_active, last_known_latitude, "
    "last_known_longitude, last_seen_at, created_at, updated_at"
)

DELIVERY_ORDER_COLUMNS = (
    "id, tenant_id, order_id, reference_type, rider_id, address, instructions, delivery_fee, "
    "status, assigned_at, picked_up_at, delivered_at, created_at, updated_at"
)


class DeliveryRepository:
    def get_tenant_delivery_settings(self, tenant_id: int) -> DeliverySettings | None:
        with get_connection() as connection:
            tenant_row = connection.execute(
                "SELECT delivery_enabled, delivery_provider FROM tenants WHERE id = ?",
                (tenant_id,),
            ).fetchone()
            if tenant_row is None:
                return None

            setting_rows = connection.execute(
                "SELECT key, value FROM app_settings WHERE tenant_id = ? AND key IN (?, ?, ?, ?)",
                (
                    tenant_id,
                    "delivery_fee_base",
                    "delivery_fee_per_km",
                    "delivery_fee_free_threshold",
                    "delivery_zones",
                ),
            ).fetchall()
            settings = dict(tenant_row)
            settings.update({row["key"]: row["value"] for row in setting_rows})
            return DeliverySettings.from_dict(settings)

    def create_rider(self, tenant_id: int, name: str, phone: str, vehicle: str) -> int:
        with get_connection() as connection:
            row = connection.execute(
                """
                INSERT INTO riders (tenant_id, name, phone, vehicle, is_active)
                VALUES (?, ?, ?, ?, 1)
                RETURNING id
                """,
                (tenant_id, name, phone, vehicle),
            ).fetchone()
        return int(row["id"])

    def get_rider(self, tenant_id: int, rider_id: int) -> dict[str, Any] | None:
        with get_connection() as connection:
            return connection.execute(
                f"SELECT {RIDER_COLUMNS} FROM riders WHERE tenant_id = ? AND id = ?",
                (tenant_id, rider_id),
            ).fetchone()

    def list_riders(self, tenant_id: int, only_active: bool = True) -> list[dict[str, Any]]:
        with get_connection() as connection:
            if only_active:
                rows = connection.execute(
                    f"SELECT {RIDER_COLUMNS} FROM riders WHERE tenant_id = ? AND is_active = 1 ORDER BY name ASC",
                    (tenant_id,),
                ).fetchall()
            else:
                rows = connection.execute(
                    f"SELECT {RIDER_COLUMNS} FROM riders WHERE tenant_id = ? ORDER BY name ASC",
                    (tenant_id,),
                ).fetchall()
        return [dict(row) for row in rows]

    def create_delivery_order(
        self,
        tenant_id: int,
        order_id: int,
        address: str,
        instructions: str,
        delivery_fee: float,
        *,
        reference_type: str = REFERENCE_SALE,
    ) -> int:
        with get_connection() as connection:
            row = connection.execute(
                """
                INSERT INTO delivery_orders (
                    tenant_id, order_id, reference_type, address, instructions, delivery_fee, status
                ) VALUES (?, ?, ?, ?, ?, ?, 'pending')
                RETURNING id
                """,
                (tenant_id, order_id, reference_type, address, instructions, delivery_fee),
            ).fetchone()
        return int(row["id"])

    def get_delivery_order(
        self,
        tenant_id: int,
        order_id: int,
        *,
        reference_type: str = REFERENCE_SALE,
    ) -> dict[str, Any] | None:
        with get_connection() as connection:
            row = connection.execute(
                f"""
                SELECT {DELIVERY_ORDER_COLUMNS}
                FROM delivery_orders
                WHERE tenant_id = ? AND order_id = ? AND reference_type = ?
                """,
                (tenant_id, order_id, reference_type),
            ).fetchone()
        return dict(row) if row else None

    def list_delivery_orders(self, tenant_id: int, status: str | None = None) -> list[dict[str, Any]]:
        with get_connection() as connection:
            if status:
                rows = connection.execute(
                    f"SELECT {DELIVERY_ORDER_COLUMNS} FROM delivery_orders WHERE tenant_id = ? AND status = ? ORDER BY created_at DESC",
                    (tenant_id, status),
                ).fetchall()
            else:
                rows = connection.execute(
                    f"SELECT {DELIVERY_ORDER_COLUMNS} FROM delivery_orders WHERE tenant_id = ? ORDER BY created_at DESC",
                    (tenant_id,),
                ).fetchall()
        return [dict(row) for row in rows]

    def count_active_delivery_assignments(self, tenant_id: int, rider_id: int) -> int:
        with get_connection() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS total FROM delivery_orders WHERE tenant_id = ? AND rider_id = ? AND status IN ('assigned', 'in_transit')",
                (tenant_id, rider_id),
            ).fetchone()
        return int(row["total"] if row is not None else 0)

    def assign_rider(self, tenant_id: int, delivery_order_id: int, rider_id: int) -> None:
        with get_connection() as connection:
            connection.execute(
                "UPDATE delivery_orders SET rider_id = ?, status = 'assigned', assigned_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE tenant_id = ? AND id = ?",
                (rider_id, tenant_id, delivery_order_id),
            )

    def update_delivery_status(self, tenant_id: int, delivery_order_id: int, status: str) -> None:
        timestamp_column = {
            "assigned": "assigned_at",
            "in_transit": "picked_up_at",
            "delivered": "delivered_at",
        }.get(status)

        if timestamp_column:
            sql = f"UPDATE delivery_orders SET status = ?, {timestamp_column} = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE tenant_id = ? AND id = ?"
        else:
            sql = "UPDATE delivery_orders SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE tenant_id = ? AND id = ?"

        with get_connection() as connection:
            connection.execute(sql, (status, tenant_id, delivery_order_id))
