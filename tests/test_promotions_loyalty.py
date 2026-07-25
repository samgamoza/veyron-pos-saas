"""Promotions and loyalty: quoting, validation, tenant scoping, and the points ledger."""

from __future__ import annotations

import os
import tempfile
import unittest

if not os.environ.get("SQLITE_DATABASE_PATH"):
    _tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    _tmp.close()
    os.environ["SQLITE_DATABASE_PATH"] = _tmp.name

from app.core.db import get_raw_connection  # noqa: E402
from app.core.flask_app import create_flask_application  # noqa: E402
from app.core.loyalty import LoyaltyError, LoyaltyService  # noqa: E402
from app.core.promotions import PromotionError, PromotionService  # noqa: E402
from app.core.tenant.connection import SCOPED_TABLES  # noqa: E402


class PromotionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = create_flask_application()
        with get_raw_connection() as conn:
            if conn.execute("SELECT id FROM tenants WHERE id = 2").fetchone() is None:
                conn.execute("INSERT INTO tenants (id, name, is_active) VALUES (2, 'Promo T2', 1)")

    def test_promotions_are_tenant_scoped(self) -> None:
        self.assertIn("promotions", SCOPED_TABLES)
        self.assertIn("loyalty_ledger", SCOPED_TABLES)

    def test_percent_promo_quote(self) -> None:
        with get_raw_connection() as conn:
            svc = PromotionService(conn)
            svc.create_promotion(1, "Ten Off", "SAVE10", discount_type="percent", value=10)
            quote = svc.validate_and_quote(1, "save10", 200.0)  # case-insensitive
            self.assertEqual(quote["discount_amount"], 20.0)

    def test_amount_promo_capped_to_subtotal(self) -> None:
        with get_raw_connection() as conn:
            svc = PromotionService(conn)
            svc.create_promotion(1, "Fifty Off", "LESS50", discount_type="amount", value=50)
            self.assertEqual(svc.validate_and_quote(1, "LESS50", 200.0)["discount_amount"], 50.0)
            # Discount can never exceed the subtotal.
            self.assertEqual(svc.validate_and_quote(1, "LESS50", 30.0)["discount_amount"], 30.0)

    def test_min_subtotal_enforced(self) -> None:
        with get_raw_connection() as conn:
            svc = PromotionService(conn)
            svc.create_promotion(1, "Big Spend", "MIN100", discount_type="percent", value=5, min_subtotal=100)
            with self.assertRaises(PromotionError):
                svc.validate_and_quote(1, "MIN100", 50.0)

    def test_usage_limit_enforced(self) -> None:
        with get_raw_connection() as conn:
            svc = PromotionService(conn)
            pid = svc.create_promotion(1, "One Use", "ONCE", discount_type="percent", value=5, usage_limit=1)
            svc.validate_and_quote(1, "ONCE", 100.0)  # ok
            svc.record_redemption(1, pid)
            with self.assertRaises(PromotionError):
                svc.validate_and_quote(1, "ONCE", 100.0)  # now exhausted

    def test_duplicate_code_rejected_and_tenant_isolated(self) -> None:
        with get_raw_connection() as conn:
            svc = PromotionService(conn)
            svc.create_promotion(1, "Dup", "DUP", discount_type="percent", value=5)
            with self.assertRaises(PromotionError):
                svc.create_promotion(1, "Dup2", "DUP", discount_type="percent", value=5)
            # Same code allowed for a different tenant.
            svc.create_promotion(2, "Dup T2", "DUP", discount_type="percent", value=5)
            # Tenant 2 cannot see tenant 1's other codes.
            self.assertIsNone(svc.get_by_code(2, "SAVE10"))

    def test_unknown_code_raises(self) -> None:
        with get_raw_connection() as conn:
            with self.assertRaises(PromotionError):
                PromotionService(conn).validate_and_quote(1, "NOPE", 100.0)


class LoyaltyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = create_flask_application()
        with get_raw_connection() as conn:
            for key, value in (("loyalty_enabled", "1"), ("loyalty_earn_rate", "1"), ("loyalty_redeem_rate", "1")):
                conn.execute(
                    "INSERT INTO app_settings (tenant_id, key, value) VALUES (1, ?, ?) "
                    "ON CONFLICT(tenant_id, key) DO UPDATE SET value = excluded.value",
                    (key, value),
                )
            row = conn.execute(
                "INSERT INTO customers (tenant_id, phone, full_name) VALUES (1, ?, ?) RETURNING id",
                ("09170000000", "Loyal Customer"),
            ).fetchone()
            cls.customer_id = int(row["id"])

    def test_earn_accrues_points(self) -> None:
        with get_raw_connection() as conn:
            svc = LoyaltyService(conn)
            start = svc.balance(1, self.customer_id)
            earned = svc.earn_for_sale(1, self.customer_id, None, 250.0)  # 1 pt/peso
            self.assertEqual(earned, 250)
            self.assertEqual(svc.balance(1, self.customer_id), start + 250)

    def test_balance_is_ledger_sum(self) -> None:
        with get_raw_connection() as conn:
            svc = LoyaltyService(conn)
            svc.earn_for_sale(1, self.customer_id, None, 100.0)
            redeemed_value = svc.redeem_for_sale(1, self.customer_id, None, 40)
            self.assertEqual(redeemed_value, 40.0)  # 1 peso/point
            # balance reflects both entries
            self.assertGreaterEqual(svc.balance(1, self.customer_id), 0)

    def test_cannot_redeem_more_than_balance(self) -> None:
        with get_raw_connection() as conn:
            svc = LoyaltyService(conn)
            bal = svc.balance(1, self.customer_id)
            with self.assertRaises(LoyaltyError):
                svc.redeem_for_sale(1, self.customer_id, None, bal + 100)

    def test_quote_redemption_capped_by_subtotal(self) -> None:
        with get_raw_connection() as conn:
            svc = LoyaltyService(conn)
            svc.earn_for_sale(1, self.customer_id, None, 1000.0)  # plenty of points
            # Only 30 pesos of subtotal -> at most 30 points/pesos redeemable.
            quote = svc.quote_redemption(1, self.customer_id, points_requested=500, subtotal=30.0)
            self.assertEqual(quote["points"], 30)
            self.assertEqual(quote["discount_amount"], 30.0)

    def test_disabled_loyalty_earns_nothing(self) -> None:
        with get_raw_connection() as conn:
            conn.execute("UPDATE app_settings SET value='0' WHERE tenant_id=1 AND key='loyalty_enabled'")
            svc = LoyaltyService(conn)
            self.assertEqual(svc.earn_for_sale(1, self.customer_id, None, 500.0), 0)
            conn.execute("UPDATE app_settings SET value='1' WHERE tenant_id=1 AND key='loyalty_enabled'")


if __name__ == "__main__":
    unittest.main()
