"""Plan-based limits (demo workspace vs paid plans)."""

from __future__ import annotations

from typing import Any, Literal

from app.modules.web import queries as web_queries

DEMO_PLAN_NAME = "demo"
STARTER_PLAN_NAME = "starter"
GROWTH_PLAN_NAME = "growth"
ENTERPRISE_PLAN_NAME = "enterprise"

GROWTH_TIER_PLANS = frozenset({GROWTH_PLAN_NAME, ENTERPRISE_PLAN_NAME})

# Demo tenants can explore real flows but cannot run a full production catalog or volume.
DEMO_MAX_PRODUCTS = 25
DEMO_MAX_COMPLETED_SALES_PER_MONTH = 75
DEMO_MAX_ONLINE_ORDERS_PER_MONTH = 10
PRICING_WIZARD_TEASER_MAX_PRODUCTS = 3

PricingWizardAccess = Literal["full", "teaser", "locked"]
QrOrderingAccess = Literal["full", "teaser", "locked"]

QR_OVERRIDE_FORCE_ON = "force_on"
QR_OVERRIDE_FORCE_OFF = "force_off"


def normalize_plan_name(plan_name: str | None) -> str:
    return (plan_name or DEMO_PLAN_NAME).strip().lower()


def is_demo_plan_name(plan_name: str | None) -> bool:
    return normalize_plan_name(plan_name) == DEMO_PLAN_NAME


def is_growth_tier_plan(plan_name: str | None) -> bool:
    return normalize_plan_name(plan_name) in GROWTH_TIER_PLANS


def tenant_plan_name(connection: Any, tenant_id: int) -> str:
    row = connection.execute(
        "SELECT plan_name FROM tenants WHERE id = ?",
        (tenant_id,),
    ).fetchone()
    if row is None:
        return DEMO_PLAN_NAME
    return normalize_plan_name(row["plan_name"])


def pricing_wizard_access(plan_name: str | None) -> PricingWizardAccess:
    plan = normalize_plan_name(plan_name)
    if plan in GROWTH_TIER_PLANS:
        return "full"
    if plan == DEMO_PLAN_NAME:
        return "teaser"
    return "locked"


def inventory_reports_access(plan_name: str | None) -> bool:
    return is_growth_tier_plan(plan_name)


def qr_ordering_access(plan_name: str | None) -> QrOrderingAccess:
    plan = normalize_plan_name(plan_name)
    if is_growth_tier_plan(plan):
        return "full"
    if is_demo_plan_name(plan):
        return "teaser"
    return "locked"


def normalize_qr_override(override: str | None) -> str:
    raw = (override or "").strip().lower()
    if raw in (QR_OVERRIDE_FORCE_ON, QR_OVERRIDE_FORCE_OFF):
        return raw
    return ""


def effective_qr_ordering_access(plan_name: str | None, override: str | None) -> QrOrderingAccess:
    forced = normalize_qr_override(override)
    if forced == QR_OVERRIDE_FORCE_OFF:
        return "locked"
    base = qr_ordering_access(plan_name)
    if forced == QR_OVERRIDE_FORCE_ON and base == "locked":
        return "full"
    return base


def demo_online_cap_applies(plan_name: str | None, override: str | None) -> bool:
    if not is_demo_plan_name(plan_name):
        return False
    return normalize_qr_override(override) != QR_OVERRIDE_FORCE_ON


def demo_online_orders_this_month(connection: Any, tenant_id: int) -> int:
    if web_queries.DATABASE_ENGINE == "postgres":
        clause = (
            "to_char((o.created_at)::timestamp, 'YYYY-MM') = to_char(CURRENT_TIMESTAMP, 'YYYY-MM')"
        )
    else:
        clause = (
            "strftime('%Y-%m', o.created_at) = strftime('%Y-%m', datetime('now','localtime'))"
        )
    row = connection.execute(
        f"""
        SELECT COUNT(*) AS c
        FROM orders o
        WHERE o.tenant_id = ? AND {clause}
        """,
        (tenant_id,),
    ).fetchone()
    return int(row["c"] if row else 0)


def assert_online_order_allowed(connection: Any, tenant_id: int) -> None:
    row = connection.execute(
        "SELECT plan_name, qr_ordering_override FROM tenants WHERE id = ?",
        (tenant_id,),
    ).fetchone()
    if row is None:
        raise ValueError("Store not found.")
    tenant_row = dict(row)
    plan_name = tenant_row.get("plan_name")
    override = tenant_row.get("qr_ordering_override")
    access = effective_qr_ordering_access(plan_name, override)
    if access == "locked":
        raise ValueError(
            "Online ordering is available on the Growth plan. Upgrade on the Plans page to accept QR orders."
        )
    if not demo_online_cap_applies(plan_name, override):
        return
    used = demo_online_orders_this_month(connection, tenant_id)
    if used >= DEMO_MAX_ONLINE_ORDERS_PER_MONTH:
        raise ValueError(
            f"Demo workspaces are limited to {DEMO_MAX_ONLINE_ORDERS_PER_MONTH} online orders per month. "
            "Upgrade to Growth for unlimited QR ordering."
        )


def qr_plan_access(connection: Any, tenant_id: int) -> dict[str, object]:
    row = connection.execute(
        "SELECT plan_name, qr_ordering_override FROM tenants WHERE id = ?",
        (tenant_id,),
    ).fetchone()
    plan = normalize_plan_name(row["plan_name"] if row else DEMO_PLAN_NAME)
    tenant_row = dict(row) if row is not None else {}
    override = normalize_qr_override(tenant_row.get("qr_ordering_override"))
    access = effective_qr_ordering_access(plan, override)
    online_used = demo_online_orders_this_month(connection, tenant_id) if access == "teaser" else 0
    cap_applies = demo_online_cap_applies(plan, override)
    return {
        "plan_name": plan,
        "upgrade_plan": GROWTH_PLAN_NAME,
        "upgrade_plan_label": "Growth",
        "upgrade_price_hint": 899,
        "qr_ordering": access,
        "show_qr_full": access == "full",
        "show_qr_teaser": access == "teaser",
        "show_qr_locked": access == "locked",
        "qr_override": override,
        "online_orders_used": online_used,
        "online_orders_max": DEMO_MAX_ONLINE_ORDERS_PER_MONTH if cap_applies else None,
        "online_orders_cap_applies": cap_applies,
    }


def inventory_plan_access(plan_name: str | None) -> dict[str, object]:
    """Feature gates for inventory dashboard sections."""
    plan = normalize_plan_name(plan_name)
    wizard = pricing_wizard_access(plan)
    reports = inventory_reports_access(plan)
    max_wizard_products: int | None = (
        PRICING_WIZARD_TEASER_MAX_PRODUCTS if wizard == "teaser" else None
    )
    return {
        "plan_name": plan,
        "upgrade_plan": GROWTH_PLAN_NAME,
        "upgrade_plan_label": "Growth",
        "upgrade_price_hint": 899,
        "pricing_wizard": wizard,
        "pricing_wizard_max_products": max_wizard_products,
        "inventory_reports": reports,
        "show_pricing_wizard_teaser": wizard == "teaser",
        "show_pricing_wizard_full": wizard == "full",
        "show_pricing_wizard_locked": wizard == "locked",
        "show_inventory_reports": reports,
    }


def empty_reports_context() -> dict[str, object]:
    empty_summary = {
        "today_revenue": 0,
        "today_receipts": 0,
        "month_revenue": 0,
        "month_receipts": 0,
    }
    return {
        "summary": empty_summary,
        "daily_sales": [],
        "top_sellers": [],
        "current_periods": [],
        "daily_inventory": [],
        "weekly_inventory": [],
        "monthly_inventory": [],
        "yearly_inventory": [],
    }


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
        "max_online_orders_month": DEMO_MAX_ONLINE_ORDERS_PER_MONTH,
        "online_orders_this_month": demo_online_orders_this_month(connection, tenant_id),
    }
