"""Plan-based limits (demo workspace vs paid plans)."""

from __future__ import annotations

from typing import Any

from app.modules.web import queries as web_queries

DEMO_PLAN_NAME = "demo"

# Demo tenants can explore real flows but cannot run a full production catalog or volume.
DEMO_MAX_PRODUCTS = 25
DEMO_MAX_COMPLETED_SALES_PER_MONTH = 75


def is_demo_plan_name(plan_name: str | None) -> bool:
    return (plan_name or "").strip().lower() == DEMO_PLAN_NAME


def tenant_is_demo(connection: Any, tenant_id: int) -> bool:
    row = connection.execute(
        "SELECT plan_name FROM tenants WHERE id = ?",
        (tenant_id,),
    ).fetchone()
    if row is None:
        return False
    # Index access works for both sqlite3.Row and psycopg dict_row; .get() does not
    # exist on sqlite3.Row and would crash the owner dashboard on SQLite.
    return is_demo_plan_name(row["plan_name"])


def demo_product_count(connection: Any, tenant_id: int) -> int:
    row = connection.execute(
        "SELECT COUNT(*) AS c FROM products WHERE tenant_id = ?",
        (tenant_id,),
    ).fetchone()
    return int(row["c"] if row else 0)


def demo_completed_sales_this_month(connection: Any, tenant_id: int) -> int:
    if web_queries.DATABASE_ENGINE == "postgres":
        clause = (
            "to_char((s.created_at)::timestamp, 'YYYY-MM') = to_char(CURRENT_TIMESTAMP, 'YYYY-MM')"
        )
    else:
        clause = (
            "strftime('%Y-%m', s.created_at) = strftime('%Y-%m', datetime('now','localtime'))"
        )
    row = connection.execute(
        f"""
        SELECT COUNT(*) AS c
        FROM sales s
        WHERE s.tenant_id = ? AND s.status = 'completed' AND {clause}
        """,
        (tenant_id,),
    ).fetchone()
    return int(row["c"] if row else 0)


def assert_demo_allows_new_product(connection: Any, tenant_id: int) -> None:
    if not tenant_is_demo(connection, tenant_id):
        return
    if demo_product_count(connection, tenant_id) >= DEMO_MAX_PRODUCTS:
        raise ValueError(
            f"Demo workspaces are limited to {DEMO_MAX_PRODUCTS} products. "
            "Upgrade on the Plans page to add more."
        )


def assert_demo_allows_checkout(connection: Any, tenant_id: int) -> None:
    if not tenant_is_demo(connection, tenant_id):
        return
    if demo_completed_sales_this_month(connection, tenant_id) >= DEMO_MAX_COMPLETED_SALES_PER_MONTH:
        raise ValueError(
            f"Demo workspaces are limited to {DEMO_MAX_COMPLETED_SALES_PER_MONTH} completed sales per month. "
            "Upgrade on the Plans page for unlimited selling."
        )


def demo_limits_summary(connection: Any, tenant_id: int) -> dict[str, Any] | None:
    if not tenant_is_demo(connection, tenant_id):
        return None
    return {
        "max_products": DEMO_MAX_PRODUCTS,
        "products_used": demo_product_count(connection, tenant_id),
        "max_sales_month": DEMO_MAX_COMPLETED_SALES_PER_MONTH,
        "sales_this_month": demo_completed_sales_this_month(connection, tenant_id),
    }
