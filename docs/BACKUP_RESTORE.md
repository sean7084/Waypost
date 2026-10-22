# Backup & Restore Runbook — Waypost

**Applies to:** Waypost (Django 5.2.8) on Ubuntu 24.04 LTS
**Production DB:** PostgreSQL · **Dev DB:** SQLite (`db.sqlite3`)
**Last Updated:** October 21, 2026

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

> 📜 **Section 2 is the bare-metal (Option 1) shape.** Production today runs on Docker Compose; the script
> that actually runs there is in §2b, and its authoritative copy is
> [`../docker/bin/waypost-backup.sh`](../docker/bin/waypost-backup.sh) rather than a block transcribed into
> this document.

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

### 2b. Docker Compose deployment (production)

Production runs PostgreSQL inside the compose stack with **no published port**,
so `pg_dump` has to run in the `db` container — there is nothing on the host to
connect to. Everything below runs as the `deploy` user from `/srv/waypost/app`.

The **authoritative copy of the script is
[`../docker/bin/waypost-backup.sh`](../docker/bin/waypost-backup.sh)**, installed on the host as
`/srv/waypost/bin/waypost-backup.sh` by `scripts/deploy-dev-to-ecs.ps1`. It is in the repository so that a
host-side change is a reviewable diff instead of an edit nobody sees, and so `sha256sum` between the two is
meaningful. The block below is the compose (`DB_CHECKPOINT_MODE=auto`) shape, kept for reading; the shipped
script sources `docker/bin/_pg_checkpoint.sh` for step 1, so under `external` it dumps the database the app
container points at instead, and under `none` it skips the dump and says so
([`RDS_MIGRATION.md`](RDS_MIGRATION.md)). It also gates the media tar on `WAYPOST_MEDIA_IN_OSS` — see §2c.

```bash
# docker/bin/waypost-backup.sh  ->  /srv/waypost/bin/waypost-backup.sh  (mode 750, owner deploy)
set -euo pipefail

BACKUP_DIR="/srv/waypost/backups"
DATA_DIR="${WAYPOST_DATA_DIR:-/home/deploy/waypost-data}"
STAMP=$(date +%Y%m%d_%H%M%S)
APP_DIR="/srv/waypost/app"

mkdir -p "${BACKUP_DIR}/pg"
cd "$APP_DIR"

# 1) Database. The credentials come from the db container's own environment, so
#    this cannot drift from what Postgres was actually initialised with.
docker compose exec -T db sh -c 'pg_dump -Fc -U "$POSTGRES_USER" "$POSTGRES_DB"' \
  > "${BACKUP_DIR}/pg/db_${STAMP}.dump"

# 2) Media uploads and 3) document templates - both are host bind mounts, so a
#    plain tar is enough; no need to go through the container.
tar czf "${BACKUP_DIR}/media_${STAMP}.tar.gz" -C "$DATA_DIR" media
tar czf "${BACKUP_DIR}/template_files_${STAMP}.tar.gz" -C "$DATA_DIR" template_files

# 4) Configuration. .env holds DJANGO_FIELD_ENCRYPTION_KEY, without which stored
#    mailbox credentials cannot be decrypted even from a good database dump.
#    It is copied at mode 600 rather than gpg'd against a passphrase stored next
#    to the ciphertext, which would be security theatre: an attacker who can read
#    the backup directory could read the passphrase too. Anything that leaves this
#    host (object storage, a laptop) must be encrypted in transit/at rest there.
install -m 600 "${APP_DIR}/.env" "${BACKUP_DIR}/env_${STAMP}"

# 5) Retention
find "${BACKUP_DIR}/pg" -name 'db_*.dump' -mtime +7 -delete
find "${BACKUP_DIR}" -maxdepth 1 -name 'media_*.tar.gz'          -mtime +28 -delete
find "${BACKUP_DIR}" -maxdepth 1 -name 'template_files_*.tar.gz' -mtime +28 -delete
find "${BACKUP_DIR}" -maxdepth 1 -name 'env_*'                   -mtime +28 -delete

echo "$(date -Is) ok ${STAMP}"
```

This is installed on the production host as `/srv/waypost/bin/waypost-backup.sh`
(mode `750`, owner `deploy`) and driven by that account's crontab:

```cron
40 3 * * *  /srv/waypost/bin/waypost-backup.sh    >> /srv/waypost/backups/backup.log 2>&1
*/5 * * * * /srv/waypost/bin/waypost-probe.sh     >> /srv/waypost/backups/probe.log 2>&1
35 9 * * *  /srv/waypost/bin/waypost-cert-check.sh >> /srv/waypost/backups/cert-check.log 2>&1
```

The minutes are offset from the other application's jobs on the same host (03:15
and 09:30) so two stacks do not spike the disk together. Cron has no login shell,
so each script sets its own `PATH`, `XDG_RUNTIME_DIR` and `DOCKER_HOST`.

`waypost-cert-check.sh` deliberately inspects the certificate that is actually
*served* (`openssl s_client … | openssl x509 -checkend 2592000`) rather than a
file under `/etc/letsencrypt`: that directory is root-only and the deploy account
has no sudo, and the served-certificate check also catches Nginx presenting the
wrong certificate.

The container image itself is **not** backed up: it is reproducible from a git
tag plus `docker/Dockerfile`. What is not reproducible is the database, the two
bind mounts, and `.env`.

### 2c. What the shipped script gates

Two of the steps above are conditional in the repository copy, because both protect a
copy that is about to stop being the only one:

| Key | Default | Effect |
|-----|---------|--------|
| `WAYPOST_MEDIA_IN_OSS` | `0` | While media is on the bind mount the media tar **is** the only copy of every inspection photo, so it runs unchanged. Set `1` only after the OSS backfill has been verified ([`OSS_STORAGE.md`](OSS_STORAGE.md) §8–§9); the script then logs `media tar skipped` and stops making them. |
| `MEDIA_TAR_RETENTION_DAYS` | `28` | How many days of media tars to keep while they are still the only copy. Raising it buys restore points; lowering it below 28 deletes the only copy of any photo newer than the window. |

`template_files/` stays **unconditional**: it is a read-only bind mount the application only ever fills with
copies, it is not going to object storage, and it is not in git.

`.env` is still copied at mode `600` (§2b step 4) with a warning in the script header that matters more than
the retention numbers: that copy is **on the same host as the database**. `DJANGO_FIELD_ENCRYPTION_KEY` has
no off-host copy, so a total host loss takes the key with it and a restored database then decrypts nothing.
The second copy has to be made by hand into a password manager until key governance exists.

> ℹ️ `docker/bin/ci-deploy.sh` already writes a `pre-<tag>-<stamp>.dump`
> checkpoint into `backups/pg/` before every deploy and keeps the newest 14.
> That is the §6.1 release safety net, automated — the cron job above is the
> nightly baseline underneath it.

**Back up before every deploy/migration** as a point-in-time safety net (see [`RELEASE_PROCEDURE.md`](RELEASE_PROCEDURE.md) §6.1). This backup is the only reliable rollback path for releases containing non-reversible data migrations — see [`RELEASE_PROCEDURE.md`](RELEASE_PROCEDURE.md) §9.2.

---

## 3. PostgreSQL Restore (Production)

### 3a. Docker Compose deployment

```bash
cd /srv/waypost/app
docker compose stop app          # nothing may write while you restore

# Restore in place. --clean --if-exists makes it re-runnable; --no-owner avoids
# role-name mismatches between environments.
docker compose exec -T db sh -c 'pg_restore --clean --if-exists --no-owner -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  < /srv/waypost/backups/pg/db_<STAMP>.dump

docker compose start app
curl -s http://127.0.0.1:8000/healthz/
```

To restore into a **scratch** database first (the drill in §8, and the safer path
for anything but a total loss):

```bash
docker compose exec -T db sh -c 'createdb -U "$POSTGRES_USER" waypost_restore'
docker compose exec -T db sh -c 'pg_restore --no-owner -U "$POSTGRES_USER" -d waypost_restore' \
  < /srv/waypost/backups/pg/db_<STAMP>.dump
docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d waypost_restore -c "\dt"'
docker compose exec -T db sh -c 'dropdb -U "$POSTGRES_USER" waypost_restore'
```

Media and templates are host directories, so restore them with `tar xzf` into
`${WAYPOST_DATA_DIR}` and re-apply the `www-data` ACLs (see `DEPLOYMENT.md` §2.3).

**A dump taken with `DB_CHECKPOINT_MODE=external` is not from this container.** Those files in
`/srv/waypost/backups/pg/` were written by `docker/bin/_pg_checkpoint.sh` against the DSN the app container
carries (an RDS instance), so restoring one means `pg_restore` against that host — the `docker compose exec
-T db` forms above would restore into a database the application is no longer using:

```bash
docker run --rm -i --network waypost_default -v /srv/waypost/backups/pg:/in \
  -e PGPASSWORD='<the DATABASE_PASSWORD the app container holds>' postgres:16 \
  pg_restore -h <the DATABASE_HOST the app container holds> -U <DATABASE_USER> -d <DATABASE_NAME> \
  --no-owner --clean --if-exists /in/pre-<tag>-<stamp>.dump
```

Read those four values out of the running container rather than out of `.env`, which is where the checkpoint
got them and why the two can never disagree. The procedure that moves the database to that state is
[`RDS_MIGRATION.md`](RDS_MIGRATION.md).

### 3b. Bare-metal deployment (Option 1)

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
# Nightly at 02:00 (bare-metal path; the compose stack uses the 03:40 crontab in §2b)
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
# Backup (all)         -> /srv/waypost/bin/waypost-backup.sh  (source: docker/bin/waypost-backup.sh)
# DB backup            -> pg_dump -Fc -U waypost_django -h 127.0.0.1 waypost_db > db.dump
# DB restore           -> pg_restore --clean --if-exists --no-owner -U waypost_django -h 127.0.0.1 -d waypost_db db.dump
# Media restore        -> tar xzf media_<STAMP>.tar.gz -C /opt/waypost
# Templates restore    -> tar xzf template_files_<STAMP>.tar.gz -C /opt/waypost
```

---

## Related Documentation

- [`DEPLOYMENT.md`](DEPLOYMENT.md) — server provisioning, Nginx/Gunicorn, basic backup script
- [`DATABASE_MIGRATION.md`](DATABASE_MIGRATION.md) — SQLite → PostgreSQL data migration
- [`RDS_MIGRATION.md`](RDS_MIGRATION.md) — the cutover that changes *which* database §2b/§3 protect
- [`OSS_STORAGE.md`](OSS_STORAGE.md) — §8–§9, the verified backfill that gates the media tar (§2c)
- [`SECURITY.md`](SECURITY.md) — secrets management & credential encryption
- [`RELEASE_PROCEDURE.md`](RELEASE_PROCEDURE.md) — versioning, tagging, deploy sequence, and rollback tiers (§8 Tier 3 restores from this runbook)

---

*Maintainer: Sean Liu (@sean7084)*
