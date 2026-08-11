#!/bin/bash
# Paste this entire script into Proxmox CT 102 shell (pct enter 102), or run:
#   bash /opt/veyron-pos/scripts/seed_via_console.sh
set -euo pipefail
cd /opt/veyron-pos

if docker compose exec -T app test -f scripts/seed_investor_demo.py 2>/dev/null; then
  echo "Using scripts/seed_investor_demo.py"
  docker compose exec -T app python scripts/seed_investor_demo.py --tenant-id 5 --plan growth
else
  echo "seed_investor_demo.py not found — running inline seed"
  docker compose exec -T app python - <<'PY'
from app.core.db import get_raw_connection

TENANT_ID = 5
PRODUCTS = [
    ("Pan de Sal", "BAKE-PDS", "Pastries", 8.0, 200),
    ("Ensaymada", "BAKE-ENS", "Pastries", 45.0, 80),
    ("Ube Cheese Pandesal", "BAKE-UBE", "Pastries", 55.0, 60),
    ("Spanish Bread", "BAKE-SPB", "Pastries", 35.0, 70),
    ("Hot Brew Coffee", "BAKE-COF", "Beverages", 65.0, 100),
    ("Iced Latte", "BAKE-LAT", "Beverages", 95.0, 100),
]

def ensure(conn, table, name):
    row = conn.execute(f"SELECT id FROM {table} WHERE tenant_id = ? AND name = ?", (TENANT_ID, name)).fetchone()
    if row:
        return int(row["id"])
    conn.execute(f"INSERT INTO {table} (tenant_id, name) VALUES (?, ?)", (TENANT_ID, name))
    return int(conn.execute(f"SELECT id FROM {table} WHERE tenant_id = ? AND name = ?", (TENANT_ID, name)).fetchone()["id"])

with get_raw_connection() as conn:
    t = conn.execute("SELECT id, name FROM tenants WHERE id = ?", (TENANT_ID,)).fetchone()
    if not t:
        raise SystemExit(f"Tenant {TENANT_ID} not found")
    conn.execute(
        """UPDATE tenants SET plan_name='growth', subscription_status='active',
           delivery_enabled=1, qr_ordering_override='', is_active=1 WHERE id=?""",
        (TENANT_ID,),
    )
    for key in ("qr_ordering_enabled", "onboarding_completed"):
        conn.execute(
            "INSERT INTO app_settings (tenant_id, key, value) VALUES (?, ?, '1') "
            "ON CONFLICT(tenant_id, key) DO UPDATE SET value='1'",
            (TENANT_ID, key),
        )
    unit = conn.execute("SELECT id FROM units WHERE tenant_id=? AND symbol='pcs'", (TENANT_ID,)).fetchone()
    if not unit:
        conn.execute(
            "INSERT INTO units (tenant_id, name, symbol, unit_type, base_factor) VALUES (?,?,?,?,1)",
            (TENANT_ID, "Piece", "pcs", "count"),
        )
    unit_id = int(conn.execute("SELECT id FROM units WHERE tenant_id=? AND symbol='pcs'", (TENANT_ID,)).fetchone()["id"])
    created = 0
    for pname, sku, cat, price, stock in PRODUCTS:
        ex = conn.execute("SELECT id FROM products WHERE tenant_id=? AND sku=?", (TENANT_ID, sku)).fetchone()
        if ex:
            conn.execute("UPDATE products SET is_public=1, status='active', price=?, stock=? WHERE id=?", (price, stock, int(ex["id"])))
            continue
        cid = ensure(conn, "categories", cat)
        bid = ensure(conn, "brands", "House Brand")
        conn.execute(
            """INSERT INTO products (tenant_id,name,sku,price,stock,reorder_level,cost,status,sort_order,is_public,category_id,brand_id,unit_id)
               VALUES (?,?,?,?,?,10,?,'active',?,1,?,?,?)""",
            (TENANT_ID, pname, sku, price, stock, round(price * 0.35, 2), created, cid, bid, unit_id),
        )
        created += 1
    n = conn.execute("SELECT COUNT(*) AS c FROM products WHERE tenant_id=? AND is_public=1 AND status='active'", (TENANT_ID,)).fetchone()["c"]
    print(f"Seeded {t['name']}: plan=growth, public_products={n}, new_skus={created}")
PY
fi

curl -fsS http://127.0.0.1:8000/order/5 | head -c 400 || true
echo
