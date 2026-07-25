"""Paths and defaults for the Flask app (shared by `flask_app` and `modules.web`)."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

from app.core.constants import ALLOWED_INVENTORY_REASONS, ALLOWED_PRODUCT_STATUSES

BASE_DIR = Path(__file__).resolve().parents[2]
load_dotenv(BASE_DIR / ".env")
INSTANCE_DIR = BASE_DIR / "instance"
DEFAULT_SQLITE_DATABASE = BASE_DIR / "pos.db"
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
DATABASE = Path(os.getenv("SQLITE_DATABASE_PATH", str(DEFAULT_SQLITE_DATABASE)))
BACKUP_DIR = Path(os.getenv("BACKUP_DIR", str(BASE_DIR / "backups")))
DATABASE_ENGINE = "postgres" if DATABASE_URL and not DATABASE_URL.startswith("sqlite") else "sqlite"
PUBLIC_LOGO = BASE_DIR / "static" / "public" / "logo.png"
FALLBACK_LOGO = "images/logo.png"
PRODUCT_IMAGES_DIR = Path(os.getenv("PRODUCT_IMAGES_DIR", str(BASE_DIR / "static" / "images" / "products")))
PLACEHOLDER_MAP = {
    "cakes": "images/products/placeholder-cake.svg",
    "pastries": "images/products/placeholder-pastry.svg",
    "pick-a pika": "images/products/placeholder-pika.svg",
    "beverages": "images/products/placeholder-beverage.svg",
}
DEFAULT_PLACEHOLDER = "images/products/placeholder-default.svg"
BUSINESS_NAME = "Veyron's Cakes and Pastries"
CURRENCY_CODE = "PHP"
# Legacy fallback only. Real VAT is per-tenant via app_settings — see app/core/tax.py
# (`vat_rate`, `vat_inclusive`, `vat_registered`).
VAT_RATE = 0.12
REAUTH_TTL_SECONDS = 600
APP_ENV = os.getenv("APP_ENV", "development").strip().lower()
IS_PRODUCTION = APP_ENV == "production"

DEFAULT_CATEGORIES = [
    "Cakes",
    "Pastries",
    "Pick-a Pika",
    "Beverages",
    "Upcoming Specials",
]
DEFAULT_BRANDS = [
    "Veyron's Signature",
    "Pick-a Pika Line",
    "Cafe Kitchen",
    "Seasonal Test Kitchen",
]
DEFAULT_UNITS = ["Slice", "Box", "Piece", "Cup", "Order", "Pack"]
DEFAULT_UNITS_DATA = [
    ("Kilogram", "kg", "weight", 1000),
    ("Gram", "g", "weight", 1),
    ("Pound", "lb", "weight", 453.592),
    ("Ounce", "oz", "weight", 28.3495),
    ("Liter", "L", "volume", 1000),
    ("Milliliter", "ml", "volume", 1),
    ("Cup", "cup", "volume", 240),
    ("Tablespoon", "tbsp", "volume", 15),
    ("Teaspoon", "tsp", "volume", 5),
    ("Piece", "pcs", "count", 1),
    ("Pack", "pack", "count", 1),
    ("Box", "box", "count", 1),
    ("Slice", "slice", "count", 1),
    ("Order", "order", "count", 1),
    ("Serving", "srv", "count", 1),
    ("Dozen", "doz", "count", 12),
    ("Tray", "tray", "count", 1),
]
ALLOWED_USER_ROLES = {"super_admin", "owner", "admin", "cashier"}
ALLOWED_SALE_STATUSES = {"completed", "voided", "refunded"}
DISCOUNT_PRESETS = {
    "none": 0.0,
    "senior": 0.20,
    "pwd": 0.20,
    "custom": None,
}
DEFAULT_APP_SETTINGS = {
    "onboarding_completed": "1",
    "auto_print_receipt": "0",
    "cash_drawer_enabled": "0",
    "printer_mode": "browser",
    "printer_bridge_url": "http://127.0.0.1:19191/print",
    "drawer_open_note": "Browser mode logs drawer opens but needs a local bridge for real ESC/POS drawer pulses.",
    "alert_low_stock_email": "1",
    "alert_void_refund_email": "1",
    "alert_variance_email": "1",
    "brand_primary_color": "#0f6a5d",
    "brand_accent_color": "#b54a2f",
    "brand_theme_mode": "warm",
    "brand_logo_path": "",
    # Philippine VAT. Retail prices are VAT-inclusive by default; set
    # vat_registered=0 for non-VAT (percentage-tax) merchants.
    "vat_rate": "0.12",
    "vat_inclusive": "1",
    "vat_registered": "1",
    # Loyalty: points earned per peso spent, and peso value per point on redemption.
    "loyalty_enabled": "0",
    "loyalty_earn_rate": "1",
    "loyalty_redeem_rate": "1",
}
DEFAULT_USERS = [
    {"full_name": "Super Admin", "username": "superadmin", "role": "super_admin", "pin": "superadmin123"},
    {"full_name": "Veyron Owner", "username": "owner", "role": "owner", "pin": "owner123"},
    {"full_name": "Veyron Admin", "username": "admin", "role": "admin", "pin": "admin123"},
    {"full_name": "Veyron Cashier", "username": "cashier", "role": "cashier", "pin": "cashier123"},
]

__all__ = [
    "ALLOWED_INVENTORY_REASONS",
    "ALLOWED_PRODUCT_STATUSES",
    "ALLOWED_SALE_STATUSES",
    "ALLOWED_USER_ROLES",
    "APP_ENV",
    "BACKUP_DIR",
    "BASE_DIR",
    "BUSINESS_NAME",
    "CURRENCY_CODE",
    "DATABASE",
    "DATABASE_ENGINE",
    "DATABASE_URL",
    "DEFAULT_APP_SETTINGS",
    "DEFAULT_BRANDS",
    "DEFAULT_CATEGORIES",
    "DEFAULT_PLACEHOLDER",
    "DEFAULT_SQLITE_DATABASE",
    "DEFAULT_UNITS",
    "DEFAULT_UNITS_DATA",
    "DEFAULT_USERS",
    "DISCOUNT_PRESETS",
    "FALLBACK_LOGO",
    "INSTANCE_DIR",
    "IS_PRODUCTION",
    "PLACEHOLDER_MAP",
    "PRODUCT_IMAGES_DIR",
    "PUBLIC_LOGO",
    "REAUTH_TTL_SECONDS",
    "VAT_RATE",
]
