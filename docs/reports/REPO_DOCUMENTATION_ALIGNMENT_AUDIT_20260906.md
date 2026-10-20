# Repository Documentation Alignment Audit — HengjiAMS1 (now Waypost)

**Date:** September 6, 2026
**Auditor:** Automated documentation review
**Repository:** sean7084/HengjiAMS1
**Scope:** API docs vs. endpoints, config docs vs. `.env.example`/`settings.py`, workflow docs vs. business logic

> ℹ️ **Filename note (2026-10-13):** renamed from `DOCUMENTATION_ALIGNMENT_AUDIT_20260906.md`.
> A *different* audit with that exact name lives in the Aliyun ops knowledge base as
> `reports/KB_DOCUMENTATION_ALIGNMENT_AUDIT_20260906.md`. That one audits the **ops knowledge base
> against the live ECS**; this one audits **this repository's documentation against its code**.
> Same date, same auditor, unrelated corpora — the `REPO_` / `KB_` prefixes exist so the two are
> never cited interchangeably.

---

## Executive Summary

| Area | Verdict | Severity |
|------|---------|----------|
| **1. API Documentation** | ~70% fictional — wrong base URL, non-existent endpoints, wrong auth model | 🔴 Critical |
| **2. Configuration / Deployment** | Env vars not consumed; missing Docker, command, and prod deps | 🔴 Critical |
| **3. Workflow Guide** | Invoice import logic materially wrong; several outdated values | 🟠 High |
| **4. Code Security Posture** | `SECRET_KEY`/`DEBUG`/`ALLOWED_HOSTS`/DB hardcoded, not env-driven | 🔴 Critical |

**Root cause:** Docs (dated Aug 20, 2026) were generated aspirationally and never validated against code. `SETUP_SUMMARY.md` self-certifies "API consumers understand available endpoints — ✅ Pass," which the evidence contradicts.

---

## Area 1 — API Documentation (`docs/API_GUIDE.md`)

### 1.1 Wrong base URL & authentication model (Critical)
| Documented | Actual |
|-----------|--------|
| Base `/api/` | Base is `/api/v1/` (`hengjiams/urls.py`) |
| JWT `Authorization: Bearer <token>` | DRF `SessionAuthentication` only (`settings.REST_FRAMEWORK`) |
| `POST /api/auth/login/` returns `access_token` | No auth API; login is Django session at `/accounts/login/` |
| Pagination "50 per page" | `PAGE_SIZE = 20` |

### 1.2 Documented endpoints that DO NOT exist (Critical)
Real REST API (`api/urls.py`) exposes only 9 viewsets: `users`, `companies`, `locations`, `categories`, `brands`, `models`, `assets`, `assignments`, `maintenance`.

Entirely fictional sections (features exist only as server-rendered HTML views):
- Products API — `/api/products/`, `/api/product-prices/`, `/api/services/`
- Quotations API — `/api/quotations/` + `/confirm/`, `/cancel/`, `/pdf/`
- Deliveries API — `/api/deliveries/` + `/dispatch/`, `/mark-delivered/`, `/pdf/`
- Invoices API — `/api/invoices/weekly-batches/`, `/infos/`, `/dispatches/`, `/forward-esker/`
- Reports API — `/api/reports/charts/status-distribution/`, `/dashboard-config/`, `/workflow-summary/`, `/export/assets.csv/`

### 1.3 Wrong identifier types (High)
Docs use `{uuid}` for quotations/deliveries/invoices/products, but those apps use integer `<int:pk>`. Only assets/companies/locations use UUIDs.

### 1.4 Internal API docs page also wrong (Medium)
`api/views.py::APIDocumentationView` lists a `divisions` endpoint, but `DivisionViewSet` is defined and never registered in the router — `/api/v1/divisions/` returns 404. (Also a dead-code finding.)

### 1.5 Real endpoints missing from docs (Medium)
Not documented: `GET /api/v1/assets/by_barcode/`, `/api/v1/assets/by_serial/`, `/api/v1/assets/{id}/assignments/`, `/api/v1/assets/{id}/maintenance/`.

---

## Area 2 — Configuration & Deployment (`docs/DEPLOYMENT.md`, `.env.example`)

### 2.1 Documented env vars NOT read by `settings.py` (Critical / Security)
| Documented env var | Reality |
|-------------------|---------|
| `SECRET_KEY` | Hardcoded `django-insecure-...` (line 27) |
| `DEBUG=False` | Hardcoded `DEBUG = True` (line 30) |
| `ALLOWED_HOSTS` | Hardcoded `['*']` (line 32) |
| `DATABASE_*` | SQLite hardcoded; PostgreSQL block commented out & hardcoded |
| `EMAIL_HOST/PORT/USE_TLS/HOST_USER/HOST_PASSWORD` | Not present — email uses per-user `UserMailboxSettings` SMTP from DB |

**Security implication:** The production checklist instructs setting `SECRET_KEY`/`DEBUG` via env, but the code cannot read them. Deploying as documented ships an insecure hardcoded key with `DEBUG=True`.

### 2.2 Minimax var name & URL mismatch (Critical)
- Docs: `MINIMAX_TOKEN_PLAN_KEY` (uppercase); code reads `minimax_token_plan_key` (lowercase) → doc value ignored.
- Docs URL `https://api.minimax.io/v1/messages`; actual `https://api.minimaxi.com/anthropic/v1/messages`.

### 2.3 Referenced infrastructure does not exist (Critical)
- `docker/` directory absent — Option 2 references `docker/README.md`, `docker-compose`, `scripts/generate-secrets.sh`.
- `run_mailbox_sync` management command does not exist (only `setup_demo_admins`, `create_sample_assets`). Auto-sync is an in-process thread.
- `requirements.txt` lacks `gunicorn`, `whitenoise`, `django_redis`, `celery`; `psycopg2` commented out despite "PostgreSQL 14+ required".

### 2.4 `template_files/` gitignored but required at runtime (High)
`.gitignore` excludes `template_files/`, yet quotation/delivery/invoice services raise `FileNotFoundError` without `quotation_template.xlsx`, `签收单 template.xlsx`, `invoice information template.xlsx`. A fresh clone deployed per the guide fails document generation. DEPLOYMENT.md never mentions provisioning these.

---

## Area 3 — Workflow Guide (`docs/WORKFLOW_GUIDE.md`)

### 3.1 Invoice import logic materially wrong (High)
| Documented | Actual (`invoices/services.py::process_sharepoint_batch`) |
|-----------|------|
| Parses XML/PDF/OFD in a ZIP archive | Reads a single Excel workbook |
| Regex-extracts Bill To, net/tax/gross, date, number | Extracts 3 columns: Kering Group PO Number, Internal Order, SAP Cost Center |
| Matches quotations by PO reference | No quotation matching; sets `invoice_date = today` |
| Upload to `/invoices/invoice-info/upload-batch/` | Actual route is `/invoices/import/` |

### 3.2 Tax formula inverted (High)
- Doc: `tax_amount = net_amount * tax_rate / (1 + tax_rate)` (extract from gross).
- Code: `line_tax = line_net * (tax_rate / 100)`; `line_gross = line_net + line_tax` (add tax on top). Different results.

### 3.3 Outdated / incorrect values (Medium)
- Language codes: doc `en` / `zh-hans`; actual `en-us` / `zh-cn`.
- Django version: doc `5.2.3`; `requirements.txt` pins `5.2.8`.
- `.env.local` vs `.env`: WORKFLOW_GUIDE says `.env.local`; `runtime_setup.py` loads `.env`.
- Dispatch stock check: doc references `Asset.available_count` and "A-R-Zone logic"; code matches discrete `Asset` rows by brand+model in `location_id=3` (`INTERNAL_WAREHOUSE_LOCATION_ID`).

### 3.4 Verified correct (Aligned)
- Delivery transitions `pending → dispatched → delivered` match `DeliveryOrder.Status` + routes.
- Mailbox auto-sync "every 5 minutes during runserver" matches `mailbox_sync.py` and README.
- Direct-dispatch vs. continue-fulfillment branching matches `build_dispatch_asset_assignments`.

---

## Area 4 — Cross-Cutting: `docs/SETUP_SUMMARY.md`
- Claims "8 impact classifications" — there are 3 impact labels.
- Claims CODEOWNERS is 116 lines — now 73 (rewritten).
- Self-certifies API guide as complete/Pass — contradicted by Area 1.
- Git snippet typos: `git add docs/.github/LICENCE CODEOWNERS` (missing space, "LICENCE").

---

## Remediation Plan (priority order)

| # | Action | Target | Priority |
|---|--------|--------|----------|
| 1 | Make `settings.py` read `SECRET_KEY`/`DEBUG`/`ALLOWED_HOSTS`/DB from env with dev-preserving defaults | `hengjiams/settings.py`, `.env.example` | P0 |
| 2 | Rewrite `API_GUIDE.md` to document only real `/api/v1/` DRF endpoints | `docs/API_GUIDE.md` | P0 |
| 3 | Fix `DEPLOYMENT.md`: remove Docker/cron/`run_mailbox_sync`; correct env vars; add `template_files/` provisioning; fix Minimax name/URL | `docs/DEPLOYMENT.md` | P0 |
| 4 | Correct `WORKFLOW_GUIDE.md` invoice import + tax formula + language codes + Django version + `.env` naming | `docs/WORKFLOW_GUIDE.md` | P1 |
| 5 | Fix `SETUP_SUMMARY.md` counts; register or remove `DivisionViewSet` dead code | `docs/`, `api/` | P2 |

---

## Findings Index (for issue tracking)

| ID | Finding | Severity | Area |
|----|---------|----------|------|
| F-01 | API_GUIDE wrong base URL `/api/` vs `/api/v1/` | P0 | API |
| F-02 | API_GUIDE fictional JWT/Bearer auth (session-only) | P0 | API |
| F-03 | API_GUIDE documents non-existent Products/Quotations/Deliveries/Invoices/Reports endpoints | P0 | API |
| F-04 | API_GUIDE wrong identifier types (uuid vs int pk) | P1 | API |
| F-05 | DivisionViewSet defined but not routed (404 + dead code) | P2 | API |
| F-06 | API_GUIDE missing real asset lookup endpoints | P2 | API |
| F-07 | settings.py hardcodes SECRET_KEY/DEBUG/ALLOWED_HOSTS/DB | P0 | Security |
| F-08 | DEPLOYMENT.md env vars not consumed by settings | P0 | Config |
| F-09 | Minimax env var name/URL mismatch | P1 | Config |
| F-10 | DEPLOYMENT.md references missing docker/ infrastructure | P1 | Config |
| F-11 | DEPLOYMENT.md references non-existent run_mailbox_sync command | P1 | Config |
| F-12 | requirements.txt missing gunicorn/whitenoise/psycopg2/redis | P1 | Config |
| F-13 | template_files/ gitignored but required at runtime | P1 | Config |
| F-14 | WORKFLOW_GUIDE invoice import logic wrong | P1 | Workflow |
| F-15 | WORKFLOW_GUIDE tax formula inverted | P1 | Workflow |
| F-16 | WORKFLOW_GUIDE outdated lang codes/Django version/.env naming | P2 | Workflow |
| F-17 | SETUP_SUMMARY.md inaccurate counts & self-certification | P2 | Cross-cutting |

---

*Generated: September 6, 2026*
