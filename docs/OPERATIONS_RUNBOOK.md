# Operations Runbook — Waypost

**Status:** consolidated from what is actually deployed and actually configured. Every
"monitoring" claim below is either implemented or explicitly marked **not implemented**.
Issue #22 asked for this document; the gap it closes is that troubleshooting was scattered
across `DEPLOYMENT.md` and `WORKFLOW_GUIDE.md` while the monitoring checklist was aspirational.

**Scope:** the Waypost application on ECS `i-f8z9x44twyxnq0w6j1pf`. That host also runs
**Helpdesk** under a second rootless Docker daemon, sharing the disk, the nginx 443 socket and
the public IP. Anything that fills the disk, restarts nginx or exhausts memory takes **both**
applications down. Host-level facts (accounts, cron, certificates, security groups) live in the
ops knowledge base, `instance/waypost.md` and `instance/operations.md`; this document does not
duplicate them.

---

## 1. First five minutes of an incident

Run these in order. Each one narrows the problem before you read a log.

```bash
# 1. Is the app itself healthy? (DB + cache only — see §3 for what it does NOT check)
curl -sS https://ams.istore-tech.cn/healthz/

# 2. What did the last deploy do, and did it take a restore point?
tail -5 /srv/waypost/backups/deploy.log

# 3. Are the containers up, and restarting?
docker ps --format '{{.Names}}\t{{.Status}}'

# 4. Is the disk full? This is the failure that kills both applications.
df -h /
du -sh /srv/waypost/backups/

# 5. Only now, logs (§2 tells you which log actually has the answer)
docker logs --tail 100 waypost_app
```

`/healthz/` returns `200` with `{"status":"ok","database":"ok","cache":"ok",…}` or `503` with
`"degraded"` and the failing dependency's **exception class name** — not a message
(`waypost/health.py:45-87`). It is exempt from the HTTPS redirect
(`waypost/settings.py:593`) and sent with `Cache-Control: no-store`, so a stale 200 cannot hide
an outage. `version` and `revision` come from `WAYPOST_VERSION` / `WAYPOST_GIT_SHA`; empty
strings mean the deploy did not set them, not that the app is old.

---

## 2. Where the signals are

| Signal | Location | Caveat that will waste your time if you miss it |
|---|---|---|
| Application log | `/app/logs/waypost.log` **inside the container** | **Not bind-mounted.** Compose mounts only `media` and `template_files` (`docker-compose.yml:83-91`), so a rebuild loses it. Copy it out before recreating the container. `RotatingFileHandler`, INFO, 10 MB × 5 (`waypost/settings.py:615-625`) |
| App loggers | `django` INFO, `assets` DEBUG, `accounts` DEBUG (`settings.py:631-650`) | The `accounts` logger is recent: credential-decryption warnings and RFQ/LLM metering land here. Without it they reached only stderr |
| Gunicorn access/error | `docker logs waypost_app` | `--capture-output` sends both to stdout (`docker/Dockerfile:169-176`); kept by the rootless daemon's json-file rotation, 20 MB × 5 |
| **Mailbox sync output** | `journalctl --user -u waypost-mailbox-sync.service` | The timer runs `docker exec … sync_mailboxes --once`, so the command's own output goes to the **systemd journal**, not to `docker logs`. A sync that never ran looks identical to a sync that ran quietly unless you check the journal |
| Deploy history | `/srv/waypost/backups/deploy.log` | **Six** tab-separated columns: timestamp, kind (`tag` / `untagged-ref` / `unresolved`, `+tarball` suffix when the codeload fallback was used), tag/ref, sha, result (`OK` / `DEV_OK` / `FAILED` / `FAILED_EDGE` / `ABORTED`), checkpoint path or `none` (`docker/bin/ci-deploy.sh:201-217`) |
| Nightly backup | `/srv/waypost/backups/backup.log` | Retention: pg dumps 7 days, media/env tars 28 days (`docker/bin/waypost-backup.sh:86-91`) |
| External probe | `/srv/waypost/backups/probe.log` | `waypost-probe.sh` every 5 min, host-only script |
| Certificate expiry | `/srv/waypost/backups/cert-check.log` | `waypost-cert-check.sh` daily 09:35, checks the **served** certificate via `openssl s_client`, WARN under 30 days |
| Disk / memory | `/srv/helpdesk/backups/disk-check.log` | Helpdesk's `disk-check.sh`, every 10 min, disk ≥ 85% or `MemAvailable` < 512 MB. Host-wide, so it covers Waypost's disk too |
| nginx access/error | host defaults under `/var/log/nginx/` | `docker/nginx/waypost.conf` sets **no** `access_log`/`error_log`, so there is no per-site log to grep |
| Certbot | `/var/log/letsencrypt/letsencrypt.log` | |

**Nothing above pages anyone.** Every one of these is a file a human must go and read. See §4.

---

## 3. Troubleshooting matrix

Symptom → most likely cause → the check that confirms it → the fix. Cross-links point at the
document that already carries the detail; this table is the index, not a second copy.

| Symptom | Likely cause | Confirm | Fix |
|---|---|---|---|
| `/healthz/` 503, `database` not `ok` | DB down, connection exhaustion, or wrong DSN after a cutover | `docker ps` (is `waypost_db` healthy?), `docker logs waypost_db` | `DEPLOYMENT.md:1075-1088` (connection exhaustion); after an RDS cutover check `DATABASE_HOST` + `DB_CHECKPOINT_MODE` in `.env` (`RDS_MIGRATION.md` §5) |
| `/healthz/` 503, `cache` not `ok` | Redis down or `DJANGO_REDIS_CACHE_URL` wrong | `docker exec waypost_redis redis-cli ping` | Redis is cache-only (`--save "" --appendonly no`), so restarting it loses nothing durable |
| Deploy refuses to start, "cannot take a checkpoint" | The pre-deploy `pg_dump` failed on a host that has been deployed before | `deploy.log` last row; the message names the reason | `ci-deploy.sh:566-609`. Reasons: empty dump, missing `postgres:16` image, app container not running (`_pg_checkpoint.sh:138-140,183-185,210-217`). **Do not** reach for `ALLOW_MISSING_CHECKPOINT=1` if the release carries a one-way-door migration (`RELEASE_PROCEDURE.md` §9.2) |
| Deploy red before it touches anything, "runner drift" | The host's `/srv/waypost/bin/*.sh` differ from this tag's `docker/bin/*.sh` | The failure message prints the exact install command | Re-dispatch the workflow with `reinstall_runner=true`; `ALLOW_RUNNER_DRIFT=1` is the incident escape hatch (`RDS_MIGRATION.md:274-287`). **Expected on the first deploy after any `docker/bin` change** |
| Deploy fails at `git fetch`, `curl 28`, connect timeouts | github.com **apex** unreachable from the host while `api.` and `codeload.` work | `curl -sS -o /dev/null -w '%{http_code}' https://api.github.com` | The codeload tarball fallback handles it automatically and refuses to deploy unless the extracted tree hash matches the tag's tree SHA (`ci-deploy.sh:163-199,231-244`) |
| `deploy.log` says `FAILED_EDGE` | Healthy locally, broken through nginx/TLS from outside | Compare `curl` from the host against `curl` from your workstation | `ci-deploy.sh:646-675`. **No automatic rollback** — this is a human decision |
| Login page 302-locales you in a loop | Locale prefix + `next` interaction | Reproduce with `curl -I` and follow the `Location` chain | `ci-deploy.sh:653-657`, `DEPLOYMENT.md:814-817` |
| Every POST returns 403 | CSRF / proxy-SSL headers | Browser devtools: is `csrftoken` present, is `X-Forwarded-Proto` https? | `DEPLOYMENT.md:1023-1035`, `waypost.conf:130-133` |
| PDF download returns an **xlsx** instead | `soffice` missing, conversion timed out (120s), or a concurrent LibreOffice profile clash | `docker exec waypost_app command -v soffice`; app log for "PDF converter not found" | `DEPLOYMENT.md:1053-1065`. `convert_xlsx_to_pdf` returns `None` and never raises, so **dispatch silently omits the PDF** — see ADR-0020 |
| PDF generation raises `OSError: cannot load library 'gobject-2.0-0'` | WeasyPrint's GTK/Pango runtime missing (usually a Windows dev box) | Run through `manage.py`, which bootstraps the runtime | `DEPLOYMENT.md:1007-1021`, `TESTING.md` §2 |
| Document generation raises `FileNotFoundError` | `template_files/` not provisioned (gitignored, required) | `ls /home/deploy/waypost-data/template_files/` | `DEPLOYMENT.md` Step 4b (`:211`). The missing `签收单 template.xlsx` makes dispatch WARN and omit that attachment (`invoices/services.py:498-507`) |
| Mailbox authenticates to nothing; looks like an IMAP/SMTP outage | The stored credential cannot be decrypted (rotated `DJANGO_FIELD_ENCRYPTION_KEY`, corruption) | `docker exec waypost_app python manage.py check_encrypted_secrets` | Exits 1 only on `unreadable`; a `legacy` row that still decrypts is a working mailbox. Re-enter the credential in the UI (`accounts/crypto.py:90-107`, `RELEASE_PROCEDURE.md:541-548`) |
| Customer emails all arrive in one inbox | `TEST_OUTBOUND_EMAIL_OVERRIDE` set **and** `DEBUG=True` | Check both in `.env` | Now impossible in production: the override is honoured only when `DEBUG` is true and defaults to empty (`accounts/email_backends.test_outbound_override`) |
| RFQ AI costs nothing / no classification happens | No poller installed, or no API key, or the monthly budget is exhausted | `journalctl --user -u waypost-mailbox-sync.service`; `manage.py rfq_llm_usage --by-mailbox` | Install `docker/systemd/waypost-mailbox-sync.{service,timer}`; an empty key means no request is ever attempted; budget exceeded degrades to the heuristic by design |
| Two syncs per interval, duplicated mail | The `runserver` gate was removed, so each of the 3 gunicorn workers polls | `docker logs waypost_app` — count sync lines per 5 minutes | Restore the gate; polling belongs in the timer (`accounts/apps.py:11-24`, ADR-0017) |
| "Another mailbox sync holds the lock; nothing done" | A previous pass is still running, or a stale Redis lock | Journal timestamps; `--max-runtime 240` bounds a pass | Usually benign. If it never clears, inspect the `waypost:mailbox-sync:lock` cache key |
| App does not come up after a deploy, migration error | `inspections/0007`'s `AddConstraint` hit existing duplicate `(location, inspection_date, slot)` rows | The traceback names the constraint | Run `merge_duplicate_inspections` (dry run first) — this is a **pre-release** step, `RELEASE_PROCEDURE.md` §9.2b |
| Disk filling; both applications die | Daily full `media` tars × 28 days on a shared system disk | `du -sh /srv/waypost/backups/`, `ls /srv/waypost/backups/media_*.tar.gz \| wc -l` | KB `instance/waypost.md` §7. The durable fix is media in OSS (`WAYPOST_MEDIA_IN_OSS=1` turns the tar off); shortening retention is the free stopgap |
| Boot fails with `ImproperlyConfigured: OSS is half-configured` | Some but not all four `OSS_*` keys set | Read `.env` | Set all four or none (`waypost/settings.py:475-484`, `OSS_STORAGE.md`) |
| Thumbnails missing on old photos | Derivatives are created on upload; pre-change photos have none | `manage.py generate_photo_thumbnails --dry-run` | Backfill with `--all`; `thumbnail_url` falls back to the original until then |
| Session list page errors with `NoReverseMatch` | `accounts/urls.py:44` uses `<int:pk>` while `UserSession.pk` is a UUID | Reproduce by opening the page | **Open defect**, recorded in the backlog (ADR-0013) |

---

## 4. Monitoring: what exists, and what does not

**Implemented**

- `/healthz/` — DB `SELECT 1` + a cache write/read-back. **No storage check, no outbound
  network call**, so it stays cheap and cannot fail because a third party is down
  (`waypost/health.py:45-87`).
- Container `HEALTHCHECK` every 30s against `/healthz/` (`Dockerfile:160-161`); compose
  healthchecks for db (`pg_isready`) and redis (`ping`), with the app gated on both being
  healthy (`docker-compose.yml:92-96,125-145`).
- Host cron: external probe (5 min), served-certificate expiry (daily), disk/memory (10 min,
  Helpdesk's script, host-wide). All write **local log files only**.

**Not implemented — do not report these as coverage**

- **No error tracking.** No `sentry_sdk` in `requirements.txt` or settings.
- **No CloudMonitor alarm rules and no site monitor.** The agent is installed; the rules are
  still pending (KB `instance/operations.md` §2.2).
- **No notification channel of any kind.** No email, SMS, webhook or chat alert fires on a
  failed deploy, a failed sync, a full disk or an expiring certificate. A red
  `deploy-ecs.yml` run is seen only if someone opens the Actions tab.
- **No uptime probe from outside the host** other than the host's own `waypost-probe.sh`,
  which cannot detect the host being unreachable.
- `check_encrypted_secrets` is deliberately **not** wired into `/healthz/`: it is an audit, and
  putting a DB aggregate on a public endpoint would cost more than it informs.

The ✅ "Monitoring" checklist in `docs/DEPLOYMENT.md:951-956` (ELK / Sentry / Pingdom /
alerting) is **aspirational — none of it exists**. Treat it as a wishlist, not a state.

---

## 5. Incident response and escalation

There is no on-call rotation and no paging channel; the project is solo-maintained. The
procedure is therefore written to be followed by one person under stress.

1. **Declare the blast radius first.** If the disk is full, nginx is down, or memory is
   exhausted, **Helpdesk is affected too**. Say so before debugging Waypost.
2. **Stop the bleeding, then diagnose.** For a bad release that is a rollback
   (`RELEASE_PROCEDURE.md` §8/§9); for a full disk it is deleting the oldest media tars —
   after confirming a pg dump exists, because those tars may be the only copy of the photos.
3. **Preserve evidence before recreating anything.** `logs/waypost.log` lives in the container's
   writable layer and a rebuild destroys it (§2). Copy it out first.
4. **Record the incident in the repository.** The hotfix flow is: branch from the deployed tag,
   fix, PR — the PR is the incident record (`RELEASE_PROCEDURE.md:636-650`). There is no
   separate incident tracker.
5. **Check the one-way doors before rolling back.** `RELEASE_PROCEDURE.md` §9.2/§9.2b classify
   every migration; a `noop` reverse or a data-cleanup migration means the only real rollback
   is a database restore, and `v0.2.0` is one of them.
6. **Escalation is a human decision, not a configured path.** If the host is unreachable or
   both applications are down, the Aliyun console (Cloud Shell, existing RAM authorization) is
   the out-of-band route; the KB's `instance/` documents carry the account-level facts.

**Never** run `docker compose down -v` on this stack: `waypost_pg_data` is the pre-migration
database (`docker-compose.yml:149-153`).

---

## 6. Commands: safe, and destructive

Run inside the app container: `docker exec waypost_app python manage.py <command>`.

| Command | Purpose | Risk |
|---|---|---|
| `check_encrypted_secrets` | Audit mailbox/SMTP credentials: `total / fernet / legacy / unreadable / empty` | Read-only. Exit 1 only on `unreadable` |
| `rfq_llm_usage --by-mailbox --json` | LLM spend by month/mailbox | Read-only |
| `sync_mailboxes --once --max-runtime 240` | One mailbox pass (what the timer runs) | Writes mail rows; takes the cross-process lock |
| `merge_duplicate_inspections` | Report duplicate `(location, date, slot)` rounds | **Dry run by default.** `--apply` **irreversibly deletes** the losing rows — a pre-release requirement, not a tidy-up |
| `cleanup_orphan_photo_zips` | Sweep orphaned deliverables | **Deletes files.** Has `--dry-run` |
| `generate_photo_thumbnails --dry-run / --all` | Backfill photo derivatives | Writes objects; `--dry-run` first |
| `import_kering_master --dry-run` | Master-data import with `ImportRun` tracking | Writes rows; dry run first |
| `import_helpdesk --dry-run` | Legacy helpdesk → Hongwen data merge (natural-key upserts, `HONGWEN_LEGACY_DSN`) | **Dry run rolls back.** Real run upserts; re-runnable. Opens the legacy source read-only. Runbook: `DEPLOYMENT.md` §2.15 |
| `reuse_historical_photos` | Copy a prior round's photos into a new inspection | **Net-additive** unless `--prune-superseded`, which deletes the superseded round |
| `group_unbatched_inspections` | Batch assignment | Writes rows |
| `setup_demo_admins`, `assets create_sample_assets`, `seed_service_cities` | Demo/seed data | **Never on production** |

Import rollback is **not** a management command: it is `utils/import_rollback.py:107`
(`rollback_run`) over `companies.ImportRun`, exercised through the views. The worked example
is `scripts/manual_verify_import_rollback.py` (`TESTING.md` §7).

**Backup and restore.** Nightly: `docker/bin/waypost-backup.sh`. Per-deploy restore point:
`docker/bin/_pg_checkpoint.sh`, sourced by both so they cannot protect two different databases.
Restore procedure, including the `external` (RDS) case via a throwaway `postgres:16` container:
`BACKUP_RESTORE.md:174-217`. Stop the app → `pg_restore --clean --if-exists --no-owner` → start
→ `/healthz/`.

⚠️ **`DJANGO_FIELD_ENCRYPTION_KEY` has no off-machine copy.** Once media is in OSS the backup
products are a pg dump and `.env`, and that key is the one thing that is neither regenerable nor
stored anywhere else. Losing it makes every encrypted credential already in the database
permanently unreadable.

---

## 7. Hongwen co-tenancy and Helpdesk retirement

The standalone FastAPI **Helpdesk** is merged into Waypost as **Hongwen (泓问)** (PR1–7):
models, services, customer API + SSE, admin pages and a native WeChat client all ship
inside this application. The legacy stack (`helpdesk_app` + `helpdesk_db` under sean's
rootless daemon, nginx → `127.0.0.1:8090`) is a **co-tenant in retirement**, not a
dependency. The cutover itself — certificate SAN, nginx deploy, the frozen-window data
import and its reconciliation — is [`DEPLOYMENT.md`](DEPLOYMENT.md) §2.15; this section
is the operational aftermath.

**While both stacks run (the retention week):**

- The legacy app is **stopped** after the migration window (`docker stop helpdesk_app`,
  as sean). Its nginx site still answers `admin./api.helpdesk.istore-tech.cn` with 502
  while stopped — that is expected, not an incident. Restarting it is the rollback.
- Waypost serves `ams.istore-tech.cn` **and** `waypost.istore-tech.cn` from one site
  file and one certificate (both SANs, HTTP-01, auto-renewed). The helpdesk wildcard
  certificate remains manual DNS-01 — do not let the two renewal stories mix.
- The legacy data (`/home/sean/helpdesk-rootless-data-v3/{uploads,kb_sources,tts}`,
  the `postgres_data` volume, `/srv/helpdesk/backups/pre-merge-*.dump`) is the rollback
  artifact. **Nothing is deleted during the retention week.**
- The temporary `helpdesk_legacy` database inside `waypost_db` (the import source) can
  be dropped once the reconciliation is signed off: `docker compose exec -T db dropdb
  -U waypost_django helpdesk_legacy`. The pre-merge dump, not this copy, is the artifact.

**Retirement (≥ 1 week after the client releases, root, ONE change):**

1. 🔴 **The helpdesk site owns the 80/443 `default_server` (bare IP).** Removing
   `/etc/nginx/sites-enabled/helpdesk` removes that default with it. The same edit
   **must** give Waypost an explicit default fallback, or bare-IP requests silently
   land on whichever block nginx picks first. Add `default_server` to the two
   `listen` lines of the waypost site's :80 and :443 blocks **in the same change**
   (this is the one moment the "no default_server" rule in `docker/nginx/waypost.conf`
   is deliberately lifted — update that comment when you do), or install a dedicated
   catch-all that 404s/redirects. Never remove the helpdesk site without this.
2. `sudo nginx -t` **before** reload; then regression-probe all names: ams, waypost,
   bare IP (now Waypost's default), and confirm the helpdesk names no longer resolve
   to a live backend (DNS records can stay; they will hit the default).
3. Stop and disable the legacy containers (as sean): `docker compose -f
   /srv/helpdesk/app/docker-compose.yml stop` — **not** `down -v`; the volume and the
   data directories are retained. Disable sean's helpdesk cron probes
   (`cert-check.sh`, `app-probe.sh`) so they stop alerting on a deliberately dead app;
   keep `disk-check.sh` (host-wide).
4. Update the host probe/monitoring to `https://waypost.istore-tech.cn/healthz/`
   (ams keeps working until the inspection client's re-release has propagated).
5. Data deletion (legacy volumes, `helpdesk-rootless-data-v3`, old backups) is a
   **separate, later, dated decision** — not part of retirement. The pre-merge dump
   plus the Hongwen rows are then the only copies; verify the Hongwen side first.

**Rollback within the retention week:** restore/re-enable the helpdesk nginx site
(it was never edited during the cutover), `sudo nginx -t && sudo systemctl reload
nginx`, `docker start helpdesk_app` (sean), probe `https://admin.helpdesk.istore-tech.cn/admin/login`
→ 200. The legacy data is exactly pre-merge (the app was frozen before the dump).
Waypost keeps running; the two systems diverge from the freeze point, so decide which
is authoritative before accepting new writes on both. Full command blocks:
[`DEPLOYMENT.md`](DEPLOYMENT.md) §2.15 Phase 6.

---

## 8. Known gaps this runbook cannot close

These are infrastructure or process gaps, tracked in `RELEASE_PROCEDURE.md` §11: **no staging
environment** (releases are verified in production), **no notification channel**, and **a real
rollback has never been drilled** — the image-rollback path was drilled with a deliberately bad
tag, but loading a database restore point has not been. Drill the `external` checkpoint path
once, deliberately, before the cutover release (§9.4 of that document).

---

## Related

- [`DEPLOYMENT.md`](DEPLOYMENT.md) — install, configuration, the detailed failure sections
- [`RELEASE_PROCEDURE.md`](RELEASE_PROCEDURE.md) — versioning, deploy, rollback, one-way doors, §11 gaps
- [`BACKUP_RESTORE.md`](BACKUP_RESTORE.md) — backup layers and restore drills
- [`RDS_MIGRATION.md`](RDS_MIGRATION.md) — the database cutover and the runner reinstall
- [`OSS_STORAGE.md`](OSS_STORAGE.md) — the media cutover, lifecycle rule, backfill
- [`ARCHITECTURAL_DECISION_RECORDS.md`](ARCHITECTURAL_DECISION_RECORDS.md) — ADR-0014 (2FA),
  ADR-0015 (mail paths), ADR-0017 (stack), ADR-0018 (settings guards), ADR-0020 (PDF paths)
- [`TESTING.md`](TESTING.md) — how to run the suite and what the coverage floor does not cover
- Ops knowledge base: `instance/waypost.md`, `instance/operations.md`, `instance/backup-dr.md`

---

*Last Updated: November 19, 2026*