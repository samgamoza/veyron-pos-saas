"""Postgres Row-Level Security isolation tests.

Skipped automatically unless DATABASE_URL points at Postgres, so the suite stays
green on SQLite dev machines. To run:

    docker compose up -d db
    DATABASE_URL=postgresql://veyron_app:...@localhost:5432/veyron \
        python -m pytest tests/test_rls_postgres.py -v

These assert the DATABASE enforces isolation independently of the application-layer
query scoper — i.e. a badly-scoped query still cannot cross tenants.
"""

from __future__ import annotations

import os
import unittest

DATABASE_URL = os.getenv("DATABASE_URL", "")
IS_POSTGRES = bool(DATABASE_URL) and not DATABASE_URL.startswith("sqlite")

pytestmark = []


def _scope(cur, tenant_id=None, bypass=False):
    cur.execute("SELECT set_config('app.bypass_rls', %s, false)", ("on" if bypass else "off",))
    cur.execute("SELECT set_config('app.tenant_id', %s, false)", ("" if tenant_id is None else str(tenant_id),))


@unittest.skipUnless(IS_POSTGRES, "RLS tests require a Postgres DATABASE_URL")
class TestPostgresRLS(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        import psycopg
        from psycopg.rows import dict_row

        cls.conn = psycopg.connect(DATABASE_URL, row_factory=dict_row)
        cur = cls.conn.cursor()
        _scope(cur, bypass=True)
        cur.execute("INSERT INTO tenants (id, name, is_active) VALUES (901,'RLS T1',1) ON CONFLICT (id) DO NOTHING")
        cur.execute("INSERT INTO tenants (id, name, is_active) VALUES (902,'RLS T2',1) ON CONFLICT (id) DO NOTHING")
        for tid in (901, 902):
            cur.execute(
                """INSERT INTO products (tenant_id,name,sku,price,stock,reorder_level,cost,status,sort_order,is_public)
                   VALUES (%s,%s,%s,10,5,1,5,'active',0,1) ON CONFLICT DO NOTHING""",
                (tid, f"RLS product {tid}", f"RLS-SKU-{tid}"),
            )
        cls.conn.commit()

    @classmethod
    def tearDownClass(cls) -> None:
        cur = cls.conn.cursor()
        _scope(cur, bypass=True)
        cur.execute("DELETE FROM products WHERE tenant_id IN (901,902)")
        cur.execute("DELETE FROM tenants WHERE id IN (901,902)")
        cls.conn.commit()
        cls.conn.close()

    def test_rls_is_enabled_and_forced(self) -> None:
        cur = self.conn.cursor()
        cur.execute("SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname='products'")
        row = cur.fetchone()
        self.assertTrue(row["relrowsecurity"], "RLS not enabled on products")
        self.assertTrue(row["relforcerowsecurity"], "FORCE RLS not set — table owner would bypass policies")

    def test_app_role_is_not_superuser(self) -> None:
        cur = self.conn.cursor()
        cur.execute("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
        row = cur.fetchone()
        self.assertFalse(row["rolsuper"], "App role is a superuser — RLS is silently bypassed")
        self.assertFalse(row["rolbypassrls"], "App role has BYPASSRLS — isolation disabled")

    def test_tenant_sees_only_own_rows(self) -> None:
        cur = self.conn.cursor()
        for tid in (901, 902):
            _scope(cur, tid)
            cur.execute("SELECT tenant_id FROM products WHERE sku LIKE 'RLS-SKU-%'")
            rows = cur.fetchall()
            self.assertTrue(rows)
            self.assertTrue(all(r["tenant_id"] == tid for r in rows))

    def test_no_tenant_context_fails_closed(self) -> None:
        cur = self.conn.cursor()
        _scope(cur, None)
        cur.execute("SELECT count(*) AS c FROM products WHERE sku LIKE 'RLS-SKU-%'")
        self.assertEqual(cur.fetchone()["c"], 0, "No tenant context must return zero rows, not all rows")

    def test_cannot_insert_for_another_tenant(self) -> None:
        import psycopg

        cur = self.conn.cursor()
        _scope(cur, 901)
        with self.assertRaises(psycopg.errors.Error):
            cur.execute(
                """INSERT INTO products (tenant_id,name,sku,price,stock,reorder_level,cost,status,sort_order,is_public)
                   VALUES (902,'smuggled','RLS-EVIL',1,1,1,1,'active',0,1)"""
            )
        self.conn.rollback()

    def test_cross_tenant_update_and_delete_affect_nothing(self) -> None:
        cur = self.conn.cursor()
        _scope(cur, 901)
        cur.execute("UPDATE products SET name='HACKED' WHERE sku='RLS-SKU-902'")
        self.assertEqual(cur.rowcount, 0)
        cur.execute("DELETE FROM products WHERE sku='RLS-SKU-902'")
        self.assertEqual(cur.rowcount, 0)
        self.conn.commit()

        _scope(cur, 902)
        cur.execute("SELECT name FROM products WHERE sku='RLS-SKU-902'")
        row = cur.fetchone()
        self.assertIsNotNone(row)
        self.assertNotEqual(row["name"], "HACKED")


if __name__ == "__main__":
    unittest.main()
