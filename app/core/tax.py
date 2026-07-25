"""Philippine VAT computation.

Pure functions — no Flask, no globals — so the math is testable in isolation.

Per-tenant settings (app_settings):
    vat_rate        decimal rate, e.g. "0.12"   (default 12%)
    vat_inclusive   "1" if displayed prices already include VAT (PH retail norm)
    vat_registered  "0" for non-VAT merchants (percentage-tax); no VAT is charged

Philippine rules implemented:

* **VAT-inclusive pricing** is the retail norm: a ₱112 shelf price is ₱100 net
  + ₱12 VAT. VAT is *extracted*, not added.
* **Senior Citizen / PWD sales are VAT-EXEMPT.** The statutory computation strips
  VAT first, then applies the 20% discount to the VAT-exclusive amount:
      net      = gross / 1.12
      discount = net * 0.20
      due      = net - discount
  Charging VAT on a senior/PWD sale, or applying the 20% to the VAT-inclusive
  price, both produce the wrong amount due.
* **Non-VAT-registered merchants** charge no VAT at all.

Not in scope here (deferred): BIR Official Receipt numbering and X/Z readings.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

DEFAULT_VAT_RATE = 0.12
# Discount types that are VAT-exempt under Philippine law.
VAT_EXEMPT_DISCOUNT_TYPES = frozenset({"senior", "pwd"})

_SETTING_KEYS = ("vat_rate", "vat_inclusive", "vat_registered")


def _money(value: float) -> float:
    return round(float(value) + 0.0, 2)


@dataclass(frozen=True)
class VatConfig:
    rate: float = DEFAULT_VAT_RATE
    inclusive: bool = True
    registered: bool = True


@dataclass(frozen=True)
class SaleTotals:
    """All values are pesos, rounded to 2dp. `total` is the amount due."""

    subtotal: float
    discount_amount: float
    net_of_vat: float
    vat_amount: float
    vat_exempt_sales: float
    total: float
    vat_rate: float
    vat_inclusive: bool
    vat_exempt: bool

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _as_bool(value: Any, default: bool) -> bool:
    if value is None or str(value).strip() == "":
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def vat_config_from_settings(settings: dict[str, Any] | None) -> VatConfig:
    settings = settings or {}
    raw_rate = settings.get("vat_rate")
    try:
        rate = DEFAULT_VAT_RATE if raw_rate in (None, "") else float(raw_rate)
    except (TypeError, ValueError):
        rate = DEFAULT_VAT_RATE
    if rate < 0 or rate >= 1:
        # Guard against a percentage being entered (e.g. "12" instead of "0.12").
        rate = rate / 100 if 1 <= rate <= 100 else DEFAULT_VAT_RATE
    return VatConfig(
        rate=rate,
        inclusive=_as_bool(settings.get("vat_inclusive"), True),
        registered=_as_bool(settings.get("vat_registered"), True),
    )


def load_vat_config(connection: Any, tenant_id: int) -> VatConfig:
    """Read a tenant's VAT settings. Falls back to PH defaults when unset."""
    settings: dict[str, Any] = {}
    try:
        rows = connection.execute(
            "SELECT key, value FROM app_settings WHERE tenant_id = ? AND key IN (?, ?, ?)",
            (tenant_id, *_SETTING_KEYS),
        ).fetchall()
        for row in rows or []:
            settings[str(row["key"])] = row["value"]
    except Exception:
        # Missing table/columns must never block a sale; fall back to defaults.
        settings = {}
    return vat_config_from_settings(settings)


def compute_sale_totals(
    subtotal: float,
    discount_rate: float = 0.0,
    *,
    config: VatConfig | None = None,
    discount_type: str = "none",
) -> SaleTotals:
    """Compute discount, VAT, and amount due from a line-item subtotal.

    `subtotal` is the sum of line totals as priced in the catalog — VAT-inclusive
    when config.inclusive is True (the PH retail default).
    """
    cfg = config or VatConfig()
    subtotal = float(subtotal or 0.0)
    discount_rate = float(discount_rate or 0.0)

    # Two distinct cases, which must not be conflated:
    #  * customer_exempt  — senior/PWD buying from a VAT-registered merchant. The
    #    shelf price DOES contain VAT, so it is stripped before discounting.
    #  * not cfg.registered — a non-VAT merchant. Its prices never contained VAT,
    #    so there is nothing to strip; the price stands as-is.
    customer_exempt = str(discount_type).strip().lower() in VAT_EXEMPT_DISCOUNT_TYPES
    exempt = (not cfg.registered) or customer_exempt
    rate = cfg.rate

    if cfg.inclusive:
        if not cfg.registered:
            discount_amount = subtotal * discount_rate
            total = subtotal - discount_amount
            net_of_vat = total
            vat_amount = 0.0
        elif customer_exempt:
            # Strip VAT first, then discount the VAT-exclusive amount (statutory order).
            net_before_discount = subtotal / (1 + rate) if rate else subtotal
            discount_amount = net_before_discount * discount_rate
            total = net_before_discount - discount_amount
            net_of_vat = total
            vat_amount = 0.0
        else:
            discount_amount = subtotal * discount_rate
            gross_after_discount = subtotal - discount_amount
            net_of_vat = gross_after_discount / (1 + rate) if rate else gross_after_discount
            vat_amount = gross_after_discount - net_of_vat
            total = gross_after_discount
    else:
        discount_amount = subtotal * discount_rate
        net_of_vat = subtotal - discount_amount
        vat_amount = 0.0 if exempt else net_of_vat * rate
        total = net_of_vat + vat_amount

    return SaleTotals(
        subtotal=_money(subtotal),
        discount_amount=_money(discount_amount),
        net_of_vat=_money(net_of_vat),
        vat_amount=_money(vat_amount),
        vat_exempt_sales=_money(net_of_vat if exempt else 0.0),
        total=_money(total),
        vat_rate=0.0 if exempt else rate,
        vat_inclusive=cfg.inclusive,
        vat_exempt=exempt,
    )
