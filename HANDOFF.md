# Veyron POS — Engineering Handoff

**Last updated:** 2026-07-25
**Engagement:** Technical evaluation + remediation of CRITICAL findings
**Status:** Work paused at a clean checkpoint. All tests green.

---

## ⚠️ Read this first

**Nothing is committed.** All work sits as uncommitted changes on `main`
(49 files: 18 new, ~31 modified). The last commit is still `ffe68e7`.

Before doing anything else, review and commit — ideally on a branch:

```bash
git checkout -b hardening/critical-fixes
git add -A
git commit -m "Security hardening, RLS tenant isolation, PayMongo payments, VAT, Docker"
```

Some modified files also contain **pre-existing** uncommitted edits from before this
engagement, so review the diff rather than assuming every change is from this work.

---

## Where things stand

MVP readiness moved from **4.5/10 → ~7.2/10**.

| Dimension | Before | Now |
|---|---:|---:|
| Security | 3.0 | **8.0** |
| Deployment | 4.0 | **7.0** |
| Performance | 3.0 | **5.5** |
| Payments | 4.0 | **7.5** |
| Testing | 1.0 | **6.0** |
| Multi-tenant readiness | 5.0 | **7.5** |
| **Overall MVP readiness** | **4.5** | **~7.2** |

**Verdict:** still *not* ready for paying merchants, but the remaining gaps are a short
concrete list rather than architectural problems. See "Outstanding work" below.

**Test suite: 45 tests — 39 pass, 6 skip** (the 6 skip unless a Postgres `DATABASE_URL`
is set). Run with:

```bash
.venv/Scripts/python.exe -m pytest tests/ -q
```

---

## What was completed

### 1. Secrets & credential hardening
- Production refuses to boot with a missing/dev `SECRET_KEY`.
- Weak seeded logins (`owner123`, `admin123`, `cashier123`, `superadmin123`) **no longer
  created in production**. Dev still seeds them for convenience.
- Initial super-admin comes from `SEED_SUPERADMIN_USERNAME` / `SEED_SUPERADMIN_PIN`
  (min 8 chars); first boot fails closed if absent.
- Files: `app/core/flask_app.py`, `.env.example`, `render.yaml`

### 2. CSRF protection
- `Flask-WTF` `CSRFProtect` enabled; `{{ csrf_field() }}` added to **all 56 POST forms**
  across 15 templates.
- JSON APIs (`/api/*`) and the public eTown JSON order endpoint are deliberately exempt
  (they authenticate via their own guards / are programmatic).
- Files: `app/core/flask_app.py`, `app/modules/api/__init__.py`, 15 templates

### 3. Rate limiting
- `Flask-Limiter`, **opt-in per endpoint** so POS/API traffic is never throttled.
- Limits on `/login`, `/signup`, `/superadmin/login`, `/reauth`.
- Set `RATELIMIT_STORAGE_URI=redis://…` when running more than one worker.
- Files: `app/core/rate_limit.py` (new), `app/modules/web/routes/auth.py`

### 4. Postgres Row-Level Security (tenant isolation) — **PROVEN**
- RLS policies on **29 tenant-scoped tables**, enforced by the database, not just the
  app-layer regex scoper.
- Per-connection GUCs `app.tenant_id` / `app.bypass_rls` set in `app/core/db.py`.
- `FORCE ROW LEVEL SECURITY` so policies apply to the table owner too.
- Apply with: `python tools/apply_rls.py` (idempotent).
- Files: `tools/apply_rls.py`, `sql/rls_policies.sql` (both new), `app/core/db.py`

**Verified against real PostgreSQL 16 — 10/10 manual checks + 6 regression tests:**
tenant A cannot read/update/delete tenant B; inserting for another tenant is rejected;
no tenant context returns **zero** rows (fail closed); super-admin bypass works.
Crucially, this held **with the app-layer scoper deliberately disabled** — a badly
scoped query can no longer leak across tenants.

### 5. PayMongo payments (GCash / Maya / QRPH / cards)
- Real HTTP gateway using PayMongo **Checkout Sessions** (one call covers all methods,
  returns a hosted `checkout_url`). Stdlib `urllib` — no new HTTP dependency.
- Registered in `PaymentService`; per-tenant encrypted credentials with platform-level
  env fallback.
- Files: `app/core/payments/paymongo_gateway.py` (new), `service.py`, `registry.py`

### 6. Payments wired end-to-end
- `POST /api/payments/checkout` — opens a checkout session for a finalized sale.
- `POST /api/payments/webhook/paymongo` — HMAC-verified, idempotent settlement.
- Uses the existing `payment_status='pending'` hook — **no schema migration**.
- Register in the PayMongo dashboard: `https://YOUR-DOMAIN/api/payments/webhook/paymongo`
- Files: `app/modules/api/services/payments_api_service.py`,
  `app/modules/api/blueprints/payments.py` (both new)

### 7. Philippine VAT
- Per-tenant `vat_rate` (default 12%), `vat_inclusive`, `vat_registered`, editable in
  Owner → Settings. Previously hardcoded to `0.00`.
- VAT-inclusive pricing **extracts** VAT rather than adding it.
- **Senior Citizen / PWD sales are VAT-exempt**, with the 20% discount applied to the
  VAT-exclusive amount (₱112 → ₱100 net → ₱80 due, not ₱89.60).
- Receipts show VATable Sales / VAT / VAT-Exempt Sales.
- Files: `app/core/tax.py` (new), POS web + POS API + eTown checkout paths, `receipt.html`

### 8. Deployment
- `Dockerfile` (non-root, healthcheck, `gunicorn --preload`), `docker-compose.yml`
  (app + PostgreSQL 16 + Redis), `.dockerignore`, `.env.production.example`.
- `docker/initdb/10-create-app-role.sh` creates the **non-superuser** DB role RLS needs.
- `render.yaml` rewritten to use **managed Postgres** (it previously deployed SQLite,
  which would have run with no RLS at all).
- Verified: image builds, container boots in production mode, `/healthz` returns 200.

### 9. Performance
- **19 composite indexes** added, all tenant-leading (`products`, `sales`, `sale_items`,
  `stock_movements`, `payments`, `audit_logs`, `customers`, …). Only 4 existed before,
  so tenant-filtered queries were full table scans.

---

## Bugs found and fixed along the way

1. **Cross-tenant customer PII leak (high severity).** `customers` and
   `customer_addresses` had **no `tenant_id`** and a global `UNIQUE(phone)`. Customer
   records were shared across every merchant — Merchant B's order would match and
   *overwrite* a customer created by Merchant A. Both tables are now tenant-scoped,
   added to `SCOPED_TABLES` (so RLS covers them), with legacy backfill. Regression tests
   in `tests/test_tenant_isolation.py`.

2. **Payment credentials stored under fake encryption.** `credentials.py` silently falls
   back to a hand-rolled XOR cipher when `cryptography` isn't installed — and it wasn't
   in `requirements.txt`. Now pinned, so Fernet is actually used.

3. **Webhook signatures could never verify.** `PaymentService.handle_webhook()` built the
   gateway with an empty config, so it never had the signing secret.

4. **Senior/PWD sales were charged VAT**, and the discount was applied to the
   VAT-inclusive price. Both wrong under PH law. Fixed and tested.

5. **RLS would have broken the public eTown storefront.** Anonymous visitors have no
   session tenant, so policies failed closed and shop pages returned nothing. Public
   eTown routes now resolve tenant from the URL (without binding the visitor's session).

---

## Outstanding work

### Blocking real merchants
1. **BIR Official Receipt numbering + X/Z readings** — deliberately deferred. Needs your
   accountant to confirm formats. VAT *math* is done; statutory receipt numbering is not.
2. **Apply RLS on the real production database** — `python tools/apply_rls.py`, and
   confirm the app connects as a **non-superuser** role. RLS is proven in a test
   environment, not yet on your production instance.
3. **One live PayMongo sandbox transaction** — the gateway is tested against mocked HTTP.
   Run a real `sk_test_` payment end-to-end in your environment before go-live.

### Strongly recommended next
4. **CI (GitHub Actions)** running this suite — nothing currently gates regressions.
5. **Monitoring / alerting** — none exists.
6. **Connection pooling** (PgBouncer) before meaningful concurrency.

### Known gaps (not started)
- No offline mode (no service worker / IndexedDB / sync) — a real risk for PH connectivity
- No multi-branch model — `multi_branch` is a feature flag with **no `locations` table**
- No promotions, loyalty, gift cards, or store credit
- Restaurant vertical (tables, KDS, courses) is descriptions only
- Hardware: no scale, label printer, or cash-drawer pulse (browser print + bridge stub only)
- `migrations/` is still empty — schema is inline DDL + `ensure_column`; adopt Alembic
- `queries.py` (1,472 lines) and `flask_app.py` (~1,000) still run parallel to the
  structured API service layer
- Duplicate `app/core/delivery/model.py` and `models.py`
- README/`prisma/` documentation drift

---

## Gotchas

- **A Postgres superuser bypasses RLS entirely.** If the app connects as a superuser,
  tenant isolation silently does nothing. Use the non-superuser role.
- **`PAYMENT_CREDENTIALS_SECRET` must be set once and never changed** — rotating it makes
  stored gateway credentials undecryptable. Keep it separate from `SECRET_KEY`.
- **VAT now defaults to 12%** where it was `0.00`. Existing sales are untouched, but new
  sales record VAT. Untick "VAT-registered business" for any non-VAT merchant *before*
  they transact.
- **Legacy SQLite databases keep the old global `UNIQUE(phone)`** on `customers` (SQLite
  can't drop constraints without a table rebuild), so two tenants can't share a phone
  there. Fresh installs and Postgres get the correct `UNIQUE(tenant_id, phone)`.
- **Rate limits are in-process by default.** With multiple gunicorn workers set
  `RATELIMIT_STORAGE_URI` to Redis or limits are per-worker.
- `gunicorn --preload` is intentional: it runs `init_db()` once in the master rather than
  racing across workers on first boot.

---

## Quick reference

```bash
# Run tests
.venv/Scripts/python.exe -m pytest tests/ -q

# Full stack (app + Postgres + Redis)
cp .env.production.example .env    # fill SECRET_KEY, DB passwords, SEED_SUPERADMIN_*
docker compose up -d --build
docker compose exec app python tools/apply_rls.py

# Verify tenant isolation against Postgres
DATABASE_URL=postgresql://veyron_app:PASSWORD@localhost:5432/veyron \
  .venv/Scripts/python.exe -m pytest tests/test_rls_postgres.py -v
```

Deployment options and setup details are in `README.md` (Docker, Render, VAT, Payments,
and RLS sections).
