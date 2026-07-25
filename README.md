# Veyron POS

This project is a Flask-based POS, admin, inventory, and owner dashboard for Veyron's Cakes and Pastries.

## Current Online-Ready Phase

This codebase is now prepared for internet deployment with:

- environment-based configuration
- SQLite for local development or PostgreSQL via `DATABASE_URL`
- production WSGI entrypoint via `wsgi.py`
- health endpoint at `/healthz`
- secure session cookie defaults for production
- Render/Procfile deployment scaffolding
- owner/admin/cashier remote login support
- owner alert tracking for low stock, voids/refunds, and suspicious inventory adjustments
- SMTP email alert hooks controlled from owner settings

## PostgreSQL (Flask) and Prisma

- **This Python app** uses Postgres when `DATABASE_URL` is set to a non-`sqlite` DSN (see [`app/core/db.py`](app/core/db.py): `DATABASE_ENGINE` becomes `postgres`, connections use `psycopg`).
- **Prisma is not part of this repository** — there is no `package.json`, `schema.prisma`, or `prisma/` folder here. If Prisma lives in another app or monorepo package, point its datasource at the **same** `DATABASE_URL` and database/schema as Flask so POS, eTown, and Prisma share one Postgres database.
- Schema migrations today are driven by Flask `init_db()` / `ensure_column`, not Prisma Migrate. If you introduce Prisma, avoid conflicting migrations (e.g. treat SQLAlchemy/raw SQL as source of truth, or use `prisma db pull` against the existing DB and align carefully).

## Tenant isolation — Postgres Row-Level Security (RLS)

On PostgreSQL, tenant isolation is enforced at the database level, not only by the
application-layer query scoper. After the schema exists, apply the policies:

```bash
python tools/apply_rls.py            # applies to $DATABASE_URL (idempotent, re-runnable)
python tools/apply_rls.py --print    # inspect the SQL without applying
```

How it works: each request sets two Postgres GUCs on its connection
(`app.tenant_id`, `app.bypass_rls`), and every tenant-scoped table has a policy that
only returns/accepts rows for the current tenant. Bypass is granted to super-admin
requests, raw connections, and non-request contexts (migrations/scripts). If neither
GUC is set, RLS returns **no rows** (fail closed).

Verify isolation at any time (skipped automatically on SQLite):

```bash
DATABASE_URL=postgresql://veyron_app:PASSWORD@localhost:5432/veyron python -m pytest tests/test_rls_postgres.py -v
```

**Critical:** a Postgres **superuser always bypasses RLS**. The app must connect as a
normal (non-superuser) role or isolation is silently disabled. `FORCE ROW LEVEL
SECURITY` is enabled so policies also apply to the table owner. RLS is a no-op on
SQLite (local dev), where the application-layer scoper is the only guard.

## Current Limits

- PostgreSQL support is now wired into the app, but you should still validate your production database and seed data before going live.
- In-app backup and restore remain SQLite-only. For PostgreSQL, use host-managed backups or `pg_dump` / point-in-time restore.
- Actual cloud deployment still requires your Render or hosting account, plus a real PostgreSQL connection string.

## Local Run

1. Create a virtual environment and install dependencies:

```bash
pip install -r requirements.txt
```

2. Copy `.env.example` to `.env` and adjust values if needed.

3. For local development, you can leave `DATABASE_URL` blank and use SQLite. For online deployment, set `DATABASE_URL` to your PostgreSQL connection string.

4. Run the app:

```bash
python veyron-pos.py
```

## VAT (Philippines)

VAT is configured **per tenant** under Owner → Settings, stored in `app_settings`:

| Setting | Default | Meaning |
|---|---|---|
| `vat_rate` | `0.12` | Decimal rate (12%) |
| `vat_inclusive` | `1` | Catalog prices already include VAT (PH retail norm) |
| `vat_registered` | `1` | Set `0` for non-VAT / percentage-tax merchants |

Rules implemented in [`app/core/tax.py`](app/core/tax.py):

- **VAT-inclusive pricing extracts VAT rather than adding it** — a ₱112.00 price is
  ₱100.00 VATable sales + ₱12.00 VAT, and the total stays ₱112.00.
- **Senior Citizen / PWD sales are VAT-exempt.** VAT is stripped first, then the 20%
  discount applies to the VAT-exclusive amount: ₱112.00 → ₱100.00 net → ₱80.00 due.
  (Discounting the VAT-inclusive price would wrongly give ₱89.60.)
- **Non-VAT-registered merchants** charge no VAT; their prices contain none to strip.

Receipts show VATable Sales / VAT / VAT-Exempt Sales accordingly.

**Not yet implemented (deferred):** BIR Official Receipt numbering and X/Z readings.
Confirm those formats with your accountant before go-live.

## Payments (PayMongo — GCash, Maya, QRPH, cards)

Set `PAYMONGO_SECRET_KEY`, `PAYMONGO_PUBLIC_KEY`, and `PAYMONGO_WEBHOOK_SECRET`
(see `.env.production.example`). Per-tenant credentials in the encrypted store
override these platform-level keys.

Flow:

1. Finalize the sale with `payment_status='pending'`.
2. `POST /api/payments/checkout` with `{"sale_id": 123}` → returns a hosted
   `checkout_url` covering GCash/Maya/QRPH/card. Send the customer there (or show it
   as a QR at the counter).
3. The customer pays; PayMongo calls the webhook; the payment row flips to
   `completed`. The webhook is signature-verified and idempotent.

Register this webhook URL in the PayMongo dashboard:

```text
https://YOUR-DOMAIN/api/payments/webhook/paymongo
```

The webhook is intentionally exempt from tenant context and CSRF — its trust boundary
is the HMAC signature. Unverified calls are rejected with HTTP 400 and never settle a
payment. Verified-but-unmatched events return 200 so the provider stops retrying.

## Docker Deployment (portable: Proxmox, VPS, Fly, Railway, Cloud Run, ECS)

Full stack — app + PostgreSQL + Redis — via `docker-compose.yml`:

```bash
cp .env.production.example .env      # fill SECRET_KEY, DB passwords, SEED_SUPERADMIN_*
docker compose up -d --build
docker compose exec app python tools/apply_rls.py   # enable tenant isolation (once)
```

Then check `http://localhost:8000/healthz`.

Notes:

- The app connects as a **non-superuser** Postgres role (`veyron_app`, created by
  `docker/initdb/10-create-app-role.sh`). This is required — a superuser bypasses RLS.
- Redis backs rate limiting so limits are shared across gunicorn workers.
- `gunicorn --preload` runs `init_db()` once in the master before forking, avoiding
  concurrent first-boot schema creation.
- Product images and backups persist in the `appdata` volume mounted at `/data`.
- The image runs as an unprivileged user and has a `/healthz` healthcheck.

To run the app container alone (against an external database), build and pass env:

```bash
docker build -t veyron-pos .
```

## Render Deployment

1. Push the project to GitHub.
2. Create a new Render Web Service.
3. Render can use `render.yaml`, or you can set:
   - Build command: `pip install -r requirements.txt`
   - Start command: `gunicorn wsgi:app`
4. Set environment variables:
   - `APP_ENV=production`
   - `FLASK_DEBUG=0`
   - `SECRET_KEY=<long-random-secret>`
   - `DATABASE_URL=<your-postgresql-connection-string>`
   - `BACKUP_DIR=/opt/render/project/src/backups`
   - `ALERT_TO_EMAIL=<owner-email>`
   - `ALERT_FROM_EMAIL=<sender-email>`
   - `SMTP_HOST=<smtp-host>`
   - `SMTP_PORT=587`
   - `SMTP_USERNAME=<smtp-user>`
   - `SMTP_PASSWORD=<smtp-password>`
5. Verify `/healthz` after deploy.

## Recommended Next Phase

To make the owner-facing remote access truly production-grade, the next work should be:

1. Validate the remaining reporting and admin flows against a real PostgreSQL database.
2. Add password change/reset and remove seeded testing credentials.
3. Add object storage for downloadable backups if deploying to an ephemeral host.
4. Add alert acknowledgement or read/unread workflow for the owner dashboard.
