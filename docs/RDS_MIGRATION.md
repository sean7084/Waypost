# Moving the production database to RDS — Waypost

**Applies to:** Waypost on the Aliyun ECS (Docker Compose, rootless) → Aliyun RDS for PostgreSQL
**Status:** **not performed.** No RDS instance exists yet. Nothing in this document is in effect until an
operator edits `/srv/waypost/app/.env`.
**Prerequisites:** [`DEPLOYMENT.md`](DEPLOYMENT.md) §2 (the compose stack), [`BACKUP_RESTORE.md`](BACKUP_RESTORE.md) §2b/§3
(restoring a dump), [`RELEASE_PROCEDURE.md`](RELEASE_PROCEDURE.md) §6 (how a release reaches this host)

This is the sibling of [`DATABASE_MIGRATION.md`](DATABASE_MIGRATION.md), which covers a different move:
SQLite → PostgreSQL. That document changes the *engine*; this one changes the *server*, and the application
code is not involved at all.

---

## 1. Why this is a configuration edit and not a code change

Two lines in `docker-compose.yml` resolve from `.env`:

```yaml
DATABASE_HOST: ${DATABASE_HOST:-db}
DATABASE_PORT: "${DATABASE_PORT:-5432}"
```

Unset, they produce exactly the values this file used to hardcode (`db`, `5432`), so deploying the change
that introduces them alters nothing. Setting `DATABASE_HOST` moves the application; nothing else does.

The **second** half of the edit is `DB_CHECKPOINT_MODE=external`, and it is not optional in practice. Before
it existed, `docker/bin/ci-deploy.sh` could only dump the compose `db` service. After the move that service
is stopped, so every deploy would either die at the checkpoint step or be waved through with
`ALLOW_MISSING_CHECKPOINT=1` — which silences the failure without restoring the safety net. The modes:

| `DB_CHECKPOINT_MODE` | What step 2 of a deploy does |
| --- | --- |
| `auto` *(default)* | Dump the compose `db` service if it is running; otherwise report that there is nothing here to dump. Today's behaviour, unchanged. |
| `compose` | Dump the compose `db` service, and fail loudly if it is not running. |
| `external` | Dump `DATABASE_HOST/PORT/NAME/USER/PASSWORD` **as the app container carries them** — i.e. RDS. |
| `none` | Take no per-deploy dump: the database has managed backups of its own. |

The mode is **explicit rather than inferred** on purpose. At the checkpoint step the running app container is
still the *previous* release, so during the cutover deploy the app still points at `db` while `.env` already
says RDS. "Is the `db` service running?" would therefore checkpoint the wrong database on the single most
important deploy of the migration.

Consequence: **`DATABASE_HOST` and `DB_CHECKPOINT_MODE` are one edit, not two.** A half-done edit leaves the
app on RDS and the deploy checkpoint on the old local database, which looks like a working system and is a
restore point that no longer contains your data.

Both keys live in `docker/bin/_pg_checkpoint.sh`'s contract and are documented in
[`../.env.example`](../.env.example); the helper is sourced by `ci-deploy.sh` and by
[`waypost-backup.sh`](../docker/bin/waypost-backup.sh) so the deploy checkpoint and the nightly backup can
never end up protecting two different databases.

---

## 2. Provision the instance

1. **Same VPC and same vSwitch as the ECS.** The ECS reaches RDS over the private network; a different VPC
   means VPC peering or public endpoints, both of which are a larger change than this runbook covers.
2. **No public endpoint.** Do not allocate one. If it must be reached from a workstation temporarily, allow
   it, connect, and revoke — the instance is otherwise unreachable from the internet by construction.
3. **PostgreSQL 16**, matching the compose `db` image. A newer server can restore an older dump; an older
   server **cannot** restore a `pg_dump` from a newer one, and you find that out at the worst possible moment.
4. **Security group inbound: 5432 (or the configured port) from the ECS's security group** — not from
   `0.0.0.0/0`, and not from the vSwitch CIDR alone if the vSwitch is shared with other tenants. Everything
   in §4–§7 runs from the ECS: the app container, the nightly backup, and the throwaway `pg_dump` container.
   If the port is not open, the cutover deploy fails at the checkpoint, which is the intended failure.
5. Storage, HA and backup window per the procurement decision in the Aliyun ops knowledge base. RDS automatic
   backups are *not* the deploy checkpoint: `ci-deploy.sh`'s pre-deploy dump is the one that is guaranteed to
   be taken seconds before a migration runs.

Create the role and database on the instance before the restore:

| Item | Value | Must equal |
| --- | --- | --- |
| Database | `waypost_db` | `POSTGRES_DB` in `.env` |
| Role | `waypost_django` | `POSTGRES_USER` in `.env` |
| Password | new, strong | `POSTGRES_PASSWORD` — which becomes `DATABASE_PASSWORD` in the app container |

Keeping the names identical means the `.env` edit is one line for the host plus one for the checkpoint.

---

## 3. Drill the external checkpoint first

Do this **before** the cutover release, as a deliberate rehearsal, not as the first time the branch runs.
`docs/RELEASE_PROCEDURE.md` §9.4 exists for this.

The useful drill is a real deploy of the tag that is already live, with the mode forced in the environment:

```bash
DB_CHECKPOINT_MODE=external FORCE_TARBALL_SOURCE=1 \
  /srv/waypost/bin/ci-deploy.sh <currently-live-tag>
```

`external` reads `DATABASE_*` from the **app container**. Until `.env` says otherwise the app container
carries `DATABASE_HOST=db`, so this dumps the local database *through the external path* — it exercises the
DSN read, the `docker image inspect postgres:16` pre-flight, the throwaway container's bind mount and the
empty-dump guard, with no data at risk. Confirm:

- the log line reads `database checkpoint written from app container DATABASE_* (host db): … (<size>)`;
- `/srv/waypost/backups/deploy.log`'s **newest row has a sixth field** holding that path (see §7);
- the dump is non-empty and restorable per [`BACKUP_RESTORE.md`](BACKUP_RESTORE.md) §8.

Two failure modes worth seeing in rehearsal:

```bash
DB_CHECKPOINT_MODE=extrenal  /srv/waypost/bin/ci-deploy.sh <tag>   # refused, naming the bad value
docker rmi postgres:16 && DB_CHECKPOINT_MODE=external /srv/waypost/bin/ci-deploy.sh <tag>
# -> "postgres:16 is not in this user's image store - run: docker pull postgres:16" at step 2,
#    not at the moment of a real rollback. That is why the pre-flight exists.
```

The runtime application image contains **no** `postgresql-client` (`libpq-dev` is installed only in the
`deps` stage of `docker/Dockerfile`), so the dump cannot run inside the app container. It runs in a throwaway
`postgres:16` container on the `waypost_default` network, writing into `${BACKUP_DIR}/pg` through a bind
mount — which is why the empty-dump guard still applies. `docker pull postgres:16` before the cutover if the
`db` service has never pulled it under this user's store.

---

## 4. Dump, restore, compare

Restore into RDS **while the application is still running against the compose database.** This is the only
ordering in which a row-count comparison means something: the source of truth has not moved yet.

```bash
cd /srv/waypost/app

# 1. Dump from the compose db service (the current source of truth).
docker compose exec -T db sh -c 'pg_dump -Fc -U "$POSTGRES_USER" "$POSTGRES_DB"' \
  > /srv/waypost/backups/pg/pre-rds-<STAMP>.dump
ls -l /srv/waypost/backups/pg/pre-rds-<STAMP>.dump   # must be non-empty; note the size

# 2. Restore into RDS. --no-owner --no-privileges because RDS roles are not the ones in the dump.
#    Read the password into a variable first: typed inline it lands in your shell history, and
#    passed as -e PGPASSWORD=<literal> it is visible in `ps`. docker/bin/_pg_checkpoint.sh avoids
#    both by writing a 0600 --env-file, which is what an unattended run must do.
read -rs RDS_PW && export RDS_PW
docker run --rm --network waypost_default \
  -v /srv/waypost/backups/pg:/in \
  -e PGPASSWORD="$RDS_PW" \
  postgres:16 pg_restore -h <rdsinternalendpoint> -U waypost_django -d waypost_db \
  --no-owner --no-privileges /in/pre-rds-<STAMP>.dump
unset RDS_PW
```

A few rows failing under `--no-privileges` is expected for objects RDS owns; `pg_restore` exits non-zero when
it skipped anything, so read its output rather than only its status. Anything failing on a `CREATE` or an
`INSERT` is a real problem — stop and fix it before continuing.

**3. Compare row counts, scripted, not by eye.** Build one query from the source database's own table list,
write it to a file, and run that identical file on both sides — no `eval`, no nested quoting, one round trip
per side:

```bash
RDS_HOST=<rds-internal-endpoint>                  # the value about to go into .env
RDS_PW='<waypost_django password on RDS>'         # never written to a file; shell-only

cd /srv/waypost/app
docker compose exec -T db psql -U waypost_django -d waypost_db -At \
  -c "select tablename from pg_tables where schemaname='public' order by 1" > /tmp/tables.txt

: > /tmp/rowcounts.sql
first=1
while read -r t; do
  if [ "$first" = 1 ]; then first=0; else printf 'union all\n' >> /tmp/rowcounts.sql; fi
  printf "select '%s='||count(*)::text from public.%s\n" "$t" "$t" >> /tmp/rowcounts.sql
done < /tmp/tables.txt

docker compose exec -T db psql -U waypost_django -d waypost_db -At \
  < /tmp/rowcounts.sql > /tmp/counts_local.txt
docker run --rm -i --network waypost_default -e PGPASSWORD="$RDS_PW" postgres:16 \
  psql -h "$RDS_HOST" -U waypost_django -d waypost_db -At \
  < /tmp/rowcounts.sql > /tmp/counts_rds.txt

diff /tmp/counts_local.txt /tmp/counts_rds.txt && echo "ROW COUNTS MATCH"
```

Django's table names are lowercase and unquoted, which is why the query needs no identifier escaping; if
`pg_tables` ever shows a name outside that shape, stop and look rather than letting the query fail on one
table and comparing an incomplete set.

Keep `/tmp/counts_local.txt` — it is the evidence for §8's follow-up. Expect a handful of tables to differ by
a row or two because the application wrote to them between the dump and the comparison; re-run the dump and
the diff immediately before the cutover and require an exact match at that point. **Sequences are not
compared** — `pg_dump` restores them, and a mismatch there would not lose data.

---

## 5. The atomic `.env` edit

One edit, both keys, saved once. On the host:

```bash
cd /srv/waypost/app
cp .env "/srv/waypost/backups/env.pre-rds-$(date +%F-%H%M%S)"   # the cutover's own rollback

${EDITOR:-vi} .env
```

Set exactly these two lines (uncomment them if they were already present commented out):

```
DATABASE_HOST=<rds-internal-endpoint>
DB_CHECKPOINT_MODE=external
```

Leave `DATABASE_NAME`, `DATABASE_USER` and `DATABASE_PASSWORD` unset: `docker-compose.yml` derives them from
`POSTGRES_*`, so `POSTGRES_PASSWORD` must be the **RDS** password for that role. Setting `DATABASE_PASSWORD`
in `.env` does nothing at all — `environment:` overrides `env_file:` — and is the single most confusing
dead end in this migration. The checkpoint helper deliberately reads the DSN from inside the running
container rather than parsing `.env`, for the same reason.

Then, and only then:

```bash
docker compose up -d          # re-creates the app container with the new environment
curl -s https://<host>/healthz/
```

---

## 6. Verify the application is on RDS

All three, in this order. A `/healthz/` that is green proves the pool connected; the login proves it can read
and write a session; the media write proves the rest of the stack is unaffected by what follows.

```bash
curl -s https://<host>/healthz/                      # version, revision, database: "ok"
```

Then, in a browser: log in as a real account, open a dashboard, and confirm the data is the current data
(the newest inspection or invoice you can name must be visible — that is the proof the *rows* moved, not just
the schema).

Then write a media file: upload one photo through the mini program or the inspection form and open it back.
This is unrelated to the database and that is exactly why it is on the list — the cutover restarts the app
container, and a bind mount or ACL that was only ever correct at boot will show up here
([`DEPLOYMENT.md`](DEPLOYMENT.md) §2.3).

---

## 7. One tag deploy, and the sixth `deploy.log` column

**Do not skip this step to save time.** Deploy the release you are on — the tag that is already live, or the
next one — and read the record:

```bash
tail -3 /srv/waypost/backups/deploy.log
```

`deploy.log` rows are **six tab-separated fields**: timestamp, kind (with its `+tarball` suffix when that
path ran), tag/ref, sha, result, **checkpoint**. The sixth is the pre-deploy dump path, or `none`. It exists
so that "was there a restore point for this release?" is answerable on the host, forever, without opening a
GitHub Actions log — and because a deploy that dies at the checkpoint step otherwise looks like a deploy bug.

Confirm all three:

- the Actions run is green, and its step **"Check the host-side runner matches this tag"** reported
  `in sync` for `ci-deploy.sh`, `_pg_checkpoint.sh` and `waypost-backup.sh`;
- the deploy log line reads `database checkpoint written from app container DATABASE_* (host <rds-endpoint>): …`;
- the newest `deploy.log` row's **sixth field is a real path with a real size**, and the file exists:

```bash
ls -l "$(tail -1 /srv/waypost/backups/deploy.log | cut -f6)"
```

If the sixth field is `none`, the cutover is **not** finished: the deploy you just ran had no restore point.
Do not stop the `db` service until it is not `none`.

`ALLOW_MISSING_CHECKPOINT=1` will not help here — it waves through a missing checkpoint, which is the failure
mode this column exists to make visible.

### If the deploy goes red on a stale runner

The drift gate fails rather than auto-installing, so the first deploy after any `docker/bin` change is red
until the host's copy is reinstalled. Nothing has been deployed when it fails; fix it with the exact command
the message prints:

```bash
gh workflow run deploy-ecs.yml --ref <tag> -f tag=<tag> -f reinstall_runner=true
```

`ALLOW_RUNNER_DRIFT=1` (a repository variable) downgrades the gate to a warning for an incident where the
reinstall is the thing that is slow. It deploys the **old** script, so clear it as soon as the release is
out. `scripts/deploy-dev-to-ecs.ps1` installs all three files itself and is the other way to get the host
current.

---

## 8. Stop the local database; keep the volume

```bash
cd /srv/waypost/app
docker compose stop db          # reversible with: docker compose start db
docker compose ps               # app and redis up, db "Exited"
```

`docker compose stop db`, not `rm`, not `down`. The service stays in `docker-compose.yml` because Compose has
no conditional `depends_on`, and `app` still declares `depends_on.db.condition: service_healthy` — dropping
that regresses cold-start ordering, and the entrypoint's `migrate` has no retry. **An idle stopped container
costs nothing.**

**Retain `waypost_pg_data` for at least 30 days** after the cutover. It is the last copy of the pre-RDS
database that does not depend on RDS working. Nothing in this runbook deletes it, and nothing may:

> 🛑 **`docker compose down -v` destroys `waypost_pg_data` and is unrecoverable.** Same for
> `docker volume rm waypost_pg_data`. The volume is declared at the bottom of `docker-compose.yml` with this
> warning above it; the media and template mounts are host directories and survive it, the database does not.

### Dated follow-up — the only change that may remove the volume

Removal is a **structural** change to `docker-compose.yml` (a `profiles:` entry plus `COMPOSE_PROFILES`, or a
second override file needing Compose ≥ 2.24 `!reset`) and it is deliberately **out of scope** for the
cutover. It may be scheduled only when all three are true:

- [ ] 30 days have passed since the cutover deploy. Earliest date, filled in at cutover: `____________`
- [ ] the volume has been **exported**, not merely superseded — `pg_dump` verified and restored per
      [`BACKUP_RESTORE.md`](BACKUP_RESTORE.md) §8, plus `docker run --rm -v waypost_pg_data:/from -v "$PWD":/to
      alpine tar czf /to/waypost_pg_data.pre-rds.tar.gz -C /from .` as the cold copy;
- [ ] the RDS row counts have been verified **after** the cutover, against `/tmp/counts_local.txt` from §4 and
      a fresh read of the live database.

Then, in a PR that is reviewed as a compose change and not folded into a release: retire `db` behind a
profile, keep the `volumes:` declaration and its warning for one more release, and delete the volume by hand
after that release has been live for a week. `waypost-backup.sh`'s media tar is a separate decision gated on
`WAYPOST_MEDIA_IN_OSS=1` and belongs after the OSS backfill in
[`OSS_STORAGE.md`](OSS_STORAGE.md) §8–§9, not after this.

---

## 9. Reverting the cutover

The cutover is one file, so it reverts as one file:

```bash
cd /srv/waypost/app
cp /srv/waypost/backups/env.pre-rds-<stamp> .env     # or delete the two keys
docker compose up -d
docker compose start db
curl -s https://<host>/healthz/
```

Any writes made **after** the cutover exist only on RDS. Before rolling back, dump from RDS
(`DB_CHECKPOINT_MODE=external` already does that on every deploy — use the newest
`/srv/waypost/backups/pg/pre-*.dump`) and restore into the restarted `db` service per
[`BACKUP_RESTORE.md`](BACKUP_RESTORE.md) §3a. That is why §7 must be green before §8 runs: a rollback needs a
dump taken from the new database.

---

## 10. What this runbook does **not** close

This makes failures legible and the switch reversible. It is not the fix for the gaps it exposes, and those
stay open in [`RELEASE_PROCEDURE.md`](RELEASE_PROCEDURE.md) §11:

- **No staging environment.** The drill in §3 runs against production data on the production host.
- **No notification channel for a failed deploy.** A red run — including a red drift gate — is only seen if
  someone opens the Actions tab.
- **A real rollback has never been drilled.** §3 drills the *checkpoint path*; it does not restore a database.
- **`DJANGO_FIELD_ENCRYPTION_KEY` still has no off-host copy** — the `.env` backup that `waypost-backup.sh`
  writes is on the same host as the database it decrypts.

---

## Related Documentation

- [`DATABASE_MIGRATION.md`](DATABASE_MIGRATION.md) — the other move: SQLite → PostgreSQL data migration
- [`BACKUP_RESTORE.md`](BACKUP_RESTORE.md) — §2b the nightly script, §3 restore, §8 the verification drill
- [`DEPLOYMENT.md`](DEPLOYMENT.md) — §2.13 "Moving the database to RDS", §2.4 the production `.env`
- [`RELEASE_PROCEDURE.md`](RELEASE_PROCEDURE.md) — §6 deploys, §9.4 the checkpoint drill, §11 open gaps
- [`OSS_STORAGE.md`](OSS_STORAGE.md) — §8–§9, the media backfill the *other* gate waits on
- [`../docker/bin/ci-deploy.sh`](../docker/bin/ci-deploy.sh) · [`../docker/bin/_pg_checkpoint.sh`](../docker/bin/_pg_checkpoint.sh) · [`../docker/bin/waypost-backup.sh`](../docker/bin/waypost-backup.sh)

---

*Maintainer: Sean Liu (@sean7084)*
