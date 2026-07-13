from pathlib import Path

p = Path(__file__).resolve().parents[1] / "app/core/flask_app.py"
text = p.read_text(encoding="utf-8")

replacements = [
    ("ensure_column(", "web_queries.ensure_column("),
    ("seed_lookup_table(", "web_queries.seed_lookup_table("),
    ("seed_units_data(", "web_queries.seed_units_data("),
    ("resequence_category_order(", "web_queries.resequence_category_order("),
    ("resequence_product_order(", "web_queries.resequence_product_order("),
    ("log_audit(", "web_queries.log_audit("),
    ("get_setting(", "web_queries.get_setting("),
    ("fetch_app_settings(", "web_queries.fetch_app_settings("),
    ("fetch_pos_products(", "web_queries.fetch_pos_products("),
    ("fetch_variants_by_product(", "web_queries.fetch_variants_by_product("),
    ("build_pos_categories(", "web_queries.build_pos_categories("),
    ("fetch_upcoming_products(", "web_queries.fetch_upcoming_products("),
    ("fetch_quick_pick_products(", "web_queries.fetch_quick_pick_products("),
    ("fetch_open_cash_shift(", "web_queries.fetch_open_cash_shift("),
    ("fetch_recent_cash_shifts(", "web_queries.fetch_recent_cash_shifts("),
    ("redirect_to_admin(", "web_queries.redirect_to_admin("),
    ("redirect_to_inventory(", "web_queries.redirect_to_inventory("),
    ("move_category_to_position(", "web_queries.move_category_to_position("),
    ("fetch_admin_context(", "web_queries.fetch_admin_context("),
    ("fetch_inventory_context(", "web_queries.fetch_inventory_context("),
    ("fetch_owner_context(", "web_queries.fetch_owner_context("),
    ("maybe_create_low_stock_alert(", "web_queries.maybe_create_low_stock_alert("),
    ("maybe_create_adjustment_alert(", "web_queries.maybe_create_adjustment_alert("),
    ("build_sale_stock_return(", "web_queries.build_sale_stock_return("),
    ("create_owner_alert(", "web_queries.create_owner_alert("),
    ("copy_database(", "web_queries.copy_database("),
]

for old, new in replacements:
    text = text.replace(old, new)

# Jinja global — avoid double prefix
text = text.replace(
    "lambda img, cat=None: web_queries.get_product_image_url(img, cat)",
    "lambda img, cat=None: web_queries.get_product_image_url(img, cat)",
)
text = text.replace(
    "lambda img, cat=None: get_product_image_url(img, cat)",
    "lambda img, cat=None: web_queries.get_product_image_url(img, cat)",
)

p.write_text(text, encoding="utf-8")
print("done")
