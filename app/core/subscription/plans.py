"""Platform subscription catalog — lowest competitive PH market positioning."""

from __future__ import annotations

import json
from typing import Any

# Prices sit below common PH POS floors (Nextar ₱500, Pinoy ₱990, UTAK ₱1,500, StoreHub ~₱1,874+).
PLATFORM_PLANS: tuple[tuple[str, float, dict[str, Any]], ...] = (
    (
        "demo",
        0.0,
        {
            "headline": "Explore the product",
            "bullets": [
                "Limited products & completed sales per month",
                "Full POS, inventory & storefront UI",
                "Upgrade anytime — your data stays put",
            ],
        },
    ),
    (
        "starter",
        399.0,
        {
            "headline": "Single store · unlimited users",
            "compare_note": "Below typical PH cloud POS from ₱990/mo",
            "badge": "Lowest paid",
            "bullets": [
                "1 location · unlimited staff & devices",
                "POS, inventory, owner reports & backups",
                "No per-user fees · pay monthly",
                "Bank transfer, GCash/PayMaya, or invoice",
            ],
            "pos": True,
            "inventory": True,
        },
    ),
    (
        "growth",
        899.0,
        {
            "headline": "Growing teams & richer operations",
            "compare_note": "Under UTAK & Pinoy POS entry tiers (₱990–₱1,500/mo)",
            "badge": "Most popular",
            "bullets": [
                "Everything in Starter",
                "Advanced reporting & team workflows",
                "Inventory reports & recipe pricing wizard",
                "Online storefront & QR ordering",
                "Email support with faster response",
            ],
            "pos": True,
            "inventory": True,
            "reports": True,
            "pricing_wizard": True,
            "inventory_reports": True,
            "qr_ordering": True,
        },
    ),
    (
        "enterprise",
        1399.0,
        {
            "headline": "Multi-outlet & priority support",
            "compare_note": "First branch included · +₱599/mo per extra branch",
            "bullets": [
                "Everything in Growth",
                "Multi-location controls (roadmap-aligned)",
                "Guided onboarding & data migration help",
                "Integration & API priority",
                "Additional branches: ₱599/mo each",
            ],
            "pos": True,
            "inventory": True,
            "multi_branch": True,
            "extra_branch_price": 599,
            "qr_ordering": True,
        },
    ),
)


def sync_platform_plans(connection: Any) -> None:
    """Insert missing catalog plans and refresh canonical price/features by name."""
    for name, price, features in PLATFORM_PLANS:
        row = connection.execute(
            "SELECT id FROM plans WHERE lower(name) = lower(?)",
            (name,),
        ).fetchone()
        payload = json.dumps(features)
        if row is None:
            connection.execute(
                "INSERT INTO plans (name, price, features, is_active) VALUES (?, ?, ?, 1)",
                (name, price, payload),
            )
        else:
            connection.execute(
                """
                UPDATE plans
                SET price = ?, features = ?, is_active = 1
                WHERE id = ?
                """,
                (price, payload, row["id"]),
            )
