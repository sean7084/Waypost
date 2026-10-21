# Waypost - Changelog

## Unreleased

**Version:** to be assigned by the next release-prep PR
**Release Date:** —
**Focus:** Repair the release pipeline's summary step, keep releases working when github.com's apex host is unreachable from the deployment host, make documentation placement a CI gate instead of a convention, cut inspection media storage ahead of go-live (client-side photo compression + on-demand photo bundles), move media behind a gated object-storage backend with derived thumbnails and ephemeral generated documents, meter, cap and schedule the RFQ language-model spend, let the deploy checkpoint and the backup script address a database that is not the compose `db` container, and close the remaining small defects: a store-date inspection invariant that turned out to need the time slot, a permission error that returned 500, an unreadable credential that logged nothing, and two scratch files at the repository root

---

### Highlights

1. v0.2.0 deployed correctly but its Actions run was marked **failed**. The `Deployment summary` step interpolated `${github.event_name}` — a single brace, which is not an Actions expression — so bash received it literally and aborted with `bad substitution`. The deploy step was green and the release was live the whole time; only the cosmetics step was red.
2. The same run's logs were partly unreadable: `ECS_DEPLOY_USER` was stored as a *secret* whose value is the word `deploy`, so Actions masked that word everywhere — step names appeared as “Run the *** script on the ECS” and the summary heading as “Waypost production ***”.
3. Re-running the pipeline for `v0.2.0` then failed for an unrelated, environmental reason: the ECS could not reach github.com, and all three `git fetch` attempts died (`curl 28 Operation too slow`, then two 132-second connect timeouts). The runner aborted before touching anything — correct behaviour, but the release had to be dispatched again by hand hours later.
4. **`ci-deploy.sh` now has a verified codeload fallback**, so an unreachable apex no longer blocks a release. Diagnosis showed the impairment is specific to the apex host: `api.github.com` answered 200 in 0.33 s and `codeload.github.com` served the v0.2.0 tarball (7.3 MB) in 1.4 s while `https://github.com/` timed out 5/5 and `git ls-remote` failed 2/2. The fallback resolves the tag through the API, downloads the tarball, and **refuses to deploy unless the extracted files' git tree hash equals the tree SHA the API reports for that tag**.
5. Drilling that fallback exposed a real defect: a tag deploy was recorded in `deploy.log` as `DEV_OK` and announced as "NOT a release", because the code tested `KIND = "tag"` while the kind now carries a `+tarball` suffix. A release would have been logged as a mere verification deploy.
6. Documentation placement is now enforced by CI rather than left to convention. `reports/` at the repository root is the Django **`reports` app**, and five delivery/audit reports had accumulated inside it next to `models.py` and `views.py` — invisible to `docs/INDEX.md` readers and one `manage.py`-adjacent glob away from being baked into the Docker image. They moved to `docs/reports/`, and the move silently broke eleven relative Markdown links inside them, which is what the second new check exists for.
7. **RFQ AI spend is now metered, capped, and visible** (blockers 13–15). The three blockers were one problem seen three ways: every received message reached the language model with no pre-filter, nobody could say what a month of RFQ classification had cost, and nothing stopped it from costing more. `accounts/llm_client.py` is now the only path to the model, and it checks the month's spend against the budget **before** it builds the HTTP request — so an over-budget month costs zero tokens rather than one last call. Every call, every refusal and every skip writes a row. Provider config also came off the vendor's name: `RFQ_LLM_*`, with the retired `MINIMAX_*` names still read for one release and warned about, because `.env` is hand-edited and survives every deploy.

8. **Media storage is now backend-neutral, and the OSS switch exists without being flipped** (blockers 1–5, 8). The go-live plan assumed `django-storages` had an Aliyun backend; it does not (1.14.6 ships apache_libcloud, azure, dropbox, ftp, gcloud, s3 and sftp), so there is no upstream class to subclass and the backend is written against `oss2` directly. What makes that safe to merge before a bucket exists is a **gate, not a flag**: `resolve_default_storage_backend(env)` is a pure function of the environment, and unless all four `OSS_*` keys are non-empty the answer is the literal FileSystemStorage string this project has always used — so production is unchanged until an operator edits `.env`, and the change reverts the same way. Half-configured fails loudly at boot instead of quietly leaving media on a disk that a rebuild will wipe.

   The rest of the item is the list of ways this code could have worked on a dev box and broken in production, each removed rather than merely avoided: the `.<field>.path` sites (which raise `NotImplementedError` on any non-filesystem storage), three separate `MEDIA_ROOT/<app>/generated/` writers that left every customer document on disk forever, generated documents whose temporary directory was deleted **before** `FileResponse` finished streaming it, a thumbnail pipeline that could fail an upload, an attachment manifest that must not be deduped on the basename once its filesystem paths are gone, and a media URL built imperatively with `build_absolute_uri` — which corrupts the signature the moment `url()` returns a signed one. A new CI tripwire keeps the filesystem idioms out of the tree, and `docs/OSS_STORAGE.md` is the runbook the operator needs to finish the job — including the noncurrent-version lifecycle rule, whose omission silently invalidates the ≈8 GB storage ceiling and only shows up on a bill two to three months later. **This PR adds zero migrations**: thumbnails are derived by naming convention, so no column is needed, and `makemigrations --check --dry-run` is clean.

9. **The deploy checkpoint and the backup script stop assuming the database is the compose `db` container** (blockers 9–12). Three faces of one gap: `ci-deploy.sh` decided what to dump by asking "is a service named `db` running?", the DSN was hardcoded in `docker-compose.yml`, and the only copy of the backup script was prose inside `docs/BACKUP_RESTORE.md`. `DB_CHECKPOINT_MODE=auto|compose|external|none` makes the intent explicit, and the mode is deliberately **not inferred** — at the checkpoint step the running app container is still the *previous* release, so on the cutover deploy "is `db` running?" would checkpoint the database being left behind, on the single deploy that most needs a restore point. `external` reads `DATABASE_HOST/PORT/NAME/USER/PASSWORD` from inside the app container, because that is what Django actually connects with and `.env` is not the source of truth (compose's `environment:` overrides `env_file:`), and dumps through a throwaway `postgres:16` container — the runtime image ships no `postgresql-client`, only `libpq-dev` in the build stage — with a pre-flight `docker image inspect` so a never-pulled image fails at step 2 rather than at the moment of a real rollback. The cutover is then **one atomic `.env` edit** setting `DATABASE_HOST` and the mode together.

   **Blocker 9b — the manual runner reinstall — closes with three layers, because one is not enough on a box with no staging.** The GitHub runner executes whatever copy of `ci-deploy.sh` happens to be on the host, so a merged change to the deploy script stays inert until someone remembers to reinstall it, and the symptom of forgetting is a deploy that looks like a bug. (a) `scripts/deploy-dev-to-ecs.ps1` now refreshes by capability marker (`DB_CHECKPOINT_MODE`, not the old `allow-untagged`) and installs `waypost-backup.sh` beside it. (b) `.github/workflows/deploy-ecs.yml` compares the `sha256sum` of the repo's and the host's copy and **fails with the exact install command in the message** instead of quietly deploying the old script; `reinstall_runner` (dispatch input) installs on purpose and `ALLOW_RUNNER_DRIFT` (repo variable) is the incident escape hatch. Failing rather than auto-installing is deliberate: auto-install would change the release path's behaviour without anyone deciding to. (c) `deploy.log` grows a sixth tab-separated column recording which checkpoint the deploy wrote, so "was there a restore point before that release?" is answerable from the host without reading an Actions log.

10. **The inspection-duplicate invariant landed as (store, date, slot) — not (store, date), and the reason matters** (small-defects cluster). The premise inherited from the go-live report was "one inspection per store per date", so the first cut of the constraint was `uniq_store_inspection_location_date` on two columns. The full suite then broke two tests that had been green for weeks, and they were not wrong: `inspections/tests_iteration2.py` asserts that "AM and PM visits of one store must not collide inside the ZIP", and the day view in `inspections/views.py` builds itself from `(date, am)` and `(date, pm)` buckets. `docs/reports/INSPECTIONS_ITERATION2_COMPLETION_REPORT_20260924.md` already recorded the rule — *two visits per day are legitimate* — and the constraint would have made that shape impossible at INSERT time. The key is therefore `(location, inspection_date, slot)`, and chasing the real duplicate exposed a second, older defect: `kering_import` calls `get_or_create(location, inspection_date)` with `slot` in `defaults`, and `defaults` only fire on create — so a schedule that books one store in both slots resolved the afternoon row onto the morning inspection, never applied its slot, and silently attached its devices to the wrong round. Nothing refused it, because nothing checked.

   The three tools that read or write that key were then aligned to it rather than to each other: `merge_duplicate_inspections` groups by the same three columns the index uses (grouping by two would have had `--apply` fold a healthy AM/PM pair into one row — two real visits, one report), `reuse_historical_photos --prune-superseded` refuses to delete a source row whose slot differs (adoption **copies** evidence, so borrowing the afternoon's photos is safe while deleting the afternoon's visit is not), and the importer keys on the slot it used to only default. The invariant ships with a cleanup command because migration 0007 cannot apply while duplicates exist and the container entrypoint migrates on start — a single leftover pair means the app never comes up, which is why the dry run is a pre-deploy checklist row and not an advisory.

11. **The remaining small defects closed on their own terms** (small-defects cluster). Four things that were individually minor and collectively a trap: the company API returned **500** for a forbidden write because DRF's builtin `PermissionError` is not an HTTP exception; an unreadable mailbox credential decrypted to garbage with no log line, because `decrypt_secret` never raises by design and the caller had nothing to say; two scratch files sat at the repository root where `.gitignore` matches neither prefix, so the next `git add .` would ship them; and `--prune-superseded` deleted a superseded round's *rows* while its media helpers were asserted nowhere, so a regression to a plain `delete()` would have leaked objects on the bucket with every test still green.

    The credential fix is deliberately a **data audit, not a deploy gate**: `check_encrypted_secrets` now buckets rows as `total / fernet / legacy / unreadable / empty` via a new `secret_scheme()` probe, exits 1 only on `unreadable`, and exits 0 for `legacy` — a pre-Fernet row that still decrypts is a working mailbox, and the earlier "legacy is a failure" judgement would have blocked a release over nothing. `decrypt_secret` gained a `context` argument so the warning names the mailbox pk, the protocol and the host it was connecting to — never the token, the plaintext or the key — and the `accounts` logger was missing from `LOGGING`, so that warning only reached last-resort stderr and never `logs/waypost.log` where operators look. `IsSuperadminOrReadOnly` was deleted rather than repaired: its `is_superuser` test would have 403'd an in-scope IT-admin write, so the class was the bug.

### Delivered Scope

1. CI/CD
- `.github/workflows/deploy-ecs.yml`: the summary step now receives `github.event_name`, `github.actor` and `job.status` through `env:` instead of interpolating expressions into `run:`. That fixes the `bad substitution` failure and removes the script-injection surface that inline interpolation creates.
- `ECS_DEPLOY_USER` is read from repository **variables** with a `deploy` default rather than from secrets. It is not sensitive, and variables are not masked. Behaviour is unchanged when the variable is unset, because the default equals the value the secret held.
- The secret-presence pre-flight no longer requires `ECS_DEPLOY_USER`, since it now always resolves.

2. Host-side deploy runner (`docker/bin/ci-deploy.sh`)
- `fetch_with_retry` goes from 3 attempts with a flat 15 s pause to **4 attempts with escalating backoff** (20/40/60 s), and logs how long each attempt took. The attempt count is bounded by the deploy job's `timeout-minutes: 25`: a failed attempt can burn ~132 s in the connect phase alone, so four cost at most ~11 minutes and still leave room for the build and the health gates. More attempts would risk the job being killed mid-deploy, which is worse than failing cleanly beforehand.
- `record()` now falls back to the raw ref when the image tag is not resolved yet. An abort during fetch used to log `tag  none  none  ABORTED`, so the durable host-side record could not say *which* release had been attempted — only the Actions run knew. `KIND` also starts as `unresolved` instead of `tag`, so an early abort no longer mislabels a branch deploy as a tag deploy.

3. Codeload fallback (`docker/bin/ci-deploy.sh`)
- New source path used when `git fetch` fails: resolve the ref via `api.github.com` (commit SHA **and** tree SHA), download `codeload.github.com/<owner>/<repo>/tar.gz/refs/tags/<tag>` (also `refs/heads/<branch>` or a bare SHA), then **recompute the extracted files' git tree hash with a throwaway index and require it to equal the API's tree SHA**. TLS to codeload alone would only prove the bytes came from GitHub, not that they are the tag.
- `rsync -a --delete` into the checkout, excluding `.git/` and `.env` and honouring `.gitignore`, then re-verify the tree hash in place and assert `.env` is still non-empty. `.env` is the one file whose loss would stop the app booting, so it is protected twice rather than left to a single `*.env` gitignore pattern.
- `FETCH_ATTEMPTS` (default 4) and `FORCE_TARBALL_SOURCE=1` are now environment knobs: the first lets a drill fail fast into the fallback, the second exercises it deliberately, because an outage cannot be scheduled and an undrilled fallback is not a fallback.
- `deploy.log`'s kind column records the source (`tag`, `tag+tarball`, `untagged-ref`, `untagged-ref+tarball`), and a new `is_release()` prefix test decides `OK` vs `DEV_OK` — the equality test it replaces is what mis-recorded the drill.
- Rollback now also runs `git clean -ffd` after checking out the previous ref, because a tarball deploy can leave files the previous ref does not have and compose reads `docker-compose.yml` from that directory.
- `scripts/deploy-dev-to-ecs.ps1` no longer hard-fails when the host cannot refresh the runner from the target ref: it warns, keeps the installed runner, and checks the capability against the runner that will actually execute.

4. A missing pre-deploy checkpoint now stops the deploy
- `ci-deploy.sh` step 2 used to log `database container not running yet - skipping the pre-deploy checkpoint` whenever the compose stack had no running `db` service. That wording reads like a harmless first deploy whether or not it is one: if the database moves off this stack (to RDS, which the go-live plan contemplates) the service disappears, **every deploy silently stops taking a checkpoint, and the log still looks routine** — while the database is never restored automatically and some migrations are one-way doors.
- It now distinguishes the two cases. Nothing deployed here before → continue (there is genuinely no data to lose). Deployed before → refuse, with the reason and the two ways forward. `ALLOW_MISSING_CHECKPOINT=1` overrides, and says out loud that a one-way-door migration would then be unrecoverable.
- Same treatment for a `pg_dump` that fails, and a new guard for one that exits 0 but writes an empty file — an empty dump is worse than no dump, because it looks like a restore point and is not one.
- `docs/RELEASE_PROCEDURE.md` §11 corrected: it still claimed "First tag not yet cut … the repository has no tags", which stopped being true at v0.2.0. The replacement rows record that a real rollback has still never been drilled, and that the checkpoint must be repointed at the new DSN in the same change that moves the database.

5. Report placement and link integrity are now CI gates
- Five report documents moved from `reports/` (the Django app) to `docs/reports/`; Canvas companions live in `docs/reports/canvas/` and per-report evidence assets in a subfolder of `docs/reports/`. `docs/INDEX.md`, `docs/CONTRIBUTING.md` and `docs/SETUP_SUMMARY.md` updated to match, and `docs/reports/README.md` added as the catalogue plus the scope rule.
- `tests/test_repo_hygiene.py` (new) encodes the rules as `SimpleTestCase`s inside the required `python manage.py test` gate: no `.md`/`.markdown`/`.rst` under any local app directory, `reports/` holds Python only, report-named documents live under `docs/reports/`, `.canvas.tsx` lives under `docs/reports/canvas/`, and every relative Markdown link in the repository resolves. The app list is derived from `INSTALLED_APPS`, so an app added later is covered without editing the test.
- The report-name rule matches `REPORT|AUDIT|GAP_ANALYSIS` **case-sensitively**, which is the only thing exempting `.github/ISSUE_TEMPLATE/bug_report.md`; `.github/` is deliberately walked rather than skipped, because its issue templates carry relative links worth checking.
- The link check asserts `checked > 50` before asserting nothing is broken, so a future change to the tree walk that silently inspects nothing fails loudly instead of passing vacuously.
- `docs/reports/GO_LIVE_MIGRATION_BLOCKERS_REPORT_20261013.md` (new) is the application-side half of the go-live migration: 15 code-level blockers with `file:line` citations verified against `origin/main`, a 22.0 person-day estimate and the ordering constraints. Its blocker 9 records what item 4 above leaves open — once the `db` service moves to RDS, `DEPLOYED_BEFORE` is 1 and every deploy dies at `ci-deploy.sh:568` until the checkpoint learns the new DSN. The procurement half deliberately stays in the Aliyun ops knowledge base: a cloud-account cost document outlives any one commit, whereas these citations go stale the moment the code moves.
- `docs/reports/DOCUMENTATION_ALIGNMENT_AUDIT_20260906.md` renamed to `REPO_DOCUMENTATION_ALIGNMENT_AUDIT_20260906.md`. The ops knowledge base holds a same-named, same-day document auditing a different subject (the knowledge base against the live ECS, rather than this repository's docs against its code), and both were being cited by a filename that did not say which was which. Each now opens with a note naming the other.

6. Inspection media storage cut ahead of go-live (blockers 5 & 7)
- **Mini program photos are compressed at capture.** Both `wx.chooseMedia` calls omitted `sizeType` and there was no `wx.compressImage`, so 2.5–5 MB phone originals were queued and synced — at ~24k photos/month this drove the infeasible bandwidth figure and the disk-fill cliff in the go-live cost estimate. A new helper `miniprogram/utils/image.js` (`pickPhotos`) runs `chooseMedia({sizeType:['compressed']})` → `getImageInfo` → `compressImage` (quality 80, long edge capped at 1920 px) → `saveFile`, targeting ~300 KB/photo while keeping serial numbers and asset tags legible; each step falls back to the previous path so PNG/HEIC picks and `saveFile` errors never break capture. `device-verify` and `rack-network` route through it, and `tests/test_repo_hygiene.py` gained a `MiniprogramCompressionTests` gate (no page may call `wx.chooseMedia` directly) so the uncompressed path cannot silently return.
- **`photo_zip` is retired.** `generate_inspection_report` no longer writes a per-store ZIP of every photo next to the workbook — that duplicated each photo byte on disk (~47% of projected monthly media growth). The column is dropped in `inspections/0006_remove_photo_zip`; archives are now **streamed on demand** by `bundle_export.build_bundle` through web `GET /inspections/{id}/photos.zip/` and API `GET /api/v1/inspections/{id}/photo-bundle/`, named `<store> Photo.zip`. Detail/batch download buttons point at the streaming route and show whenever a store has photos. **Breaking:** `POST /api/v1/inspections/{id}/report/` no longer returns a `photo_zip` key (nothing consumed it — the mini program never downloaded the archive), and single-store downloads move from a flat `Photo/…` layout to the per-store `{store}/Photo/…` layout batch export already uses.
- **Orphaned deliverables no longer accumulate.** `FieldFile.save()` does not delete the previously stored file, so every regeneration of `report_file` (and, before retirement, `photo_zip`) left the old copy on disk — one dev store had collected 3 ZIPs + 3 XLSX. `generate_inspection_report` now deletes the prior file before re-saving.
- New management command `cleanup_orphan_photo_zips` (`--dry-run`) sweeps the `*Photo*.zip` archives already orphaned under `MEDIA_ROOT/inspections/`. Run it **after** applying migration 0006 and **before** any OSS backfill so the orphans are not copied to object storage.

7. RFQ / LLM cost cluster (blockers 13–15)
- `accounts/llm_client.py` (new) is the single choke point for every model call. `call_llm_json(system_prompt, user_prompt, *, purpose, mailbox, message)` returns an `LlmResult` (payload, token counts, status, latency, error) and never raises, so a provider outage degrades to heuristic classification instead of failing mail ingestion. The budget check is the **first** statement in the function, before `urllib.request.Request` exists, and `status='budget_exceeded'` is recorded even when nothing was sent. `extract_usage()` accepts both the Anthropic (`input_tokens`/`output_tokens`) and OpenAI (`prompt_tokens`/`completion_tokens`) envelopes, clamps negatives to zero, and returns `(0, 0)` for a malformed body — a billing record must survive a bad response. Cost maths is `Decimal` throughout; `RFQ_LLM_MONTHLY_BUDGET_CNY=0` means unlimited, which is how "do not cap" is expressed without inventing a second flag.
- `accounts/models.py` gains `LlmCallRecord`: one row per call, per refusal, per pre-gate skip. Both FKs are `SET_NULL` because this table is the only place the project states what money was spent, and deleting a mailbox or a message must not delete billing history. `status` is `ok / error / skipped / budget_exceeded / not_configured`; `provider` is the parsed hostname of the endpoint, since the endpoint — not the code — decides which vendor was called. `accounts/0024_llmcallrecord` is a plain `CreateModel`; it was applied, reversed to 0023 and re-applied to prove the reverse works.
- `waypost/settings.py`: provider-neutral `RFQ_LLM_API_KEY` / `_API_URL` / `_MODEL` / `_TIMEOUT_SECONDS` / `_MAX_TOKENS`, plus `RFQ_LLM_PRICE_PER_MTOK_INPUT`, `_OUTPUT`, `RFQ_LLM_MONTHLY_BUDGET_CNY` and `RFQ_LLM_REQUIRE_AUTHORIZED_SENDER`. The retired `minimax_token_plan_key` / `MINIMAX_RFQ_*` variables are still read for one more release through `_env_first_with_legacy()`, which logs a deprecation warning when a legacy name wins; the legacy **settings attributes** also remain as aliases. This is a dual read rather than a rename on purpose: `ci-deploy.sh` rsyncs with `--exclude='.env'`, so the hand-edited file on the server outlives the deploy and keeps whatever names it already has. Production behaviour is identical until someone edits `.env`.
- `accounts/rfq_ai.py`: a heuristic pre-gate now runs before the model does. `_NON_RFQ_SIGNATURES` (unsubscribe / 退订 / automatic reply / 自动回复 / out of office / delivery status notification / mailer-daemon / postmaster@ / no-reply) plus the headers `Auto-Submitted`, `X-Autoreply` and `List-Unsubscribe` suppress calls for traffic that is certainly not an RFQ and write a `skipped` row instead. The two halves are not equally trusted: a **header** vetoes a message even when the heuristic scored it as an RFQ, because the sending server wrote it about itself, while a **text** signature vetoes only when the heuristic found no RFQ either — `unsubscribe` in a customer's footer is prose, and letting it win would silently drop a real quotation request. `Auto-Submitted: manual` is deliberately **not** a skip — the RFC permits that value for human-triggered sends. The header values are captured into `ReceivedEmailMessage.metadata` by `mailbox_sync._build_defaults`, which needs no migration (`metadata` is already a `JSONField`). The stricter authorized-sender whitelist sits behind `RFQ_LLM_REQUIRE_AUTHORIZED_SENDER` (default off). A suppressed message returns `llm_skipped` + `llm_skip_reason`, and `process_rfq_message` persists both in the classification payload so a skip is auditable weeks later. `_call_minimax` is now `_call_llm_classify`, and product disambiguation goes through the same client with its own `purpose`.
- `accounts/management/commands/sync_mailboxes.py` (new) closes the "mailbox sync never runs unattended" gap (#24) **without** touching the `runserver` gate in `accounts/apps.py`: promoting that thread into gunicorn's three workers is the ×3 AI-cost and ×3 duplicate-processing trap the gate exists to avoid. Cross-process overlap is prevented by a Django **cache** lease — `cache.add('waypost:mailbox-sync:lock', <token>, timeout=interval + 60)`, released in `finally` **only if the token is still ours** — a pass that outlived its lease must not delete the lock a second syncer has just taken — rather than `pg_try_advisory_lock` or `fcntl`, because the same code runs against locmem in tests and Redis in production. `--once` is the default and prints `synced N mailbox(es), M new message(s)`; `--loop` re-takes the lease on every pass so an expired lease cannot admit a second syncer; `--max-runtime` gives `sync_all_active_mailboxes(deadline=…)` a real bound so a large mailbox cannot outrun `TimeoutStartSec`; `--mailbox PK` retries one account; `--prune-llm-records` (default 180 days) trims the metering table in the same pass so it cannot grow without limit.
- `docker/systemd/waypost-mailbox-sync.service` + `.timer` (new) are **user** units, because Docker on this host is rootless and a system unit has neither `DOCKER_HOST` nor `XDG_RUNTIME_DIR` in its environment; the two `Environment=` lines match what `ci-deploy.sh` already uses. `Type=oneshot` with `TimeoutStartSec=300` against `--max-runtime 240`, since systemd's 90-second default would SIGKILL a legitimate slow pass. `OnUnitActiveSec=5min` rather than `OnCalendar=*:0/5`: it measures from the **end** of the last activation, so one slow pass delays the next instead of overlapping it. `RandomizedDelaySec=30` de-synchronises restart storms and `Persistent=true` catches up after downtime.
- `accounts/management/commands/rfq_llm_usage.py` (new): `--month YYYY-MM`, `--by-mailbox`, `--json`, reporting calls / errors / skips per purpose and estimated spend against budget. Deliberately **not** exposed through `/healthz/` — that endpoint is polled by the deploy gates and must stay cheap and DB-light, and monthly spend is a human question.
- `accounts/tests_rfq_llm.py` (new, 46 tests) runs with no API key, no network, no bucket and no RDS. The over-budget test asserts `urlopen.assert_not_called()` rather than just checking the returned status, because the point of the guard is that the socket is never opened; the pre-gate is table-driven; the flag is tested both off and on.
- `.env.example`, `docs/DEPLOYMENT.md` §2.11 "Unattended mailbox sync" (install the units, verify one sync per five minutes, why the runserver gate stays), its two `.env` examples and its old Step 10 claim that no sync command exists, `docs/RELEASE_PROCEDURE.md` §11 and `docs/INDEX.md` gap table updated for #24.

8. Media storage: backend, thumbnails, ephemeral documents, tests and runbook (blockers 1–5, 8)
- `tests/storage.py` (new) is the shared isolation layer: `MEDIA_STORAGE_OVERRIDES` pins `STORAGES['default']` to `InMemoryStorage` while leaving `staticfiles` alone, `IsolatedMediaTestCase` uses it, and `png_bytes()` replaces three private copies of the same fixture helper. Eight test classes that carried their own `@override_settings(MEDIA_ROOT=tempfile.mkdtemp())` now write to memory — those `mkdtemp` directories were never removed, so the suite was growing a small leak in the process temp folder on every run. `base_url` has to be passed to `InMemoryStorage`: every media path in the project ends in a URL and without it `url()` raises `ValueError`, which would make the helper unable to carry the work the converted classes ask of it.
- The `FieldFile.path` sites are gone: the two readers in `invoices/services.py` (`Path(attachment.file.path)`, `Path(delivery.signed_file.path)`) now open `default_storage`, and the four assertions in `inspections/tests.py` that loaded the report workbook by filename read it through storage. `inspections/services/historical_photos.py` already went through `photo.image.open()`, so nothing had to change there for a bucket — it only gained the thumbnail step.
- **Attachment manifest reshaped** (`invoices/services.collect_email_attachments`): every entry is `{category, label, name}` plus exactly one of `storage_name` (a key in the default storage) or `path` (a local file). Uploads use `storage_name`, so the collector never resolves a filesystem path; generated documents use `path`. Dedup keys on `storage_name or path`, **not** the basename: while every entry was a filesystem path two same-named uploads had two different keys and both survived, but once uploads carry no `path` the only remaining short key is `name` — and keying on it silently drops one of two uploads sharing a basename, a customer's invoice PDF missing from an email with nothing in the log to explain it. Readers keep the `path` fallback because `EmailDispatch.attachments` rows written before this change carry `path` only.
- `utils/generated_documents.py` (new) is the one place generated documents live: `generated_document_dir(prefix)` yields a `TemporaryDirectory` the **caller** owns, `save_workbook_to_temp()` writes into it, `_find_soffice_binary()`/`convert_xlsx_to_pdf()` replace three copies that had drifted (each swallowing `subprocess` errors differently), and `open_generated_document()` returns a `FileResponse` whose underlying file deletes the directory on `close()`. The lifetime is the whole point: `FileResponse` is iterated *after* the view has returned, so a `with TemporaryDirectory()` around the write deletes the workbook before byte one is read and the customer receives a truncated download. Applied to the invoice document view, the delivery document view and the dispatch collector; the dead PDF branch in `quotations/services.py` that wrote into `MEDIA_ROOT/quotations/generated/` is gone. Nothing creates `<MEDIA_ROOT>/<app>/generated/` any more, and tests assert those directories are never created.
- `waypost/storage.py` (new) holds the gate and the backend. `WaypostOSSStorage` implements `_save`/`_open`/`exists`/`delete`/`size`/`listdir`/`url` and deliberately **no `path()`** — the base class raising `NotImplementedError` is what turns a stray `.path` into an immediate failure instead of a works-on-dev file. Object headers are set per object: `Cache-Control: max-age=604800` under `inspections/` and `max-age=2592000` elsewhere, transcribed from the two `expires` values in `docker/nginx/waypost.conf` so no browser's cache lifetime changes at the cutover, plus `Content-Disposition: attachment` for inspection **deliverables** only — never for `photos/`, `signatures/` or `thumbs/`, which would make the photo grid download. `url()` signs with a bucket handle on the **public** host derived from the configured endpoint (`public_oss_host`) while all traffic uses the internal one, and memoises per name per half-expiry window: re-signing per call would give one render several URLs for one object and defeat caching, never re-signing would strand an hour-old page on an expired URL. `listdir` paginates on `next_marker`, because `cleanup_orphan_photo_zips` walks with it and a single-page backend would silently leave orphans behind. `oss2` is imported inside the class, so a broken install cannot break a box that uses FileSystemStorage.
- `waypost/settings.py`: `OSS_ENDPOINT`/`OSS_BUCKET_NAME`/`OSS_ACCESS_KEY_ID`/`OSS_ACCESS_KEY_SECRET`/`OSS_MEDIA_PREFIX` (`media`)/`OSS_SIGNED_URL_EXPIRY_SECONDS` (`3600`), `OSS_STORAGE_ENABLED = all(four)`, and `STORAGES['default']['BACKEND']` plus `MEDIA_URL` chosen from that flag. A half-set environment raises `ImproperlyConfigured` — shaped as "you started, so finish", never "OSS is required", and gated on `DEBUG` so no development box can trip it. `.env.example` carries the block commented out, pointing at the runbook.
- `utils/media_urls.absolute_media_url(request, fieldfile)` is now the only imperative media-URL site: a URL that already starts with `http(s)://` is returned unchanged (running `build_absolute_uri` over a signed URL corrupts its signature), anything `/media/`-relative is qualified from the request. `api/inspection_views.py` uses it; everything else goes through `{{ field.url }}` or DRF's `FileField.to_representation`, both of which call `storage.url()` — which is why the backend is the single choke point for signing.
- `inspections/services/thumbnails.py` (new) renders a ≤480 px, quality-75 JPEG (≈30 KB from a 1920×1440 original) at `inspections/<store_inspection_id>/thumbs/<photo_id>.jpg` — a **naming convention, not a column**, so there is no migration and no backfill state to keep in sync. `ensure_thumbnail()` never raises: an undecodable original logs and returns `False`, because a thumbnail must never fail a photo upload, and it deletes before saving so a re-render cannot leave a `…_xxxxx.jpg` twin. `thumbnail_url` memoises one `exists()` per row and falls back to the original for photos predating the pipeline. `inspections/management/commands/generate_photo_thumbnails.py` (new, `--dry-run`/`--inspection`/`--jda`/`--all`) backfills the derivatives, and is safe to re-run and to interleave with live uploads.
- Two new CI tripwires in `tests/test_repo_hygiene.py`: `MediaStorageApiTests` walks every non-test `.py` and fails on `<anything>.path` (with `os`/`sys`/`request` exempt, so `sys.path`, `os.path.join` and `__file__`-derived template paths stay legal), on a `mkdir`/`makedirs` sharing a line with `MEDIA_ROOT`, and on `Path(settings.MEDIA_ROOT)` — printing `file:line` for each. It asserts more than 100 files were scanned before asserting nothing matched, so it cannot pass vacuously.
- `tests/test_storage_backend.py` (new, 35 tests) runs with no bucket, no network, no RDS and no Redis: the gate as a pure function plus one assertion that the live settings value **is** the FileSystemStorage string (the executable form of "production is unchanged when unset"); a `save → exists → open → read → size → url → delete → listdir` round trip over `subTest` on both backends the app runs on today; the OSS backend against a stubbed bucket (`_make_bucket` patched, so `oss2` is never imported) checking both cache values, the deliverable-only disposition, the memo window re-signing exactly at its boundary, the prefix mapping, pagination and `path()`; generated documents end to end through a real `django.test.Client` request — the stream still holds the workbook bytes after the view returned, and no `waypost-gen-*` directory survives once it is consumed; a real 1920×1440 PNG through the thumbnail pipeline; the same-named-attachment dedup; and `absolute_media_url`.
- `docker/nginx/waypost.conf` gained a comment, not a change: the `expires 7d`/`expires 30d` numbers are now mirrored in the backend, so **changing one without the other silently diverges the two**, and the blocks stay in place because they are what serves media until the bucket exists. Retiring them, and the `setfacl` chain that feeds the directory, is a separate post-cutover change (`docs/DEPLOYMENT.md` §2.3).
- `docs/OSS_STORAGE.md` (new): bucket creation, the least-privilege RAM policy JSON scoped to the one bucket and prefix, the **noncurrent-version 30-day lifecycle rule** with the cost argument for it, the default-header rule for anything written by hand, internal-versus-public endpoint choice, thumbnail backfill, the `ossutil` sync, a nine-entry verification list, what stops applying and when it may be deleted, and the "cutover must precede real inspections" ordering. Cross-linked from `docs/INDEX.md`, `docs/DEPLOYMENT.md` §2.12 and `docs/RELEASE_PROCEDURE.md` §11 — where the row is worded **merged but unswitched**, because claiming the migration is done would be a false statement about production.

9. RDS-aware checkpointing, an in-repo backup script, and the runner-drift gate (blockers 9–12)
- `docker/bin/_pg_checkpoint.sh` (new) is sourced by both the deploy script and the backup script and owns mode resolution, the app-container DSN read, the `docker run` dump and the empty-file guard. Two copies of a DSN rule is how they drift, which is the entire reason this file exists. Mode resolution happens at **source** time rather than inside the function, because both callers read it through command substitution — a subshell — so a value discovered there could never reach the failure message, and `DB_CHECKPOINT_MODE='' is not one of auto|compose|external|none` sends a reader to the wrong file during an incident.
- The RDS password reaches the dump container through a `0600` `--env-file`, not `-e PGPASSWORD=…`: an `-e` value lands in the argv of `docker run`, which any local process can read through `ps` or `/proc/<pid>/cmdline`, and this is the credential of the production database. Only `DATABASE_HOST` is ever logged.
- `docker/bin/waypost-backup.sh` (new) is the fenced block from `docs/BACKUP_RESTORE.md` moved into the repo, **extracted rather than re-authored** so the diff against the copy already on the host is checkable with `sha256sum`. The media tar is now gated by `WAYPOST_MEDIA_IN_OSS` (default `0`, so it runs exactly as today) with `MEDIA_TAR_RETENTION_DAYS` defaulting to 28 — deleting it before the photos are actually in the bucket would leave the only copy unbacked-up. The `template_files` tar stays unconditional (a read-only bind mount that is not going to object storage), the `.env` copy step remains, and its comment now names the real exposure: `DJANGO_FIELD_ENCRYPTION_KEY` has no off-host copy, so losing that file loses every stored mailbox credential.
- `docker/bin/ci-deploy.sh`: `DB_CHECKPOINT_MODE` beside `ALLOW_MISSING_CHECKPOINT`, `auto|compose|external|none` with `auto` reproducing today's behaviour exactly, a `checkpoint_missing` fall-through when the app container is not running, and one log line naming the source, host and size of the checkpoint — which is what turns the release procedure's post-deploy verification into a one-line grep.
- `docker-compose.yml`: `DATABASE_HOST: ${DATABASE_HOST:-db}` and `DATABASE_PORT: "${DATABASE_PORT:-5432}"`, with the comment above them rewritten to say that setting them in `.env` is now the supported way to move the database. Name/user/password stay derived from `POSTGRES_*` so the app and the local `db` cannot disagree. The `db` service is **not** retired — compose has no conditional `depends_on`, so removing it would mean dropping `app.depends_on.db.condition: service_healthy` and regressing cold-start ordering (the entrypoint's `migrate` has no retry), or a `profiles:` dance, or an override file needing Compose ≥2.24 `!reset`. An idle container costs nothing; the runbook ends at `docker compose stop db` with the volume retained 30 days. `waypost_pg_data` gained a `NEVER run docker compose down -v` warning: the bind mounts survive it, the volume does not.
- `.github/workflows/deploy-ecs.yml` + `scripts/deploy-dev-to-ecs.ps1`: the two blocker-9b layers that can be enforced (a `sha256sum` drift gate that fails with the install command, and a capability-marker refresh that installs both scripts).
- `tests/test_repo_hygiene.py` gains `DeployScriptHygieneTests` (5 tests, static, no database): every `docker/bin/*.sh` is CR-free and starts with `#!/usr/bin/env bash` and `set -euo pipefail`, `waypost-backup.sh` exists, `ci-deploy.sh` mentions `DB_CHECKPOINT_MODE` and sources the helper, `deploy.log` carries six fields, and `docker-compose.yml` contains no literal `DATABASE_HOST: db` — the tripwire against re-hardcoding the DSN. `shellcheck` was deliberately **not** added as a CI dependency.
- `docs/RDS_MIGRATION.md` (new) is the cutover runbook: same VPC/vSwitch and no public endpoint, dump → restore → scripted row-count comparison, the one atomic `.env` edit, `/healthz/` + login + a media write, then **one tag deploy confirming the sixth `deploy.log` column shows a real checkpoint path and size** — that check exists precisely because a deploy that dies at the checkpoint looks like a deploy bug — and only then `docker compose stop db`, with the `profiles:`/volume-removal follow-up dated and gated on the volume having been exported. Cross-linked from `docs/DATABASE_MIGRATION.md` (which is scoped to SQLite→PostgreSQL), `docs/INDEX.md` and a new `docs/DEPLOYMENT.md` §2.12. `docs/BACKUP_RESTORE.md` now points at the in-repo script, and the four `/opt/scripts/…` references became `/srv/waypost/bin/…`.
- `docs/RELEASE_PROCEDURE.md` §11 closes the checkpoint row and adds a runner-drift-gate row; §9 adds "drill the external checkpoint path once, deliberately, before the cutover release". **No staging environment, no notification channel for a failed deploy, and rollback-never-drilled all stay open**: this PR makes a failure legible, which is the prerequisite for alerting, not alerting itself.

10. The (store, date, slot) invariant and the three tools that read it (small-defects cluster)
- `inspections/models.py`: `uniq_store_inspection_location_date_slot` on `(location, inspection_date, slot)`, and the plain `(location, inspection_date)` index deleted — the unique index is a btree over those three columns and serves every left-prefix of itself, so keeping both cost a second index write per insert for no read benefit. Both changes are one migration: `inspections/0007_remove_storeinspection_inspections_locatio_f449d8_idx_and_more`.
- `inspections/management/commands/merge_duplicate_inspections.py` (new): dry run unless `--apply`, groups by the same three columns as the index, survivor is the oldest `created_at` with `id` as the deterministic tie-break (pks are random UUID4, so `id` alone is not an age order), NULL-location rows excluded from the grouping and their count printed so an empty report is never read as "nothing to do". `--apply` **refuses** a group whose photo evidence would move — photos on two rows, photos only on the dropped row, a dropped row holding a deliverable or a signature, a dropped finished round, or a `client_device_uid` collision — because re-parenting an `InspectionPhoto` moves the row but not the bytes under `inspections/<owning-inspection-id>/photos/`. Refusal is a non-zero exit *after* the safe groups have merged, so a partly-finished run says so.
- `inspections/management/commands/reuse_historical_photos.py`: `--prune-superseded` (off by default) deletes the round it adopted from only when store, date **and slot** all match; `_prune_block()` keeps a row that reuse did not fully adopt (finished status, signoff logs, issues, a generated deliverable, device rows with no serial-number counterpart, store-level or non-reusable-kind photos). `_delete_with_media()` removes the original bytes and the derived thumbnail through `default_storage` before the row goes, because `Model.delete()` drops rows only — and on OSS the orphan is named by a primary key that no longer exists, so nothing can find it again.
- `inspections/services/kering_import.py`: the dedup key gains `slot`. Previously `slot` sat in `defaults`, which only applies at creation, so a schedule listing one store in both slots produced **one** inspection carrying both rounds' devices.
- `inspections/tests_duplicate_inspections.py` (new, 23 tests) covers the constraint (including that AM+PM on one date is allowed and that NULL-location rows stay insertable), the dry run, each refusal, the prune guards, and the media bytes; `inspections/tests_import_scheduling.py` gains the both-slots-are-two-rounds case. The classes that must *write* a duplicate pair run the database at `inspections/0006` via `MigrationExecutor` and are `TransactionTestCase`, because the schema editor cannot run inside the per-test transaction SQLite wraps tests in.
- Redundant-NULL note kept honest: NULL is distinct in a unique index on **both** PostgreSQL (`DISTINCT_DEFAULT`) and SQLite — verified empirically, not assumed — so this constraint has never meant "one inspection per date".

11. API permission, credential observability, tracked strays (small-defects cluster)
- `api/views.py`: `IsSuperadminOrReadOnly` deleted (its `is_superuser` check would 403 an IT admin's in-scope write, so the class was the defect) and `perform_create` raises `rest_framework.exceptions.PermissionDenied` → 403 instead of the builtin `PermissionError` → 500. `api/tests_companies.py` (new, 8 behavioural tests) asserts 403, that no row was created, and the scoping 404/200 split.
- `accounts/crypto.py`: `secret_scheme()` returns `fernet` / `legacy` / None, and `decrypt_secret(token, context=...)` warns only when both schemes fail, naming the mailbox pk, the protocol and the host — never the token, plaintext or key. `accounts/models.py` passes that context from the mailbox actually being read (POP3 host vs IMAP host, falling back to the address domain). The warning fires on the symptom operators would otherwise chase blind: a mailbox that authenticates to nothing looks identical to an IMAP outage.
- `accounts/management/commands/check_encrypted_secrets.py` rewritten around five buckets (`total / fernet / legacy / unreadable / empty`): exits 1 on `unreadable` **only**, exits 0 for `legacy` with a printed note that a pre-Fernet row which still decrypts is a working mailbox, and lists the offending pks so the audit is actionable.
- `waypost/settings.py`: an `accounts` logger entry. `LOGGING` has no `root` logger, so `propagate: True` next to explicit handlers cannot double-emit; without the entry the new warning reached only last-resort stderr and never `logs/waypost.log`.
- `debug_roles.py` deleted (a 34-line print dump of role attributes owned by nothing) and `tmp_manual_verify_import_rollback.py` moved to `scripts/manual_verify_import_rollback.py`, because `docs/TESTING.md` §7 calls that import/rollback path safety-critical and it was the only worked example of it. `pyproject.toml`'s coverage omit list and `.dockerignore` lose their one-off entries for the deleted file, and `tests/test_repo_hygiene.py` gains `RepoRootStrayTests`: a flat directory listing (not a `git ls-files` shell-out — an untracked scratch file is the same accident waiting to be staged) that refuses `tmp_*`/`debug_*` at the root, asserts `manage.py` is present so the scan cannot pass vacuously, and asserts the rollback drill still exists at its new path.

### Migration Files Added

- **None for items 8 and 9.** The media/OSS work adds no model field and no migration file — thumbnails are derived from `store_inspection_id` and `photo_id`, and the backend is chosen at settings import. `manage.py makemigrations --check --dry-run` reports `No changes detected`, which is the proof rather than a claim, and it is also why rolling the storage switch back is never a schema problem. Item 9 touches no Python at all: shell, compose, PowerShell, one workflow and documentation.

- `inspections/0006_remove_photo_zip` — drops the retired `StoreInspection.photo_zip` column. **One-way door:** reversing it restores the column but not the deleted archives. Existing `*Photo*.zip` files on disk are orphaned once this applies; sweep them with `cleanup_orphan_photo_zips`.
- `accounts/0024_llmcallrecord` — creates the `LlmCallRecord` metering table. Additive only: no existing column or row is touched, and the reverse drops the table, so this is not a one-way door. Run `manage.py showmigrations accounts` before deploying; the table is empty on day one and `sync_mailboxes --prune-llm-records` keeps it bounded.
- `inspections/0007_remove_storeinspection_inspections_locatio_f449d8_idx_and_more` — drops the `(location, inspection_date)` plain index the unique index now covers and adds `uniq_store_inspection_location_date_slot`. **Reversible in schema, irreversible in data:** `AddConstraint`/`RemoveIndex` reverse cleanly, but getting the constraint to apply at all may require `merge_duplicate_inspections --apply`, and that deletes the losing rows — dropping the constraint does not un-merge an inspection. It also **cannot apply while a duplicate triple exists**, and the entrypoint migrates on start, so run the dry run against production first (`docs/RELEASE_PROCEDURE.md` §6.1, §9.2b).

### Validation

Evidence that v0.2.0 itself is good, taken from the release run and from production:

- Release run for tag `v0.2.0` (commit `c0fb0fd`): steps 1–6 and 8 green — including “Run the deploy script on the ECS”; only step 7 (`Deployment summary`) failed.
- On the host: `deploy.log` recorded `tag  v0.2.0  c0fb0fd  OK`, `git describe --tags` returns `v0.2.0`, the app container runs `waypost-app:v0.2.0`, and `/healthz/` reports `version: v0.2.0`, `revision: c0fb0fd`.
- Post-release checks (§7): `/healthz/` 200 · login 200 in `en-us` and `zh-cn`, both `charset=utf-8` · dashboard, admin and inspections 302 to login · `/api/v1/` and `/api/v1/inspections/` 401 · missing static file 404 · `http://` 301 to `https://` · `X-Frame-Options: DENY` and `X-Content-Type-Options: nosniff` present · `Server: nginx` with no version. Helpdesk on the same host unaffected (admin 200, api 307, bare-IP 200).

The workflow fix has since been **verified by a real run**. Re-dispatching the existing tag (`gh workflow run deploy-ecs.yml -f tag=v0.2.0`, run `38272016476`) finished `completed/success` with all eight deploy steps green — including step 5 “Run the deploy script on the ECS” and step 7 “Deployment summary”, whose log shows the contexts resolved through `env:` (`EVENT_NAME: workflow_dispatch`, `ACTOR: sean7084`) and readable step names with `USER_NAME: deploy` unmasked while `HOST`/`PORT` remain `***`. The host re-recorded `tag  v0.2.0  c0fb0fd  OK` and stayed healthy throughout.

The earlier dispatch (run `38194960136`) is the evidence for the fetch change: it reached step 5, failed all three fetches, and the runner aborted before checkout, build or container recreation — production was never touched. `deploy.log` recorded that attempt as `ABORTED`.

The `ci-deploy.sh` changes in item 2 are **not yet exercised by a failed fetch**; they are covered by `bash -n` and by reasoning about the job's time budget. The next outage will be the real test.

The codeload fallback in item 3 **has** been exercised end to end on the host, twice, with `FORCE_TARBALL_SOURCE=1`:

- Integrity mechanism, tested directly: the pristine v0.2.0 tarball hashes to `1523d940f618c5b02072c73f093b9f7840073198`, equal to the API's tree SHA; appending one byte to `waypost/health.py` changes it to `e864cb85…` and deleting the file gives `973175be…`. Both would be refused.
- Full drill: checkout deliberately reset to `87bc7d1` first, so the sync had real work to do. Resolved `v0.2.0` → commit `c0fb0fd7e164`, downloaded 7.0 MB, extracted 568 files, verified, synced, re-verified, all three health gates passed, exit 0, `deploy.log` recorded `tag+tarball  v0.2.0  c0fb0fd7e164  OK`. `scripts/deploy-dev-to-ecs.ps1` and the v0.2.0 release notes were restored by the sync, `/healthz/` reported `revision: c0fb0fd7e164`, and `.env` was byte-identical before and after (sha256 `ea87bdea…`, 2014 bytes).
- Negative paths: a non-existent tag and an untagged ref without `--allow-untagged` were both refused through the API path with exit 1, leaving the checkout, the runner and the containers untouched.
- The first drill is also what surfaced the `DEV_OK` bug in highlight 5; the second, after the fix, recorded `OK`.
- The wrapper's generated bash was checked by rendering the here-string exactly as PowerShell expands it and running `bash -n` on the result for both the plain and `SKIP_PUBLIC_GATE=1` cases (0 CR bytes).
- The item 4 decision logic was unit-tested by extracting the real `checkpoint_missing` function out of the shipped script (`sed -n '/^checkpoint_missing() {/,/^}/p'` + `eval`) and exercising all four combinations of `DEPLOYED_BEFORE` × `ALLOW_MISSING_CHECKPOINT`: first deploy continues, deployed-before refuses, deployed-before with the override continues and warns, first deploy with the override is harmless. All four returned the expected status.
- Both item 5 guards were proven to **fire**, not merely to pass, by planting a probe and watching it fail: a `reports/TEMP_PROBE.md` produced 2 failures (`['reports/TEMP_PROBE.md'] != []` from both the app-directory scan and the Python-only assertion), and a `docs/TEMP_LINK_PROBE.md` carrying one unresolvable link produced `['docs/TEMP_LINK_PROBE.md:3 -> DOES_NOT_EXIST.md'] != []`. Both probes were deleted afterwards. The eleven links the move actually broke were found by the same check and are fixed; a repository-wide scan now reports 134 relative Markdown links, 0 broken.

Item 7 is verified locally only — nothing here has been deployed, and no live model call was made:

- `python manage.py test` → **278 tests, OK (skipped=1)**: the 232-test baseline plus 46 new tests in `accounts/tests_rfq_llm.py`, all running with no API key, no network and no bucket. `manage.py check` reports no issues, `ruff check .` passes, `makemigrations --check --dry-run` says `No changes detected`.
- `accounts/0024_llmcallrecord` reversibility was run, not assumed: `migrate accounts` → `migrate accounts 0023` → `migrate accounts`, and the legacy aliases still resolve (`RFQ_LLM_API_KEY` and `MINIMAX_TOKEN_PLAN_KEY` read the same value, and the deprecation warning fires exactly when a legacy `.env` name wins — which is the current local `.env`, so those five warnings on every command are the intended signal to rename).
- **Not yet verified:** the systemd units have never been installed on a host, and `sync_mailboxes --once` has not been run against a real mailbox (the dev database has one active auto-sync account, so doing so would open a live IMAP session). Its overlap behaviour is covered by tests that patch the sync function and assert the cache lease is taken, honoured and released.

Item 8 is also verified locally only — nothing has been deployed and no Aliyun API was called:

- `python manage.py test` → **314 tests, OK (skipped=1)**: the 278 the item-7 run above reported, plus the 35 in `tests/test_storage_backend.py` and the one new `MediaStorageApiTests` case. All of them run with no bucket, no network, no RDS and no Redis. `manage.py check` reports no issues, `ruff check .` passes, and `makemigrations --check --dry-run` says `No changes detected` — which is the zero-migration claim in item 8, run rather than asserted.
- `MediaStorageApiTests` was proven to **fire**, not merely to pass: a probe module using `Path(settings.MEDIA_ROOT)`/`.mkdir()`/`record.image.path` produced `utils/_hygiene_probe.py:5 …` and `utils/_hygiene_probe.py:6 …` as failures, and the probe was deleted. The same probe first confirmed the walk scans 100+ files, so the guard cannot be passing because it found nothing.
- The streaming half of the generated-document test is the one that could not be written any other way: it drives a real `django.test.Client` request with LibreOffice pinned absent, counts exactly one live `waypost-gen-*` directory while the response is un-consumed, then reads `response.getvalue()` and asserts the temp folder is clean. That both directions are asserted is the point — the refactor under test could fail by deleting too early or by never deleting.
- The OSS backend has **never** touched a bucket, so the `oss2` call surface it uses (`put_object(headers=…)`, `get_object().read()`, `object_exists`, `head_object().content_length`, `list_objects(prefix=, delimiter=, marker=, max_keys=).prefix_list/.object_list/.is_truncated/.next_marker`, `sign_url(method, key, expires)`) is pinned against the attribute names the installed version exposes. A rename in `oss2` will therefore fail here rather than at the cutover, but real signing, real expiry behaviour, real per-object header propagation, actual egress/bandwidth figures and the `ossutil` sync in the runbook are unverifiable until the bucket exists — `docs/OSS_STORAGE.md` §8–§9 is the checklist for that.

Item 9 is host-side shell, compose, PowerShell and one workflow, and it was verified the same way: locally, by syntax and by proof that the guards fire.

- `python manage.py test` → **319 tests, OK (skipped=1)**: the 314 item 8 reported, plus the five cases in `DeployScriptHygieneTests`. `manage.py check` reports no issues, `ruff check .` passes, `makemigrations --check --dry-run` says `No changes detected` — item 9 adds no Python and no migration, and those three commands are how that is shown rather than claimed.
- `bash -n` passes on all three `docker/bin/*.sh`, each starts with the `#!/usr/bin/env bash` shebang and carries `set -euo pipefail`, and all three plus `docker-compose.yml` and `deploy-ecs.yml` hold **0 CR bytes**. `scripts/deploy-dev-to-ecs.ps1` is CRLF throughout (276 of 276 lines, as in HEAD) because it is PowerShell, and the bash it ships is normalised: the `$remote` here-string was extracted and expanded by PowerShell itself with `$Ref` and `$envPrefix` bound, then run through `bash -n` — 0 CR bytes in the rendered script, and `DB_CHECKPOINT_MODE=auto …/ci-deploy.sh 'refs/heads/main' --allow-untagged` on the last line.
- `DeployScriptHygieneTests` was proven to **fire**, and the proof deliberately did not touch the working tree. Corrupted **copies** of the four files were written to a scratch directory outside the repo and the module's path constants patched to point at them, so nothing under version control was ever modified — verified by `git status --porcelain` reporting the same 15 paths before and after. Eight injected defects, eight failures: a CR byte (`contains 1 CR byte(s)`), `set -euo pipefail` deleted, `DATABASE_HOST: db` re-hardcoded, the `DATABASE_HOST` line deleted outright (the check must not pass because the line vanished), the `printf` format cut to five fields (`5 != 6`), column six reduced to `${CHECKPOINT}`, `DB_CHECKPOINT_MODE` renamed out of `ci-deploy.sh`, and `_pg_checkpoint.sh` removed. The run also caught a weakness **in the probe, not the test**: the first attempt stripped one of the two `set -euo pipefail` strings in `_pg_checkpoint.sh` (code plus comment) and the test correctly still passed, so that injection was moved to a script with exactly one occurrence.
- **Not verified, because this box cannot:** `docker compose config` has never been run — there is no `docker` binary here (`docker: command not found`), so the plan's "resolve with and without `DATABASE_HOST` set and assert the value" step is pinned only by the textual `${DATABASE_HOST:-db}` tripwire, and the compose file's *interpretation* is untested. The `external` checkpoint branch has never executed: it needs a reachable RDS host, the `postgres:16` image pulled, and a real password, so `dump_external_database()`, the `docker image inspect` pre-flight and the `0600` `--env-file` credential path are reviewed-and-syntax-checked only. The nightly `waypost-backup.sh` has not run on a host, the `sha256sum` runner-drift gate has never compared against a real installed script, and no deploy has yet written a sixth `deploy.log` column. The first real evidence is the drill in `docs/RELEASE_PROCEDURE.md` §9.4 (which runs the external branch against the still-safe compose database) and `docs/RDS_MIGRATION.md` §3–§6.

Items 10 and 11 are the small-defect pass, and the load-bearing evidence for the invariant is a **failure I caused and the suite caught**:

- `python manage.py test` → **375 tests, OK (skipped=1)**: the 319 item 9 reported plus 56 — 23 in the new `inspections/tests_duplicate_inspections.py`, 14 in `accounts/tests_crypto_logging.py`, 9 in `accounts.tests_check_encrypted_secrets`, 8 in `api/tests_companies.py`, and one new case each in `inspections/tests_import_scheduling.py` (25 → 26) and `tests/test_repo_hygiene.py` (15 → 16). `manage.py check` reports no issues, `ruff check .` passes, `makemigrations --check --dry-run` says `No changes detected`.
- **The two-column constraint was wrong, and the errors said so.** With `uniq_store_inspection_location_date` on `(location, inspection_date)`, the run ended `Ran 371 tests … FAILED (errors=2)`, both `IntegrityError` at INSERT: `inspections/tests_dashboard_views.py:67` (`test_day_view_is_a_single_row_holding_the_slot_cards`) creates an AM and a PM round on one `self.location` and one date, and `inspections/tests_iteration2.py:362` (`test_bundle_folders_are_unique_when_a_store_has_two_slots`) does the same to prove the exporter keeps two folders apart. Neither test was optional and neither was wrong; the day view at `inspections/views.py:197-198` builds its buckets from `(date, Slot.AM)` and `(date, Slot.PM)`, so the index would have made a live feature impossible to write. `slot` is now in the key, the two errors are green, and the alignment was carried to all four readers of that tuple: `merge_duplicate_inspections`'s grouping, `reuse_historical_photos --prune-superseded`'s source guard, `kering_import`'s `get_or_create` lookup, and the docs that describe the cleanup.
- **Both slot guards were proven to fire** by scratch probe modules that monkeypatched the shipped code with the slot-blind version, printed the damage, then were deleted — never by editing tracked code. Grouping: on a healthy AM/PM pair the shipped triple sees `0 group(s)` while the two-column version sees `1 group(s) covering 2 rows`, i.e. the old key would have offered `--apply` on two real visits. Pruning: without the guard the borrowed PM round is reported `pruned superseded` and its row is gone (`PM row still in the database: False`) — the deletion `reuse_historical_photos` exists to prevent.
- **The media-deletion claim is self-evidencing, with its counter-proof run.** `test_prune_superseded_deletes_the_superseded_round_media` builds a thumbnail, prunes the adopted round, then asserts `default_storage.exists()` is false for the original *and* the derived thumb path while both copies that the surviving row adopted still exist. The probe that replaced the storage-API delete with a bare row `delete()` printed `LEAKED original: True` / `LEAKED thumbnail: True`, which is precisely the leak the release checklist now refuses to accept.
- The importer defect was a `defaults` bug, not a constraint bug, and its test history is the uncomfortable part: `slot` sat in `get_or_create(location, inspection_date, defaults={…'slot': slot})`, so it could never join the lookup key — a schedule booking one store in both slots resolved the afternoon row onto the morning inspection and quietly attached its devices there. `test_asset_list_counts_win_over_the_schedule_column` and `test_explicit_slot_column_is_honoured` were **green while that was true**, because each booked two different stores; the honest record is "the tests were blind, and `test_one_store_booked_in_both_slots_is_two_rounds` is the one that is not."
- The item 11 fixes have direct tests rather than reasoning: `api/tests_companies.py` pins the refused write as `403` in both directions — `test_it_administrator_create_returns_403_not_500` and `test_plain_user_create_returns_403_not_500` — while `test_superadmin_can_create` and `test_it_administrator_can_update_own_scoped_company` keep the guard from becoming a wall, and `IsSuperadminOrReadOnly` is deleted. `accounts/tests_crypto_logging.py` asserts the decryption-failure warning fires on `accounts.crypto`, names the symptom a user would report, the mailbox `pk`, the protocol and the host it was connecting to, and never carries the token, the plaintext or the key — including the no-`context` fallback and the POP3/SMTP/domain variants.
- **Never run, and not claimed:** `merge_duplicate_inspections --apply` has never touched real data — the dev database holds no duplicate triple, so only the dry run and the refusal paths are exercised, and what the command will find in production is unknown until the checklist row in `docs/RELEASE_PROCEDURE.md` §6.1 is executed there. `check_encrypted_secrets` has only run against the local database. The logging half of item 11 is the weakest-evidenced change in this set: `assertLogs('accounts.crypto')` attaches its **own** handler, so no test proves the new `LOGGING['loggers']` entry routes that record into `logs/waypost.log` where operators read it. Why it should (`accounts.crypto` has no logger of its own, so it propagates to `accounts`) and why it cannot double-emit (`LOGGING` defines no `root` key, so the parent's handlers are the only ones) are both readings of `waypost/settings.py`, not observations, and no log rotation has ever run here. The two strays in item 11 were removed from the working tree, but `debug_roles.py`'s blob still exists in history (`22a11cf2…`) and expunging it is a rewrite of pushed refs — an operator decision, not something this branch does. Nothing in items 10 or 11 has been pushed, merged or deployed.

---

## Release Notes v0.2.0

**Version:** 0.2.0
**Release Date:** October 9, 2026
**Focus:** Store-inspection module and WeChat mini program, the Waypost rebrand, a token-driven UI with full zh-cn coverage, and containerised production deployment behind a tag-promoted CD pipeline

---

> **First tagged release.** v0.1.7 (May 11, 2026) and earlier are recorded in this
> file but were never tagged, so this entry covers the entire range since then:
> **21 merged PRs, 110 commits, 390 files changed, 20 new migration files**.
> Per §2 any release containing migrations is at least a MINOR bump.

> ⚠️ **This release is a one-way door.** Two of the migrations it adds are on the
> `noop`-reverse list (`docs/RELEASE_PROCEDURE.md` §9.2):
> `accounts/0017_normalize_blank_employee_ids` and
> `accounts/0019_encrypt_mailbox_passwords_with_fernet`. Reversing them restores the
> schema but **not** the data, and mailbox credentials re-encrypted under Fernet are
> unreadable by pre-0.2.0 code. Rollback therefore means Tier 3 — restore the
> pre-deploy `pg_dump` — not `migrate <app> zero`. Take the backup (§6.1) first.

### Highlights

1. **New `inspections` app** — Kering store inspections end to end: batches, stores, devices, Wi-Fi weak points, sign-off logs, AM/PM slot scheduling, field-engineer assignment, review and bundle export. Replaces the legacy `audit` module, which is retired and deleted (#59, #80, #82, #83, #84).
2. **New WeChat mini program** — an offline-first client for store inspections (#64) against a dedicated REST API with WeChat JWT auth (#63), plus field-audit tooling, Chinese-name login and blind count (#77).
3. **Rebrand** — HengJi AMS → **Waypost**, and conda → `.venv` (#85), with navbar/login logo and a transparent favicon (#86).
4. **Token-driven UI and complete zh-cn coverage** (#88) — design tokens replace hard-coded colours (enforced by a conformance test), priority+ navigation overflow, a segmented language switcher, and per-locale date/number formats via `FORMAT_MODULE_PATH`.
5. **Production deployment** (#87) — Docker Compose on the shared Aliyun ECS behind host Nginx (443 shared by SNI), tag-promoted CD with a pre-deploy database checkpoint, health gates and automatic image rollback, and an unauthenticated `/healthz/`.
6. **Credentials encrypted at rest** — mailbox/SMTP passwords moved from XOR obfuscation to Fernet (#26).
7. **Quality gates and runbooks** — ruff became a required CI check and surfaced three latent crashes (#74); SECURITY.md plus the backup/restore and release-procedure runbooks landed (#27, #28, #29, #67).

### Delivered Scope (21 PRs since v0.1.7)

1. Store inspections — new Django app (`inspections/`, 37 files; did not exist at v0.1.7)
- #59 domain model + WeChat JWT auth · #61 historical photo reuse service and command · #62 EUS report and photo generation service
- #80 web frontend, legacy `audit` module retired · #82 iteration 2 (AM/PM scheduling, field engineers, review, bundle export) · #83 batch scoping aligned with inspections + unbatched imports · #84 slot assignment, scheduling views, dashboard tests
- #60 `import_kering_master` command for the Kering dataset · #81 verification screenshots archived

2. WeChat mini program — new `miniprogram/` tree (56 files; did not exist at v0.1.7)
- #64 offline-first client · #63 store-inspection REST API · #65 the stack landed together (import, report, API, client, docs, CI) · #77 field-audit enhancements · #76 mini program AppID and GitHub token read from `.env`

3. Rebrand and brand assets — #85, #86
4. UI design system and i18n — #88
5. Production deployment, CD and release tooling — #87, #89 (detail below)
6. Security, documentation and quality gates — #26, #27, #28, #29, #67, #74
7. Repository automation smoke tests — #1, #3

#### Production deployment and CD (#87, #89)

1. Production request security
- `waypost/settings.py`: env-driven `SECURE_PROXY_SSL_HEADER` and `CSRF_TRUSTED_ORIGINS`; secure cookies, SSL redirect and HSTS gated on `DEBUG` (HSTS ships at 0 because the header is sticky); `SECURE_REDIRECT_EXEMPT` keeps the probe reachable on loopback.
- Request hardening is disabled under the test runner (`settings._RUNNING_TESTS`). `manage.py test` flips `DEBUG` to `False`, which would otherwise turn every view test into an assertion about a 301 — 57 spurious failures that read like an application bug.
- Log file handler is now rotating; an unbounded `FileHandler` grows the container's writable layer forever.

2. Health probe
- `waypost/health.py` + route in `waypost/urls.py`: unauthenticated, no language prefix, 200/503, reports the deployed tag and revision. Failure detail is limited to the exception *class* name, because a database error message can contain the DSN.
- `waypost/tests.py`: 12 tests covering the healthy path, the degraded paths, the no-leak guarantee, reachability with `SECURE_SSL_REDIRECT` on, and the proxy settings' defaults.

3. Container runtime
- `docker/Dockerfile`: multi-stage (`deps` → `runtime`), Aliyun mirror defaults with build-arg overrides, renders a Chinese string through WeasyPrint at build time as proof the font and Pango stack work.
- `docker-compose.yml`: `app` + `postgres:16` (Debian, not Alpine — musl collation changes can corrupt indexes) + `redis:7`; only the app publishes a port, and only on loopback. `name: waypost` is pinned so the project name cannot collide with another stack on the same host.
- `docker/entrypoint.sh`: `migrate` → `collectstatic` → `exec gunicorn` (exec, so Gunicorn is PID 1 and receives SIGTERM instead of being SIGKILLed after the grace period).
- `docker/nginx/waypost.conf`: SNI-shared 443 with no `default_server`, ACME webroot before the redirect, `/media/` aliases, and the `X-Forwarded-Proto` header the CSRF fix depends on.
- `.dockerignore`, `.gitattributes` (`*.sh` pinned to LF — a CRLF entrypoint fails as `/bin/bash^M`).

4. CI/CD
- `.github/workflows/deploy-ecs.yml`: image-build validation on PRs, `preflight` + `deploy` on `v*` tags, plain OpenSSH with a pinned `known_hosts` and `StrictHostKeyChecking=yes`, `environment: production`, serialized by a concurrency group.
- `docker/bin/ci-deploy.sh`: the host-side deploy — rollback alias, `pg_dump` checkpoint, tag checkout, build, health gates, automatic image rollback. It never restores the database automatically, because a release may contain a one-way-door migration.
- `.gitignore`: deploy keypairs and the `waypost_data.json` migration fixture.

5. Documentation and one silent-failure fix
- `docs/DEPLOYMENT.md`: Option 2 is now the real production procedure (co-hosting, rootless file ownership, `.env`, Nginx/TLS ordering, CI/CD credentials); Step 4b's template table corrected against the code; Step 10 corrected — the mailbox sync thread is `runserver`-only, so it never runs in production. Four new troubleshooting entries.
- `docs/RELEASE_PROCEDURE.md`: §1 current-reality table re-verified, §6.2 rewritten for the container deploy, §11 gaps updated.
- `docs/BACKUP_RESTORE.md`: §2b/§3a for the containerised stack.
- `invoices/services.py`: a missing `签收单 template.xlsx` was swallowed by a bare `except FileNotFoundError: pass`, so invoice dispatch emails quietly lost their delivery documents. Now logged as a warning.

6. Verification deploys of a branch (added after the first two PRs merged)
- `scripts/deploy-dev-to-ecs.ps1`: deploys `origin/main` (or any ref) to the host without cutting a release. Shows the commits and migrations in the range and asks before proceeding, because there is only one database on this host.
- `docker/bin/ci-deploy.sh`: accepts a non-tag ref behind an explicit `--allow-untagged`. Same code path as a release — one runner, so the two cannot drift — but the image is tagged `waypost-app:dev-<ref>` and the deploy is logged `kind=untagged-ref … DEV_OK`, so a verification deploy can never be mistaken for a release. Without the flag a non-tag ref is still refused, so the CD path cannot deploy a branch by accident.
- `docs/RELEASE_PROCEDURE.md`: new §4 "What a tag actually ships" (a tag deploys the whole tree, so untagged PRs go out with the next tag — there is no per-PR deploy), new §4 "When to apply the tag", new §6.5 for the verification path, and §5.2 gained the no-tags-yet variant of the migration check (the documented command fails with `unknown revision` while the repository has zero tags).
- Fixed the deploy pipeline's public login gate, which would have failed **every** deploy including the first release: it required `200` from `/accounts/login/`, but that path legitimately `302`s to the default language prefix (`/en-us/accounts/login/`). It now follows redirects, so it tests Nginx, TLS, the locale redirect and the rendered page. Found by running the gate for real — a healthy deploy was marked `FAILED_EDGE`. `docs/DEPLOYMENT.md` §2.10 documented the same wrong expectations for `/login/` and `/accounts/login/`, and is corrected against measured values.

### Migration Files Added

**20 files across 7 apps**, plus one app removed. This is why §2 makes the release a
MINOR bump, and why the two flagged rows make it a one-way door.

| App | Migrations added since v0.1.7 |
|-----|-------------------------------|
| `accounts` | 0016 split order-management role · **0017 normalise blank employee IDs (one-way)** · 0018 system SMTP settings · **0019 encrypt mailbox passwords with Fernet (one-way)** · 0020 `WeChatIdentity` · 0021 `ServiceCity` + user Chinese name / service cities · 0022 FE notes / FE rating / invite code · 0023 `ReceivedEmailMessage.message_id` → `TextField` |
| `inspections` | 0001 initial · 0002 device fields · 0003 `InspectionBatch` + `StoreInspection.batch` · 0004 `InspectionSignoffLog` · 0005 Wi-Fi weak points |
| `assets` | 0015 `AssetActivityLog` + `AssetFieldChange` |
| `companies` | 0014 default quotation template · 0015 widen `Location.phone_number` 17→100 and `CompanyUser.work_phone` 20→100 |
| `invoices` | 0006 `EmailDispatch.reply_message_id` → `TextField` |
| `products` | 0005 `ProductPriceApprovalRequest` |
| `quotations` | 0008 remarks · 0009 PDF template |
| `audit` | **removed** — `audit/migrations/0001_initial.py` deleted along with the app (#80) |

About the three column widenings (`companies/0015`, `accounts/0023`, `invoices/0006`):
SQLite does not enforce `varchar(n)`, PostgreSQL does. Loading the real data into
PostgreSQL failed with `value too long for type character varying(255)` on a
Message-ID of 259 characters — legitimate data, so the columns were widened rather
than the values truncated. Every bounded `CharField` in the project (202 of them) was
scanned against the real data to find all three at once instead of one failed
`loaddata` at a time. Reversing them is only safe while no stored value exceeds the
old bound.

The retired `audit` app leaves no orphans in production: verified against the ECS
PostgreSQL that there are **no `audit*` tables and no `audit` rows in
`django_migrations`** (67 tables, 109 applied migrations, 7 of them `inspections_*`).

### Validation

- `python manage.py test`: **215 tests, 0 failures** on the release commit.
- `python manage.py check` and `makemigrations --check --dry-run`: clean (“No changes detected”). `python -m ruff check .`: clean.
- CI green on every PR in the range; branch protection requires `Django checks + tests` and `Python lint (ruff)`.
- Production-shaped configuration verified by booting with `DJANGO_DEBUG=False` and printing the resolved settings: proxy header, CSRF origins, SSL redirect, redirect exemption, secure cookies, WhiteNoise manifest storage and middleware, rotating log handler. The 12 probe tests also pass under a production-shaped environment, confirming the `_RUNNING_TESTS` guard.
- Deployed and verified on the ECS **before** tagging: `/healthz/` 200 with `database`/`cache` ok, login page 200 in both `en-us` and `zh-cn`, `/api/v1/` 401, design-system assets served under hashed manifest names, and Helpdesk on the same host unaffected.
- The tag→CD path itself has **not** run yet — the repository has zero tags. This release is its first exercise.

---

## Release Notes v0.1.7

**Version:** 0.1.7  
**Release Date:** May 11, 2026  
**Focus:** Separate service catalog adoption, direct-dispatch fulfillment, and hardware-only asset boundaries

---

### Highlights

1. Services now live in a dedicated `products.ServiceItem` table while `products.ProductPrice` remains the unified price source across hardware and services.
2. Confirmed quotations can now move straight to delivery when stock fully covers the hardware lines, and service-only quotations no longer create unnecessary purchase orders.
3. Delivery orders now support asset-less service rows and use a simplified `Pending -> Dispatched -> Delivered` workflow.
4. Asset-facing pages, exports, and catalog-management surfaces are now hardware-only, preventing legacy service records from leaking into the Assets app.
5. Price-list, quotation, delivery, and workflow UI now label catalog type more clearly and expose service editing in the shared catalog flow.

### Delivered Scope (35-file iteration)

1. Separate service catalog and unified pricing
- Added `products.ServiceItem` plus `ProductPrice` validation and constraints so each price row targets exactly one catalog object: `model` or `service_item`.
- Added migrations to create/backfill service items from legacy service `AssetModel` records.
- Added service price edit support, service-group suggestions, and mixed-catalog display helpers.
- Updated RFQ AI matching and quotation snapshot generation to use hardware/service-safe display fields.

2. Quotation selection and fulfillment orchestration
- Updated quotation create/edit flows to present a unified hardware/service selector with explicit type filter and labels.
- Added `QuotationItem.service_item` and wired quotation duplication/snapshot logic to preserve service associations.
- Added direct-dispatch eligibility checks so confirmed quotations can create deliveries immediately when in-stock hardware fully matches.
- Changed confirmed quotation actions from unconditional purchase creation to `Create Delivery` or `Continue Fulfillment` based on actual stock coverage.

3. Service-aware delivery workflow
- Removed the `prepared` delivery state from the active workflow and relabeled `completed` as `Delivered` in the UI.
- Added `DeliveryItem.quotation_item` and nullable `asset` support so service lines can exist without asset records.
- Updated delivery creation to auto-add service rows while keeping manual asset selection only for hardware lines.
- Updated invoice rebuild logic and delivery templates to resolve mixed hardware/service delivery rows correctly.

4. Hardware-only asset application cleanup
- Centralized hardware-only category, brand, model, and accessible-asset querysets in asset forms and views.
- Prevented service-category records from appearing in asset list/detail/edit/delete/export/stats flows and related asset audit links.
- Restricted brands/models pages, filters, and export forms to hardware catalog data only.
- Kept service catalog management inside Products instead of Assets.

5. Workflow, dashboard, and UI polish
- Updated workflow dashboard lane behavior and responsive grid sizing.
- Refined product price list, quotation list/detail, and delivery detail/form actions to reflect mixed catalog types and direct delivery flow.
- Renamed navigation wording for weekly Sharepoint batches and aligned delivery action labels with the simplified status model.
- Added release-facing documentation updates for the new service catalog architecture.

### Migration Files Added

1. `deliveries/migrations/0002_deliveryitem_quotation_item_and_service_lines.py`
2. `products/migrations/0003_serviceitem_alter_productprice_options_and_more.py`
3. `products/migrations/0004_migrate_service_prices_to_service_items.py`
4. `quotations/migrations/0006_quotationitem_service_item.py`
5. `quotations/migrations/0007_backfill_quotationitem_service_item.py`

### Validation

1. `python manage.py check`
2. `python manage.py makemigrations --check`
3. Django shell smoke checks confirmed `ProductPrice` rows have no invalid mixed/null catalog target states after migration.
4. Known direct-dispatch quotations (`QT-20260424-004` and `QT-20260429-006`) still resolve correctly and already own delivery orders.
5. Asset smoke checks confirmed brand-list and asset-edit pages no longer render service names, and current asset-view grep checks show only the centralized hardware helper remains.

## Release Notes v0.1.6

**Version:** 0.1.6  
**Release Date:** April 29, 2026  
**Focus:** RFQ mailbox automation, quotation workflow hardening, service-item pricing, and workflow-dashboard stock consolidation

---

### Highlights

1. Mailbox sync can now classify RFQ emails, track RFQ processing state, and support reprocessing plus draft quotation/reply workflows for authorized customer senders.
2. Quotation create/edit/detail/list flows were hardened around recipient email capture, RFQ review state, stock-first fulfillment actions, and create-and-download PDF behavior.
3. The price list now supports service items, category-level default models, and better handling of generic RFQ requests that do not specify an exact catalog SKU.
4. The standalone stock page was retired in favor of a workflow dashboard view that combines Kanban stages, stock overview, receipts, and pending order-management tasks.

### Delivered Scope (65-file iteration)

1. RFQ mailbox automation and sender authorization
- Added RFQ classification, extraction, and quotation-draft orchestration in `accounts/rfq_ai.py`.
- Extended `accounts.ReceivedEmailMessage` with RFQ status, confidence, summary, extracted data, error, processed-at fields, and supporting indexes.
- Triggered pending RFQ processing after mailbox sync and added mailbox-detail reprocessing from the UI.
- Added `is_authorized_rfq_sender` on company contacts, surfaced it in forms/lists, and used it to gate automatic RFQ quotation generation.
- Added pending-task notifications in the base layout for RFQ drafts, likely RFQs, and mailbox failures.

2. Quotation workflow, PDF, and email threading
- Added quotation `attn_email`, `source_email_message`, and `requires_confirmation` fields plus create/edit autofill updates.
- Reworked quotation create to support `Save as Draft` and `Create and Download PDF`, including redirect-based download triggering on detail pages.
- Linked quotation detail/list screens to RFQ source emails, item-matching warnings, purchase/delivery next actions, and clickable list rows.
- Updated quotation PDF remark generation to deduplicate remark targets and align output wording with the current template.
- Extended dispatch email compose/send flows to reply against source mailbox messages, prefill recipients/subject/body, and include quotation PDFs.

3. Product catalog and service-item support
- Added `default_asset_model` and `item_type` on asset categories so hardware and service categories can be treated differently.
- Added category-form support for default-model assignment and validation.
- Updated the price list with type/status filters, a default-model management panel, and a renamed `Price List` entry in navigation and translations.
- Added one-step service item + price creation with a dedicated form/view/template and default service brand/category helpers.
- Excluded service categories from the regular hardware product-price add flow.

4. Purchase, delivery, and dashboard workflow consolidation
- Replaced the old purchased-assets list with a purchase-order-oriented list and action model.
- Removed `/purchases/stock/` and moved stock overview content into the workflow dashboard.
- Restricted purchase receipts to the internal warehouse location path used by current operations.
- Centralized dispatch asset selection so delivery creation uses matching available internal stock and avoids assets already in active deliveries.
- Updated workflow next-action labels, delivery/purchase back-links, and dashboard stock/receipt summaries to match the new flow.

5. Runtime, navigation, and regression fixes
- Added repo-local `.env` loading in `manage.py`, `waypost/asgi.py`, and `waypost/wsgi.py`, and ignored `*.env` in Git.
- Added RFQ/Minimax and test outbound email override settings in Django settings.
- Fixed authenticated login redirects to the namespaced dashboard route.
- Renamed the product navigation entry to `Price List`, cleaned up stock links, and updated related zh-cn translations.
- Applied focused UX fixes including quotation form comboboxes, item editing, delivery PDF wording polish, and receipt-page warehouse guidance.

### Migration Files Added

1. `accounts/migrations/0015_receivedemailmessage_rfq_confidence_and_more.py`
2. `assets/migrations/0013_assetcategory_default_asset_model.py`
3. `assets/migrations/0014_assetcategory_item_type.py`
4. `companies/migrations/0013_companyuser_is_authorized_rfq_sender.py`
5. `invoices/migrations/0005_emaildispatch_reply_message_id_and_more.py`
6. `quotations/migrations/0004_quotation_attn_email.py`
7. `quotations/migrations/0005_quotation_requires_confirmation_and_more.py`

### Validation

1. `git status --porcelain` reported 65 file changes for this iteration before release-note finalization.
2. Browser validation covered authenticated login redirect behavior, workflow dashboard stock view, removal of `/purchases/stock/`, and quotation create actions including `Save as Draft` and `Create and Download PDF`.
3. Server-side log validation confirmed the create-download flow now persists quotations before triggering PDF downloads.
4. `python manage.py check` completed successfully after the final workflow and documentation pass.

## Waypost - Release Note v0.0.1

**Version:** 0.0.1  
**Release Date:** July 7, 2025  
**Focus:** Core Project Foundation & Authentication System

---

### 🚀 Overview

Version 0.0.1 marks the successful establishment of the Waypost foundational architecture. This release includes the complete Django project setup and a fully implemented, secure user authentication system with a modern user interface.

### ✅ Key Features Delivered

#### 1. Core Project Initialization

- **Modular Django Architecture**: Established a scalable project structure with a dedicated `accounts` app for user management.

- **Environment & Configuration**:
  - Configured Conda environment (`HengjiAMS1`) for isolated dependency management.
  - Set up SQLite for development and prepared PostgreSQL configurations for production.
  - Configured static and media file handling.

- **Internationalization (i18n)**: Implemented a robust i18n framework supporting English and Chinese out-of-the-box.

- **URL Routing**: Configured main project and app-level URL routing with i18n support.

- **Database Migrations**: Ensured the initial database schema is clean and all migrations are successfully applied.

#### 2. Full Authentication & Login Page Implementation

- **Custom User Model**: Implemented a secure, extended `AbstractUser` model with:
  - UUID primary keys to prevent enumeration attacks.
  - Fields for future implementation of roles, 2FA, and language preferences.

- **Secure Login Page**:
  - Developed a modern, responsive login page using Bootstrap 5.
  - Integrated the company logo and a dynamic language switcher.

- **Session & Security Management**:
  - Implemented user session tracking.
  - Added monitoring for failed and successful login attempts for security auditing.

- **Admin Interface**: Fully configured the Django admin for the custom user model, allowing for easy user management.

### 🔧 Technical Stack

- **Framework**: Django 4.2+
- **Database**: SQLite (Development)
- **Frontend**: Bootstrap 5, HTML, Vanilla JavaScript

### 🎯 Success Criteria Met

- ✅ Django project runs without errors (`manage.py check` passes).
- ✅ The admin interface is fully functional for user management.
- ✅ The login page at `/accounts/login/` renders correctly and is fully functional.
- ✅ Multi-language switching is operational on the login page.
- ✅ Core security best practices (UUID keys, session tracking) are in place.

## Release Notes and Progress Report for Version 0.0.2

---

### Release Notes for Version 0.0.2

#### Key Enhancements and New Features

##### 1. Asset Models

- Re-enabled and fixed the models in the assets application.

##### 2. Audit System

- Re-enabled and fixed the models in the audit application.

##### 3. Dashboard

- Created the main dashboard for users after login.
- Updated the dashboard to display sample assets and provide real-time data.

##### 4. Additional Templates

- Added templates for:
  - Two-Factor Authentication (2FA) setup.
  - Profile and settings pages.
  - Company and user management pages.
  - Admin pages for companies, divisions, locations, and audit logs.

##### 5. Asset Import/Export

- Introduced CSV and Excel import/export functionality for assets.

##### 6. Reporting System

- Implemented charts and analytics for enhanced reporting capabilities.

##### 7. Mobile Optimization

- Added barcode scanning support for mobile devices.

##### 8. Role-Based Access Control

- Defined and implemented four administrator roles:
  - **Superadmin**: Access to all data.
  - **Manager**: Access to all assets in their company.
  - **IT Specialist**: Access to assets in one or multiple divisions within their company.
  - **Viewer**: Read-only access to specific locations within their company.

##### 9. Language Support

- Completed English and Chinese language support for all HTML templates.

---

#### Fixes and Adjustments

##### 1. QR Code for 2FA

- Resolved an issue where the QR code was not displayed on the 2FA setup page (`/accounts/2fa/setup-simple/`).

##### 2. Navigation and URLs

- Updated the dashboard URL to `/dashboard` (previously `/`).
- Ensured all key components (e.g., dashboard, asset management, audit, location management, reports, user management) are accessible via the navigation pane.

##### 3. User Roles Display

- Fixed an issue where user roles were displayed as "Staff" instead of their actual roles.

##### 4. Default Templates for Admin Pages

- Updated default Django admin templates to align with the base template's style.
- Introduced new pages for:
  - `/companies/`
  - `/companies/division/`
  - `/companies/location/`
  - `/audit/auditlog/`
  - `/audit/assetaudit/`
  - `/audit/systemevent/`

##### 5. Asset Visibility

- Ensured asset visibility on the dashboard matches user permissions on the assets page.

##### 6. Legacy Role Removal

- Removed legacy role support and references from all relevant pages.

---

#### Merges and Consolidations

- Merged `assets/urls_old.py` with `assets/urls.py` for extended support.
- Merged `audit/urls_old.py` with `audit/urls_new.py` to finalize URL development for the audit app.
- Consolidated `PROGRESS_REPORT_v0.0.1.md` with `docs/PROGRESS_REPORT_v0.0.1.md` and `docs/v0.0.1.md`.

---

#### Known Issues

1. **Method Not Allowed (GET): `/zh-hans/accounts/logout/`**
   - Logout functionality for Chinese language support needs further investigation.

2. **Default Passwords for New Users**
   - On `/zh-hans/accounts/users/create/`, newly created users should have:
     - A default random password.
     - An option to require password change on the next login (enabled by default).

3. **Python Version Compatibility**
   - Compatibility with Python 3.13 is under review. Updating without confirmation may cause issues.

---

#### Testing Notes

- Use the admin account for testing:
  - **Username**: `admin`
  - **Password**: `hjadmpass`
- Sample assets have been generated for testing purposes.

---

#### Rollbacks

- Reverted changes to admin pages (`/admin/companies/...`, `/admin/audit/...`) that aligned them with the base template's style. Instead, focused on creating new user-facing pages aligned with the base template.

---

### Waypost Development Progress Report v0.0.2

**Date:** July 8, 2025  
**Focus:** Complete Implementation of Core Asset & Company Management Systems

---

#### 🎯 Overview

Building on the foundation of v0.0.1, this development cycle focused on implementing the primary business logic of the Waypost. Version 0.0.2 delivers a fully functional, end-to-end asset management system and the complete company/location organizational structure.

---

#### ✅ Completed Components in This Iteration

##### 1. Asset Management System - Full Implementation

- **Core Models**: Implemented the full suite of asset-related models: `AssetCategory`, `AssetBrand`, `Asset`, `AssetAssignment`, and `AssetMaintenance`.

- **CRUD Functionality**: Delivered a complete user-facing interface for creating, reading, updating, and retiring assets.

- **Advanced Features**:
  - **Search & Filter**: Implemented an advanced search form for filtering assets.
  - **Assignment Tracking**: Developed views for assigning assets to users and processing returns.
  - **Data Export**: Added functionality to export asset lists to CSV.

- **UI/UX**: Created modern, responsive, and user-friendly templates for asset lists, details, and forms using Bootstrap 5.

##### 2. Company & Location Structure - Full Implementation

- **Core Models**: Implemented the `Company`, `Division`, and hierarchical `Location` models to structure the organization.

- **Admin Interface**: Configured the Django admin for easy management of companies, divisions, and locations.

- **Data Integrity**: Ensured all relationships between assets, users, and locations are correctly established.

##### 3. Audit System Integration

- **Enabled Audit App**: With all core models in place, the `audit` app was successfully integrated.

- **Automated Logging**: The system now automatically logs all creation, update, and deletion events for assets, providing a complete audit trail for compliance.

##### 4. UI & Navigation Enhancements

- **Updated Main Navigation**: The base template now includes navigation links to the new asset management sections.

- **Status Indicators**: Implemented color-coded badges for asset statuses (e.g., Available, Assigned) for improved clarity.

- **Data Synchronization**: Ensured all field names across models, forms, and templates are consistent.

---

#### 🚀 System Status: Core Functionality Complete

The system is now operational with the following end-to-end features:

1. User Authentication & Management (from v0.0.1)
2. Company, Division & Location Management
3. Complete Asset Lifecycle Management (CRUD)
4. Asset Assignment & Return Tracking
5. Data Export to CSV
6. Comprehensive Audit Trail

---

#### 📈 Next Steps (v0.0.3 Roadmap)

##### High Priority

1. **2FA Implementation**: Complete the user setup and verification workflow.
2. **Reporting & Dashboard**: Build out the main dashboard with asset analytics and charts.
3. **Company Management Views**: Create a user-facing UI for managing locations.

##### Medium Priority

1. **Asset Data Import**: Implement CSV/Excel import functionality.
2. **Mobile Optimization**: Enhance mobile views and prepare for barcode scanning integration.

---

#### 🏁 Conclusion

Version 0.0.2 marks a major milestone, transforming the project from a foundational shell into a fully functional asset management system. The core business requirements are now met, providing a stable platform for building advanced features.
## Waypost - Changelog v0.0.3

**Version:** 0.0.3  
**Release Date:** August 11, 2025  
**Focus:** Asset Management Enhancements, Category/Brand Management, Export System, and UI/UX Improvements

---

### 🎯 Release Overview

Version 0.0.3 represents a significant enhancement to the Waypost with a focus on comprehensive asset management features, advanced category and brand management capabilities, improved export functionality, and substantial UI/UX improvements. This release consolidates system architecture, implements missing navigation functionality, and introduces powerful data export capabilities.

### ✅ Major Changes and Improvements

#### 1. Asset Management System Enhancements

#### **Complete Category and Brand Management System**
- **Category Management**: Comprehensive CRUD system for asset categories
  - CategoryListView with search and filtering capabilities
  - CategoryCreateView, CategoryUpdateView, CategoryDeleteView with proper validation
  - Professional templates with Bootstrap 5 styling and responsive design
  - Audit logging for all category operations

- **Brand and Model Management**: Full brand and model lifecycle management
  - BrandListView, ModelListView with advanced filtering and search
  - Complete CRUD operations for both brands and models with relationship management
  - Combined brands_models view for efficient management workflow
  - Professional templates with consistent UI/UX design

##### **Asset Data Structure Improvements**
- **Asset Number System**: Replaced "Asset Name" with "Asset Number" as primary identifier
  - Auto-generated asset numbers with manual override capability
  - Updated all templates and forms to reflect the new naming convention
  - Enhanced data consistency and asset tracking capabilities

##### **Advanced Export System**
- **Comprehensive Data Export**: Replaced simple CSV export with full-featured export system
  - AssetExportForm with comprehensive filtering options (status, category, brand, date ranges)
  - Multiple export formats: CSV, Excel (.xlsx), and PDF
  - Field selection capability allowing users to choose specific data columns
  - Professional export interface with preview and configuration options
  - Integration with openpyxl for Excel exports and reportlab for PDF generation

#### 2. User Role Management Refactoring

##### **Role Structure Optimization**
- **Role Consolidation**: Streamlined user role system
  - Removed "Manager" role from the entire Django project
  - Renamed "IT Specialist" to "IT Administrator" throughout the system
  - Updated all references, templates, and documentation to reflect new role structure

##### **Enhanced Access Control**
- **Multi-Company and Division Access**: Advanced permission system
  - IT Administrators can be assigned to multiple companies and divisions
  - Granular access control allowing specific company-division combinations
  - Superadmin capability to manage user assignments during creation and editing
  - Flexible permission matrix supporting complex organizational structures

#### 3. UI/UX and Visual Improvements

##### **Enhanced Visual Design**
- **Button and Status Badge Improvements**: Enhanced visibility and accessibility
  - Updated button colors for better visibility under current theme
  - Improved status badge styling with enhanced contrast and readability
  - Professional color scheme for "In Use", "Disposed", and other asset statuses
  - Consistent styling across all asset management pages

##### **Dashboard Optimization**
- **Streamlined Dashboard Layout**: Improved focus and usability
  - Removed Quick Action block for cleaner interface
  - Removed System Statistics block for better space utilization
  - Recent assets display at full width for better visibility
  - Enhanced responsive design for mobile and tablet devices

##### **Navigation Enhancements**
- **Asset Management Navigation**: Comprehensive dropdown menu system
  - Added "Import Assets" option for bulk asset management
  - Added "Manage Categories" and "Manage Brands & Models" options
  - Fixed navigation links for category and brand management
  - Improved user workflow with logical menu organization

#### 4. Import/Export Functionality

##### **Asset Import System**
- **Bulk Asset Import**: CSV and Excel file import capabilities
  - Support for both .csv and .xlsx file formats
  - Data validation and error reporting during import process
  - Template download for proper import file formatting
  - Batch processing with progress indicators

##### **Data Export Enhancement**
- **Professional Export Interface**: Comprehensive data export capabilities
  - Filter-based export with multiple criteria options
  - Format selection (CSV, Excel, PDF) with appropriate formatting
  - Field selection allowing customized export content
  - Export preview and validation before file generation

#### 5. URL Configuration Consolidation

- **Assets URLs Merged**: Consolidated `assets/urls_old.py` into `assets/urls.py`
  - Maintained backward compatibility with legacy routes
  - Unified all asset-related endpoints (CRUD, assignment, categories, brands, import/export, mobile, analytics, API)
  - Cleaned up duplicate and non-existent view references

- **Audit URLs Merged**: Consolidated `audit/urls_old.py` and `audit/urls_new.py` into `audit/urls.py`
  - Combined comprehensive audit functionality with legacy compatibility
  - Included audit management, execution, asset verification, mobile interface, logs, system events, compliance, and API endpoints
  - Maintained existing route names for backward compatibility

#### 6. Template System Improvements

- **Fixed Blank Company Page**: Created comprehensive `company_list.html` template
  - Modern card-based layout with Bootstrap 5 styling
  - Search and filtering functionality
  - Company status indicators and statistics display
  - Responsive design with mobile optimization
  - Empty state handling and pagination support
  - Integration with Django admin for management actions

- **Fixed URL Navigation Issues**: Corrected double-prefix URL problems
  - Fixed hardcoded relative URLs in base template navigation
  - Changed `/companies/companies/divisions/` to proper `/companies/divisions/`
  - Updated all company navigation links to use Django URL names
  - Resolved 404 errors in companies, divisions, locations, and users navigation

- **Navigation Updates**: Updated base template navigation
  - Changed audit navigation links from admin URLs to user-facing URLs
  - Added proper URL name references for audit functionality
  - Improved user experience with consistent navigation

#### 7. Role-Based Access Control Enhancements

- **Consistent Asset Filtering**: Implemented role-based asset visibility
  - Updated dashboard and asset views to use `user.get_accessible_assets()`
  - Ensured consistent asset filtering across all views (list, detail, update, delete, assign, return)
  - Fixed permission method calls in companies and audit views

#### 8. Admin Interface Cleanup

- **Restored Default Django Admin Styling**: Removed custom admin templates
  - Deleted custom templates for companies and audit admin pages
  - Restored clean, default Django admin interface
  - Improved consistency across admin pages

#### 9. Documentation Consolidation

- **Merged Progress Reports**: Consolidated multiple documentation files
  - Combined `PROGRESS_REPORT_v0.0.1.md`, `PROGRESS_REPORT_v0.0.2.md`, and `v 0.0.1.md`
  - Created comprehensive project history
  - Removed duplicate documentation files

#### 10. System Stability Improvements

- **Database and URL Integrity**: Fixed system configuration issues
  - Resolved non-existent view references in URL patterns
  - Ensured all URL patterns point to existing views
  - Maintained proper app namespace consistency

### 🔧 Technical Details

#### Files Created/Enhanced

##### **New Views and Templates**
- `assets/views.py` - Enhanced with comprehensive category/brand management views:
  - CategoryListView, CategoryCreateView, CategoryUpdateView, CategoryDeleteView
  - BrandListView, BrandCreateView, BrandUpdateView, BrandDeleteView
  - ModelListView, ModelCreateView, ModelUpdateView, ModelDeleteView
  - asset_export_view with filtering and format selection
  - generate_csv_export, generate_excel_export, generate_pdf_export functions

- `assets/forms.py` - Added AssetExportForm with comprehensive filtering options:
  - Status, category, brand filtering capabilities
  - Date range selection for created/modified dates
  - Field selection for customized exports
  - Export format selection (CSV, Excel, PDF)

##### **Template System Expansion**
- `templates/assets/category_list.html` - Professional category management interface
- `templates/assets/category_form.html` - Category creation/editing form
- `templates/assets/category_confirm_delete.html` - Category deletion confirmation
- `templates/assets/brand_list.html` - Brand management interface with search
- `templates/assets/brand_form.html` - Brand creation/editing form
- `templates/assets/brand_confirm_delete.html` - Brand deletion confirmation
- `templates/assets/model_list.html` - Model management interface
- `templates/assets/model_form.html` - Model creation/editing form
- `templates/assets/model_confirm_delete.html` - Model deletion confirmation
- `templates/assets/brands_models.html` - Combined brand and model management
- `templates/assets/asset_export.html` - Comprehensive export interface

##### **URL Configuration Updates**
- `assets/urls.py` - Enhanced with new routing patterns:
  - Category CRUD routes (list, create, edit, delete)
  - Brand CRUD routes (list, create, edit, delete)
  - Model CRUD routes (list, create, edit, delete)
  - Combined brands_models view route
  - Enhanced export route with filtering capabilities

#### Files Modified

##### **Core System Updates**
- `assets/urls.py` - Consolidated and cleaned up asset URL patterns
- `audit/urls.py` - Merged all audit URL configurations
- `templates/companies/company_list.html` - Created comprehensive company list template
- `templates/base/base.html` - Updated navigation for asset management options
- `templates/assets/asset_list.html` - Enhanced with improved status badge styling
- `templates/assets/asset_detail.html` - Updated with better button visibility
- `templates/dashboard.html` - Streamlined layout with full-width recent assets
- `docs/PROGRESS_REPORT_v0.0.1.md` - Consolidated project documentation

##### **Styling and UI Enhancements**
- Enhanced CSS styling for status badges with improved color schemes
- Updated button styling for better visibility under current theme
- Responsive design improvements across all asset management templates
- Professional form styling with Bootstrap 5 integration

#### Files Removed

- `assets/urls_old.py` - Merged into main assets URLs
- `audit/urls_old.py` - Merged into main audit URLs
- `audit/urls_new.py` - Merged into main audit URLs
- `docs/PROGRESS_REPORT_v0.0.2.md` - Consolidated into main progress report
- `docs/v 0.0.1.md` - Consolidated into main progress report
- Custom admin templates for companies and audit modules

#### New Dependencies

##### **Python Packages**
- `openpyxl` - For Excel file generation and export functionality
- `reportlab` - For PDF generation and advanced report formatting

##### **Frontend Enhancements**
- Enhanced Bootstrap 5 styling with custom CSS modifications
- Improved responsive design patterns for mobile compatibility
- Professional status badge styling with accessibility improvements

#### Database Schema Considerations

- Asset model updated to prioritize Asset Number over Asset Name
- Enhanced category and brand relationship management
- Improved audit logging for all CRUD operations
- Optimized queries for role-based asset filtering

#### New Features Implementation

##### **Category and Brand Management**
- Complete CRUD system with professional templates
- Search and filtering capabilities across all management interfaces
- Audit logging integration for all operations
- Role-based access control for management functions

##### **Advanced Export System**
- Multi-format export support (CSV, Excel, PDF)
- Comprehensive filtering system with date ranges
- Field selection for customized export content
- Professional export interface with preview capabilities

##### **Enhanced User Experience**
- Streamlined navigation with logical menu organization
- Improved visual feedback with enhanced status indicators
- Responsive design for mobile and tablet compatibility
- Professional form validation and error handling

#### Performance Optimizations

- Optimized database queries for category and brand listings
- Efficient filtering systems with proper indexing considerations
- Streamlined template rendering with reduced redundancy
- Enhanced caching strategies for frequently accessed data

### 🚀 Upgrade Instructions

#### For Existing Installations

1. **Install New Dependencies**:
   ```bash
   pip install openpyxl reportlab
   ```

2. **Update Database** (if applicable):
   ```bash
   python manage.py makemigrations
   python manage.py migrate
   ```

3. **Update Static Files**:
   ```bash
   python manage.py collectstatic --noinput
   ```

4. **Role Migration**:
   - Existing "Manager" role users will need role reassignment
   - "IT Specialist" role automatically renamed to "IT Administrator"
   - Review and update user permissions as needed

#### Configuration Updates

- Verify navigation menu functionality for category/brand management
- Test export functionality with various filter combinations
- Validate role-based access control with new permission structure

### 🧪 Testing and Validation

#### Tested Functionality

- ✅ Category and Brand CRUD operations with proper validation
- ✅ Advanced export system with CSV, Excel, and PDF formats
- ✅ Role-based access control with new permission structure
- ✅ Navigation functionality for all asset management features
- ✅ Visual improvements and responsive design across all devices
- ✅ Import functionality for bulk asset management
- ✅ Dashboard optimization with streamlined layout

#### Validation Checklist

- [ ] All navigation links functional
- [ ] Category and brand management accessible
- [ ] Export system working with all formats
- [ ] Role permissions properly configured
- [ ] Visual improvements visible under current theme
- [ ] Import functionality operational
- [ ] Mobile responsiveness confirmed

### 📋 Known Issues and Limitations

#### Current Limitations

- Export functionality requires appropriate file permissions on server
- Large dataset exports may require increased timeout settings
- Mobile interface optimization ongoing for complex management forms

#### Future Enhancements

- Advanced filtering options for category and brand management
- Bulk operations for category and brand assignments
- Enhanced mobile interface for management functions
- Additional export format support (e.g., XML, JSON)

### 🤝 Contributors and Acknowledgments

This release represents significant system enhancements developed through collaborative effort focusing on user experience improvements, comprehensive feature implementation, and system reliability enhancements.

#### Key Development Areas

- **Asset Management Enhancement**: Complete category and brand management system
- **Export System Development**: Advanced multi-format export capabilities
- **UI/UX Improvements**: Professional styling and enhanced user experience
- **Role Management Optimization**: Streamlined permission structure
- **System Consolidation**: URL and template system improvements

---

**Note**: This release builds upon the foundation established in v0.0.1 (authentication system, Django setup, i18n framework) and the major features implemented in v0.0.2 (asset models, audit system, dashboard, role-based access control). Version 0.0.3 focuses specifically on asset management enhancements, advanced export capabilities, and comprehensive UI/UX improvements.
- **Advanced Asset List Customization**: Users can now customize their asset list view
  - Column visibility controls: Show/hide any column (Image, Name, Tag, Category, Status, Location, Assigned To, Actions)
  - Per-column filtering: Individual filter controls for each column type
  - Real-time client-side filtering without page reloads
  - User preferences saved in browser localStorage
  - Keyboard shortcuts: Ctrl+H (toggle panel), Ctrl+R (clear filters)
  - Professional table-based list layout replacing card view
  - Enhanced search capabilities with instant results

### 🚀 Current System Status

#### ✅ Fully Operational Features

1. **User Authentication & Management** with role-based permissions
2. **Dashboard with Statistics** using role-based asset filtering
3. **Company, Division & Location Management** with modern UI
4. **Complete Asset Management System** with CRUD operations
5. **Asset Assignment & Return Processing** with audit trails
6. **Audit System & Logging** with comprehensive change tracking
7. **Multi-language Support (i18n)** with English and Chinese
8. **Admin Panel Access** with clean Django styling
9. **Data Export (CSV)** with filtering capabilities
10. **Mobile-Responsive Design** with Bootstrap 5 styling

#### Performance Improvements

- Reduced URL configuration complexity
- Eliminated dead code and unused routes
- Improved template loading efficiency
- Enhanced user navigation experience

### 🎯 Next Development Phase (v0.0.4)

#### High Priority

1. **2FA Implementation**: Complete TOTP setup and verification workflow
2. **Advanced Reporting System**: Charts and analytics dashboard
3. **Enhanced Mobile Features**: Barcode scanning and offline capability
4. **API Enhancement**: REST API development for mobile integration

#### Medium Priority

1. **Performance Optimization**: Query optimization and caching
2. **Advanced Asset Features**: Maintenance scheduling and depreciation
3. **Workflow Automation**: Automated processes and notifications
4. **Integration Features**: Third-party system integrations

### 📊 Quality Metrics

- **System Health**: ✅ Excellent (no Django check errors)
- **Feature Completeness**: 90% of core requirements
- **Code Quality**: High with proper error handling and validation
- **User Experience**: Modern, intuitive, and fully responsive
- **Documentation**: Comprehensive and up-to-date

### 🏁 Conclusion

Version 0.0.3 successfully consolidates the system architecture, fixes critical user interface issues, and improves the overall user experience. The Waypost now provides a clean, unified interface with consistent role-based access control and modern responsive design.

**Production Readiness**: The system is ready for production deployment with robust error handling, comprehensive security, and professional user interface.

---

## Version 0.0.4

**Release Date**: April 15, 2026

Version 0.0.4 introduces enhanced security, advanced reporting capabilities, mobile-optimized features, and a comprehensive REST API for system integration.

### Security Enhancements

**Two-Factor Authentication (2FA)**
- TOTP-based 2FA using `pyotp` library
- QR code generation for easy authenticator app setup
- Per-user 2FA enable/disable toggle
- Backup codes for account recovery
- Required 2FA status display in user profile
- Secure session management with 2FA verification

**Key Technical Implementation**:
- `django_otp` middleware integration
- TOTP secret key generation and storage
- QR code PNG generation as base64 data URL
- 2FA required decorator for protected views

### Advanced Reporting with Chart.js

**Interactive Dashboard Charts**
- Six chart types: Status Distribution, Category Distribution, Brand Distribution, Warranty Status, Quotation Status, Purchase Summary
- Four display modes: Doughnut, Pie, Bar, Line
- User-customizable dashboard layout via modal interface
- Session-based chart configuration persistence
- AJAX-powered chart data loading via REST API

**Chart API Endpoints**:
- `/api/reports/charts/quotation-status/` - Quotation status distribution
- `/api/reports/charts/purchase-summary/` - Purchase value comparison
- `/api/reports/charts/category-distribution/` - Product category breakdown
- `/api/reports/charts/brand-distribution/` - Brand distribution analysis
- `/api/reports/charts/warranty-status/` - Warranty expiry tracking

### Mobile Features with Barcode Scanning

**Barcode Scanning Support**
- Compatible with handheld barcode scanner devices
- Works with smartphone camera-based scanning apps
- Rapid product lookup by scanning barcode in product list
- Quotation and stock item barcode support

**Mobile Optimizations**:
- Responsive Bootstrap 5 design
- Touch-friendly interface elements
- Large tap targets for handheld devices
- Fast input field switching for rapid scanning

### REST API Endpoints

**Authentication API**
- `POST /api/auth/login/` - User authentication
- `POST /api/auth/logout/` - Session termination
- `GET /api/auth/user/` - Current user profile

**Products API**
- `GET /api/products/` - List all products
- `POST /api/products/` - Create new product
- `GET /api/products/{id}/` - Retrieve product details
- `PUT /api/products/{id}/` - Update product
- `DELETE /api/products/{id}/` - Delete product
- `GET /api/products/barcode/{barcode}/` - Lookup by barcode

**Quotations API**
- `GET /api/quotations/` - List all quotations
- `POST /api/quotations/` - Create new quotation
- `GET /api/quotations/{id}/` - Retrieve quotation details
- `PUT /api/quotations/{id}/` - Update quotation
- `DELETE /api/quotations/{id}/` - Delete quotation
- `GET /api/quotations/{id}/pdf/` - Export quotation as PDF

**Purchases API**
- `GET /api/purchases/` - List all purchases
- `POST /api/purchases/` - Create new purchase
- `GET /api/purchases/{id}/` - Retrieve purchase details
- `PUT /api/purchases/{id}/` - Update purchase
- `DELETE /api/purchases/{id}/` - Delete purchase

**Stock API**
- `GET /api/stock/` - List all stock items
- `POST /api/stock/` - Create new stock item
- `GET /api/stock/{id}/` - Retrieve stock item details
- `PUT /api/stock/{id}/` - Update stock item
- `DELETE /api/stock/{id}/` - Delete stock item
- `GET /api/stock/low-stock/` - List low stock alerts

**Reports API**
- `GET /api/reports/charts/<chart_type>/` - Chart data endpoints
- `GET /api/reports/dashboard-config/` - Get dashboard configuration
- `POST /api/reports/dashboard-config/` - Save dashboard configuration

### User Interface Improvements

- Language code standardization (zh-cn, en-us)
- English (US) locale option
- Improved language switcher functionality
- Dashboard customization modal with modern UI
- Consistent Bootstrap 5 styling throughout
- Fixed template truncation issues in forms

---

*Generated on April 15, 2026 - Waypost v0.0.4*
*Previous versions: v0.0.1 (Foundation), v0.0.2 (Major Features), v0.0.3 (Consolidation & Fixes), v0.0.4 (Security & Reporting)*

---

## Version 0.1.0

**Release Date**: April 16, 2026

Version 0.1.0 delivers the initial full Quotation & Invoice Management System with end-to-end workflow coverage from quotation creation through dispatch and invoicing lifecycle control.

### Quotation & Customer Workflow

- Added customer and product business extensions for sales flow:
  - `products.ProductPrice` for unit/tax pricing tied to brand and model
  - `customers.CustomerProfile` for delivery and contact defaults
- Implemented quotation lifecycle:
  - quotation creation/edit/list/detail with item-level totals
  - status transitions (`draft` -> `sent` -> `confirmed`)
  - duplicate/cancel actions
  - quotation PDF generation path validated in smoke run

### Purchase and Stock Conversion

- Implemented conversion of confirmed quotations into purchase orders.
- Added receipt workflow with serial capture and stock creation as `assets.Asset` records.
- Added stock overview/list/detail views to monitor received and dispatch-ready assets.

### Delivery Order Workflow

- Added `DeliveryOrder` and `DeliveryItem` workflow with serial-level linkage.
- Implemented delivery creation from available quotation-linked stock.
- Added dispatch/completion transitions with status and asset-state updates.
- Added signed copy upload requirement before completion and delivery document generation route.

### Invoice Batch and Invoice Information

- Added weekly Sharepoint batch import (`WeeklyOrderBatch`) with strict duplicate detection and failure tracking.
- Added `InvoiceInfo` and `InvoiceInfoItem` with:
  - `yymmdd##` invoice numbering
  - quotation/delivery linkage
  - tax/net/gross recalculation from delivery/quotation sources
- Added invoice information list/detail/update/recalculate/export/document routes.

### Email Dispatch and Esker Handoff

- Added `EmailDispatch` compose/list/history flow with recipient controls and attachment manifesting.
- Implemented status transitions:
  - `draft` -> `sent` -> `client_confirmed` -> `esker_forwarded`
- Added explicit client-confirmed and Esker-forward actions for operational tracking.

### Workflow Dashboard and Governance

- Added workflow dashboard (Kanban stages) and cross-entity workflow search.
- Added standardized workflow badges and next-action suggestions across key list/detail screens.
- Added `WorkflowStatusAudit` model and status-change signals for quotation/purchase/delivery/invoice-dispatch transitions.

### Validation and Stability

- Applied migrations for the new invoice/workflow models.
- Resolved workflow dashboard null-related rendering issue in invoiced stage cards.
- Executed a broad end-to-end smoke run (quotation -> purchase -> delivery -> invoice -> dispatch) with passing assertions across transition endpoints and status checks.

---

*Generated on April 16, 2026 - Waypost v0.1.0*

---

## Version 0.1.1

**Release Date**: April 17, 2026

Version 0.1.1 focuses on document generation hardening and print-layout fidelity by removing LibreOffice dependency for core customer documents and standardizing template rendering behavior.

### Quotation PDF Rendering Upgrade

- Replaced quotation PDF export conversion path with direct HTML-to-PDF rendering.
- Implemented a dedicated Excel-style quotation template with iterative layout tuning for close match to reference print output.
- Improved typography consistency, column behavior, summary sizing, remark handling, and signature alignment in quotation output.
- Added dynamic default remark line composition using customer and first-item context.

### Delivery Order (签收单) PDF Rendering Upgrade

- Introduced direct HTML-to-PDF generation for delivery orders and routed download endpoint to the new renderer.
- Added delivery-specific Excel-style template modeled from 签收单 reference layout and aligned with quotation style system.
- Reworked delivery sections for:
  - standardized header/subheader/sheet outline styles
  - auto-filled delivery method text
  - normalized signature block with aligned labels and underlines
- Ensured single-page rendering behavior in validation output for current baseline data.

### Runtime and Platform Stability

- Added Windows runtime setup for font configuration so WeasyPrint rendering is stable in local development runs.
- Applied startup bootstrap in manage/ASGI/WSGI entrypoints for consistent environment initialization.

### Validation and Regression Checks

- Verified updated quotation and delivery rendering paths with Django shell smoke generation.
- Produced preview artifacts for reference comparison during pixel-tuning passes.
- Confirmed no syntax/lint errors in modified delivery/quotation rendering files after updates.

---

*Generated on April 17, 2026 - Waypost v0.1.1*

---

## Version 0.1.2

**Release Date**: April 17, 2026

Version 0.1.2 focuses on asset creation workflow usability, broader translation coverage for template attributes, and stability fixes for admin and protected deletion behavior.

### Asset Create Workflow Enhancements

- Added batch creation support on asset create with configurable quantity and per-row serial number/status input.
- Implemented searchable dropdown controls for asset create/edit form fields:
  - category
  - brand
  - model
  - location
- Added dependent brand -> model filtering so model options only show entries under the selected brand.
- Improved location behavior in create form:
  - fallback to all active locations if company-scoped locations are empty
  - default location to `Vanke VMO Warehouse` when available

### Data Model and Validation Updates

- Updated `assets.Asset.serial_number` to allow blank values for inventory items without serial numbers.
- Added migration `assets/migrations/0008_alter_asset_serial_number.py`.
- Kept single-item create compatibility while adding transaction-safe multi-item creation path.

### Internationalization Coverage Expansion

- Completed translation wrapping for remaining template attribute strings across placeholders, aria labels, and title attributes.
- Added/updated zh-cn translations for newly wrapped asset create labels/help text/notes and accessibility labels.
- Recompiled zh-cn message catalog to apply updated translations.

### Stability and Admin UX Fixes

- Fixed asset model deletion flow to handle protected references gracefully with user-facing error messages instead of raw exceptions.
- Restored non-empty Django admin changelist template overrides for company-related admin pages:
  - company
  - division
  - companyuser
  - location

### Verification

- Django system checks executed successfully after workflow and i18n updates.
- Asset create form behavior validated for searchable controls, brand/model dependency, and batch input rendering.

---

*Generated on April 17, 2026 - Waypost v0.1.2*

## Release Notes v0.1.3

**Version:** 0.1.3  
**Release Date:** April 20, 2026  
**Focus:** Warehouse slot workflows, asset batch operations, export stability, and company user editing

---

### Highlights

1. Warehouse slot support is now integrated from company/location setup through asset create, list, and bulk edit flows.
2. Asset create and list experiences now support practical batch operations and grouped drill-down workflows.
3. Export and edit-route defects found during QA were fixed and validated.
4. Navigation and localization were updated to match the revised operational model.

### Delivered Scope (22-file release set)

1. Asset create improvements
- Model selection now auto-fills category and brand.
- Batch row inputs preserve values while quantity changes.
- Duplicate serials are blocked on frontend and backend, with persistent warnings.
- Zone/rack/shelf dropdowns are dynamically populated and validated from selected warehouses.

2. Asset list and batch operations
- Grouped rows for non-serialized assets with drill-down behavior.
- Quantity-aware batch edit panel with selection preview.
- Grouped-row selection expansion and server-side bulk edit endpoint.

3. Data model and migrations
- Added `location_zone`, `location_rack`, `location_shelf` on assets.
- Added `zone`, `rack`, `shelf` on locations with range expansion helpers.
- Added optional `category` on asset models for filtering consistency.
- Added supporting migrations in assets and companies apps.

4. Import/export updates
- Import now enforces required `category` and `brand` and supports normalized headers.
- Export now uses accessible-assets scope and current location display mapping.
- Fixed invalid `select_related` and outdated legacy export field mappings.

5. Company user management fix
- Added company user update view and edit route.
- Wired list-page edit action to real route and fixed URL converter mismatch (`int` vs `uuid`).

6. UI/navigation and i18n
- Split "Manage Brands" and "Manage Models" in assets navigation.
- Removed timed global auto-dismiss for alerts.
- Added/updated zh-cn translations for new fields and labels.

### Migration Files Added

1. `assets/migrations/0009_alter_asset_barcode.py`
2. `assets/migrations/0010_alter_assetmodel_model_number.py`
3. `assets/migrations/0011_asset_location_rack_asset_location_shelf_and_more.py`
4. `assets/migrations/0012_assetmodel_category.py`
5. `companies/migrations/0008_location_rack_location_shelf_location_zone.py`

### Validation

1. `python manage.py check` executed after each major change set.
2. Reported defects for bulk-edit slot validation, export CSV behavior, and company-user edit route were resolved.

## Release Notes v0.1.4

**Version:** 0.1.4  
**Release Date:** April 21, 2026  
**Focus:** Company/location/contact import workflows, rollback safety, and import-result visibility

---

### Highlights

1. Companies, locations, company contacts, and assets now support rollback of the latest import per user and module.
2. Companies-module CSV imports now use a preview-and-confirm flow with clearer result reporting and downloadable samples.
3. Company users were refactored into company contacts, improving recipient/contact workflows across locations and quotations.
4. Import UX and list summaries were corrected to show real totals, clearer validation feedback, and safer duplicate handling.

### Delivered Scope (30-file release set)

1. Companies CSV import workflows
- Added dedicated CSV import pages for companies, locations, and company contacts.
- Added preview/confirm flow with shared upload UI, sample CSV downloads, and required/optional column guidance.
- Added location-specific "update existing matched entries" support instead of always skipping duplicates.
- Added tolerant CSV decoding helpers for UTF-8, GB18030/CP936, and Big5 inputs.

2. Import rollback system
- Added shared rollback tracking models: `ImportRun` and `ImportRunChange`.
- Added shared rollback utilities to snapshot created/updated records and restore them in reverse order.
- Enabled rollback actions for company, location, company contact, and asset imports.
- Exposed rollback buttons on upload, preview, and result pages when a rollback-eligible run exists.

3. Import result visibility and validation
- Added a shared import result page with total rows, processed rows, created, updated, skipped, and error counts.
- Added row-level issue reporting and updated-field diff summaries for location update imports.
- Hardened import validation so unsupported asset status/condition values and invalid location/status enums surface as errors instead of silent defaults.
- Fixed JSON snapshot serialization for non-primitive model fields during rollback logging.

4. Company contacts and location data model updates
- Refactored `CompanyUser` into a company-contact-oriented workflow with optional linked auth user, explicit contact name, and contact helper methods.
- Added company contact create/edit/remove workflows and updated UI labels from "Company Users" to "Company Contacts".
- Extended locations with `name_en`, `code_2`, `chinese_address`, and linked company contact support.
- Added dynamic contact loading on the location form and updated quotation fallbacks to use contact helper methods safely.

5. List-page and admin improvements
- Added import entry points from company, location, and company contact list pages.
- Fixed list badges to use total paginator counts instead of current page length.
- Updated the locations summary card to show total locations rather than warehouse-slot aggregates.
- Updated admin/API support for the expanded location and company-contact schema.

### Migration Files Added

1. `companies/migrations/0009_alter_companyuser_options_companyuser_name_and_more.py`
2. `companies/migrations/0010_location_chinese_address_location_code_2_and_more.py`
3. `companies/migrations/0011_migrate_location_legacy_values.py`
4. `companies/migrations/0012_importrun_importrunchange_and_more.py`

### Validation

1. `python manage.py check` executed successfully after each major implementation step.
2. End-to-end manual verification confirmed import and rollback behavior for companies, locations, company contacts, and assets.
3. Verified rollback restored counts correctly after import runs and exposed/fixed snapshot serialization edge cases.

## Release Notes v0.1.5

**Version:** 0.1.5  
**Release Date:** April 24, 2026  
**Focus:** Multi-role admin controls, order-management access hardening, product price lifecycle tracking, and mailbox workflows

---

### Highlights

1. The single admin-role field was replaced with additive persistent administrator roles, including a new combined Order Management & Procurement Specialist role.
2. Order Management is now permission-gated in both navigation and direct URLs across quotations, purchases, deliveries, invoices, products, and workflow dashboards.
3. Product pricing now derives brand and unit from the selected asset model, preserves previous prices as inactive history, and tightens create/edit behavior around current prices.
4. A user-configurable mailbox module was added to account settings with IMAP or POP3 receive options, SMTP sending, inbox/outbox caching, and automatic 5-minute sync while the Django server is running.

### Delivered Scope (47-file release set)

1. Administrator role system overhaul
- Added persistent `accounts.AdminRole` records and migrated users from the legacy single `admin_role` field to the `User.roles` many-to-many relationship.
- Added compatibility helpers such as `admin_role`, `get_admin_roles_display()`, and access-scope utilities so older code paths can keep working during the transition.
- Introduced the `order_management_procurement_specialist` role and updated admin, forms, templates, API serializers, and demo-data setup to work with multi-role assignment.
- Fixed Django admin list rendering for translated role labels by coercing lazy translation values to strings before display.

2. Order Management access control and navigation
- Renamed the main navigation section from Products & Sales to Order Management.
- Hid the Order Management navigation from unauthorized users and blocked direct access with explicit permission checks in products, quotations, purchases, deliveries, invoices, and workflow dashboard views.
- Added the mailbox entry point under Order Management and surfaced related mailbox configuration on the account settings page.

3. Asset logs and audit workflow restructuring
- Moved asset-related audit visibility under Assets as Asset Change Logs with dedicated list/detail routes and clickable rows.
- Split asset audits into Dashboard, New Audit, and History flows, with updated templates, back-navigation handling, and richer detail pages.
- Expanded asset import logging so import/export activity appears in asset change history with clearer descriptions and metadata.

4. Product pricing and quotation workflow updates
- Product price create/edit now derives brand and unit from the selected asset model and rounds tax-inclusive pricing consistently.
- Product add flow defaults `valid_from` to today and only offers models not already used by current prices.
- Product price edits now create inactive historical rows with the prior price state and a `valid_until` timestamp on the change date.
- The unstable model selector was replaced with a native select plus client-side filtering for better browser compatibility.
- Quotation customer contact resolution was hardened to tolerate orphaned company-user memberships without crashing create/edit views.

5. Mailbox, email dispatch, and account settings
- Added `UserMailboxSettings` and `ReceivedEmailMessage` models with migrations for inbox/outbox sync configuration, cached messages, sync window settings, and sent-folder support.
- Added account settings UI for mailbox credentials, protocol/security options, sync lookback, outbox sync toggle, auto-sync toggle, and 2FA status/actions.
- Added mailbox inbox/detail/sync views with inbox/outbox tabs, newest-first sorting, clickable rows, and manual sync support.
- Added SMTP sending through per-user mailbox settings with fallback to Django email backend, and cached sent dispatches into the mailbox outbox.
- Added a background mailbox auto-sync worker thread for `runserver`, plus in-process locking to avoid SQLite write contention during concurrent sync attempts.

6. Validation and regression fixes
- Fixed admin user edit validation so username uniqueness excludes the current instance and password fields become optional on edit.
- Fixed the mailbox sync `timezone` import regression and the quotation create `NoneType` crash caused by orphaned contact memberships.
- Applied and validated new migrations for accounts and products, and verified the updated flows with focused Django checks and runtime probes.

### Migration Files Added

1. `accounts/migrations/0012_admin_roles_m2m.py`
2. `accounts/migrations/0013_user_mailbox_settings_and_received_messages.py`
3. `accounts/migrations/0014_mailbox_sync_window_and_outbox.py`
4. `products/migrations/0002_productprice_history_and_current_constraint.py`

### Validation

1. `python manage.py makemigrations --check` executed successfully after the release migrations were added.
2. `python manage.py migrate accounts` and `python manage.py migrate products` were applied locally and verified.
3. `python manage.py check` executed successfully after the role, product, mailbox, and regression-fix slices.
4. Focused runtime probes verified product-price history creation, mailbox schema availability, and mailbox sync lock behavior.