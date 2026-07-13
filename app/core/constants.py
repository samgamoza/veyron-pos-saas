from __future__ import annotations

ALLOWED_PRODUCT_STATUSES = {
    "active",
    "upcoming",
    "inactive",
    "disabled",
    "out_of_stock",
}

ALLOWED_INVENTORY_REASONS = {
    "opening_balance",
    "restock",
    "manual_count",
    "damaged",
    "wastage",
    "sale",
    "purchase_receive",
    "void",
    "refund",
}

ALLOWED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".pdf"}
