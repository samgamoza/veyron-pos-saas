"""
Tenant POS operating profiles — super admin assigns one per tenant.

``maturity``:
  - current_fit: matches what Veyron POS does well today
  - partial: usable with gaps; roadmap items matter
  - roadmap: table/KDS/slip flows not built yet; label tenant expectations
"""

from __future__ import annotations

from typing import Any, Iterator

DEFAULT_POS_PROFILE_ID = "cafe_bakery"

# Ordered for dropdowns and docs (your three anchors first, then common PH adjacencies).
PROFILES: dict[str, dict[str, Any]] = {
    "cafe_bakery": {
        "label": "Café · milk tea · bakery · cake",
        "summary": (
            "Quick-service drinks and baked goods — moderate SKU count, variants for sizes "
            "and add-ons, simple counter flow."
        ),
        "maturity": "current_fit",
        "typical_operators": "Coffee shops, milk tea, cake studios, small bakeries, dessert bars.",
        "veyron_today": "Grid/tile POS, variants, VAT & discounts, scanning by SKU, optional customer screen.",
        "roadmap_hooks": "Kitchen display, modifiers matrix, loyalty cups, delivery handoff.",
    },
    "retail_grocery": {
        "label": "Retail · grocery · convenience · high SKU",
        "summary": "High-volume scanning, large catalogs, dual-monitor checkout, fast line throughput.",
        "maturity": "partial",
        "typical_operators": "Minimarts, groceries, convenience stores, warehouse clubs (front counter).",
        "veyron_today": "Barcode/QR wedge scan, catalog-wide SKU match, customer-facing order mirror, inventory.",
        "roadmap_hooks": "Weighed PLU scales, mix & match promos, shelf labels, offline queue mode.",
    },
    "restaurant_table_service": {
        "label": "Restaurant · table service · QSR-style slips",
        "summary": (
            "Jollibee-style dine-in: tables, order slips, kitchen routing, bill-out, split payments, receipts."
        ),
        "maturity": "roadmap",
        "typical_operators": "Full-service restaurants, fast-casual with tables, food courts with table numbers.",
        "veyron_today": (
            "Restaurant checkout lane: optional table / order reference on each sale (prints on receipt). "
            "No floor plan, open tickets, or KDS yet — same register shell as other profiles."
        ),
        "roadmap_hooks": "Floor plan, open tickets per table, fire/hold, KDS, service charge, split bill, z-read per shift.",
    },
    "pharmacy_health": {
        "label": "Pharmacy · clinic counter",
        "summary": "Regulated items, batch/expiry tracking, prescription flags, audit-friendly receipts.",
        "maturity": "roadmap",
        "typical_operators": "Drugstores, clinic pharmacies, veterinary supply counters.",
        "veyron_today": "Standard product + receipt flow; compliance fields on tenant (BIR) only.",
        "roadmap_hooks": "RX numbers, expiry lots, SRP warnings, controlled-substance logs, insurer formats.",
    },
    "salon_spa": {
        "label": "Salon · spa · wellness",
        "summary": "Appointments, stylists/rooms, packages, tips, retail add-ons at checkout.",
        "maturity": "roadmap",
        "typical_operators": "Hair and nail salons, spas, barbershops with retail shelves.",
        "veyron_today": "Sell services as products and use variants for duration/tier; no booking engine.",
        "roadmap_hooks": "Calendar, staff commission, tip pooling, membership passes.",
    },
    "hotel_fnb": {
        "label": "Hotel · banquet · F&B outlet",
        "summary": "Room charges to folio, banquet packages, banquet event billing, multiple outlets.",
        "maturity": "roadmap",
        "typical_operators": "Hotel restaurants, catering kitchens, resort outlets.",
        "veyron_today": "Single-outlet POS and delivery flags; no PMS/folio integration.",
        "roadmap_hooks": "Room lookup, charge posting, banquet deposits, banquet BEO sheets.",
    },
    "hardware_builders": {
        "label": "Hardware · builders · industrial counter",
        "summary": "Cut-to-length, odd units, quotations converted to sales, contractor pricing tiers.",
        "maturity": "roadmap",
        "typical_operators": "Lumber yards, hardware chains, electrical/plumbing counters.",
        "veyron_today": "SKU catalog and variants; no length/cut calculator or quote-to-PO flow.",
        "roadmap_hooks": "Dimensional pricing, quote hold, contractor price lists, delivery tickets.",
    },
    "wholesale_distribution": {
        "label": "Wholesale · distribution counter",
        "summary": "Case packs, customer price tiers, credit limits, delivery notes, B2B invoices.",
        "maturity": "partial",
        "typical_operators": "Distributors, cash-and-carry, van-sales depots.",
        "veyron_today": "Volume via quick keys and variants; invoicing table exists at platform level.",
        "roadmap_hooks": "Customer tier matrix, MOQ, pallet picks, AR aging in POS.",
    },
    "services_professional": {
        "label": "Services · time & materials",
        "summary": "Billable hours, deposits, milestones, retainer top-ups, professional receipts.",
        "maturity": "roadmap",
        "typical_operators": "Repair shops (non-parts), studios, consultants with walk-in payments.",
        "veyron_today": "Sell fixed-fee SKUs; no timers or job costing in POS.",
        "roadmap_hooks": "Time entries, job tickets, deposit vs balance, pro-forma invoices.",
    },
    "fashion_boutique": {
        "label": "Fashion · boutique · specialty retail",
        "summary": "Style/size/color matrix, exchanges, layaway, low-touch premium experience.",
        "maturity": "partial",
        "typical_operators": "Apparel, shoes, accessories, curated lifestyle shops.",
        "veyron_today": "Variants per SKU; returns/exchanges need disciplined manual flow.",
        "roadmap_hooks": "Matrix grid UI, exchange wizard, layaway schedules, clienteling notes.",
    },
    "wet_market_stall": {
        "label": "Wet market · bazaar · tiangge",
        "summary": "Ultra-fast keypad, intermittent connectivity, simple totals, minimal master data.",
        "maturity": "partial",
        "typical_operators": "Public market stalls, weekend bazaars, pop-up vendors.",
        "veyron_today": "Works online; short SKU lists and PLU-style naming fit best.",
        "roadmap_hooks": "Offline-first queue, Bluetooth scales, preset PLU keys, sunud-sunod receipt mode.",
    },
    "custom_enterprise": {
        "label": "Custom · enterprise integration",
        "summary": "Franchise HQ rules, ERP sync, bespoke workflows — sold as implementation project.",
        "maturity": "roadmap",
        "typical_operators": "Chains with central kitchen, franchise ops, ERP-mandatory retailers.",
        "veyron_today": "API/feature flags as negotiated; core POS unchanged without SOW.",
        "roadmap_hooks": "Per-tenant modules, webhooks, data warehouse exports, SSO.",
    },
}


def normalize_pos_profile_id(raw: str | None) -> str:
    s = (raw or "").strip().lower().replace(" ", "_")
    if s in PROFILES:
        return s
    return DEFAULT_POS_PROFILE_ID


def get_profile(profile_id: str | None) -> dict[str, Any]:
    pid = normalize_pos_profile_id(profile_id)
    base = PROFILES.get(pid) or PROFILES[DEFAULT_POS_PROFILE_ID]
    return {"id": pid, **base}


def iter_profiles_for_admin() -> Iterator[tuple[str, dict[str, Any]]]:
    order = [
        "cafe_bakery",
        "retail_grocery",
        "restaurant_table_service",
        "pharmacy_health",
        "salon_spa",
        "hotel_fnb",
        "hardware_builders",
        "wholesale_distribution",
        "services_professional",
        "fashion_boutique",
        "wet_market_stall",
        "custom_enterprise",
    ]
    for key in order:
        if key in PROFILES:
            yield key, PROFILES[key]
