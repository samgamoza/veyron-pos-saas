from __future__ import annotations

import re
from typing import Any

from flask import g, has_request_context

from .context import current_tenant_id, is_super_admin

SCOPED_TABLES = {
    "users",
    "products",
    "product_variants",
    "sales",
    "sale_items",
    "stock_movements",
    "suppliers",
    "purchase_orders",
    "purchase_order_items",
    "categories",
    "brands",
    "units",
    "app_settings",
    "owner_alerts",
    "audit_logs",
    "payment_credentials",
    "subscriptions",
    "invoices",
    "stock_counts",
    "stock_count_items",
    "daily_inventory_shifts",
    "daily_shift_snapshots",
    "delivery_orders",
    "riders",
    "orders",
    "order_items",
    "payments",
    "customers",
    "customer_addresses",
}
TABLE_NAME_RE = re.compile(
    r"(?:insert\s+into|update|delete\s+from|from)\s+([a-zA-Z_][a-zA-Z0-9_]*)",
    re.IGNORECASE,
)
NO_TENANT_SCOPE_RE = re.compile(r"/\*\s*no_tenant_scope\s*\*/", re.IGNORECASE)
TENANT_ID_RE = re.compile(r"\btenant_id\b", re.IGNORECASE)
WHERE_CLAUSE_RE = re.compile(r"(?i)\bwhere\b")
TAIL_CLAUSE_RE = re.compile(r"(?i)\b(order\s+by|group\s+by|limit)\b")
INSERT_COLS_RE = re.compile(r"insert\s+into\s+[a-zA-Z_][a-zA-Z0-9_]*\s*\(([^)]+)\)", re.IGNORECASE)


def inject_tenant_scope(sql: str, params: Any = None) -> tuple[str, Any]:
    if NO_TENANT_SCOPE_RE.search(sql):
        return NO_TENANT_SCOPE_RE.sub("", sql), params

    if not has_request_context():
        return sql, params

    if is_super_admin():
        return sql, params

    tenant_id = current_tenant_id()
    if tenant_id is None:
        return sql, params

    if TENANT_ID_RE.search(sql):
        return sql, params

    match = TABLE_NAME_RE.search(sql)
    if not match:
        return sql, params

    table = match.group(1).lower()
    if table not in SCOPED_TABLES:
        return sql, params

    trimmed = sql.strip()
    lower = trimmed.lower()
    if lower.startswith("insert into"):
        return _inject_tenant_into_insert(sql, params, tenant_id)

    return _inject_tenant_filter(sql, params, tenant_id)


def _inject_tenant_into_insert(sql: str, params: Any, tenant_id: int) -> tuple[str, Any]:
    match = INSERT_COLS_RE.search(sql)
    if not match:
        return sql, params

    columns = match.group(1)
    if TENANT_ID_RE.search(columns):
        return sql, params

    new_columns = f"{columns.strip()}, tenant_id"
    injected_sql = sql[: match.start(1)] + new_columns + sql[match.end(1) :]
    new_params = tuple(params or ()) + (tenant_id,)
    return injected_sql, new_params


def _inject_tenant_filter(sql: str, params: Any, tenant_id: int) -> tuple[str, Any]:
    if WHERE_CLAUSE_RE.search(sql):
        tail_match = TAIL_CLAUSE_RE.search(sql)
        if tail_match:
            before = sql[: tail_match.start()]
            tail = sql[tail_match.start() :]
        else:
            before = sql
            tail = ""
        # Space before tail so Postgres/SQLite never see "?ORDER" / "$1ORDER" when tail is "ORDER BY …".
        injected_sql = f"{before} AND tenant_id = ? {tail}"
    else:
        tail_match = TAIL_CLAUSE_RE.search(sql)
        if tail_match:
            before = sql[: tail_match.start()]
            tail = sql[tail_match.start() :]
        else:
            before = sql
            tail = ""
        injected_sql = f"{before} WHERE tenant_id = ? {tail}"
        # First placeholder is tenant_id; remaining ?s follow (e.g. LIMIT ?).
        new_params = (tenant_id,) + tuple(params or ())
        return injected_sql, new_params

    new_params = tuple(params or ()) + (tenant_id,)
    return injected_sql, new_params


class TenantAwareConnection:
    def __init__(self, connection: Any) -> None:
        self._connection = connection

    def __enter__(self) -> "TenantAwareConnection":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is None:
            self._connection.commit()
        else:
            self._connection.rollback()
        self._connection.close()

    def execute(self, sql: str, params: Any = None) -> Any:
        sql, params = inject_tenant_scope(sql, params)
        return self._connection.execute(sql, params or ())

    def executemany(self, sql: str, seq_of_params: Any) -> Any:
        if not has_request_context():
            return self._connection.executemany(sql, seq_of_params)

        if TENANT_ID_RE.search(sql):
            return self._connection.executemany(sql, seq_of_params)

        if sql.strip().lower().startswith("insert into"):
            tenant_id = current_tenant_id()
            if tenant_id is None or TENANT_ID_RE.search(sql):
                return self._connection.executemany(sql, seq_of_params)

            match = INSERT_COLS_RE.search(sql)
            if not match:
                return self._connection.executemany(sql, seq_of_params)

            columns = match.group(1)
            if TENANT_ID_RE.search(columns):
                return self._connection.executemany(sql, seq_of_params)

            new_columns = f"{columns.strip()}, tenant_id"
            injected_sql = sql[: match.start(1)] + new_columns + sql[match.end(1) :]
            new_params = [tuple(params) + (tenant_id,) for params in seq_of_params]
            return self._connection.executemany(injected_sql, new_params)

        return self._connection.executemany(sql, seq_of_params)

    def executescript(self, script: str) -> Any:
        return self._connection.executescript(script)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)
