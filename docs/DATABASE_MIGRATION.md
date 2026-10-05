# Database Migration Guide: SQLite → PostgreSQL

**Applies to:** Waypost (Django 5.2.8)
**Scenario:** Moving existing data from the local development database (`db.sqlite3`) to a production PostgreSQL server (Ubuntu 24.04 LTS).
**Last Updated:** September 6, 2026

> ℹ️ Development continues to use **SQLite** by default. Production uses **PostgreSQL**, selected entirely through environment variables (see [`DEPLOYMENT.md`](DEPLOYMENT.md) and `.env.example`). No code changes are required to switch — only configuration.

---

## Overview

Django's ORM abstracts the database, so the **schema** is recreated on PostgreSQL by running the same migrations. The **data** is moved with Django's `dumpdata` / `loaddata` fixtures. The high-level flow:

```
SQLite (dev)                     PostgreSQL (prod)
─────────────                    ─────────────────
1. dumpdata  ──► data.json ──►  4. loaddata
                                 2. migrate (creates empty schema)
                                 3. (switch settings via .env)
```

**Two migration strategies:**
- **A. Data migration** (this guide) — you have real data in SQLite you must keep.
- **B. Fresh start** — production begins empty; just run `migrate` + `createsuperuser` and re-import business data. Simpler; use if dev data is throwaway.

---

## Prerequisites

- PostgreSQL 14+ installed on the target server (`sudo apt install postgresql postgresql-contrib`).
- `psycopg2-binary` installed (already pinned in `requirements.txt`).
- A backup of `db.sqlite3` (copy the file before you start).
- Django app stopped (no writes during export) to get a consistent dump.

---

## Step 1 — Provision the PostgreSQL database

```bash
sudo -u postgres psql
```

```sql
CREATE DATABASE waypost_db WITH ENCODING 'UTF8';
CREATE USER waypost_django WITH PASSWORD 'your_secure_password_here';
GRANT ALL PRIVILEGES ON DATABASE waypost_db TO waypost_django;
ALTER DATABASE waypost_db OWNER TO waypost_django;
\q
```

> ⚠️ **UTF-8 is mandatory.** The system stores Simplified Chinese content (`zh-cn`), so the database, client encoding, and collation must be UTF-8. Verify with `\l` in `psql`.

On PostgreSQL 15+, also grant schema privileges (the `public` schema is no longer writable by default):

```sql
\c waypost_db
GRANT ALL ON SCHEMA public TO waypost_django;
```

---

## Step 2 — Export data from SQLite

Run this **while still pointed at SQLite** (i.e., on your dev machine or before changing `.env`):

```bash
python manage.py dumpdata \
  --exclude contenttypes \
  --exclude auth.permission \
  --exclude admin.logentry \
  --exclude sessions.session \
  --output data.json
```

**Why these exclusions?**
- `contenttypes` and `auth.permission` are **auto-created** by `migrate` on the new database. Loading them from the fixture causes duplicate-key / integrity conflicts.
- `admin.logentry` references those content types and is not worth carrying over.
- `sessions.session` holds only transient dev login sessions.

> 💡 For maximum portability you can add `--natural-foreign --natural-primary`. If `loaddata` later complains about natural keys, drop those two flags — the plain command above preserves primary keys and works for a like-for-like schema migration.
>
> 🔐 2FA devices (`django-otp` tokens) **are** included by default, so users keep their authenticator enrollments. Exclude `otp_totp.totptoken` / `otp_static.staticdevice` if you'd rather force re-enrollment.

---

## Step 3 — Point Django at PostgreSQL

Set the database variables in the production `.env` (loaded automatically by `waypost/runtime_setup.py`):

```bash
DATABASE_ENGINE=django.db.backends.postgresql
DATABASE_NAME=waypost_db
DATABASE_USER=waypost_django
DATABASE_PASSWORD=your_secure_password_here
DATABASE_HOST=127.0.0.1
DATABASE_PORT=5432
```

Confirm Django sees the new backend:

```bash
python manage.py shell -c "from django.db import connection; print(connection.vendor, connection.settings_dict['ENGINE'])"
# Expected: postgresql django.db.backends.postgresql
```

Copy `data.json` (from Step 2) to the production server, and copy the `media/` directory too — **uploads live on the filesystem, not in the database** (delivery signatures, invoice files, asset photos).

---

## Step 4 — Create the schema

```bash
python manage.py migrate
```

This creates all tables on the empty PostgreSQL database, including `contenttypes` and permissions.

---

## Step 5 — Load the data

```bash
python manage.py loaddata data.json
```

- If you did **not** exclude the superuser from the dump, your admin account comes across — do **not** run `createsuperuser`.
- If starting fresh (Strategy B) or the dump had no user, run `python manage.py createsuperuser`.

---

## Step 6 — Reset sequences (PostgreSQL gotcha)

SQLite has no sequences; PostgreSQL does. Django's `loaddata` normally resets them automatically, but if you later see:

```
duplicate key value violates unique constraint "<table>_pkey"
```

when **creating new** records, reset the sequences for the integer-PK apps:

```bash
python manage.py sqlsequencereset \
  accounts assets companies customers quotations purchases \
  deliveries invoices audit reports products users dashboard \
  | psql -U waypost_django -h 127.0.0.1 -d waypost_db
```

> Models with **UUID** primary keys (e.g., assets, companies) do not need sequence resets — only `BigAutoField`/integer-PK tables do. Running the command for all apps is harmless.

---

## Step 7 — Verify

```bash
python manage.py check --deploy          # production readiness warnings
python manage.py showmigrations          # all migrations applied
python manage.py shell -c "from django.contrib.auth import get_user_model; print(get_user_model().objects.count())"
```

Then log in to the app and spot-check:
- Asset counts and a few asset detail pages
- A quotation → delivery → invoice record chain
- An admin/superadmin login (and 2FA if enabled)
- Chinese (`zh-cn`) text renders correctly (confirms UTF-8)

---

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `duplicate key ... contenttypes_pkey` | Contenttypes loaded from fixture | Re-dump with `--exclude contenttypes` (Step 2) |
| `duplicate key ... <table>_pkey` on new inserts | Sequences not advanced | Run `sqlsequencereset` (Step 6) |
| `permission denied for schema public` | PostgreSQL 15+ default | `GRANT ALL ON SCHEMA public TO waypost_django;` |
| Garbled Chinese characters | Non-UTF-8 database/connection | Recreate DB with `ENCODING 'UTF8'`; ensure client encoding UTF-8 |
| `loaddata` integrity error on a FK | Dump order / missing natural keys | Re-dump adding `--natural-foreign --natural-primary` |
| Numeric/date field rejected | SQLite loose typing hid bad data | Clean the offending rows in SQLite, re-dump |
| Missing images/files after migration | `media/` not copied | Copy the `media/` directory to the server (Step 3) |

---

## Alternatives

- **`pgloader`** — bulk-loads a SQLite file directly into PostgreSQL (fast for large datasets), then run `migrate` to reconcile. Requires care with type mappings.
- **Per-app fixtures** — dump app-by-app (`dumpdata assets > assets.json`) for finer control over load order.
- **Fresh start (Strategy B)** — skip data entirely; `migrate` + `createsuperuser` + re-import catalogs/locations via the built-in CSV import flows.

---

## Post-Migration Checklist

- [ ] `db.sqlite3` backed up before starting
- [ ] PostgreSQL DB created with UTF-8 encoding
- [ ] `.env` `DATABASE_*` variables set (and `DJANGO_DEBUG=False`, `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`)
- [ ] `migrate` completed on PostgreSQL
- [ ] `loaddata` completed without errors
- [ ] Sequences reset (if needed)
- [ ] `media/` directory copied to the server
- [ ] `collectstatic` run (required for WhiteNoise manifest storage)
- [ ] `check --deploy` reviewed
- [ ] Superuser login + 2FA verified
- [ ] Chinese text renders correctly
- [ ] Record counts match the source SQLite database

---

## Rollback

The source `db.sqlite3` is untouched by this process. To roll back, remove the `DATABASE_*` variables from `.env` (Django falls back to SQLite) and restart. Keep the SQLite backup until production is confirmed stable.

---

## Related Documentation

- [`DEPLOYMENT.md`](DEPLOYMENT.md) — full production setup (Gunicorn, Nginx, Redis, static files)
- [`GITHUB_SETTINGS.md`](GITHUB_SETTINGS.md) — repository configuration
- [`INDEX.md`](INDEX.md) — documentation index

---

*Last Updated: September 6, 2026*
*Maintainer: Sean Liu (@sean7084)*
