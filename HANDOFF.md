# Veyron POS — Engineering Handoff

**Last updated:** 2026-07-26
**Engagement:** Technical evaluation + remediation of CRITICAL findings, then a
feature sprint (multi-branch, promotions/loyalty, QR scan-to-order, pay-on-order).
**Status:** Two branches pushed to `origin`, CI green. One small uncommitted diff
in progress (Proxmox reverse-proxy deployment verification) — see "In progress" below.

---

## ⚠️ Read this first

**Branches (both pushed to `origin`, neither merged to `main`):**

| Branch | Tip | Contains |
|---|---|---|
| `hardening/critical-fixes` | `22320f4` | Security hardening, RLS, PayMongo, VAT, Docker (9 commits) |
| `feature/ci-locations-promotions` | `cf4f2b8` | Stacked on the above: CI, multi-branch, promotions/loyalty, QR ordering, pay-on-order (7 more commits) |

`main` is still at the original `ffe68e7` — nothing has been merged. Two PRs are
effectively ready to raise whenever desired:
1. `hardening/critical-fixes` → `main`
2. `feature/ci-locations-promotions` → `hardening/critical-fixes` (or → `main` directly)

**Uncommitted right now** (in-progress Proxmox deployment work, see below):
`requirements.txt` (added `redis` client), `Caddyfile` (new), `docker-compose.proxmox.yml` (new).

**CI is real and green**, verified via `gh run view` against GitHub's own infrastructure
(not just local claims) — both the SQLite suite and the Postgres+RLS job passed on
the latest push to `feature/ci-locations-promotions`.

---

## Where things stand

MVP readiness: **4.5/10 → ~7.5/10** (last full scored pass, before this session's
sprint work on locations/promotions/QR ordering — those add capability and tests on
top of that baseline; not yet re-scored formally).

**Test suite: 82 tests across 13 files — 76 pass, 6 skip** (the 6 skip unless a
Postgres `DATABASE_URL` is set; they run in CI). Run with:

```bash
.venv/Scripts/python.exe -m pytest tests/ -q
```

**Verdict:** still *not* ready for paying merchants (BIR receipt numbering + a live
payment sandbox run are the remaining blockers), but the app is functionally rich
now — multi-branch, promotions, loyalty, and a full QR scan-to-order flow with
optional online payment are all built and tested. See "Outstanding work" below.

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
- `Flask-WTF` `CSRFProtect` enabled; `{{ csrf_field() }}` added to all POST forms.
- JSON APIs (`/api/*`) and public JSON order endpoints are deliberately exempt
  (they authenticate via their own guards / are programmatic).

### 3. Rate limiting
- `Flask-Limiter`, **opt-in per endpoint** so POS/API/order traffic is never throttled.
- Limits on `/login`, `/signup`, `/superadmin/login`, `/reauth`, and the public
  order/pay endpoints.
- Set `RATELIMIT_STORAGE_URI=redis://…` when running more than one worker — **see the
  Redis client bug in "Bugs found" below; this is now fixed but re-verification is
  in progress.**
- Files: `app/core/rate_limit.py`

### 4. Postgres Row-Level Security (tenant isolation) — **PROVEN**
- RLS policies on **32 tenant-scoped tables** (grew from 29 as locations/promotions/
  loyalty tables were added), enforced by the database, not just the app-layer regex
  scoper.
- Per-connection GUCs `app.tenant_id` / `app.bypass_rls` set in `app/core/db.py`.
- `FORCE ROW LEVEL SECURITY` so policies apply to the table owner too.
- Apply with: `python tools/apply_rls.py` (idempotent).
- Files: `tools/apply_rls.py`, `sql/rls_policies.sql`, `app/core/db.py`

**Verified against real PostgreSQL 16** — 10/10 manual checks + `tests/test_rls_postgres.py`
(6 tests, runs in CI): tenant A cannot read/update/delete tenant B; inserting for
another tenant is rejected; no tenant context returns **zero** rows (fail closed);
super-admin bypass works — all confirmed **with the app-layer scoper deliberately
disabled**, so a badly-scoped query can no longer leak across tenants.

### 5. PayMongo payments (GCash / Maya / QRPH / cards)
- Real HTTP gateway using PayMongo **Checkout Sessions** (one call covers all methods,
  returns a hosted `checkout_url`). Stdlib `urllib` — no new HTTP dependency.
- Registered in `PaymentService`; per-tenant encrypted credentials with platform-level
  env fallback.
- Files: `app/core/payments/paymongo_gateway.py`, `service.py`, `registry.py`

### 6. Payments wired end-to-end (POS sales)
- `POST /api/payments/checkout` — opens a checkout session for a finalized sale.
- `POST /api/payments/webhook/paymongo` — HMAC-verified, idempotent settlement.
- Register in the PayMongo dashboard: `https://YOUR-DOMAIN/api/payments/webhook/paymongo`

### 7. Philippine VAT
- Per-tenant `vat_rate` (default 12%), `vat_inclusive`, `vat_registered`, editable in
  Owner → Settings. Previously hardcoded to `0.00`.
- VAT-inclusive pricing **extracts** VAT rather than adding it.
- **Senior Citizen / PWD sales are VAT-exempt**, with the 20% discount applied to the
  VAT-exclusive amount (₱112 → ₱100 net → ₱80 due, not ₱89.60).
- Files: `app/core/tax.py`, POS web + POS API + eTown checkout paths, `receipt.html`

### 8. Deployment (base)
- `Dockerfile` (non-root, healthcheck, `gunicorn --preload`), `docker-compose.yml`
  (app + PostgreSQL 16 + Redis), `.dockerignore`, `.env.production.example`.
- `docker/initdb/10-create-app-role.sh` creates the **non-superuser** DB role RLS needs.
- `render.yaml` rewritten to use **managed Postgres** (previously deployed SQLite,
  which would have run with no RLS at all).
- `.gitattributes` forces LF on `*.sh`/Dockerfile/`docker/**` — without it, Windows
  `core.autocrlf=true` checks out the initdb script with CRLF, breaking it inside the
  Linux container ("bad interpreter").

### 9. Performance
- **19 composite indexes** added, all tenant-leading (`products`, `sales`, `sale_items`,
  `stock_movements`, `payments`, `audit_logs`, `customers`, `locations`, …).

### 10. CI (GitHub Actions)
- `.github/workflows/ci.yml`: two jobs on Python 3.12.
  - `test-sqlite`: full pytest suite.
  - `test-postgres-rls`: spins up a real Postgres service container, creates the
    non-superuser `veyron_app` role (same SQL as production), applies RLS, runs
    `tests/test_rls_postgres.py`.
- **Confirmed actually green on GitHub** (`gh run view`), not just passing locally.

### 11. Multi-branch / locations
- Tenant-scoped `locations` table; every tenant gets a default branch auto-seeded on
  creation; `sales.location_id` records which branch a sale belongs to.
- `app/core/locations.py`: `LocationService` — CRUD, default resolution, tenant isolation.
- Owner dashboard: branch management panel (add / set default / deactivate; the
  default branch can't be deactivated without first assigning a new default).
- **Deferred:** no cashier-facing branch selector at POS (sales currently attribute to
  the tenant's default branch), no per-location stock, no branch-filtered reports.
- **Bonus fix:** `app/core/subscription/entitlements.py` called `sqlite3.Row.get()`,
  which doesn't exist — this crashed the **entire owner dashboard** on SQLite. Fixed
  (index access works on both `sqlite3.Row` and psycopg `dict_row`).

### 12. Promotions & loyalty
- **Promotions** (`app/core/promotions.py`): tenant-scoped coupon codes — percent or
  fixed-amount, minimum spend, validity window, usage limit. Wired into POS checkout
  as a vatable discount; usage is only counted on a genuinely new sale, never on an
  idempotent replay.
- **Loyalty** (`app/core/loyalty.py`): append-only points ledger (balance is always
  `SUM(points_change)` — never a mutable column, so it can't silently drift).
  Earn-on-spend wired into eTown order placement. Redeem logic is implemented and
  tested but **not yet wired to any POS UI** — there's no way for a cashier to apply
  a redemption today.
- Owner dashboard: promotions management + loyalty enable/earn-rate/redeem-rate settings.

### 13. QR scan-to-order
- Public mobile-first order page at `/order/<tenant_id>` — works for **any active
  merchant**, not just eTown-marketplace opt-ins (new `get_orderable_tenant()` +
  `create_order(..., require_marketplace=False)` path). Gated per-tenant by
  `qr_ordering_enabled` (default on).
- Owner dashboard renders a scannable SVG QR of the order link (optional `?table=`
  param for per-table dine-in QRs), via `app/core/qrcodes.py` (`qrcode` library, SVG
  output — no Pillow dependency).
- Orders flow through the same tenant-scoped order service, so customers, VAT, and
  loyalty accrual all apply automatically.
- Files: `app/core/qrcodes.py`, `app/modules/order/` (new blueprint),
  `templates/order/order.html`, `templates/order/confirmation.html`

### 14. Pay-on-order (optional online payment for QR orders)
- `orders` gained its own `payment_status` / `payment_method` / `payment_reference`
  columns — kept **separate** from the `payments` table (which requires a `sale_id`
  the orders table doesn't have; bolting orders onto it risked the existing POS
  payment code).
- Confirmation page shows an optional **"Pay online now"** button; default remains
  pay-at-counter. Auto-refreshes while a payment is awaiting confirmation.
- The **same webhook** now settles either a POS sale payment or a QR order payment
  (tries `payments` first, falls back to `orders` by reference) — fully backward
  compatible, with an explicit regression test proving the POS sale path is untouched.
- `submit_order` switched to Post/Redirect/Get (avoids resubmission on refresh, gives
  the confirmation page a real URL for PayMongo's redirect-back).

---

## Bugs found and fixed along the way

1. **Cross-tenant customer PII leak (high severity).** `customers` and
   `customer_addresses` had no `tenant_id` and a global `UNIQUE(phone)` — one
   merchant's order could match and overwrite another merchant's customer record.
   Fixed: tenant-scoped, RLS-covered, legacy-backfilled. Tests in `test_tenant_isolation.py`.

2. **Payment credentials stored under fake encryption.** `credentials.py` silently
   fell back to a hand-rolled XOR cipher when `cryptography` wasn't installed — and it
   wasn't in `requirements.txt`. Now pinned, so Fernet is actually used.

3. **Webhook signatures could never verify.** `PaymentService.handle_webhook()` built
   the gateway with an empty config, so it never had the signing secret.

4. **Senior/PWD sales were charged VAT**, with the discount applied to the
   VAT-inclusive price rather than the VAT-exclusive amount. Both wrong under PH law.

5. **RLS would have broken the public eTown storefront.** Anonymous visitors have no
   session tenant, so policies failed closed and shop pages returned nothing. Public
   eTown/order routes now resolve tenant from the URL without binding the session.

6. **Cross-test webhook-secret collision.** Two test files each set the shared
   `PAYMONGO_WEBHOOK_SECRET` env var to a *different* value at import time. Since
   it's one process-wide variable, whichever test module the runner imported last
   silently broke signature verification for the other — a real, order-dependent
   flakiness bug (caught because a new test failed only in the full suite, not in
   isolation). Fixed by unifying the value across test modules.

7. **Docker Compose stack was fundamentally broken with Redis.**
   `docker-compose.yml` sets `RATELIMIT_STORAGE_URI=redis://redis:6379` for the app
   unconditionally, but the `redis` Python client library was **never in
   `requirements.txt`** — Flask-Limiter crashed the app on every boot under the full
   compose stack. This had never been caught because earlier Docker smoke tests ran
   the app container standalone (defaulting to in-process `memory://` limiting), never
   via `docker compose up` with Redis actually wired. Found while verifying a Proxmox
   reverse-proxy deployment path. **Fix applied** (`redis` added to
   `requirements.txt`) but **end-to-end re-verification was in progress and not yet
   completed** — see "In progress" below.

---

## In progress (uncommitted)

Mid-session task: verify a Proxmox/VPS deployment path with a reverse proxy in front
of the app. Two new files plus the requirements.txt fix above:

- **`Caddyfile`** — reverse proxy with automatic HTTPS (Let's Encrypt, HTTP-01
  challenge). Deliberately **not** wildcard: checking the actual routes confirmed QR
  ordering, eTown, and the POS/owner dashboard are all path-based or session-based —
  only the separate subdomain-resolved "storefront" feature needs wildcard DNS. So a
  single domain + standard TLS is sufficient for testing everything shipped so far.
- **`docker-compose.proxmox.yml`** — overlay adding a `caddy` service (ports 80/443)
  proxying to `app:8000`. Validated with `docker compose config` (merges cleanly, no
  port conflicts, required `DOMAIN` var guard works).

**Status when this session's work paused:** rebuilt the image with the `redis` fix,
then hit the compose stack's Postgres role-creation step failing — but this turned
out to be **a testing-harness artifact, not a product bug**: verification was run
from a scratchpad copy of the compose files that was missing the bind-mounted
`docker/initdb/` folder, so the non-superuser role was never actually created. That's
now understood and corrected in the test harness; a clean `docker compose down -v`
followed by `up -d --build` from the **actual project directory** (not a scratch
copy) should be the next step to get a final, trustworthy green result.

**Next steps to close this out:**
1. `git status` — confirm only `requirements.txt`, `Caddyfile`, `docker-compose.proxmox.yml` are dirty.
2. From the real repo directory: `docker compose down -v` (if any prior stack is running), then
   `docker compose -f docker-compose.yml -f docker-compose.proxmox.yml up -d --build`
   with `DOMAIN=:80` (or a real domain) and dummy Postgres passwords in `.env`.
3. Confirm the app container **stays up** (no redis crash) and that `db` logs show the
   `veyron_app` role was created successfully.
4. Hit `http://localhost/healthz` (through Caddy) and confirm 200.
5. Once green, commit as: fix the missing `redis` dependency + add the Proxmox Caddy overlay.

---

## Outstanding work

### Blocking real merchants
1. **BIR Official Receipt numbering + X/Z readings** — deliberately deferred. Needs
   your accountant to confirm formats. VAT *math* is done; statutory receipt
   numbering is not.
2. **Apply RLS on the real production database** — `python tools/apply_rls.py`, and
   confirm the app connects as a **non-superuser** role. RLS is proven against real
   Postgres in throwaway containers and in CI, not yet on your actual production instance.
3. **One live PayMongo sandbox transaction** — the gateway and webhook are tested
   against mocked HTTP only. Run a real `sk_test_` payment end-to-end before go-live.

### Strongly recommended next
4. Finish the Proxmox/Docker Compose Redis re-verification above.
5. Wire loyalty **redemption** into a POS UI (accrual works; redemption has no
   front-end path yet).
6. Add a cashier-facing branch selector at POS (sales default to the tenant's
   default branch today).
7. Monitoring / alerting — none exists.
8. Connection pooling (PgBouncer) before meaningful concurrency.

### Known gaps (not started)
- No offline mode (no service worker / IndexedDB / sync) — a real risk for PH connectivity
- No gift cards or store credit
- Restaurant vertical (tables, KDS, courses) is descriptions only
- Hardware: no scale, label printer, or cash-drawer pulse (browser print + bridge stub only)
- `migrations/` is still empty — schema is inline DDL + `ensure_column`; adopt Alembic
- `queries.py` (1,472 lines) and `flask_app.py` (~1,000+) still run parallel to the
  structured API service layer
- Duplicate `app/core/delivery/model.py` and `models.py`
- README/`prisma/` documentation drift

---

## Gotchas

- **A Postgres superuser bypasses RLS entirely.** If the app connects as a superuser,
  tenant isolation silently does nothing. Use the non-superuser role.
- **`PAYMENT_CREDENTIALS_SECRET` must be set once and never changed** — rotating it
  makes stored gateway credentials undecryptable. Keep it separate from `SECRET_KEY`.
- **VAT now defaults to 12%** where it was `0.00`. Untick "VAT-registered business"
  for any non-VAT merchant *before* they transact.
- **Legacy SQLite databases keep the old global `UNIQUE(phone)`** on `customers`
  (SQLite can't drop constraints without a table rebuild). Fresh installs and
  Postgres get the correct `UNIQUE(tenant_id, phone)`.
- **`RATELIMIT_STORAGE_URI=redis://…` requires the `redis` package in
  `requirements.txt`** or Flask-Limiter crashes the app at boot. This is the bug fixed
  in this session — watch for a regression if requirements.txt is ever trimmed.
- **Wildcard DNS/TLS is not needed** for POS, QR ordering, or eTown (all path-based /
  session-based tenant resolution). Only the separate subdomain-resolved "storefront"
  feature needs it. Don't over-build TLS for a testing deploy.
- **PayMongo webhook secret is one process-wide value.** Any new test module that
  sets `PAYMONGO_WEBHOOK_SECRET` must reuse the existing constant (see
  `test_payments_api.py`), not invent a new one.
- `gunicorn --preload` is intentional: it runs `init_db()` once in the master rather
  than racing across workers on first boot.

---

## Quick reference

```bash
# Run tests
.venv/Scripts/python.exe -m pytest tests/ -q

# Full stack (app + Postgres + Redis)
cp .env.production.example .env    # fill SECRET_KEY, DB passwords, SEED_SUPERADMIN_*
docker compose up -d --build
docker compose exec app python tools/apply_rls.py

# Same, behind a reverse proxy with automatic HTTPS (single domain, no wildcard needed)
# add DOMAIN=your.domain.com to .env, then:
docker compose -f docker-compose.yml -f docker-compose.proxmox.yml up -d --build

# Verify tenant isolation against Postgres
DATABASE_URL=postgresql://veyron_app:PASSWORD@localhost:5432/veyron \
  .venv/Scripts/python.exe -m pytest tests/test_rls_postgres.py -v

# Check CI status on the pushed branch
gh run list --branch feature/ci-locations-promotions --limit 5
```

Deployment options and setup details are in `README.md` (Docker, Render, VAT,
Payments, and RLS sections).
