"""Catalog of payment gateways (live + roadmap) and platform-level enable flags."""

from __future__ import annotations

import json
from typing import Any

from app.core.db import get_connection

PLATFORM_GATEWAYS_SETTING_KEY = "payment_gateways_platform"

# Slugs must match PaymentGateway.gateway_name() once implemented.
GATEWAY_CATALOG: tuple[dict[str, Any], ...] = (
    {
        "id": "paymongo",
        "label": "PayMongo (GCash, Maya, QRPH, cards)",
        "description": "Philippine payments aggregator — one integration for GCash, Maya, QRPH, and cards.",
        "implementation": "available",
    },
    {
        "id": "paypal",
        "label": "PayPal",
        "description": "Cards and PayPal balance; common for cross-border and some PH merchants.",
        "implementation": "available",
    },
    {
        "id": "gcash",
        "label": "GCash (direct)",
        "description": "Standalone GCash — covered today via PayMongo; kept for a future direct integration.",
        "implementation": "planned",
    },
    {
        "id": "paymaya",
        "label": "Maya (direct)",
        "description": "Standalone Maya — covered today via PayMongo; kept for a future direct integration.",
        "implementation": "planned",
    },
    {
        "id": "grabpay",
        "label": "GrabPay",
        "description": "Grab wallet checkout where supported.",
        "implementation": "planned",
    },
    {
        "id": "bank_transfer",
        "label": "Bank transfer / PESONet",
        "description": "Manual or batch settlement via local banks; invoice-led flows.",
        "implementation": "planned",
    },
    {
        "id": "cod",
        "label": "Cash on delivery",
        "description": "No online capture; useful for delivery and storefront orders.",
        "implementation": "planned",
    },
)


def catalog_ids() -> set[str]:
    return {str(entry["id"]) for entry in GATEWAY_CATALOG}


def default_platform_flags() -> dict[str, dict[str, bool]]:
    """Default: implemented ('available') gateways enabled; 'planned' ones off."""
    out: dict[str, dict[str, bool]] = {}
    for entry in GATEWAY_CATALOG:
        gid = str(entry["id"])
        is_live = entry.get("implementation") == "available"
        out[gid] = {"enabled": bool(is_live)}
    return out


def load_platform_flags_raw(connection: Any) -> dict[str, dict[str, Any]]:
    row = connection.execute(
        "SELECT value FROM app_settings WHERE tenant_id = 1 AND key = ?",
        (PLATFORM_GATEWAYS_SETTING_KEY,),
    ).fetchone()
    if row is None or not row.get("value"):
        return {}
    try:
        parsed = json.loads(str(row["value"]))
    except (json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {str(k): v for k, v in parsed.items() if isinstance(v, dict)}


def merge_platform_flags(connection: Any) -> dict[str, dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    defaults = default_platform_flags()
    stored = load_platform_flags_raw(connection)
    for gid in catalog_ids():
        base = dict(defaults.get(gid, {"enabled": False}))
        if gid in stored and isinstance(stored[gid], dict):
            if "enabled" in stored[gid]:
                base["enabled"] = bool(stored[gid]["enabled"])
        merged[gid] = base
    return merged


def gateway_enabled_for_platform(connection: Any, gateway_id: str) -> bool:
    flags = merge_platform_flags(connection)
    entry = flags.get(gateway_id)
    return bool(entry and entry.get("enabled"))


def catalog_rows_for_admin(connection: Any) -> list[dict[str, Any]]:
    flags = merge_platform_flags(connection)
    rows: list[dict[str, Any]] = []
    for entry in GATEWAY_CATALOG:
        gid = str(entry["id"])
        row = {**entry, "platform_enabled": bool(flags.get(gid, {}).get("enabled"))}
        rows.append(row)
    return rows
