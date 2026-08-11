# Veyron POS — MVP Launch Checklist

**Last updated:** 2026-08-11  
**Target:** First paying merchants on production (`https://veyronpos.guma.one`)  
**Scope:** Cafe / bakery / small retail POS (not full restaurant or BIR-accredited receipt rollout)

This document lists **priority tasks before MVP launch**, ordered by impact. Use it as a working checklist — mark items complete as you go.

---

## MVP definition (what “launch” means)

A merchant can:

1. Sign up or be onboarded on a **paid plan** (after platform approval).
2. Log in securely, run **POS checkout**, and manage **inventory**.
3. Trust that **tenant data is isolated** on Postgres production.
4. Receive **Growth-tier features** only when entitled (plan + approval).
5. Optionally collect **GCash / Maya / QRPH / card** via PayMongo (API ready; POS UI wiring optional for v1).

Out of scope for MVP (explicitly deferred): BIR Official Receipt numbering, X/Z statutory readings, offline mode, restaurant KDS/tables, direct GCash/Maya integrations.

---

## Current production snapshot (2026-08-11)

| Area | Status |
|------|--------|
| Hosting | Proxmox CT 102, Docker Compose (app + Postgres + Redis), public URL live |
| Auth | Production login cleaned (no demo credentials on login page) |
| Branding | Veyron logo + tenant brand tokens deployed |
| Plans | Demo / Starter ₱399 / Growth ₱899 / Enterprise ₱1,399 synced to DB |
| Plan gating | Pricing Wizard + Inventory Reports gated on Growth; demo gets 3-product wizard teaser |
| Upgrades | Owner requests plan → **pending** → Super Admin approves in Health & risk |
| PayMongo | Gateway + webhook implemented; **not** wired in POS cashier UI yet |
| RLS on prod | **Verify** — may not be applied yet on live Postgres |
| Subscriptions table | Often empty; entitlements read `tenants.plan_name` (works, but billing rows incomplete) |

---

## P0 — Blockers (do not launch without these)

### Security & multi-tenancy

- [ ] **Apply RLS on production Postgres**
  - Run inside app container: `python tools/apply_rls.py`
  - Confirm app connects as **`veyron_app`** (non-superuser), not `veyron_admin`
  - Run: `pytest tests/test_rls_postgres.py -v` against production DSN (from a safe jump host)

- [ ] **Production secrets audit**
  - [ ] `SECRET_KEY` — strong, unique, set in CT 102 `.env`
  - [ ] `PAYMENT_CREDENTIALS_SECRET` — set once, documented, never rotated casually
  - [ ] Postgres passwords — not defaults; not in git
  - [ ] `SEED_SUPERADMIN_*` — strong PIN; remove or rotate if ever exposed

- [ ] **Disable or protect dev-only paths**
  - [ ] Confirm demo accounts (`owner123`, etc.) are **not** seeded in production
  - [ ] Confirm `FLASK_DEBUG=0` and `APP_ENV=production`

### Billing & entitlements

- [ ] **End-to-end upgrade flow test**
  1. Demo/starter owner requests Growth on `/subscription/plans`
  2. Super Admin sees row under **Health & risk → Pending plan upgrades**
  3. Approve → tenant gets Growth features (wizard unlimited, reports unlocked)
  4. Reject path tested (owner alert + status restored)

- [ ] **Document manual payment process for MVP**
  - Bank transfer / GCash to platform → Super Admin approves upgrade
  - No automated subscription charging until post-MVP

### Operational readiness

- [ ] **Backups**
  - [ ] Postgres volume backup schedule (Proxmox snapshot or `pg_dump` cron to `/data/backups`)
  - [ ] Restore drill — restore to a test container once

- [ ] **Deploy runbook**
  - [ ] Document tarball deploy steps (local → Proxmox → CT 102 → `docker compose up -d --build app`)
  - [ ] Rollback: previous image tag or previous tarball

- [ ] **Health monitoring**
  - [ ] Fix or accept `/healthz` reporting (`database_engine` still says `sqlite` on Postgres — cosmetic but confusing)
  - [ ] External uptime check on `https://veyronpos.guma.one/healthz`

### Legal / product (PH)

- [ ] **VAT defaults**
  - Confirm each merchant’s **VAT-registered** flag before go-live (default 12% VAT math applies when enabled)

- [ ] **BIR scope sign-off**
  - MVP launches **without** BIR Official Receipt numbering and X/Z readings
  - Merchant-facing copy should not claim BIR accreditation until implemented

---

## P1 — Strongly recommended before first paying merchant

### Payments (PayMongo)

- [ ] **Configure PayMongo sandbox (then live)**
  ```env
  PAYMONGO_SECRET_KEY=sk_test_...
  PAYMONGO_PUBLIC_KEY=pk_test_...
  PAYMONGO_WEBHOOK_SECRET=whsk_...
  ```
- [ ] Register webhook: `https://veyronpos.guma.one/api/payments/webhook/paymongo`
- [ ] **One real sandbox transaction** end-to-end (not mocked HTTP)
- [ ] Enable PayMongo in **Super Admin → Payment gateways**
- [ ] Per-tenant credentials (encrypted store) if merchants use their own PayMongo accounts

- [ ] **POS PayMongo UX** (if digital pay is in MVP scope)
  - After sale finalize → call `POST /api/payments/checkout` → show QR / open `checkout_url`
  - Today: API + webhook exist; **cashier UI does not call them**

### Onboarding & admin

- [ ] **Super Admin alerts email** (upgrade requests)
  ```env
  SMTP_HOST=...
  ALERT_FROM_EMAIL=...
  ALERT_TO_EMAIL=ops@yourdomain.com
  ```
  Without SMTP, dashboard + audit log still work; email is skipped.

- [ ] **Seed first real merchant** (non-demo)
  - Starter or Growth tenant with owner account
  - Units seeded per tenant (auto on first inventory load — verify)

- [ ] **Super Admin runbook**
  - Approve/reject upgrades
  - Manual plan change (Edit tenant) bypasses pending queue
  - Deactivate tenant

### Quality

- [ ] **Full test suite green**
  ```bash
  .venv/Scripts/python.exe -m pytest tests/ -q
  ```
- [ ] **Smoke test script** (manual or automated)
  - Login → POS sale → receipt
  - Inventory adjust → low-stock alert
  - Owner dashboard → plans page
  - Super Admin → tenant list

- [ ] **Merge / tag release branch**
  - `HANDOFF.md` notes feature branches not merged to `main` — decide single release branch for MVP

---

## P2 — Post-MVP (first 30–60 days)

| Task | Notes |
|------|--------|
| BIR Official Receipt numbering + X/Z | Requires accountant-specified formats |
| Automated subscription billing | PayMongo or invoice flow for platform fees |
| POS branch selector | Sales default to default branch today |
| Loyalty redemption UI | Accrual exists; redemption not in POS UI |
| Monitoring / alerting | Sentry, Uptime Kuma, or similar |
| Connection pooling | PgBouncer before high concurrency |
| Alembic migrations | Replace inline `ensure_column` DDL |
| Offline / flaky connectivity mode | Service worker + sync — PH connectivity risk |
| Marketing site alignment | `veyron.guma.one` vs product URL consistency |
| CI on every deploy | GitHub Actions gate before tarball deploy |

---

## Environment checklist (CT 102 `.env`)

| Variable | Required for MVP | Notes |
|----------|------------------|--------|
| `SECRET_KEY` | Yes | Session signing |
| `APP_ENV=production` | Yes | Set in compose |
| `DATABASE_URL` | Yes | Via compose (`veyron_app@db`) |
| `POSTGRES_*` / `APP_DB_*` | Yes | Compose provisioning |
| `SEED_SUPERADMIN_*` | First boot only | Then rely on DB user |
| `PAYMENT_CREDENTIALS_SECRET` | If storing gateway creds | Do not rotate |
| `PAYMONGO_*` | If accepting digital pay | Sandbox first |
| `PAYMONGO_WEBHOOK_SECRET` | If webhooks enabled | Platform-wide |
| `SMTP_*` / `ALERT_*` | Recommended | Upgrade notifications |
| `RATELIMIT_STORAGE_URI` | If >1 worker | `redis://redis:6379` in compose |
| `DOMAIN` | If using Caddy overlay | `veyronpos.guma.one` |

---

## Launch day verification (30-minute script)

1. **Public health** — `curl https://veyronpos.guma.one/healthz` → `"status":"ok"`
2. **Staff login** — tenant owner/admin/cashier; no demo hints on login page
3. **POS** — add items, complete sale, print/view receipt
4. **Inventory** — product list, stock movement; Growth reports locked on Starter
5. **Plan upgrade** — owner submits Growth → pending banner → admin approves → features unlock
6. **Super Admin** — audit log shows `plan_upgrade_requested` / `plan_upgrade_approved`
7. **PayMongo** (if enabled) — one test checkout + webhook settles `payments` row
8. **Backup** — confirm last backup timestamp exists

---

## Related docs

| Document | Purpose |
|----------|---------|
| [README.md](../README.md) | Local dev, Docker, RLS, PayMongo flow |
| [HANDOFF.md](../HANDOFF.md) | Engineering history, known gaps, test counts |
| [.env.production.example](../.env.production.example) | Production env template |

---

## Ownership suggestions

| Area | Owner |
|------|--------|
| Infra / Proxmox / deploy | Platform ops |
| RLS + security sign-off | Engineering |
| PayMongo sandbox + webhook | Engineering + finance |
| Merchant onboarding + approval | Operations / Super Admin |
| BIR / VAT policy | Business + accountant |
| MVP go/no-go | Product + ops |

---

*Update this file when items ship or priorities change. After MVP launch, archive completed P0 items and promote P1 leftovers to the active sprint.*
