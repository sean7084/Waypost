# Release Procedure — Waypost

**Applies to:** Waypost (Django 5.2.8) on Ubuntu 24.04 LTS · Gunicorn + Nginx + PostgreSQL + Redis
**Current released version:** v0.1.7 (May 11, 2026)
**Last Updated:** September 7, 2026

> This document is the authoritative procedure for cutting, deploying, verifying, and **rolling back** a release.
> Server provisioning lives in [`DEPLOYMENT.md`](DEPLOYMENT.md); backup/restore lives in [`BACKUP_RESTORE.md`](BACKUP_RESTORE.md).

---

## 1. Current Reality (verified October 7, 2026)

This procedure is written against what actually exists in the repository today, not against an idealised pipeline.

| Aspect | Verified state | Implication for releases |
|--------|----------------|--------------------------|
| **Version source of truth** | No `__version__` anywhere in Python code. Version appears only in `CHANGELOG.md` headings and `README.md` (Project Progress table + "Current Status") | A release is a **manual, two-file** update. Easy to miss one — see §5.3 |
| **Git tags** | **Zero tags** exist in the repository | `deploy-ecs.yml` deploys on `v*` tags, so **nothing reaches production until the first tag is pushed** (§4). `git describe` and Tier-1 rollback both need tags to exist |
| **CI** | `.github/workflows/backend-ci.yml` (Django checks, `makemigrations --check`, tests + coverage floor, ruff) and `miniprogram-ci.yml`; `deploy-ecs.yml` builds the image on PRs and deploys on tags | Merging is gated. Deployment is automated **from a tag**, not from `main` |
| **Automated tests** | 205 tests (measured 2026-10-07, `manage.py test`), coverage floor 40% enforced in `pyproject.toml`; `assets`/`deliveries`/`purchases` still thinly covered | Test suite passing is **necessary but not sufficient**. Smoke-test the workflow manually (§8) |
| **Branch protection on `main`** | PR required · 1 approval · CODEOWNERS enforced · dismiss stale reviews · `enforce_admins=false` · required status checks: `Django checks + tests`, `Python lint (ruff)` (strict) | A red build blocks the merge. `enforce_admins=false` still lets the owner bypass, so the bypass is a deliberate act |
| **Production runtime** | Rootless Docker Compose on a shared ECS (`app` + `postgres:16` + `redis:7`), Nginx terminating TLS on the host | Deploy = build image + recreate container; the entrypoint runs `migrate` and `collectstatic` (§6.2) |
| **Merge style** | Squash preferred (`CONTRIBUTING.md`), though merge commits also appear in history | One commit per PR on `main` is the intent; tags make the deployed state unambiguous either way |
| **Maintainers** | Single maintainer (`@sean7084`) | No separate release manager; the author deploys |

> ⚠️ **CI gates the merge, not the deploy.** A green build on `main` says nothing
> about whether a tag was cut, and the deploy pipeline will happily ship whatever
> tag you push. The checklists in §7 and §8 remain the quality gate for *release
> content*, and there is still no staging environment (§11).

---

## 2. Versioning Policy

Waypost follows [Semantic Versioning](https://semver.org/) in the `MAJOR.MINOR.PATCH` form, prefixed with `v` in tags and headings.

The project is **pre-1.0** (`0.1.x`). Under SemVer §4, a `0.y.z` version makes **no stability guarantee** — anything may change at any time. This project therefore adds its own binding rules so that version numbers stay meaningful:

| Bump | When | Project-specific rule |
|------|------|-----------------------|
| **MAJOR** (`1.0.0`) | First stable production GA | Reserved. Do not increment until the app has run in production without a rollback for a sustained period |
| **MINOR** (`0.1.7` → `0.2.0`) | A delivered feature iteration | **Any release containing new migration files is at least a MINOR bump.** This is the hard rule — migrations change the schema and therefore the rollback story (§9) |
| **PATCH** (`0.1.7` → `0.1.8`) | Bug fixes, documentation-only changes, template/UI tweaks | **Must contain zero new migrations** and must be backward-compatible with the currently deployed schema |

Additional conventions:

- **No pre-release suffixes** (`-rc.1`, `-beta`) are currently used. If you introduce them, they sort *before* the final release and must not be deployed to production.
- Each release corresponds to **one merged PR series** and one "Delivered Scope" block in `CHANGELOG.md`. The historical pattern (v0.1.3 … v0.1.7) is a single themed iteration per release.
- Version numbers are **never reused**. If a release is pulled, the next release still increments.

---

## 3. Release Types

| Type | Trigger | Bump | Deploys with downtime? |
|------|---------|------|------------------------|
| **Feature release** | Planned iteration merged to `main` | MINOR | No — but migrations may lock tables briefly |
| **Patch release** | Non-urgent bug fix | PATCH | No |
| **Hotfix release** | Production is broken; fix cannot wait | PATCH | No — expedited path (§10) |

---

## 4. Tag Naming & Tagging Policy

**Format:** `v` + full version, matching the `CHANGELOG.md` heading exactly.

```
v0.1.8      ✅ correct
0.1.8       ❌ missing v prefix
v0.1.8-hotfix  ❌ no suffixes
```

**Tag type:** **annotated** (`git tag -a`), never lightweight. Annotated tags store the tagger, date, and a message, and are the objects that GitHub releases attach to.

**Tag message:** the `**Focus:**` line from the CHANGELOG entry.

```bash
git tag -a v0.1.8 -m "Separate service catalog adoption, direct-dispatch fulfillment"
```

**Tag target:** the **squash-merge commit on `main`** that completed the release — i.e. tag after merging, not before.

### Back-tagging historical releases

**Decision: do not back-tag.** The repository has no tags for v0.0.1 … v0.1.7, and the squash-merge history does not contain a reliable, unambiguous commit that corresponds to each historical release boundary (several releases predate the current PR workflow). Guessing would create tags that misrepresent what was deployed.

Instead:

- Historical releases are identified by their `CHANGELOG.md` entry and release date.
- Tagging **begins with the next release**. From that point forward every release is tagged.
- If a known-good commit is later identified for v0.1.7, tag it then — but only with evidence.

---

## 5. Cutting a Release

Work on a release branch following the established convention `<type>/<issue-number>-<slug>` (e.g. `docs/15-release-procedure`, `fix/13-encrypt-mailbox-credentials`). For a release-prep branch use `chore/<version>-release`.

### 5.1 Pre-flight

```bash
git checkout main
git pull --ff-only origin main
```

Confirm:

- [ ] All PRs intended for this release are **merged**
- [ ] `git status` is clean
- [ ] Working tree matches `origin/main`

### 5.2 Decide the version

Apply §2. The deciding question is: **does this release add migration files?**

```bash
# List migrations added since the last release tag (or since a known commit)
git diff --name-only --diff-filter=A v0.1.7..HEAD -- '*/migrations/*.py'
```

Any output ⇒ MINOR bump. No output ⇒ PATCH is permitted.

### 5.3 Update CHANGELOG.md

`CHANGELOG.md` currently uses **five different heading formats** accumulated over 12 releases:

```
## Release Notes v0.1.7                                  ← modern, use this
## Waypost - Release Note v0.0.1
## Release Notes and Progress Report for Version 0.0.2
## Waypost - Changelog v0.0.3
## Version 0.0.4
```

Entries are also **not consistently ordered** (v0.1.7 and v0.1.6 sit at the top, followed by v0.0.1 ascending through v0.1.5).

**Mandated format for all new entries** — prepend at the top of the file, directly under the `# Waypost - Changelog` title:

```markdown
## Release Notes v0.1.8

**Version:** 0.1.8
**Release Date:** September 15, 2026
**Focus:** <one-line theme of the release>

---

### Highlights

1. <user-visible outcome, not implementation detail>
2. ...

### Delivered Scope (N-file iteration)

1. <theme group>
- <specific change>

### Migration Files Added

1. `<app>/migrations/<NNNN>_<name>.py`

*(Write "None." for a PATCH release with no migrations.)*

### Validation

- <what was actually run/verified>
```

Do not reformat or reorder the historical entries as part of a release PR — that is separate cleanup work and would obscure the release diff.

### 5.4 Update README.md

Two places carry the version, and **both** must be updated:

1. **"Last three updates"** table — add the new row at the top, drop the oldest so three remain.
2. **"Current Status (vX.Y.Z)"** heading — update the version in the heading, and add newly-shipped items to the *Fully Operational Features* list.

Also update, if applicable:

- **"vX.Y.Z Migration Notes"** section — list the new migration filenames and any manual steps (the v0.1.7 block is the model).
- **App Responsibilities** table — if an app's purpose changed.

### 5.5 Verify locally

```bash
python manage.py check
python manage.py makemigrations --check --dry-run   # must report "No changes detected"
python manage.py test
```

`makemigrations --check --dry-run` failing means **uncommitted model changes exist** — migrations were not generated and committed. Fix before releasing; never let the server generate them (§6.3).

### 5.6 Open the PR, merge, tag, push

```bash
git checkout -b chore/0.1.8-release
git add CHANGELOG.md README.md
git commit -m "chore(release): prepare v0.1.8"
git push -u origin chore/0.1.8-release
```

Open the PR against `main` using [`.github/PULL_REQUEST_TEMPLATE.md`](../.github/PULL_REQUEST_TEMPLATE.md); check **Documentation update**, and reference the release issue.

After the squash merge:

```bash
git checkout main
git pull --ff-only origin main
git tag -a v0.1.8 -m "<Focus line from CHANGELOG>"
git push origin v0.1.8
```

Optionally publish a GitHub Release from the tag, using the CHANGELOG entry as the body.

---

## 6. Deploying a Release

### 6.1 Before you touch the server

- [ ] **Take a backup.** This is non-negotiable and is the *only* reliable rollback path (§9 Tier 3).
      Run `/opt/scripts/waypost-backup.sh` per [`BACKUP_RESTORE.md`](BACKUP_RESTORE.md) §2.
- [ ] Confirm the backup succeeded and note its `<STAMP>` — you will need it if you roll back.
- [ ] Read the release's **Migration Files Added** section. Are any of them in the `noop`-reverse list (§9.2)? If so, this release is a **one-way door** — decide *now* that rollback means DB restore, not migration reversal.
- [ ] Confirm `template_files/` is present on the server (gitignored, but document generation raises `FileNotFoundError` without it — `DEPLOYMENT.md` Step 4b).

### 6.2 Deploy sequence

Pushing the tag **is** the deploy: `deploy-ecs.yml` runs `preflight` and then
SSHes to the host to run `docker/bin/ci-deploy.sh <tag>`. Watch the run, then
verify (§7).

```bash
git checkout main && git pull --ff-only origin main
git tag -a v0.1.8 -m "<Focus line from CHANGELOG>"
git push origin v0.1.8            # <- this triggers the production deploy
gh run watch                      # or: the Actions tab
```

What the host-side script does, in order — knowing this is what makes a failed
run diagnosable:

1. Alias the running image as `waypost-app:rollback` (the rollback target).
2. `pg_dump -Fc` checkpoint into `/srv/waypost/backups/pg/` (§6.1, automated).
3. `git fetch --tags && git checkout -f <tag> && git clean -ffd` — **never
   `-x`**, because `.env` is gitignored and must survive.
4. `docker compose build --pull app && docker compose up -d`. The container
   entrypoint then runs `migrate --noinput` and `collectstatic --noinput --clear`
   before exec'ing Gunicorn, which preserves the ordering this section used to
   spell out by hand: dependencies and schema first, static files before traffic.
5. Health gate on `http://127.0.0.1:8000/healthz/`, then on the public URL.

A failed loopback gate rolls the image back automatically and exits non-zero. A
failed *public* gate does not roll back — the app is healthy and the edge
(Nginx/certificate/DNS) is not, which a rollback cannot fix — but it still fails
the run and prints the diagnostics.

<details>
<summary>Option 1 (bare-metal venv + systemd) — the manual equivalent</summary>

Order matters. Deviating causes avoidable outages.

```bash
cd /opt/waypost

# 1. Fetch the release tag (NOT "git pull origin main" — deploy the tag you released)
git fetch --tags origin
git checkout v0.1.8

# 2. Dependencies FIRST — new migrations may import new packages
source .venv/bin/activate
pip install -r requirements.txt

# 3. Schema — apply committed migrations
python manage.py migrate --noinput

# 4. Static files BEFORE restart — production uses WhiteNoise
#    CompressedManifestStaticFilesStorage; restarting first means templates
#    reference assets missing from the not-yet-rebuilt manifest => 500 errors.
python manage.py collectstatic --noinput --clear

# 5. Restart the application
sudo systemctl restart waypost

# 6. Verify
sudo systemctl status waypost --no-pager
```

</details>

### 6.3 Two anti-patterns to avoid

> 🚫 **Never run `makemigrations` on the server.**
> `DEPLOYMENT.md` §"Applying Hotfixes" currently includes `python manage.py makemigrations` in its command list. **That is incorrect and should be removed.** Migrations are source code: they must be generated on a workstation, committed, reviewed, and released. Generating them on the server produces untracked schema drift that no tag can reproduce and that silently breaks rollback.
>
> 🚫 **Never deploy `main` directly — deploy the tag.** `main` may already contain commits for the *next* release. Deploying a tag is what makes "redeploy the previous release" a well-defined operation.

### 6.4 Environment changes

If the release introduces new settings, `settings.py` will fail fast on boot rather than run degraded — production guards raise `ImproperlyConfigured` when `DJANGO_DEBUG=False` and either `DJANGO_SECRET_KEY` is still the insecure dev fallback or `DJANGO_FIELD_ENCRYPTION_KEY` is unset.

So: **check the release notes for new `.env` variables before restarting.** A boot failure with `ImproperlyConfigured` almost always means a missing env var, not a code defect. Update `.env` (and its encrypted backup per `BACKUP_RESTORE.md` §6), then restart again.

---

## 7. Post-Release Verification Checklist

Run immediately after deploy. Everything here is manual until CI exists (issue #17).

**Boot & config**

- [ ] `docker compose ps` → `app`, `db`, `redis` all `Up`/`healthy`, no restart loop
      *(Option 1: `sudo systemctl status waypost` → `active (running)`)*
- [ ] `docker compose logs --tail=80 app` → entrypoint ran `migrate` and
      `collectstatic`, Gunicorn booted, no tracebacks
      *(Option 1: `tail -n 50 /var/log/waypost/gunicorn-error.log`)*
- [ ] `curl -s https://<host>/healthz/` → `200` with `database` and `cache` both
      `"ok"`, and `version` equal to the tag just deployed
- [ ] `docker compose exec -T app python manage.py showmigrations` → all boxes ticked `[X]`

**HTTP**

- [ ] `/login/` loads over HTTPS with a valid certificate
- [ ] Static assets render (CSS/JS present — a bare unstyled page means `collectstatic` failed or Nginx `alias` is wrong)
- [ ] `/api/v1/` returns the DRF browsable root for an authenticated session
- [ ] `/admin/` loads

**Language**

- [ ] Both `/en-us/dashboard/` and `/zh-cn/dashboard/` render, and Chinese strings are not mojibake

**Core workflow smoke test** (the highest-value check — this path spans 6 apps and is largely untested)

- [ ] Log in as an order-management user; **2FA** prompt appears and accepts a TOTP code
- [ ] Dashboard shows the workflow board and the pending-tasks badge
- [ ] Open an existing **quotation** → generate/download its **PDF** (exercises WeasyPrint + `template_files/`)
- [ ] Open an existing **delivery order** → generate/download its **PDF**
- [ ] **Mailbox sync**: opening a user's mailbox view triggers
      `maybe_auto_sync_mailbox` and completes without error. Note that the
      background polling thread is `runserver`-only, so in production nothing
      syncs unattended (see `DEPLOYMENT.md` Step 10)
- [ ] Create a throwaway quotation, then delete it — confirms write paths and permissions

**Rollback readiness**

- [ ] You know the previous tag (`git describe --tags --abbrev=0 v0.1.8^`)
- [ ] You know the backup `<STAMP>` taken in §6.1

Record the verification result (who, when, outcome) in the release PR or the CHANGELOG *Validation* section.

---

## 8. Rolling Back a Bad Release

**Stop and classify first.** The correct action depends entirely on whether the bad release changed the schema.

```
Is production actively harming data or users?
├─ YES → take the site down first:  sudo systemctl stop waypost
│        (a clean outage beats silent corruption), then work Tier 2/3
└─ NO  → proceed to classify

Did the release add migration files?  (§5.2 / CHANGELOG "Migration Files Added")
├─ NO  → Tier 1: code-only rollback           (minutes, no data risk)
└─ YES → Are those migrations in the noop-reverse list (§9.2)?
         ├─ NO  → Tier 2: reverse migrations  (possible, with care)
         └─ YES → Tier 3: restore from backup (the ONLY safe path)
```

### Tier 1 — Code-only rollback (no schema change)

Applies to any PATCH release, and to a MINOR release whose migrations are reversible *and* already judged unnecessary to undo.

```bash
cd /opt/waypost
source .venv/bin/activate

git fetch --tags origin
git checkout v0.1.7                      # previous known-good tag
pip install -r requirements.txt          # in case deps changed
python manage.py collectstatic --noinput --clear
sudo systemctl restart waypost
```

Then re-run the §7 checklist. No database action is required.

### Tier 2 — Reverse migrations

Only when **every** migration being reversed has a real reverse function (§9.1).

```bash
# 1. BACK UP AGAIN — reversing migrations is itself a destructive operation
/opt/scripts/waypost-backup.sh

# 2. Check out the previous tag first, so the OLD migration graph is what runs
git checkout v0.1.7

# 3. Reverse to the specific prior migration, per app
python manage.py migrate products 0003
python manage.py migrate deliveries 0001

# 4. Never use `migrate <app> zero` unless you intend to destroy that app's tables
```

Cautions:

- Reverse **in dependency order** — reverse the app that depends on another *first*.
- A migration with a real reverse still may not restore *semantically* identical data (e.g. a role migration reverses to a role that no longer matches current business rules).
- If any migration in the range is `noop`-reverse, **stop**. Go to Tier 3.

### Tier 3 — Restore from backup

The only safe path when the release contains a `noop`-reverse migration, and the fallback for everything else.

Follow [`BACKUP_RESTORE.md`](BACKUP_RESTORE.md) §3 (PostgreSQL restore), then §5 (media + `template_files/`), then:

```bash
git checkout v0.1.7
pip install -r requirements.txt
python manage.py migrate --noinput        # reconciles any migrations newer than the backup
python manage.py collectstatic --noinput --clear
sudo systemctl restart waypost
```

Restore `.env` from its encrypted backup **only if** the encryption key changed — see the warning below.

> 🛑 **The Fernet one-way door (`accounts/migrations/0019_encrypt_mailbox_passwords_with_fernet.py`).**
> This migration re-encrypted every stored mailbox/SMTP password from legacy XOR to Fernet. Its reverse is deliberately a no-op — the source states: *"Reverting to insecure XOR obfuscation is intentionally not supported."*
>
> Consequence: **you cannot roll back to any code version older than v0.1.6-era `0019`.** The old code calls `_xor_secret_restore()` on values that are now Fernet tokens, producing garbage — every mailbox and SMTP credential silently fails authentication, and the failure looks like a mail-server problem rather than a rollback problem.
>
> If you must run pre-`0019` code, users will have to **re-enter all mailbox credentials** afterwards. Treat `0019` as a permanent point of no return and prefer forward-fixing over rolling back past it.
>
> Related: `DJANGO_FIELD_ENCRYPTION_KEY` is now **mandatory** when `DEBUG=False`. A rollback that restores an older `.env` lacking this key will refuse to boot (`ImproperlyConfigured`). Keep the current key.

---

## 9. Migration Rollback Reference

Twelve data migrations use `RunPython`. Their reversibility differs, and this determines whether Tier 2 is available.

### 9.1 Real reverse functions (reversible)

| Migration | Forward ↔ Reverse |
|-----------|-------------------|
| `accounts/0008_update_roles_to_it_administrator` | `update_roles` ↔ `reverse_update_roles` |
| `accounts/0009_migrate_roles_to_it_administrator` | `update_user_roles` ↔ `reverse_user_roles` |
| `accounts/0012_admin_roles_m2m` | `seed_admin_roles` ↔ `unseed_admin_roles` |
| `accounts/0012_admin_roles_m2m` | `migrate_user_roles_forward` ↔ `migrate_user_roles_reverse` |
| `accounts/0016_split_order_management_role` | `forwards` ↔ `backwards` |
| `companies/0011_migrate_location_legacy_values` | `migrate_location_legacy_values` ↔ `reverse_migrate_location_legacy_values` |

Reversing these *does* undo the data change — but verify the outcome anyway; a reverse is only as good as the assumptions it was written against.

### 9.2 `noop` reverse (schema reverts, **data does not**)

| Migration | What the forward pass did | Effect of reversing |
|-----------|---------------------------|---------------------|
| `accounts/0011_normalize_language_preference_codes` | Rewrote language preference codes | Old codes are **not** restored; users keep new codes that old code may not recognise |
| `accounts/0017_normalize_blank_employee_ids` | Normalised blank employee IDs | Blank values **not** restored |
| `accounts/0019_encrypt_mailbox_passwords_with_fernet` | XOR → Fernet re-encryption | **One-way door** — see §8 warning. Mailbox credentials break under old code |
| `deliveries/0002_..._service_lines` | Normalised the retired `prepared` status | Rows already migrated **stay** on their new status; only the schema choice returns |
| `products/0004_migrate_service_prices_to_service_items` | Created `ServiceItem` rows from legacy service `AssetModel`s and repointed prices | `ProductPrice` rows would point at a **dropped** `service_item` column — prices are effectively lost. **Backup restore required** |
| `quotations/0007_backfill_quotationitem_service_item` | Backfilled `QuotationItem.service_item` | Backfilled links **lost**; quotation line items revert to hardware-only resolution |

> **Rule of thumb:** any release touching `products`, `quotations`, `deliveries`, or mailbox credentials is a Tier-3-only rollback. Take the backup seriously.

### 9.3 Going forward

New data migrations should either:

1. Provide a genuine reverse function, **or**
2. Explicitly pass `migrations.RunPython.noop` with a docstring stating why reversal is unsupported (the `0019` pattern — good practice, it makes the one-way door discoverable).

Silently omitting the second argument is the worst option: `migrate` raises `NotSupportedError` mid-rollback, at the exact moment you are under pressure.

---

## 10. Emergency Hotfix Flow

When production is broken and cannot wait for a normal cycle:

1. **Stabilise first.** If data is being corrupted, `sudo systemctl stop waypost`. Decide between rolling back (§8) and fixing forward — a rollback is usually faster and safer than a hotfix written under pressure.
2. Branch from the **deployed tag**, not from `main`:
   ```bash
   git checkout -b fix/NN-short-slug v0.1.8
   ```
   Branching from `main` risks shipping unreleased work alongside the fix.
3. Make the minimal change. Add a regression test if the bug is reproducible in a test.
4. PR → review → squash merge. Branch protection allows the owner to merge with `enforce_admins=false`, but **do not skip writing the PR description** — it is the incident record.
5. Bump PATCH, add a CHANGELOG entry (the *Validation* section should state what was verified on production), tag, deploy per §6.
6. If the hotfix branch was cut from a tag, merge it back into `main` so the fix is not lost at the next release.
7. Write up the incident. If monitoring/alerting exists by then (issue #22), confirm it fired; if it did not, record that as a gap.

---

## 11. Known Gaps Blocking a Fully Automated Release

These are tracked as open issues and are **not** solved by this document:

| Gap | Issue | Effect today |
|-----|-------|--------------|
| No pytest/coverage tooling; thin coverage of `assets`/`deliveries`/`purchases` | #16 | Resolved in part: pytest + coverage are configured and CI enforces a 40% floor, but a low floor still gives little assurance for `assets` (2,230-line `views.py`) and `deliveries` |
| No linter/formatter configured | #21 | Resolved in part: ruff runs in CI with a correctness-only rule set and is a required check. `ruff format` is still deliberately not enforced |
| Mailbox sync never runs unattended in production | #24 | The background thread is `runserver`-only by design (`accounts/apps.py`), so invoice/RFQ mail is only ingested when a user opens their mailbox view. Needs a management command + timer |
| No staging environment | — | Releases are verified **in production**. This is the single largest risk in the current process |
| No notification channel for a failed deploy | — | A red `deploy-ecs.yml` run is only seen if someone looks at the Actions tab. The host writes `/srv/waypost/backups/deploy.log`, but nothing pages anyone |
| First tag not yet cut | — | The CD pipeline is tag-triggered and the repository has no tags, so it has never run against production. The first release must prove it end to end, including a rollback drill |

> ✅ **Resolved since this document was first written:** CI now exists and both
> `Django checks + tests` and `Python lint (ruff)` are required status checks on
> `main`; a `/healthz/` endpoint gives the deploy pipeline and monitoring a cheap
> probe instead of loading real pages; `DEPLOYMENT.md` no longer instructs
> operators to run `makemigrations` on the server or to `git pull origin main`.

---

## Related Documentation

- [`DEPLOYMENT.md`](DEPLOYMENT.md) — server provisioning, Gunicorn/Nginx, environment variables
- [`BACKUP_RESTORE.md`](BACKUP_RESTORE.md) — backup, restore, and the verification drill (Tier 3 rollback depends on it)
- [`DATABASE_MIGRATION.md`](DATABASE_MIGRATION.md) — SQLite → PostgreSQL data migration
- [`SECURITY.md`](SECURITY.md) — secrets management and the Fernet credential-encryption standard
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — branch/commit conventions and the PR checklist
- [`GITHUB_SETTINGS.md`](GITHUB_SETTINGS.md) — branch protection rules in force on `main`
- [`ARCHITECTURAL_DECISION_RECORDS.md`](ARCHITECTURAL_DECISION_RECORDS.md) — ADR-0008 (PDF generation paths) affects release verification
- `../CHANGELOG.md` — the release history this procedure appends to

---

*Maintainer: Sean Liu (@sean7084)*
