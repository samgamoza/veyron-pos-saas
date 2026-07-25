"""Philippine VAT computation tests.

Uses round pesos so expected values are checkable by hand:
a ₱112.00 VAT-inclusive price is exactly ₱100.00 net + ₱12.00 VAT.
"""

from __future__ import annotations

import unittest

from app.core.tax import (
    DEFAULT_VAT_RATE,
    VatConfig,
    compute_sale_totals,
    vat_config_from_settings,
)

INCLUSIVE = VatConfig(rate=0.12, inclusive=True, registered=True)
EXCLUSIVE = VatConfig(rate=0.12, inclusive=False, registered=True)
NON_VAT = VatConfig(rate=0.12, inclusive=True, registered=False)


class VatInclusiveTest(unittest.TestCase):
    def test_extracts_vat_from_inclusive_price(self) -> None:
        t = compute_sale_totals(112.00, config=INCLUSIVE)
        self.assertEqual(t.net_of_vat, 100.00)
        self.assertEqual(t.vat_amount, 12.00)
        self.assertEqual(t.total, 112.00, "VAT-inclusive total must not grow")
        self.assertFalse(t.vat_exempt)

    def test_components_reconcile_to_total(self) -> None:
        t = compute_sale_totals(1234.56, config=INCLUSIVE)
        self.assertAlmostEqual(t.net_of_vat + t.vat_amount, t.total, places=2)

    def test_ordinary_discount_still_vatable(self) -> None:
        # 10% off ₱112 -> ₱100.80 gross, which is ₱90.00 net + ₱10.80 VAT.
        t = compute_sale_totals(112.00, 0.10, config=INCLUSIVE, discount_type="custom")
        self.assertEqual(t.discount_amount, 11.20)
        self.assertEqual(t.total, 100.80)
        self.assertEqual(t.net_of_vat, 90.00)
        self.assertEqual(t.vat_amount, 10.80)
        self.assertFalse(t.vat_exempt)


class VatExclusiveTest(unittest.TestCase):
    def test_adds_vat_on_top(self) -> None:
        t = compute_sale_totals(100.00, config=EXCLUSIVE)
        self.assertEqual(t.net_of_vat, 100.00)
        self.assertEqual(t.vat_amount, 12.00)
        self.assertEqual(t.total, 112.00)


class SeniorPwdExemptionTest(unittest.TestCase):
    """Statutory PH computation: strip VAT, then 20% off the VAT-exclusive amount."""

    def test_senior_is_vat_exempt_and_discounts_net_amount(self) -> None:
        t = compute_sale_totals(112.00, 0.20, config=INCLUSIVE, discount_type="senior")
        self.assertTrue(t.vat_exempt)
        self.assertEqual(t.vat_amount, 0.00, "Senior sales must not be charged VAT")
        # net 100.00, less 20% = 80.00 due
        self.assertEqual(t.discount_amount, 20.00)
        self.assertEqual(t.total, 80.00)
        self.assertEqual(t.vat_exempt_sales, 80.00)

    def test_pwd_matches_senior_treatment(self) -> None:
        senior = compute_sale_totals(112.00, 0.20, config=INCLUSIVE, discount_type="senior")
        pwd = compute_sale_totals(112.00, 0.20, config=INCLUSIVE, discount_type="pwd")
        self.assertEqual(senior.total, pwd.total)
        self.assertEqual(pwd.vat_amount, 0.00)

    def test_senior_beats_naive_calculation(self) -> None:
        """Guards the bug this replaces: 20% off the VAT-inclusive price is wrong."""
        naive_total = round(112.00 * 0.80, 2)  # 89.60 — the incorrect figure
        correct = compute_sale_totals(112.00, 0.20, config=INCLUSIVE, discount_type="senior")
        self.assertNotEqual(correct.total, naive_total)
        self.assertEqual(correct.total, 80.00)


class NonVatRegisteredTest(unittest.TestCase):
    def test_no_vat_charged(self) -> None:
        t = compute_sale_totals(112.00, config=NON_VAT)
        self.assertTrue(t.vat_exempt)
        self.assertEqual(t.vat_amount, 0.00)
        self.assertEqual(t.total, 112.00)


class ConfigParsingTest(unittest.TestCase):
    def test_defaults_when_unset(self) -> None:
        cfg = vat_config_from_settings({})
        self.assertEqual(cfg.rate, DEFAULT_VAT_RATE)
        self.assertTrue(cfg.inclusive)
        self.assertTrue(cfg.registered)

    def test_percentage_entered_by_mistake_is_normalised(self) -> None:
        # "12" means 12%, not 1200%.
        self.assertAlmostEqual(vat_config_from_settings({"vat_rate": "12"}).rate, 0.12)

    def test_garbage_rate_falls_back(self) -> None:
        self.assertEqual(vat_config_from_settings({"vat_rate": "abc"}).rate, DEFAULT_VAT_RATE)

    def test_flags_parse(self) -> None:
        cfg = vat_config_from_settings({"vat_inclusive": "0", "vat_registered": "0"})
        self.assertFalse(cfg.inclusive)
        self.assertFalse(cfg.registered)

    def test_zero_rate_is_safe(self) -> None:
        t = compute_sale_totals(100.00, config=VatConfig(rate=0.0, inclusive=True, registered=True))
        self.assertEqual(t.vat_amount, 0.00)
        self.assertEqual(t.total, 100.00)


if __name__ == "__main__":
    unittest.main()
