# Backup & Restore Runbook — Waypost

**Applies to:** Waypost (Django 5.2.8) on Ubuntu 24.04 LTS
**Production DB:** PostgreSQL · **Dev DB:** SQLite (`db.sqlite3`)
**Last Updated:** September 7, 2026

> ⚠️ **An untested backup is not a backup.** This runbook covers both backup **and restore**, including a verification drill. A basic `pg_dump` script also appears in [`DEPLOYMENT.md`](DEPLOYMENT.md); this document is the authoritative, fuller procedure.

---

## 1. What Must Be Backed Up

| Asset | Location | Why | Method |
|-------|----------|-----|--------|
| **Database** | PostgreSQL `waypost_db` (prod) / `db.sqlite3` (dev) | All business data | `pg_dump` / file copy |
| **Media uploads** | `media/` (delivery signatures, invoice files, asset photos, quotation tuning, profiles) | Not in the DB; user-uploaded files | `tar` / `rsync` |
| **Document templates** | `template_files/` | **Gitignored** but required — PDF/Excel generation fails without it | `tar` |
| **Environment/secrets** | `.env` | `DJANGO_SECRET_KEY`, `DJANGO_FIELD_ENCRYPTION_KEY`, DB creds | Encrypted copy |
| **Web/service config** | `/etc/nginx/sites-available/waypost`, `/etc/systemd/system/waypost.service` | Reproduce the server | Copy |

> 🔐 **Critical:** `.env` holds `DJANGO_FIELD_ENCRYPTION_KEY`. Without it, Fernet-encrypted mailbox/SMTP credentials **cannot be decrypted** even if the database is restored. Back it up securely (see §6).

**Recovery objectives (set targets with stakeholders):**
- **RPO** (max tolerable data loss): e.g., 24h with nightly backups.
- **RTO** (max tolerable downtime): e.g., 2–4h for a full rebuild.

---

## 2. PostgreSQL Backup (Production)

Use the **custom format** (`-Fc`) — it is compressed and allows selective/parallel restore via `pg_restore`.

```bash
# /opt/scripts/waypost-backup.sh
set -euo pipefail

BACKUP_DIR="/backups/waypost"
STAMP=$(date +%Y%m%d_%H%M%S)
DB_NAME="waypost_db"
DB_USER="waypost_django"
APP_DIR="/opt/waypost"

mkdir -p "$BACKUP_DIR"

# 1) Database (custom format)
pg_dump -Fc -U "$DB_USER" -h 127.0.0.1 "$DB_NAME" > "$BACKUP_DIR/db_$STAMP.dump"

# 2) Media uploads
tar czf "$BACKUP_DIR/media_$STAMP.tar.gz" -C "$APP_DIR" media

# 3) Document templates (gitignored but essential)
tar czf "$BACKUP_DIR/template_files_$STAMP.tar.gz" -C "$APP_DIR" template_files

# 4) Config (nginx + systemd)
tar czf "$BACKUP_DIR/config_$STAMP.tar.gz" \
  -C / etc/nginx/sites-available/waypost etc/systemd/system/waypost.service 2>/dev/null || true

echo "Backup complete: $STAMP"
```

> `.pgpass` (mode `600`) avoids embedding the DB password: `127.0.0.1:5432:waypost_db:waypost_django:<password>`.

**Back up before every deploy/migration** as a point-in-time safety net (see [`RELEASE_PROCEDURE.md`](RELEASE_PROCEDURE.md) §6.1). This backup is the only reliable rollback path for releases containing non-reversible data migrations — see [`RELEASE_PROCEDURE.md`](RELEASE_PROCEDURE.md) §9.2.

---

## 3. PostgreSQL Restore (Production)

```bash
# Restore into a fresh/empty database (recommended)
sudo -u postgres psql -c "DROP DATABASE IF EXISTS waypost_db;"
sudo -u postgres psql -c "CREATE DATABASE waypost_db WITH ENCODING 'UTF8';"
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE waypost_db TO waypost_django;"
# PostgreSQL 15+:
sudo -u postgres psql -d waypost_db -c "GRANT ALL ON SCHEMA public TO waypost_django;"

# Load the dump (--clean --if-exists makes it re-runnable)
pg_restore --clean --if-exists --no-owner -U waypost_django -h 127.0.0.1 -d waypost_db /backups/waypost/db_<STAMP>.dump
```

**Restore into the live database** (overwrites data — stop the app first):
```bash
sudo systemctl stop waypost
pg_restore --clean --if-exists --no-owner -U waypost_django -h 127.0.0.1 -d waypost_db /backups/waypost/db_<STAMP>.dump
sudo systemctl start waypost
```

---

## 4. SQLite Backup / Restore (Development)

SQLite is a single file. For a **consistent** copy, use the online backup API rather than copying a live file:

```bash
# Consistent backup (safe even while the dev server runs)
python manage.py shell -c "import sqlite3; src=sqlite3.connect('db.sqlite3'); dst=sqlite3.connect('db_backup.sqlite3'); src.backup(dst); dst.close(); src.close()"

# Restore = stop the server and replace the file
#   (dev only) cp db_backup.sqlite3 db.sqlite3
```

A plain file copy is acceptable **only** when the app is stopped.

---

## 5. Media & Template Restore

```bash
# Media uploads
tar xzf /backups/waypost/media_<STAMP>.tar.gz -C /opt/waypost

# Document templates (required for PDF/Excel generation)
tar xzf /backups/waypost/template_files_<STAMP>.tar.gz -C /opt/waypost

# Fix ownership/permissions
sudo chown -R waypost:waypost /opt/waypost/media /opt/waypost/template_files
```

> Without `template_files/`, quotation/delivery/invoice document generation raises `FileNotFoundError` (see [`DEPLOYMENT.md`](DEPLOYMENT.md) Step 4b).

---

## 6. Secrets / Config Backup

`.env` contains secrets — **do not** store it in plaintext alongside ordinary backups.

```bash
# Encrypt the .env backup (example: age or gpg)
gpg --symmetric --cipher-algo AES256 -o /backups/waypost/env_<STAMP>.gpg /opt/waypost/.env

# Restore
gpg --decrypt /backups/waypost/env_<STAMP>.gpg > /opt/waypost/.env
chmod 600 /opt/waypost/.env
```

Store the passphrase/key **separately** from the backups (e.g., a password manager). Config restore:
```bash
tar xzf /backups/waypost/config_<STAMP>.tar.gz -C /
sudo nginx -t && sudo systemctl restart nginx
sudo systemctl daemon-reload && sudo systemctl restart waypost
```

---

## 7. Automation & Retention

**Schedule** (cron):
```cron
# Nightly at 02:00
0 2 * * * /opt/scripts/waypost-backup.sh >> /var/log/waypost/backup.log 2>&1
```

**Retention** (example policy — adjust to your RPO):
| Cadence | Keep |
|---------|------|
| Daily | 7 days |
| Weekly | 4 weeks |
| Monthly | 12 months |

```bash
# Prune daily backups older than 7 days
find /backups/waypost -name 'db_*.dump' -mtime +7 -delete
```

Store at least one copy **off-host** (object storage / separate machine) to survive host loss.

---

## 8. Restore Verification Drill (do this regularly)

A backup you have never restored is an assumption. Run this drill monthly:

1. Restore the latest `db_<STAMP>.dump` into a **scratch** database (`waypost_restore_test`).
2. Point a local/staging instance at it and run:
   ```bash
   python manage.py check
   python manage.py showmigrations           # all applied
   python manage.py shell -c "from django.contrib.auth import get_user_model; print(get_user_model().objects.count())"
   ```
3. Spot-check record counts against production (users, assets, quotations, invoices).
4. Restore `media_<STAMP>.tar.gz` and open a delivery signature / invoice file.
5. Restore `template_files_<STAMP>.tar.gz` and generate a quotation PDF.
6. Confirm `.env` decrypts and the app starts with `DJANGO_FIELD_ENCRYPTION_KEY` (mailbox credentials decrypt).
7. Record the drill result and duration (feeds RTO confidence).

---

## 9. Full Disaster Recovery (bare-metal rebuild)

Order matters:
1. Provision Ubuntu 24.04; install PostgreSQL, Redis, Nginx (see [`DEPLOYMENT.md`](DEPLOYMENT.md)).
2. Clone the repo; create the venv; `pip install -r requirements.txt`.
3. Restore `.env` (§6) — includes `DJANGO_SECRET_KEY` and `DJANGO_FIELD_ENCRYPTION_KEY`.
4. Create the database and `pg_restore` (§3).
5. Restore `media/` and `template_files/` (§5).
6. `python manage.py migrate` (applies any migrations newer than the backup).
7. `python manage.py collectstatic --noinput`.
8. Restore Nginx/systemd config (§6); `systemctl enable --now waypost`.
9. Run the verification drill (§8) before declaring recovery complete.

---

## 10. Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `pg_restore: error: could not execute query` | Objects already exist | Use `--clean --if-exists`, or restore into an empty DB |
| `permission denied for schema public` | PostgreSQL 15+ default | `GRANT ALL ON SCHEMA public TO waypost_django;` |
| Garbled Chinese after restore | Non-UTF-8 database | Recreate DB with `ENCODING 'UTF8'` and re-restore |
| Mailbox passwords unusable after restore | Missing/rotated `DJANGO_FIELD_ENCRYPTION_KEY` | Restore the **same** `.env` key used at backup time |
| Document generation `FileNotFoundError` | `template_files/` not restored | Restore §5 templates |
| Media 404 / permission denied | Ownership lost | `chown -R waypost:waypost media` |

---

## 11. Quick Reference

```bash
# Backup (all)         -> /opt/scripts/waypost-backup.sh
# DB backup            -> pg_dump -Fc -U waypost_django -h 127.0.0.1 waypost_db > db.dump
# DB restore           -> pg_restore --clean --if-exists --no-owner -U waypost_django -h 127.0.0.1 -d waypost_db db.dump
# Media restore        -> tar xzf media_<STAMP>.tar.gz -C /opt/waypost
# Templates restore    -> tar xzf template_files_<STAMP>.tar.gz -C /opt/waypost
```

---

## Related Documentation

- [`DEPLOYMENT.md`](DEPLOYMENT.md) — server provisioning, Nginx/Gunicorn, basic backup script
- [`DATABASE_MIGRATION.md`](DATABASE_MIGRATION.md) — SQLite → PostgreSQL data migration
- [`SECURITY.md`](SECURITY.md) — secrets management & credential encryption
- [`RELEASE_PROCEDURE.md`](RELEASE_PROCEDURE.md) — versioning, tagging, deploy sequence, and rollback tiers (§8 Tier 3 restores from this runbook)

---

*Maintainer: Sean Liu (@sean7084)*
