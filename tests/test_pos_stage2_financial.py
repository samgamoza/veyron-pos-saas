"""
POS Stage 2: payments lifecycle, split tender, idempotency, refund rows, concurrency.

Requires env SQLITE_DATABASE_PATH set before importing app (same pattern as test_etown_order).

    python -m unittest tests.test_pos_stage2_financial -v
"""

from __future__ import annotations

import os
import tempfile
import unittest

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["SQLITE_DATABASE_PATH"] = _tmp.name

from flask import g, session  # noqa: E402

from app.core.db import get_connection, get_raw_connection  # noqa: E402
from app.core.flask_app import create_flask_application  # noqa: E402
from app.core.pos_finalize import finalize_pos_sale  # noqa: E402
from app.core.pos_payment_model import assert_payment_lines_match_total, normalize_payment_lines  # noqa: E402
from app.core.pos_refund import apply_sale_financial_reversal  # noqa: E402


def _tenant_ctx(app, *, user_id: int, tenant_id: int = 1):
    ctx = app.test_request_context()
    ctx.push()
    session["user_id"] = user_id
    session["tenant_id"] = tenant_id
    g.tenant_id = tenant_id
    return ctx


class TestPosStage2Financial(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = create_flask_application()
        cls.app.config["TESTING"] = True
        with get_raw_connection() as conn:
            conn.execute(
                """
                INSERT INTO products (tenant_id, name, sku, price, stock, reorder_level, cost, status, sort_order)
                VALUES (1, 'Stage2 Widget', 'STG2-1', 10.0, 5, 5, 0, 'active', 0)
                """
            )
            row = conn.execute(
                "SELECT id FROM products WHERE sku = 'STG2-1' AND tenant_id = 1"
            ).fetchone()
            cls.product_id = int(row["id"])
            u = conn.execute(
                "SELECT id FROM users WHERE username = 'cashier' AND tenant_id = 1"
            ).fetchone()
            cls.cashier_id = int(u["id"])

    def _base_cart(self, qty: int = 1) -> list[dict]:
        return [
            {
                "id": self.product_id,
                "variant_id": None,
                "name": "Stage2 Widget",
                "sku": "STG2-1",
                "quantity": qty,
                "unit_price": 10.0,
                "line_total": round(10.0 * qty, 2),
            }
        ]

    def test_payment_totals_consistency_split_ok(self) -> None:
        lines = normalize_payment_lines(
            [{"amount": 6.0, "method": "Cash"}, {"amount": 4.0, "method": "Card"}],
            sale_total=10.0,
            default_method="Cash",
        )
        assert_payment_lines_match_total(lines, 10.0)

    def test_payment_totals_mismatch_raises(self) -> None:
        lines = normalize_payment_lines(
            [{"amount": 5.0, "method": "Cash"}, {"amount": 4.0, "method": "Card"}],
            sale_total=10.0,
            default_method="Cash",
        )
        with self.assertRaises(ValueError):
            assert_payment_lines_match_total(lines, 10.0)

    def test_idempotency_replay_no_double_payments(self) -> None:
        ctx = _tenant_ctx(self.app, user_id=self.cashier_id)
        try:
            with get_raw_connection() as conn:
                conn.execute("UPDATE products SET stock = 10 WHERE id = ?", (self.product_id,))
            with get_connection() as c:
                sid1, cr1 = finalize_pos_sale(
                    c,
                    tenant_id=1,
                    cashier_user_id=self.cashier_id,
                    cart=self._base_cart(1),
                    subtotal=10.0,
                    discount_type="none",
                    discount_rate=0.0,
                    discount_amount=0.0,
                    discount_note="",
                    service_reference="",
                    tax=0.0,
                    total=10.0,
                    payment_method="Cash",
                    cash_shift_id=None,
                    idempotency_key="idem-stage2-a",
                )
            self.assertTrue(cr1)
            with get_connection() as c:
                sid2, cr2 = finalize_pos_sale(
                    c,
                    tenant_id=1,
                    cashier_user_id=self.cashier_id,
                    cart=self._base_cart(1),
                    subtotal=10.0,
                    discount_type="none",
                    discount_rate=0.0,
                    discount_amount=0.0,
                    discount_note="",
                    service_reference="",
                    tax=0.0,
                    total=10.0,
                    payment_method="Cash",
                    cash_shift_id=None,
                    idempotency_key="idem-stage2-a",
                )
            self.assertFalse(cr2)
            self.assertEqual(sid1, sid2)
            with get_raw_connection() as conn:
                pc = conn.execute(
                    "SELECT COUNT(*) AS c FROM payments WHERE sale_id = ? AND tenant_id = 1",
                    (sid1,),
                ).fetchone()
                self.assertEqual(int(pc["c"]), 1)
        finally:
            ctx.pop()

    def test_refund_creates_negative_linked_payment(self) -> None:
        ctx = _tenant_ctx(self.app, user_id=self.cashier_id)
        try:
            with get_raw_connection() as conn:
                conn.execute("UPDATE products SET stock = 10 WHERE id = ?", (self.product_id,))
            with get_connection() as c:
                sid, _ = finalize_pos_sale(
                    c,
                    tenant_id=1,
                    cashier_user_id=self.cashier_id,
                    cart=self._base_cart(1),
                    subtotal=10.0,
                    discount_type="none",
                    discount_rate=0.0,
                    discount_amount=0.0,
                    discount_note="",
                    service_reference="",
                    tax=0.0,
                    total=10.0,
                    payment_method="Cash",
                    cash_shift_id=None,
                    idempotency_key=None,
                    payment_lines=[
                        {"amount": 10.0, "method": "Cash", "status": "completed", "reference": ""},
                    ],
                )
            with get_connection() as c:
                apply_sale_financial_reversal(c, 1, sid, "refund-test")
            with get_raw_connection() as conn:
                net = conn.execute(
                    "SELECT COALESCE(SUM(amount), 0) AS s FROM payments WHERE sale_id = ?",
                    (sid,),
                ).fetchone()
                self.assertAlmostEqual(float(net["s"]), 0.0, places=2)
                rev = conn.execute(
                    "SELECT amount, reverses_payment_id, status FROM payments WHERE sale_id = ? AND amount < 0",
                    (sid,),
                ).fetchone()
                self.assertIsNotNone(rev)
                self.assertLess(float(rev["amount"]), 0)
                self.assertIsNotNone(rev["reverses_payment_id"])
        finally:
            ctx.pop()

    def test_oversell_blocked_on_second_checkout(self) -> None:
        """With stock=1, first sale succeeds; second finalize aborts (conditional stock UPDATE)."""
        ctx = _tenant_ctx(self.app, user_id=self.cashier_id)
        try:
            with get_raw_connection() as conn:
                conn.execute("UPDATE products SET stock = 1 WHERE id = ?", (self.product_id,))

            with get_connection() as c:
                finalize_pos_sale(
                    c,
                    tenant_id=1,
                    cashier_user_id=self.cashier_id,
                    cart=self._base_cart(1),
                    subtotal=10.0,
                    discount_type="none",
                    discount_rate=0.0,
                    discount_amount=0.0,
                    discount_note="",
                    service_reference="",
                    tax=0.0,
                    total=10.0,
                    payment_method="Cash",
                    cash_shift_id=None,
                    idempotency_key=None,
                )
            with get_connection() as c:
                with self.assertRaises(ValueError):
                    finalize_pos_sale(
                        c,
                        tenant_id=1,
                        cashier_user_id=self.cashier_id,
                        cart=self._base_cart(1),
                        subtotal=10.0,
                        discount_type="none",
                        discount_rate=0.0,
                        discount_amount=0.0,
                        discount_note="",
                        service_reference="",
                        tax=0.0,
                        total=10.0,
                        payment_method="Cash",
                        cash_shift_id=None,
                        idempotency_key=None,
                    )
            with get_raw_connection() as conn:
                st = conn.execute(
                    "SELECT stock FROM products WHERE id = ?", (self.product_id,)
                ).fetchone()
                self.assertEqual(int(st["stock"]), 0)
        finally:
            ctx.pop()


if __name__ == "__main__":
    unittest.main()
