# Deployment Guide - Waypost

This guide covers deploying Waypost to production environments.

---

## Upgrading a pre-rebrand `hengjiams` deployment

The project was renamed from `hengjiams` / HengJi AMS to `waypost` / Waypost. A
server provisioned before this change still carries the old identifiers, so the
rest of this guide will not match it. Apply the following once, in a maintenance
window, **after** taking a `pg_dump`. No schema is touched - this is purely a
rename of the database, role, paths and service units.

```bash
# 1. Stop the app: renaming a PostgreSQL database requires zero connections.
sudo systemctl stop hengjiams

# 2. Rename the database and its owning role.
sudo -u postgres psql -c 'ALTER DATABASE hengjiams_db RENAME TO waypost_db;'
sudo -u postgres psql -c 'ALTER ROLE hengjiams_django RENAME TO waypost_django;'

# 3. Rename the OS account, deploy directory, log directory and backup paths.
sudo usermod -l waypost hengjiams_django 2>/dev/null || sudo usermod -l waypost hengjiams
sudo groupmod -n waypost hengjiams
sudo mv /opt/hengji-ams /opt/waypost
sudo mv /var/log/hengjiams /var/log/waypost
sudo mv /backups/hengjiams /backups/waypost
sudo mv /opt/scripts/hengjiams-backup.sh /opt/scripts/waypost-backup.sh

# 4. Rename the systemd unit and nginx site, then fix the paths inside them:
#    WorkingDirectory/ExecStart -> /opt/waypost, User/Group -> waypost,
#    gunicorn target -> waypost.wsgi:application, alias -> /opt/waypost/...
sudo mv /etc/systemd/system/hengjiams.service /etc/systemd/system/waypost.service
sudo mv /etc/nginx/sites-available/hengjiams /etc/nginx/sites-available/waypost
sudo ln -s /etc/nginx/sites-available/waypost /etc/nginx/sites-enabled/waypost
sudo rm /etc/nginx/sites-enabled/hengjiams

# 5. Update /opt/waypost/.env - DJANGO_SETTINGS_MODULE=waypost.settings,
#    DATABASE_NAME=waypost_db, DATABASE_USER=waypost_django.

# 6. Pull the rebranded code, reinstall, recompile translations, restart.
cd /opt/waypost && git pull
sudo -u waypost /opt/waypost/.venv/bin/pip install -r requirements.txt
sudo -u waypost /opt/waypost/.venv/bin/python compile_translations.py
sudo -u waypost /opt/waypost/.venv/bin/python manage.py collectstatic --noinput
sudo systemctl daemon-reload && sudo systemctl enable --now waypost
sudo nginx -t && sudo systemctl reload nginx

# 7. Update the cron/systemd timer that calls the backup script (new path).
```

Each developer machine needs the same three things: pull the branch (the
`hengjiams/` package is now `waypost/`), update the local `.env`
`DJANGO_SETTINGS_MODULE`, and recreate the venv if it was built with an editable
install. Local SQLite needs no rename - `db.sqlite3` is path-independent.

> ℹ️ **Repository:** the GitHub repository has been renamed to
> `sean7084/Waypost`. GitHub redirects the old URL, but every clone should still
> run `git remote set-url origin https://github.com/sean7084/Waypost.git` so
> pushes do not depend on the redirect.
>
> The old **conda** environment (`HengjiAMS1`) has been removed - development now
> runs entirely on the repo-local `.venv` (`python -m venv .venv`). The only
> lingering conda awareness is `CONDA_PREFIX`, which `waypost/runtime_setup.py`
> still probes as one optional candidate location for the WeasyPrint GTK DLLs;
> it is simply skipped when that variable is unset.

---

## Prerequisites

- **Server**: Ubuntu 24.04 LTS (recommended) or a compatible Linux instance
- **Memory**: Minimum 4GB RAM (8GB recommended)
- **CPU**: 2+ cores
- **Storage**: 20GB+ SSD space
- **Python**: 3.11+ (Ubuntu 24.04 ships 3.12; Django 5.2.8 — see `requirements.txt`)
- **Database**: PostgreSQL 14+ (production). SQLite remains the zero-config default for local development.
- **Cache**: Redis 6+ (production, via `django-redis`). Local development falls back to an in-memory cache.
- **Web Server**: Nginx (serves `/static/` and `/media/` directly)
- **WSGI Server**: Gunicorn

> ✅ **Production dependencies are pinned in `requirements.txt`**: `gunicorn`, `psycopg2-binary`, `whitenoise`, `django-redis`, `redis`. `gunicorn` carries a `sys_platform != 'win32'` marker, so a single `pip install -r requirements.txt` works on both Windows (dev) and Ubuntu (prod) — Windows simply skips gunicorn.

---

## Environment Options

### Option 2: Docker Compose — the production path

`docker/Dockerfile` + `docker-compose.yml` package Gunicorn, the WeasyPrint native
libraries, LibreOffice and the CJK fonts into one image, with PostgreSQL 16 and
Redis 7 as sibling services. This is how the production ECS runs Waypost, and it
is what [`RELEASE_PROCEDURE.md`](RELEASE_PROCEDURE.md) §6 deploys. See the
"Option 2" section below.

### Option 1: Manual Installation

Bare-metal venv + systemd + host PostgreSQL. Still supported, and still the right
choice where Docker is unavailable or where you want the app to run as its own
system service. Note that the two options use **different paths** (`/opt/waypost`
vs `/srv/waypost/app`), so pick one and keep the runbooks consistent with it.

---

## Option 1: Manual Installation Steps

### Step 1: System Setup

```bash
# Update system packages
sudo apt update && sudo apt upgrade -y

# Install dependencies
sudo apt install -y \
    python3.12 python3.12-venv python3-pip \
    postgresql postgresql-contrib \
    nginx redis-server \
    git curl wget build-essential libpq-dev

# Create system user for application
sudo adduser --system --no-create-home waypost
```

### Step 2: PostgreSQL Database Setup

```bash
# Switch to postgres user
sudo -u postgres psql

-- Create database and user
CREATE DATABASE waypost_db;
CREATE USER waypost_django WITH PASSWORD 'your_secure_password_here';
GRANT ALL PRIVILEGES ON DATABASE waypost_db TO waypost_django;
ALTER DATABASE waypost_db OWNER TO waypost_django;
\q
```

### Step 3: Clone Repository

```bash
cd /opt
sudo git clone https://github.com/your-org/waypost.git
sudo chown -R waypost:waypost /opt/waypost
cd /opt/waypost

# Install Python dependencies
python3.12 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip setuptools wheel
pip install -r requirements.txt
```

### Step 4: Configure Environment Variables

The app loads a repo-local **`.env`** file automatically (`waypost/runtime_setup.py::load_local_env`, called by `manage.py`, `wsgi.py`, and `asgi.py`). Create it with the variables that `settings.py` **actually reads**:

```bash
cat > /opt/waypost/.env << EOF
DJANGO_SETTINGS_MODULE=waypost.settings

# Django core (REQUIRED in production)
DJANGO_SECRET_KEY=${RANDOM_SECRET_KEY_GENERATED_HERE}
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=yourdomain.com,www.yourdomain.com
# Encrypts stored mailbox/SMTP credentials at rest (Fernet). REQUIRED when DEBUG=False.
DJANGO_FIELD_ENCRYPTION_KEY=${FERNET_KEY_GENERATED_HERE}

# Database (defaults to SQLite if omitted; set these for PostgreSQL)
DATABASE_ENGINE=django.db.backends.postgresql
DATABASE_NAME=waypost_db
DATABASE_USER=waypost_django
DATABASE_PASSWORD=your_secure_password_here
DATABASE_HOST=localhost
DATABASE_PORT=5432

# Cache (Redis) - production
DJANGO_REDIS_CACHE_URL=redis://127.0.0.1:6379/1

# Minimax RFQ integration (note the LOWERCASE key name)
minimax_token_plan_key=${YOUR_MINIMAX_API_KEY}
MINIMAX_RFQ_API_URL=https://api.minimaxi.com/anthropic/v1/messages
MINIMAX_RFQ_MODEL=MiniMax-M2.7-highspeed
MINIMAX_RFQ_TIMEOUT_SECONDS=30
MINIMAX_RFQ_MAX_TOKENS=800

# Safety override for non-production email testing (leave empty in prod)
TEST_OUTBOUND_EMAIL_OVERRIDE=
EOF
```

> ✅ **Safety guard:** if `DJANGO_DEBUG=False` while `DJANGO_SECRET_KEY` is unset (still the insecure dev fallback), Django raises `ImproperlyConfigured` at startup. This prevents shipping an insecure key. The same applies to `DJANGO_FIELD_ENCRYPTION_KEY`, which encrypts stored mailbox/SMTP credentials (see `accounts/crypto.py`).
>
> 📧 **Email:** outbound email is **not** configured via env vars. It is sent through each user's mailbox settings (`accounts.models.UserMailboxSettings`, stored in the database) with a Django email fallback. There is no `EMAIL_HOST`/`EMAIL_PORT`/etc. in `settings.py`.
>
> 🪟 **WeasyPrint:** `WEASYPRINT_DLL_DIRECTORIES` / `MSYS2_ROOT` are **Windows-only** runtime overrides. On Linux, install the native Pango/GTK libraries instead (see Troubleshooting).

Generate secret key:
```bash
python3.12 -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

Generate field encryption key (Fernet, for stored mailbox/SMTP credentials):
```bash
python3.12 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### Step 4b: Provision Document Templates (Required)

The `template_files/` directory is **excluded from Git** (`.gitignore`), so it must
be copied to the server separately. What each file is actually used for, verified
against the code rather than assumed:

| Expected file | Used by | Behaviour when missing |
|---------------|---------|------------------------|
| `template_files/quotation_template.xlsx` | `quotations/services.py::fill_quotation_template` | Raises `FileNotFoundError`. **That function currently has no callers** — quotation PDFs are rendered from HTML by WeasyPrint (`quotations/views.py::generate_quotation_pdf`), so nothing breaks today. |
| `template_files/签收单 template.xlsx` | `deliveries/services.py::fill_delivery_template`, called from `invoices/services.py::collect_email_attachments` | The delivery Excel/PDF attachments are **silently omitted** from the invoice dispatch email. As of 2026-10 this file is not present in the development copy either — only `签收单 template.pdf` is. Obtain the `.xlsx` from the business owner before relying on invoice dispatch attachments; the omission is now logged as a warning rather than swallowed. |
| `template_files/invoice information template.xlsx` | `invoices/services.py::fill_invoice_template` | Raises `FileNotFoundError`. |

Copy the templates from a secure internal source into the deployment's
`template_files/` directory before first run (`/opt/waypost/template_files/` for
Option 1, `${WAYPOST_DATA_DIR}/template_files/` for Option 2 — the compose file
bind-mounts it read-only). They are **not** distributed with the repository.

### Step 5: Run Migrations

```bash
source .venv/bin/activate
python manage.py migrate
python manage.py collectstatic --noinput
```

> 📦 **Migrating existing data from SQLite?** If your development `db.sqlite3` holds real data that must move to PostgreSQL, follow [`DATABASE_MIGRATION.md`](DATABASE_MIGRATION.md): `dumpdata` → `migrate` → `loaddata` → sequence reset. `collectstatic` above is required for the WhiteNoise manifest storage.

### Step 6: Create Superuser

```bash
python manage.py createsuperuser
# Follow prompts for username, email, and password
```

### Step 7: Configure Gunicorn

Create systemd service file:

```bash
sudo nano /etc/systemd/system/waypost.service
```

Add content:

```ini
[Unit]
Description=Waypost Gunicorn daemon
After=network.target postgresql.service

[Service]
User=waypost
Group=waypost
WorkingDirectory=/opt/waypost
ExecStart=/opt/waypost/.venv/bin/gunicorn \
    --access-logfile - \
    --error-logfile /var/log/waypost/gunicorn-error.log \
    --capture-output \
    --timeout 120 \
    --workers 4 \
    --bind 127.0.0.1:8000 \
    waypost.wsgi:application

Restart=on-failure

[Install]
WantedBy=multi-user.target
```

Enable and start service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable waypost
sudo systemctl start waypost
sudo systemctl status waypost
```

### Step 8: Configure Nginx

Create Nginx config:

```bash
sudo nano /etc/nginx/sites-available/waypost
```

Add content:

```nginx
server {
    listen 80;
    server_name yourdomain.com www.yourdomain.com;
    
    # Static files (from `manage.py collectstatic`). WhiteNoise gives hashed
    # filenames, so they can be cached immutably for a year.
    location /static/ {
        alias /opt/waypost/staticfiles/;
        access_log off;
        expires 1y;
        add_header Cache-Control "public, immutable";
    }
    
    # Media files
    location /media/ {
        alias /opt/waypost/media/;
        expires 30d;
    }
    
    # Proxy to Gunicorn
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
    }
    
    # Security headers
    add_header X-Frame-Options "SAMEORIGIN";
    add_header X-Content-Type-Options "nosniff";
    add_header X-XSS-Protection "1; mode=block";
}

# Redirect HTTP to HTTPS (if SSL enabled)
server {
    listen 443 ssl http2;
    server_name yourdomain.com www.yourdomain.com;
    
    ssl_certificate /etc/letsencrypt/live/yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/yourdomain.com/privkey.pem;
    
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;
    
    # ... same location block as above ...
}
```

Enable site and restart Nginx:

```bash
sudo ln -s /etc/nginx/sites-available/waypost /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx
```

### Step 9: SSL Certificate (Let's Encrypt)

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d yourdomain.com -d www.yourdomain.com
```

Auto-renewal test:

```bash
sudo certbot renew --dry-run
```

### Step 10: Mailbox Sync (In-Process Thread)

Mailbox synchronization runs as an **in-process background thread** — but only
under `runserver`. `accounts/apps.py::AccountsConfig.ready` returns early unless
`sys.argv[1] == 'runserver'` **and** `RUN_MAIN == 'true'`, so:

- Implementation: `accounts/mailbox_sync.py` (`start_mailbox_sync_thread`, `maybe_auto_sync_mailbox`).
- Development (`runserver`): the thread starts once and polls every
  `SYNC_INTERVAL_SECONDS` (≈5 minutes).
- **Production (Gunicorn, with or without Docker): the thread never starts.**
  There is no `run_mailbox_sync` management command either, so nothing polls
  unattended.
- Scope when it does run: only mailboxes with `is_active = true` **and**
  `auto_sync_enabled = true` (`accounts.models.UserMailboxSettings`).

Sync still happens on demand: `accounts/views.py` calls `maybe_auto_sync_mailbox`
when a user opens their mailbox view, so an active user keeps their own mailbox
fresh. What production lacks is *unattended* polling — if the invoice/RFQ flow
must ingest mail with nobody logged in, add a management command and drive it
from cron or a systemd timer. Tracked as a known gap, not a deployment blocker.

> ℹ️ The previous warning about "each Gunicorn worker starting its own thread" no
> longer applies: the `runserver` guard makes duplicate syncing impossible. The
> trade-off is that automatic syncing is off in production entirely.

---

## Option 2: Docker Compose Deployment (production)

This is how the production ECS runs Waypost. Everything the app needs — Gunicorn,
WeasyPrint's native libraries, LibreOffice for xlsx→PDF, and CJK fonts — lives in
the image, so the host needs no Python, no PostgreSQL and no font packages
installed on Waypost's behalf.

| Asset | Purpose |
|-------|---------|
| `docker/Dockerfile` | Multi-stage: `deps` builds the venv, `runtime` adds the native libraries. Asserts at build time that `soffice` exists and that a Chinese string actually renders, so a missing font fails the build instead of shipping empty boxes. |
| `docker-compose.yml` | `app` + `db` (postgres:16) + `redis` (redis:7). At the repository root on purpose — the header comment explains why. |
| `docker/entrypoint.sh` | `migrate` → `collectstatic` → `exec gunicorn`. |
| `docker/nginx/waypost.conf` | Host Nginx site: ACME webroot, `/media/` aliases, proxy headers. |
| `docker/bin/ci-deploy.sh` | The tag-promoted deploy script CI runs over SSH. |
| `.dockerignore` | Keeps `.env`, `media/`, `db.sqlite3` and QA screenshots out of the image. |

### 2.1 Co-hosting on a shared machine

Waypost shares the ECS with another application (Helpdesk). Two properties make
that safe:

1. **Nginx multiplexes 443 by SNI.** Each application gets its own `server`
   block with an exact `server_name`; the public port is not a scarce resource
   and neither app needs a dedicated one. Waypost serves
   `ams.istore-tech.cn` → `127.0.0.1:8000`. The pre-existing site keeps its own
   hostnames and its `default_server` blocks.
   > ⚠️ Never add `default_server` to `docker/nginx/waypost.conf`. Only one
   > server block per listen socket may hold it; a second one fails `nginx -t`
   > with `duplicate default server` and, if it slipped through, would take the
   > other application down.
2. **A dedicated Linux user with its own rootless Docker daemon.** Waypost runs
   under `deploy`, Helpdesk under a different account. Two rootless daemons
   cannot see each other's containers, images, volumes or networks, so a bad
   `docker compose down -v` here cannot reach that stack. `deploy` has no `sudo`
   and is not in any docker group.

### 2.2 Host prerequisites

```bash
# As an administrator, once.
sudo adduser --disabled-password --gecos "" deploy     # NOT in sudo, NOT in docker
grep deploy /etc/subuid /etc/subgid || \
  sudo usermod --add-subuids 165536-231071 --add-subgids 165536-231071 deploy
sudo loginctl enable-linger deploy                     # daemon must survive reboot

# Directories. Code and compose under /srv, runtime data under the home of the
# user whose daemon runs the stack (see 2.3).
sudo mkdir -p /srv/waypost/{app,bin,backups/pg}
sudo chown -R deploy:deploy /srv/waypost
```

Then, **as `deploy`**, install the rootless daemon and configure it *before* the
first start so the log rotation and registry mirrors are in place from the outset:

```bash
mkdir -p ~/.config/docker
cat > ~/.config/docker/daemon.json <<'EOF'
{
  "registry-mirrors": ["https://docker.1panel.live"],
  "log-driver": "json-file",
  "log-opts": { "max-size": "20m", "max-file": "5" }
}
EOF

dockerd-rootless-setuptool.sh install
systemctl --user enable --now docker
docker info | grep -i rootless        # must say rootless: true
docker compose version                # the CLI plugin is system-wide
mkdir -p ~/waypost-data/{media,template_files}
```

If the setup tool complains about cgroup delegation, add
`/etc/systemd/system/user@.service.d/delegate.conf` with
`[Service] Delegate=cpu cpuset io memory pids` and `sudo systemctl daemon-reload`.

### 2.3 File ownership under rootless Docker (the trap)

The container runs as **root inside itself**. Under rootless Docker, container
root maps to the host user that owns the daemon — `deploy` — so files written to
the bind-mounted `media/` appear on the host as `deploy:deploy` and everything
just works.

Running the container as an arbitrary non-zero UID instead is what breaks: that
UID lands in the host's subordinate-ID range, the bind mount shows up inside the
container as `nobody:nogroup`, and uploads fail with permission errors that look
like an application bug. Do not "fix" this by `chown`-ing the host directories to
a UID like `10001`.

```bash
# Nginx (www-data) must be able to read media/ without /home/deploy becoming
# world-readable: traverse permission on the home directory, read on the data.
sudo setfacl -m g:www-data:x /home/deploy
setfacl -R -m g:www-data:rX ~/waypost-data/media
# The *default* ACL is what makes files the container creates LATER readable too.
# It is applied to directories only: a default ACL on a regular file is an error,
# so `setfacl -R -m d:...` over the whole tree aborts part-way.
find ~/waypost-data/media -type d -exec setfacl -m d:g:www-data:rx {} +
```

> ⚠️ **Seeding `media/` from a Windows-produced tarball.** `tar -czf` on Windows
> (bsdtar) records directory modes without the owner write bit, and GNU tar's
> `--no-same-permissions` applies the umask as a *mask* — it cannot add that bit
> back. Extraction as an unprivileged user therefore dies with `Cannot mkdir:
> Permission denied` part-way through. Extract as root (or re-archive on Linux),
> then set ownership and modes explicitly.

### 2.4 Production `.env`

Create `/srv/waypost/app/.env` (mode `600`, owner `deploy`). Compose reads this
one file twice: for `${...}` interpolation and as the container's `env_file`.

```bash
DJANGO_SETTINGS_MODULE=waypost.settings
DJANGO_DEBUG=False

# Fail-fast: the app refuses to boot without these three groups.
DJANGO_SECRET_KEY=<python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())">
DJANGO_FIELD_ENCRYPTION_KEY=<python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())">
WECHAT_MINI_APPID=wx________________
WECHAT_MINI_APPSECRET=________________________________
JWT_SIGNING_KEY=<openssl rand -base64 48>

DJANGO_ALLOWED_HOSTS=ams.istore-tech.cn
# Required behind Nginx, otherwise every POST fails CSRF with 403.
DJANGO_SECURE_PROXY_SSL_HEADER=1
DJANGO_CSRF_TRUSTED_ORIGINS=https://ams.istore-tech.cn

POSTGRES_DB=waypost_db
POSTGRES_USER=waypost_django
POSTGRES_PASSWORD=<openssl rand -base64 24>
WAYPOST_DATA_DIR=/home/deploy/waypost-data

minimax_token_plan_key=<key>
TEST_OUTBOUND_EMAIL_OVERRIDE=

# Internal mirrors: same packages, no public bandwidth cost.
APT_MIRROR=mirrors.cloud.aliyuncs.com
PIP_INDEX_URL=http://mirrors.cloud.aliyuncs.com/pypi/simple/
PIP_TRUSTED_HOST=mirrors.cloud.aliyuncs.com
```

Do **not** set `DATABASE_*` or `DJANGO_REDIS_CACHE_URL`: `docker-compose.yml`
derives the DSN from `POSTGRES_*` and hardcodes the cache URL to the `redis`
service name, so the app and the database container cannot disagree.

> 🔐 `DJANGO_FIELD_ENCRYPTION_KEY` encrypts stored mailbox/SMTP credentials
> (`accounts/crypto.py`). Losing it makes them unrecoverable even from a good
> database backup; changing it invalidates every stored credential. Back it up
> separately per [`BACKUP_RESTORE.md`](BACKUP_RESTORE.md) §6.

### 2.5 Build and start

```bash
sudo -u deploy git clone https://github.com/sean7084/Waypost.git /srv/waypost/app
# .env, template_files/ and media/ are provisioned separately (2.4, Step 4b).

cd /srv/waypost/app
docker compose build --pull app
docker compose up -d
docker compose ps                      # db + redis healthy, app healthy
docker compose logs --tail=100 app     # entrypoint: migrate, collectstatic, gunicorn
curl -s http://127.0.0.1:8000/healthz/ # {"status":"ok","database":"ok","cache":"ok",...}
```

The image is tagged `waypost-app:${TAG:-latest}`; passing `TAG=v0.2.0` makes
`docker image ls` a deploy history and a rollback a re-tag rather than a rebuild.

### 2.6 Nginx and TLS

**Order matters: issue the certificate first.** `nginx -t` fails when
`ssl_certificate` points at a missing file, and this Nginx is shared — a broken
config takes the other application down with it.

1. Decide the validation method by testing it, not by guessing. Create
   `/var/www/letsencrypt-ams/.well-known/acme-challenge/probe`, install the
   `:80` half of the site, and fetch
   `http://ams.istore-tech.cn/.well-known/acme-challenge/probe` from outside.
   - **200 with the probe text** → HTTP-01 with the system certbot into
     `/etc/letsencrypt`, which `certbot.timer` then renews automatically:
     ```bash
     sudo certbot certonly --webroot -w /var/www/letsencrypt-ams \
       -d ams.istore-tech.cn -m <ops-email> --agree-tos --no-eff-email \
       --deploy-hook "systemctl reload nginx"
     ```
   - **403 / 301 / timeout** (this host has a documented history of HTTP-01
     interception on domain names) → DNS-01 instead, and switch the two
     `ssl_certificate*` lines in `docker/nginx/waypost.conf` to the commented
     DNS-01 paths. Renewal is then manual or via AliDNS hooks.
2. Install the site and reload:
   ```bash
   sudo install -m 644 docker/nginx/waypost.conf /etc/nginx/sites-available/waypost
   sudo ln -s /etc/nginx/sites-available/waypost /etc/nginx/sites-enabled/waypost
   sudo nginx -t && sudo systemctl reload nginx
   ```
3. **Prove the other application still works** — this is a shared Nginx:
   ```bash
   curl -s -o /dev/null -w '%{http_code}\n' https://ams.istore-tech.cn/healthz/
   curl -s -o /dev/null -w '%{http_code}\n' https://<the-other-hostname>/   # unchanged
   ```

Static files are served by WhiteNoise inside the container, not by Nginx;
`/media/` is served by Nginx from the bind mount, because Django's `static()`
helper returns nothing when `DEBUG=False`.

### 2.7 Data migration

Follow [`DATABASE_MIGRATION.md`](DATABASE_MIGRATION.md); under Compose the
server-side commands run through the container:

```bash
docker compose up -d db && sleep 5
docker compose cp waypost_data.json app:/tmp/
docker compose exec -T app python manage.py loaddata /tmp/waypost_data.json
docker compose exec -T app python manage.py sqlsequencereset \
  accounts assets companies quotations purchases deliveries invoices \
  inspections products users reports \
  | docker compose exec -T db psql -U waypost_django -d waypost_db
```

Stage the fixture outside the checkout (e.g. `/srv/waypost/incoming/`), not in
`/srv/waypost/app/`: `ci-deploy.sh` runs `git clean -ffd` there on every deploy.

> ⚠️ **Fernet keys do not travel.** A development database encrypts mailbox/SMTP
> credentials with a key derived from `SECRET_KEY` (`accounts/crypto.py::_resolve_key`)
> — and in development that is the public `django-insecure-` fallback committed to
> this repository. Production must use a freshly generated
> `DJANGO_FIELD_ENCRYPTION_KEY`, which means those rows will not decrypt after a
> migration (`decrypt_secret` returns `''` silently, so it looks like a
> mail-server fault). Re-enter the credentials through the UI after cutover, or
> write a one-off re-encryption step. Never ship the development-derived key to
> production: anyone with the repository can compute it.

### 2.8 CI/CD (tag-promoted)

`.github/workflows/deploy-ecs.yml` implements the promotion model from
[`RELEASE_PROCEDURE.md`](RELEASE_PROCEDURE.md): merging to `main` only proves the
image still builds; **production deploys when an annotated tag `v*` is pushed.**

| Trigger | Jobs |
|---------|------|
| `pull_request` | `image-validate` — builds the `deps` stage only (upstream mirrors, not Aliyun: the runners are nowhere near cn-heyuan) |
| `push: tags: v*` | `preflight` (Django checks, `makemigrations --check`, ruff) → `deploy` |
| `workflow_dispatch` | same as a tag push, for redeploys and rollback drills |

The `deploy` job opens an SSH session and runs `/srv/waypost/bin/ci-deploy.sh <tag>`.
The script snapshots the running image as `waypost-app:rollback`, takes a
`pg_dump` checkpoint, checks out the tag, builds, restarts, and gates on
`/healthz/` — first on loopback, then through Nginx. A failed loopback gate
rolls the image back automatically; a failed public gate does **not** (the app is
healthy, the edge is not, and rolling back cannot fix that) but still fails the
run. The database is never restored automatically: see §8/§9 of
`RELEASE_PROCEDURE.md` for why that decision belongs to a human.

**Credentials.** Generate the keypair outside the repository:

```powershell
ssh-keygen -t ed25519 -C waypost-ci-deploy -f "$env:TEMP\waypost_deploy_key" -N '""'
ssh-keyscan -p 49153 <host> > "$env:TEMP\waypost_known_hosts"
```

Install the public half as the *only* entry in `/home/deploy/.ssh/authorized_keys`,
prefixed with `restrict` (no pty, no agent or port forwarding, no user rc):

```bash
sudo install -d -m 700 -o deploy -g deploy /home/deploy/.ssh
sudo install -m 600 -o deploy -g deploy /dev/stdin /home/deploy/.ssh/authorized_keys <<'EOF'
restrict ssh-ed25519 AAAA... waypost-ci-deploy
EOF
```

Then set the repository secrets (`gh secret set <name> --repo sean7084/Waypost`):
`ECS_DEPLOY_HOST`, `ECS_DEPLOY_PORT`, `ECS_DEPLOY_USER`, `ECS_DEPLOY_SSH_KEY`,
`ECS_DEPLOY_KNOWN_HOSTS`. Delete the local private key afterwards.

Finally install the script **outside** the git checkout, so that a broken script
version cannot be shipped by the pipeline that depends on it:

```bash
sudo install -d -m 755 -o deploy -g deploy /srv/waypost/bin
sudo install -m 755 -o deploy -g deploy /srv/waypost/app/docker/bin/ci-deploy.sh /srv/waypost/bin/
```

Re-install it by hand whenever `docker/bin/ci-deploy.sh` changes — or just run
`scripts/deploy-dev-to-ecs.ps1` once, which installs the copy from the ref it is
deploying before running it.

> ⚠️ **Firewall interaction.** GitHub-hosted runners use dynamic IPs. If SSH is
> ever restricted to known source addresses at the security-group or `ufw` level,
> this pipeline stops working — move to a self-hosted runner in that case rather
> than widening the rule.

#### Updating the server from `main` without cutting a release

Merging a PR does **not** deploy anything — only a `v*` tag does. To put merged
work on the host for inspection before deciding to release it:

```powershell
.\scripts\deploy-dev-to-ecs.ps1          # origin/main; shows the diff and asks first
```

It runs the same `ci-deploy.sh` with `--allow-untagged`, builds `waypost-app:dev-main`
and logs the deploy as `DEV_OK` rather than `OK`. It needs an SSH key the `deploy`
account accepts: `/home/deploy/.ssh/authorized_keys` holds two entries — the
`restrict`ed `waypost-ci-deploy` key (GitHub Secrets only, unusable from a
workstation) and `operator-waypost-deploy`, which is the **same ed25519 key already
authorised for `sean`**, so the script needs no `-i` and there is no extra private
key to look after.

> ⚠️ There is one database on this host, so a `main` deploy applies `main`'s
> migrations to production data. The script lists them before asking. Full details
> and the release-vs-verification comparison: `RELEASE_PROCEDURE.md` §6.5.

### 2.9 Backups, logs and rollback

- **Logs:** `docker compose logs -f app` (Gunicorn logs to stdout/stderr; the
  daemon's `json-file` driver rotates at 20 MB × 5). Django's own file handler
  writes to `logs/waypost.log` inside the container and rotates at 10 MB × 5.
- **Backups:** see [`BACKUP_RESTORE.md`](BACKUP_RESTORE.md). Nightly
  `pg_dump` from the `db` container plus the `media/` and `template_files/`
  bind mounts, run as `deploy`.
- **Rollback, fastest first:**
  ```bash
  cd /srv/waypost/app
  docker tag waypost-app:rollback waypost-app:rollback-use   # only if you need to keep it
  git checkout -f <previous-tag>
  TAG=<previous-tag> docker compose up -d --no-build app
  ```
  Schema reversibility decides whether that is enough — `RELEASE_PROCEDURE.md` §8
  classifies it into three tiers.

### 2.10 Post-deploy verification

```bash
curl -s https://ams.istore-tech.cn/healthz/          # 200, database/cache "ok"

# Unprefixed paths 302 to the default language prefix, so follow redirects (-L)
# rather than expecting 200 on the first hop. Measured values:
curl -sL -o /dev/null -w '%{http_code} %{url_effective}\n' \
     https://ams.istore-tech.cn/accounts/login/      # 200 https://.../en-us/accounts/login/
curl -s -o /dev/null -w '%{http_code} -> %{redirect_url}\n' \
     https://ams.istore-tech.cn/login/               # 302 -> https://.../en-us/login/
curl -s -o /dev/null -w '%{http_code} %{size_download}\n' \
     https://ams.istore-tech.cn/zh-cn/accounts/login/  # 200, ~6 KB of HTML
```

> A bare `302` from `/`, `/login/` or `/accounts/login/` is **correct**, not a
> failure: `LocaleMiddleware` redirects to `/en-us/…`. Anything that asserts on
> these URLs must either follow redirects or request a language-prefixed path.
> This trap marked an otherwise healthy deploy `FAILED_EDGE` once already.

Then in a browser: static assets render (an unstyled page means `collectstatic`
did not run or the manifest is stale), **log in and submit any form** — a 403
here means `DJANGO_SECURE_PROXY_SSL_HEADER` or `DJANGO_CSRF_TRUSTED_ORIGINS` is
missing, `/en-us/` and `/zh-cn/` both render, and generating a quotation PDF
shows Chinese glyphs rather than empty boxes.

---

## Production Configuration Checklist

### Security

- ✅ Disable DEBUG mode (`DEBUG=False`)
- ✅ Set strong `SECRET_KEY` (use environment variable, NOT in Git)
- ✅ Restrict `ALLOWED_HOSTS` to actual domains
- ✅ Enable HTTPS everywhere
- ✅ Configure CORS properly
- ✅ Use `require_https` in secure settings

### Database

- ✅ PostgreSQL connection pool settings optimized
- ✅ Read replicas for read-heavy loads (optional)
- ✅ Regular backup schedule configured
- ✅ Failover strategy tested

### Email

- ✅ SMTP credentials secured (not in code)
- ✅ SPF/DKIM/DMARC records configured for domain
- ✅ Outbound email override disabled in production

### Monitoring

- ✅ Logs aggregated (ELK stack, CloudWatch, etc.)
- ✅ Application performance monitoring (New Relic, Sentry)
- ✅ Uptime monitoring (Pingdom, UptimeRobot)
- ✅ Alerting configured (email/slack notifications)

### Maintenance

- ✅ Automated backups scheduled
- ✅ Regular software updates planned
- ✅ Disk space monitoring active
- ✅ Log rotation configured

---

## Backup Strategy

### Database Backups

```bash
#!/bin/bash
# /opt/scripts/db-backup.sh

BACKUP_DIR="/backups/waypost-db"
DATE=$(date +%Y%m%d_%H%M%S)
DB_NAME="waypost_db"

mkdir -p "$BACKUP_DIR"
pg_dump -U waypost_django "$DB_NAME" > "$BACKUP_DIR/$DB_NAME_$DATE.sql.gz"
gzip -9 "$BACKUP_DIR/$DB_NAME_$DATE.sql"

# Keep last 30 days
find "$BACKUP_DIR" -name "*.gz" -mtime +30 -delete
```

Add to cron: `0 2 * * * /opt/scripts/db-backup.sh`

### File Backups

```bash
# Backup static/media files
tar czf "/backups/waypost-files_$DATE.tar.gz" \
    /opt/waypost/staticfiles \
    /opt/waypost/media
```

---

## Troubleshooting

### WeasyPrint Issues on Production

**Symptom**: `Missing native GTK/Pango libraries` error

**Solution**:

```bash
# Ubuntu
sudo apt install -y \
    librsvg2-common libpangoft-2-0 libgtk-3-0 \
    libgdk-pixbuf2.0 libffi-dev shared-mime-info

# Verify installation
python3.12 -c "import weasyprint; weasyprint.HTML(string='<div>Hello</div>').write_pdf('-')"
```

### Every POST returns 403 (CSRF) behind Nginx

**Symptom**: pages render, but submitting any form — starting with login — returns
`403 Forbidden` with `Origin checking failed` in the response body or logs.

**Cause**: Nginx terminates TLS, so Django sees a plain-HTTP request and rebuilds
the expected origin as `http://<host>` while the browser sent
`Origin: https://<host>`.

**Fix**: set `DJANGO_SECURE_PROXY_SSL_HEADER=1` and
`DJANGO_CSRF_TRUSTED_ORIGINS=https://<your-host>` in `.env`, confirm Nginx sends
`proxy_set_header X-Forwarded-Proto $scheme;`, and restart. Trusting that header
is only safe because Gunicorn is bound to the loopback interface.

### Tests fail with "Missing staticfiles manifest entry"

**Symptom**: `manage.py test` produces dozens of errors, each ending in
`ValueError: Missing staticfiles manifest entry for '...'`.

**Cause**: `DJANGO_DEBUG=False` in the environment (or in a repo-local `.env`)
switches `STORAGES["staticfiles"]` to WhiteNoise's manifest storage, which needs
`collectstatic` to have produced `staticfiles.json`.

**Fix**: run tests with `DJANGO_DEBUG=True` — which is exactly why
`.github/workflows/backend-ci.yml` sets it explicitly — or run
`manage.py collectstatic` first. Note that request hardening
(`SECURE_SSL_REDIRECT`, secure cookies) is deliberately disabled under the test
runner via `settings._RUNNING_TESTS`; without that, every view test would assert
against a 301 to `https://`.

### xlsx → PDF conversion returns the Excel file instead

**Symptom**: downloading an invoice or delivery document yields `.xlsx`, with a
"PDF converter not found" warning.

**Cause**: `convert_xlsx_to_pdf` returns `None` when `soffice` is missing, and the
views fall back to the spreadsheet. On a manual (Option 1) install LibreOffice is
not pulled in by `requirements.txt`.

**Fix**: `sudo apt install libreoffice-calc-nogui` and confirm `which soffice`.
The Docker image installs it and fails the build if it is missing. Be aware that
concurrent conversions share one LibreOffice user profile and can fail
intermittently; the fallback path keeps the request succeeding.

### A shell script fails with `/bin/bash^M: bad interpreter`

**Cause**: CRLF line endings, typically from a Windows checkout.

**Fix**: `.gitattributes` pins `*.sh` to `eol=lf`, and the Dockerfile strips any
stray carriage returns as a backstop. For a script already on the host:
`sed -i 's/\r$//' <file>`.

### Database Connection Pool Exhaustion

**Symptom**: `too many connections for role waypost_django`

**Solution**: Adjust PostgreSQL settings:

```sql
-- Check current connection limit
SELECT datname, numbackends, maxconn FROM pg_database WHERE datname='waypost_db';

-- Increase limit (restart PostgreSQL required)
# postgresql.conf
max_connections = 200
```

### Memory Issues

**Symptom**: OOM kills or slow performance

**Mitigations**:
1. Reduce Gunicorn workers: `--workers 2`
2. Increase swap space
3. Optimize database queries (check slow query logs)

---

## Performance Optimization

### Django Settings Adjustments (already applied)

Redis caching and WhiteNoise static handling are **already wired into `settings.py`** (env-driven) and pinned in `requirements.txt`:

- **Cache** — `CACHES` uses `django_redis.cache.RedisCache` when `DJANGO_REDIS_CACHE_URL` is set; otherwise a local-memory cache (development).
- **Static files** — in production (`DEBUG=False`), `STORAGES["staticfiles"]` uses `whitenoise.storage.CompressedManifestStaticFilesStorage` and the WhiteNoise middleware is enabled automatically. Run `collectstatic` on every deploy so the manifest and pre-compressed variants exist.

Optional extra (off by default):

```python
# Response compression - only if you do NOT already gzip at Nginx.
MIDDLEWARE += ['django.middleware.gzip.GZipMiddleware']
```

> Prefer Nginx `gzip on;` (plus WhiteNoise's pre-compressed `.gz`/`.br`) over compressing inside the Django process.

### Query Optimization Tips

1. Use `select_related()` for ForeignKey relations
2. Use `prefetch_related()` for ManyToMany relations
3. Paginate large datasets
4. Avoid N+1 queries (use `explain()` to detect)
5. Add proper database indexes on frequently queried fields

---

## Updates and Upgrades

> 📘 **The authoritative procedure is [`RELEASE_PROCEDURE.md`](RELEASE_PROCEDURE.md).** It covers versioning, tagging, the pre-deploy backup requirement, the full deploy sequence, post-release verification, and the three rollback tiers. The summary below is a quick reference only.

### Applying a Release or Hotfix

```bash
# 0. BACK UP FIRST - the only reliable rollback path (see BACKUP_RESTORE.md §2)
/opt/scripts/waypost-backup.sh

cd /opt/waypost
git fetch --tags origin
git checkout v0.1.8                      # deploy the released TAG, not main

source .venv/bin/activate
pip install -r requirements.txt          # deps before migrate: migrations may import new packages
python manage.py migrate --noinput
python manage.py collectstatic --noinput --clear   # before restart: WhiteNoise manifest storage
sudo systemctl restart waypost
```

> 🚫 **Never run `makemigrations` on the server.** Migrations are source code — they must be generated on a workstation, committed, reviewed, and released. Generating them in production creates untracked schema drift that no tag can reproduce and that silently breaks rollback. Verify instead that the release contains no uncommitted model changes:
>
> ```bash
> python manage.py makemigrations --check --dry-run   # must report "No changes detected"
> ```

### Major Version Upgrade

See `CHANGELOG.md` for per-version migration notes, and [`RELEASE_PROCEDURE.md`](RELEASE_PROCEDURE.md) §9 for which data migrations are reversible. Any release touching `products`, `quotations`, `deliveries`, or mailbox credentials is a **backup-restore-only** rollback (§9.2).

---

## Support & Contact

**Technical Support**: support@istore-tech.com  
**Emergency Escalation**: +86 XXX-XXXX-XXXX  
**Documentation**: https://docs.istore-tech.com  

---

*Last Updated: September 7, 2026*
