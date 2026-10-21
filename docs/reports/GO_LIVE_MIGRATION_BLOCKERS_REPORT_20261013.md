# Go-Live Migration Blockers Report — 2026-10-13

**Scope:** the application-side half of the pre-go-live migration (media to Aliyun OSS, database to
Aliyun RDS PostgreSQL, RFQ model switch). Code-level blockers, effort estimates and ordering.

**Companion document:** the infrastructure half — cloud service sizing, unit prices, monthly and
three-year cost, phasing triggers and risk register — lives in the **Aliyun ops knowledge base** at
`account/GO_LIVE_COST_ESTIMATE_20261012.md` (repo `aliyun_i-f8z9x44twyxnq0w6j1pf`). It is not
linked from here because that knowledge base is not published. Section references below use its
numbering (§3.2, §5, …).

**Why this report is separate from the cost estimate:** the cost estimate is an account-level
procurement document that outlives any one commit, whereas everything below cites `file:line` in
this repository and goes stale the moment the code moves. That is not hypothetical — while the cost
estimate was being written, this project's package was renamed `hengjiams` → `waypost`
(`settings.py:292` → `:375`) and every citation had to be re-verified. Keeping code-level facts in
the repository puts them next to the code that invalidates them.

**Citation basis:** all line numbers verified against `origin/main` @ `ccadb1e` (the PR #95 merge
commit) on 2026-10-13, using `git grep -n <pattern> ccadb1e -- <path>` rather than the working tree.
Re-run `git rev-parse origin/main` before acting on any of them — this report has already been
re-anchored twice in one day (`8723786` → `5f05dfe` → `ccadb1e`), which is the argument in the
previous paragraph made concrete.

> **Resolution status (2026-10-20).** Blockers **5** (no client-side image compression) and **7**
> (`photo_zip` stores every photo twice) — effort items **1** and **12** — are **resolved** on branch
> `feat/go-live-media-cost-reduction` (issues #97, #98, #99). Item 12 was pulled forward from its
> Phase 2 trigger gate by request.
>
> - **Blocker 5:** `miniprogram/utils/image.js` (`pickPhotos`) now runs
>   `chooseMedia({sizeType:['compressed']})` → `getImageInfo` → `compressImage` (quality 80, 1920 px
>   long edge) → `saveFile` at both capture sites (`device-verify.js`, `rack-network.js`), targeting
>   ~300 KB/photo. Guarded by `MiniprogramCompressionTests` in `tests/test_repo_hygiene.py` so a raw
>   `wx.chooseMedia` in a page fails CI.
> - **Blocker 7:** the `photo_zip` column is dropped in `inspections/0006_remove_photo_zip`; archives
>   stream on demand via `bundle_export.build_bundle` (web `GET /inspections/{id}/photos.zip/`, API
>   `GET /api/v1/inspections/{id}/photo-bundle/`). `POST …/report/` no longer returns a `photo_zip`
>   key. Single-store downloads move from flat `Photo/…` to the per-store `{store}/Photo/…` layout.
> - **Related finding (not previously numbered):** `FieldFile.save()` orphaned the prior
>   `report_file`/`photo_zip` on every regeneration (one dev store had 3 ZIPs + 3 XLSX). Fixed by
>   deleting before re-saving; `cleanup_orphan_photo_zips` sweeps the existing orphans.
>
> The remaining OSS blockers (1–4, 6, 8) are untouched and still gate the storage migration; landing
> blocker 7 first removes one `.path` site (`inspections/tests.py`) from blocker 2's test rework. The
> `file:line` citations below remain anchored to `ccadb1e` and were **not** re-verified against the
> fix branch.

> **Re-verification and landing status (2026-10-21).** Every row of §1 and §2 was re-checked against the
> tree, on the assumption that a citation is only useful if it is right. **The tables below are not
> edited** — `docs/reports/README.md` says a report is a point-in-time snapshot, and this block is the
> honourable form of that rule.
>
> **Citations still correct, unchanged:** blockers 1, 3, 4, 8, 9, 9b, 10, 11, 12, 13, 14 and 15 still
> describe what the code did, at the line numbers given. That is a statement about this report's
> accuracy, **not** about what is still open — the last paragraph records which of them the branches
> below close. Blocker 9's silent-skip half had already been fixed by PR #95, and blocker 7's
> orphaned-`FieldFile.save()` sibling by commit `2aeca68`.
>
> **Wrong in this report, as of its own citation basis `ccadb1e`:**
>
> - **Blocker 2 line numbers.** The five `.path` uses in `inspections/tests.py` are at
>   `192, 265, 298, 466, 595`, not `192, 234, 255, 423, 552`. The count of five was right.
> - **Blocker 6 line numbers.** The eight classes pinning `MEDIA_ROOT` are
>   `inspections/tests.py:69, 176, 314, 433, 489, 557`, `api/tests_miniprogram.py:159` and
>   `inspections/tests_frontend.py:71`. Again the count of eight was right, the lines were not.
> - **Blocker 4's remedy.** "`requirements.txt` has no `django-storages` / `oss2` … one must be added" is
>   true, but `django-storages` 1.14.6 ships **no Aliyun OSS backend** (apache_libcloud, azure, dropbox,
>   ftp, gcloud, s3, sftp). The backend had to be written against `oss2` directly.
> - **§2's effort total.** Items **1** (thumbnail pipeline) and **12** (in-repo backup script) are
>   delivered, so the pending total is ≈**19.0**, not 22.0 — Phase 1 ≈15.0, Phase 2 ≈4.0.
> - **Storage-volume arithmetic.** "1.2 Gbps", "3.3 months to fill the disk", "47% of the monthly storage
>   **increment**" (should be **ceiling**), §2's "cannot be cut" argument, §8's three Phase-2 triggers and
>   §9's disk-fill projection all moved with capture-side compression and the thumbnail derivative. Those
>   are account-level figures and were corrected in the ops knowledge base, not here.
> - **Backup script path.** `/opt/scripts/waypost-backup.sh` is not where production's backup lives: the
>   runner is `/srv/waypost/bin/waypost-backup.sh`, now installed from the in-repo
>   `docker/bin/waypost-backup.sh`. The path is right in `docs/RELEASE_PROCEDURE.md` and §2b of
>   `docs/BACKUP_RESTORE.md`; `docs/DEPLOYMENT.md`'s "Backup Strategy" now says so at its top, and the
>   remaining `/opt/scripts/` blocks in `docs/BACKUP_RESTORE.md` §2/§7 are left as written because they
>   are the explicitly-labelled bare-metal shape.
> - **Tracked strays.** `db.sqlite3` was never a stray — it is gitignored. The two tracked ones were
>   `debug_roles.py` (deleted: a print dump of role attributes, owned by nothing) and
>   `tmp_manual_verify_import_rollback.py` (moved to `scripts/manual_verify_import_rollback.py`, because
>   `docs/TESTING.md` §7 calls that import/rollback path safety-critical and this was the only worked
>   example of it). `RepoRootStrayTests` now refuses either prefix at the root.
> - **The (store, inspection_date) "unique key" premise is wrong in both directions.** No database
>   constraint existed, and the application-level dedup that did exist was keyed on
>   `(location, inspection_date)` with `slot` in `defaults` only — so a schedule that booked one store
>   in both the morning and the afternoon resolved the second row onto the first, never applied its
>   slot, and silently attached its devices to the wrong round. The invariant is **not** "one
>   inspection per store per day": `docs/reports/INSPECTIONS_ITERATION2_COMPLETION_REPORT_20260924.md`
>   records that two visits per day are legitimate, the day view buckets by `(date, am)`/`(date, pm)`
>   and the bundle export names one folder per slot. It is promoted to
>   `uniq_store_inspection_location_date_slot` on `(location, inspection_date, slot)`, the importer key
>   gains `slot`, and `merge_duplicate_inspections --dry-run` — which must run against production
>   **before** that migration deploys — groups by the same three columns so a healthy AM/PM pair is
>   never reported as a duplicate.
>
> **Landing status as of this writing.** Delivered as four stacked local branches —
> `feat/rfq-llm-cost-guards` (blockers 13–15, commit `167204c`), then `feat/oss-storage-backend` with
> blockers 1–4 and 6–8 (`517fce4`), then `feat/rds-checkpoint-and-compose-dsn` with 9, 9b, 10, 11 and 12
> (`3e407e2`), then `feat/small-defects-and-doc-sync` (this branch) with the `(location,
> inspection_date, slot)` invariant, its slot-aware `merge_duplicate_inspections` cleanup and the
> importer dedup-key fix, the company-API 500→403
> fix, credential-decryption observability, the two tracked strays and this block. Blocker 12 is
> delivered as a **gate, not a deletion**: `docker/bin/waypost-backup.sh` skips the media tar when
> `WAYPOST_MEDIA_IN_OSS=1` rather than dropping the step, because until the bucket exists that tar is
> the only copy of the photos. **Nothing has been pushed, merged or deployed**, and no Aliyun resource
> exists yet: the OSS and RDS cutovers remain operator actions, which is why every change here is gated
> on an environment value and production behaviour is unchanged until `.env` is edited.

---

## 1. Code-level blockers

### 1.1 OSS storage migration

| # | Blocker | Location | Why it blocks |
| --- | --- | --- | --- |
| 1 | `FileField.path` used to get a local filesystem path | `invoices/services.py:482`, `invoices/services.py:497` | `.path` raises `NotImplementedError` on an OSS backend; both sites also feed `Path(...).exists()` |
| 2 | Same, in tests | `inspections/tests.py:192,234,255,423,552` | 5 uses of `.path`; the suite goes red the moment the backend changes |
| 3 | Writes bypass the storage API entirely | `quotations/services.py:108`, `invoices/services.py:330`, `deliveries/services.py:215` | `Path(settings.MEDIA_ROOT) / ... + mkdir(parents=True)`. On an OSS backend these documents land on the container's local disk and are invisible outside it |
| 4 | The backend switch point itself (good news) | `waypost/settings.py:375-377` | Django 5.x `STORAGES` API; `default` is currently `FileSystemStorage`, so this is a **single-point change**. `requirements.txt` has **no** `django-storages` / `oss2` / `boto` — one must be added |
| 5 | No client-side image compression | `miniprogram/pages/device-verify/device-verify.js:168`, `miniprogram/pages/rack-network/rack-network.js:63` | Neither `wx.chooseMedia` call passes `sizeType`, and there is no `wx.compressImage`, so phone originals (2.5–5 MB) are uploaded. This single fact drives both the "1.2 Gbps" bandwidth figure (cost estimate §3.2) and the "3.3 months to fill the disk" cliff (§5) |
| 6 | Tests assume a local filesystem | `inspections/tests.py:69,176,271,390,446,514`, `api/tests_miniprogram.py:159`, `inspections/tests_frontend.py:71` | **8** `@override_settings(MEDIA_ROOT=...)` test classes; they need a temp/in-memory storage or a mock |
| 7 | `photo_zip` stores every photo twice | `inspections/services/report_generator.py:271` (`_build_photo_zip`), `:320-321` (`.save()`) | 47% of the monthly storage increment. `inspections/services/bundle_export.py:build_bundle` already streams an equivalent archive on demand and can replace it |
| 8 | Nginx `/media/` and the ACL chain are retired | `docker/nginx/waypost.conf:103-111` (`alias` + `expires 7d/30d`) | Both locations and the `setfacl` grant chain documented in the ops KB go away. But the **`expires` semantics must be re-created as `Cache-Control` on the OSS objects** (cost estimate §2.3), otherwise repeat views re-download and the egress estimate is wrong |

### 1.2 RDS migration

| # | Blocker | Location | Why it blocks |
| --- | --- | --- | --- |
| 9 | The pre-deploy checkpoint only knows the compose `db` service | `docker/bin/ci-deploy.sh:542` (the `grep -qx db` gate), `:567-568` (its `else` branch) | **The silent half was fixed by PR #95, merged 2026-10-13.** `checkpoint_missing()` (`:523`) now tells a genuine first deploy (`DEPLOYED_BEFORE=0`, `:518-521`) from a re-deploy, catches a `pg_dump` that exits 0 having written an empty file (`:547-556`), and `die`s unless `ALLOW_MISSING_CHECKPOINT=1` (`:90`). What the RDS migration inherits is the *consequence*: once the `db` service leaves compose, `DEPLOYED_BEFORE` is 1, so **every deploy dies at `:568`**. The script already names the right fix ("If the database has moved off this compose stack, point the checkpoint at the new DSN rather than silencing this", `:537-538`), so `ci-deploy.sh` must learn to `pg_dump` the RDS endpoint. Leaving `ALLOW_MISSING_CHECKPOINT=1` set permanently is not an alternative: the schema contains one-way-door migrations (`accounts/0017`, `accounts/0019` have `noop` reverses) and `docs/RELEASE_PROCEDURE.md` §9.2 assumes this checkpoint is real |
| 9b | Editing `ci-deploy.sh` does **not** take effect on its own | host `/srv/waypost/bin/ci-deploy.sh` | Tag-triggered CD only SSHes in and runs the script **already on the host**; the script deliberately lives outside the checkout because it runs `git checkout` / `git clean` against it. So PR #95's fix, and the DSN change blocker 9 still needs, reach production **only after the runner is reinstalled by hand** (or via `scripts/deploy-dev-to-ecs.ps1`, which reinstalls from the target ref) — otherwise the deployed host keeps running the old script that fails silently. Counted in §2 item 6 |
| 10 | The DSN is hardcoded in compose | `docker-compose.yml`, `app.environment.DATABASE_HOST: db` | The file's own comment says "Do not also set `DATABASE_*` in .env - these entries override it". Migrating to RDS therefore **requires editing compose** (drop the `db` service, change `DATABASE_HOST`/`DATABASE_PORT`); editing `.env` alone has no effect |
| 11 | The pg volume must be exported before removal | `docker-compose.yml`, `volumes.waypost_pg_data` | Removing the `db` service does not delete the volume, but `docker compose down -v` does. The order must be dump → verify row counts → only then `down` |
| 12 | The backup script targets the container's pg | host `/srv/waypost/bin/waypost-backup.sh` (`docker compose exec -T db pg_dump -Fc`) | After migration it must either `pg_dump` against the RDS internal endpoint from the ECS, or rely on RDS's own backups plus periodic download. Its `media` tar step should be **deleted outright** — OSS versioning replaces it, and keeping it re-stores the same bytes on the system disk for 28 days (cost estimate §5) |

### 1.3 RFQ / LLM

| # | Blocker | Location | Why it blocks |
| --- | --- | --- | --- |
| 13 | Production does no mailbox polling, so AI spend is currently ¥0 | `accounts/apps.py:12-18` (the `runserver` + `RUN_MAIN` double gate), `docker/Dockerfile:169-171` (gunicorn `--workers 3`) | Adding polling by simply removing that gate starts a loop in **every** worker: AI cost ×3 and duplicate email processing ×3, because `_SYNC_LOCKS` in `accounts/mailbox_sync.py:22` is a process-local dict and gives no cross-worker mutual exclusion. It must be a dedicated process (management command + systemd timer) |
| 14 | No observability on LLM usage | `accounts/rfq_ai.py:341-379` (`_call_minimax_json`) | The response's `usage` field is never parsed, so "how many tokens did we spend this month" is unanswerable and no budget guard can be built |
| 15 | The heuristic is computed but not used as a gate | `accounts/rfq_ai.py:670-671` | `_heuristic_classification(message)` runs, then `_call_minimax(message)` is called unconditionally. Turning the heuristic into a real pre-filter is the only change that reduces AI call volume by an order of magnitude (cost estimate §4.3.4) |

> ℹ️ Two corrections to the list this was derived from, both verified against `ccadb1e`:
>
> 1. **There are 2 `wx.chooseMedia` call sites, not 3.** `miniprogram/pages/signoff/signoff.js` does
>    not pick photos — its two signature images come from canvas `exportPad()` (`:54`, `:110`,
>    `:114`) and are queued via `saveLocal()`, so the "uncompressed original" problem does not apply
>    there. Signature volume is already counted at 50 KB each.
> 2. **The project package was renamed.** Older checkouts have `hengjiams/settings.py`; `ccadb1e`
>    has `waypost/settings.py`, with line numbers shifted (`STORAGES` moved 292 → 375). Any note or
>    document predating the rename has unusable paths and line numbers.

---

## 2. Effort

| # | Work item | Phase | Person-days | Cost |
| --- | --- | --- | --- | --- |
| 1 | Mini program photo compression (add `sizeType:['compressed']` at the 2 `chooseMedia` sites + `wx.compressImage` before upload; fix quality and long-edge cap) | 1 | 1.5 | _day rate TBD_ |
| 2 | OSS storage backend (new dependency, `STORAGES['default']` switch, private bucket + signed URLs, 4 new `.env` keys, RAM policy) | 1 | 2.0 | |
| 3 | Blockers 1–3: `.path` and direct-write refactors (5 sites) | 1 | 2.0 | |
| 4 | Thumbnail pipeline (Pillow is already at `requirements.txt:21`, no new native dependency) | 1 | 1.5 | |
| 5 | Test rework (8 `override_settings` classes + 5 `.path` sites) | 1 | 2.0 | |
| 6 | RDS migration (instance/VPC/allowlist/account → dump/restore → row-count verification → edit compose → teach `ci-deploy.sh`'s checkpoint the RDS DSN, blocker 9 → **manually reinstall the host runner (blocker 9b)** → update `waypost-backup.sh`) | 1 | 2.5 | |
| 7 | ECS bandwidth upgrade + CloudMonitor bandwidth/traffic threshold alarms | 1 | 0.5 | |
| 8 | RFQ LLM switch to DeepSeek (3 `.env` values + rename `MINIMAX_RFQ_*` → `RFQ_LLM_*` + heuristic pre-gate + `usage` parsing and monthly budget alarm) | 1 | 1.5 | |
| 9 | Mailbox polling as a dedicated process (management command + systemd timer) | 1 | 1.0 | |
| 10 | Snapshot policy + alarm SMS channel | 1 | 0.5 | |
| 11 | Documentation sync (ops KB `instance/waypost.md` §5/§6/§7 and `instance/backup-dr.md`; this repo's `docs/DEPLOYMENT.md`; both CHANGELOGs) | 1 | 1.5 | |
| | **Phase 1 subtotal** | | **16.5** | |
| 12 | `photo_zip` on demand instead of persisted (reuse `build_bundle`, adjust the frontend download entry point) | 2 | 1.5 | |
| 13 | Mini program direct-to-OSS upload (STS temporary-credential endpoint + `wx.uploadFile` to OSS PostObject) | 2 | 3.0 | |
| 14 | Backfill: upload existing `media/` to OSS and rewrite stored paths | 2 | 1.0 | |
| | **Total** | | **22.0** | |

Person-days are **not** converted to money: no day rate has been agreed, so the cost column is left
blank deliberately rather than filled with an invented assumption.

Within the Phase 1 total, **items 1 and 6 cannot be cut**. Item 1 decides whether the bandwidth and
disk numbers are achievable at all (cost estimate §3.2 and §5); item 6 decides whether the system of
record has a standby. Items 4 and 5 are consequences of items 2 and 3 and cannot be deferred
independently.

---

## 3. Ordering

Phase 1 has real dependencies; do not reorder:

1. **Mini program compression** (item 1) first — every volume figure downstream assumes it.
2. **OSS backend + `.path` refactor + thumbnails + test rework** (items 2–5) in one PR, otherwise
   the suite stays red and nothing can merge.
3. **RDS** (item 6), including repointing `ci-deploy.sh`'s checkpoint at the RDS DSN (blocker 9).
   After merging, **reinstall the host runner by hand (blocker 9b)**, then run one deployment and
   **confirm a checkpoint file was actually written**. Two reasons this confirmation is not
   redundant even though PR #95 made the failure loud: the runner reinstall is manual and therefore
   easy to skip, and a deploy that dies at `:568` looks like a deploy bug rather than a missing
   checkpoint until someone reads the log line.
4. **Bandwidth upgrade + alarms** (items 7, 10). The bandwidth is shared with the Helpdesk tenant on
   the same ECS, so afterwards re-verify all three entry points (`ams.istore-tech.cn`,
   `admin.helpdesk.istore-tech.cn`, bare IP).
5. **RFQ switch + heuristic gate + usage alarm + polling process** (items 8, 9).
6. **Documentation sync** (item 11).

Phase 2 items are trigger-gated in the cost estimate §8 (e.g. `photo_zip` on demand when the OSS
bill exceeds ¥30/month or storage exceeds 200 GB; direct-to-OSS upload when ECS inbound becomes the
bottleneck).

---

## 4. What is deliberately not in this report

Cloud pricing, service sizing, the disk-fill projection, the bandwidth-vs-concurrency derivation and
the risk register all stay in the ops knowledge base's cost estimate. Duplicating them here would
create two sources of truth for the same numbers, and only one of them would be updated.
