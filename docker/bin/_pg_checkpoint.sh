#!/usr/bin/env bash
# Shared database-checkpoint logic for Waypost's two dump sites.
#
# Sourced by docker/bin/ci-deploy.sh (the pre-deploy checkpoint, which is the only
# restore point a release gets) and by docker/bin/waypost-backup.sh (the nightly
# dump). It exists as a separate file because both need one answer to "where is the
# database, and how do I dump it?" - two copies of a DSN rule is how the deploy
# checkpoint and the nightly backup end up protecting two different databases.
#
# Nothing runs at source time except the defaults and the mode resolution below, so a
# caller may set BACKUP_DIR, APP_DIR, PG_DUMP_IMAGE or COMPOSE_NETWORK before sourcing.
# Required from the caller: a `log` function, APP_DIR as the compose project directory
# AND the current working directory, and BACKUP_DIR (host directory, must exist).
#
# `set -euo pipefail` is repeated here on purpose: this file is also meant to be
# readable and `bash -n`-checkable on its own, and the hygiene test in
# tests/test_repo_hygiene.py checks every docker/bin/*.sh for it.
set -euo pipefail

# Image the dump runs in. The application image has no postgresql-client at all -
# `libpq-dev` appears only in the `deps` stage of docker/Dockerfile - so the
# checkpoint uses a throwaway container of the same image the compose `db` service
# already pulls. It stays in the local image store after the volume is retired.
PG_DUMP_IMAGE="${PG_DUMP_IMAGE:-postgres:16}"

# Network of this compose project (`name: waypost` in docker-compose.yml), so the
# throwaway container reaches the database by service name or by VPC hostname.
COMPOSE_NETWORK="${COMPOSE_NETWORK:-waypost_default}"

# DB_CHECKPOINT_MODE: auto | compose | external | none
#   auto      Today's behaviour, unchanged: dump the compose `db` service when it is
#             running, otherwise report that there is nothing here to dump.
#   compose   Dump the compose `db` service, and fail loudly if it is not running.
#   external  Dump DATABASE_HOST/PORT/NAME/USER/PASSWORD as the *app container*
#             carries them - RDS, or any database outside this compose stack.
#   none      Take no dump: the database has managed backups of its own. The honest
#             form of ALLOW_MISSING_CHECKPOINT=1.
#
# Explicit rather than inferred. At the checkpoint step the running app container is
# still the *previous* release, so during the cutover deploy "is db running?" would
# checkpoint the wrong database on the single most important deploy of the migration.
#
# Captured before the default below, so "the operator said auto on the command line"
# stays distinguishable from "nobody said anything, so ask .env".
_DB_CHECKPOINT_MODE_FROM_ENV="${DB_CHECKPOINT_MODE:-}"
DB_CHECKPOINT_MODE="${DB_CHECKPOINT_MODE:-auto}"

# Result of the last take_database_checkpoint call: the path (empty when no dump was
# written), a human-readable source, the host it was read from, and - on failure -
# the reason a caller should put in its own "no checkpoint" message.
CHECKPOINT=""
CHECKPOINT_SOURCE=""
CHECKPOINT_HOST=""
CHECKPOINT_FAILURE_REASON=""

env_file_value() {
    # Read exactly one key out of ${APP_DIR}/.env.
    #
    # This is used ONLY for DB_CHECKPOINT_MODE, because that knob exists so the RDS
    # cutover can be one atomic edit of the one file the operator already edits.
    # It is deliberately NOT used for DATABASE_*: docker-compose.yml's
    # `environment:` overrides `env_file:`, so the container's environment is the
    # DSN's source of truth and this file is not.
    local key="$1" line
    [ -f "${APP_DIR}/.env" ] || return 1
    line="$(grep -E "^[[:space:]]*${key}=" "${APP_DIR}/.env" | head -n 1 || true)"
    [ -n "$line" ] || return 1
    line="${line%%#*}"
    line="${line#*=}"
    printf '%s' "$line" | tr -d ' \t\r"'
}

# Resolution happens here, at source time, rather than inside db_checkpoint_mode():
# both callers read the mode through command substitution, which runs in a subshell,
# so a value discovered there could never be reported back in a failure message - and
# `DB_CHECKPOINT_MODE='' is not one of auto|compose|external|none` is exactly the
# message that sends a reader to the wrong file during an incident.
if [ -z "$_DB_CHECKPOINT_MODE_FROM_ENV" ]; then
    _DB_CHECKPOINT_MODE_FROM_FILE="$(env_file_value DB_CHECKPOINT_MODE || true)"
    if [ -n "$_DB_CHECKPOINT_MODE_FROM_FILE" ]; then
        DB_CHECKPOINT_MODE="$_DB_CHECKPOINT_MODE_FROM_FILE"
    fi
    unset _DB_CHECKPOINT_MODE_FROM_FILE
fi

db_checkpoint_mode() {
    # Maps DB_CHECKPOINT_MODE to the branch to take: compose | external | none |
    # missing | invalid. `missing` is auto with no running db service - today's
    # "there is nothing to dump here" branch.
    case "${DB_CHECKPOINT_MODE:-auto}" in
        compose|external|none) printf '%s' "${DB_CHECKPOINT_MODE}" ;;
        auto|'')
            if docker compose ps --status running --services 2>/dev/null | grep -qx db; then
                printf 'compose'
            else
                printf 'missing'
            fi
            ;;
        *) printf 'invalid' ;;
    esac
}

db_service_is_running() {
    docker compose ps --status running --services 2>/dev/null | grep -qx db
}

read_app_database_dsn() {
    # Populate DATABASE_HOST/PORT/NAME/USER/PASSWORD from inside the app container.
    # That is what Django actually connects with, so the checkpoint cannot disagree
    # with the application - the same property the compose branch gets by reading
    # $POSTGRES_USER/$POSTGRES_DB from the db container.
    #
    # Never print these. Only DATABASE_HOST is ever logged.
    local out
    out="$(docker compose exec -T app printenv \
              DATABASE_HOST DATABASE_PORT DATABASE_NAME DATABASE_USER DATABASE_PASSWORD 2>/dev/null)" || return 1
    {
        IFS= read -r DATABASE_HOST &&
        IFS= read -r DATABASE_PORT &&
        IFS= read -r DATABASE_NAME &&
        IFS= read -r DATABASE_USER &&
        IFS= read -r DATABASE_PASSWORD
    } <<< "$out" || return 1
    [ -n "$DATABASE_HOST" ] && [ -n "$DATABASE_NAME" ] && [ -n "$DATABASE_USER" ]
}

dump_compose_database() {
    # $1 = destination path on the host. Credentials come from the db container's
    # own environment, so this cannot drift from what Postgres was initialised with.
    docker compose exec -T db sh -c 'pg_dump -Fc -U "$POSTGRES_USER" "$POSTGRES_DB"' > "$1"
}

dump_external_database() {
    # $1 = file name to write inside ${BACKUP_DIR}/pg (bind-mounted to /out).
    local filename="$1" env_file status=0
    # Fail here, while the deploy still has everything to lose, rather than at the
    # moment of a real rollback when the image turns out never to have been pulled.
    if ! docker image inspect "$PG_DUMP_IMAGE" >/dev/null 2>&1; then
        CHECKPOINT_FAILURE_REASON="${PG_DUMP_IMAGE} is not in this user's image store - run: docker pull ${PG_DUMP_IMAGE}"
        return 1
    fi
    # The password reaches the container through a 0600 --env-file rather than
    # `-e PGPASSWORD=...`, because a `-e` value lands in the argv of `docker run`,
    # which any local process can read through ps or /proc/<pid>/cmdline - and this
    # is the credential of the production database.
    env_file="$(mktemp "${BACKUP_DIR}/.pgpw.XXXXXX")" || return 1
    chmod 600 "$env_file"
    printf 'PGPASSWORD=%s\n' "$DATABASE_PASSWORD" > "$env_file"
    docker run --rm \
        --network "$COMPOSE_NETWORK" \
        -v "${BACKUP_DIR}/pg:/out" \
        --env-file "$env_file" \
        "$PG_DUMP_IMAGE" pg_dump -Fc \
            -h "$DATABASE_HOST" -p "$DATABASE_PORT" -U "$DATABASE_USER" -d "$DATABASE_NAME" \
            -f "/out/${filename}" || status=$?
    rm -f "$env_file"
    return "$status"
}

take_database_checkpoint() {
    # $1 = file name inside ${BACKUP_DIR}/pg, $2 = mode from db_checkpoint_mode.
    # Returns 0 and sets CHECKPOINT on success; returns 1 with
    # CHECKPOINT_FAILURE_REASON set otherwise. Policy - refuse the deploy, or warn
    # and continue - belongs to the caller.
    local filename="$1" mode="${2:-$(db_checkpoint_mode)}"
    local candidate="${BACKUP_DIR}/pg/${filename}"
    CHECKPOINT=""
    CHECKPOINT_SOURCE=""
    CHECKPOINT_HOST=""
    CHECKPOINT_FAILURE_REASON=""

    case "$mode" in
        compose)
            CHECKPOINT_SOURCE="compose db service"
            CHECKPOINT_HOST="db"
            if ! dump_compose_database "$candidate"; then
                rm -f "$candidate"
                CHECKPOINT_FAILURE_REASON="pg_dump inside the compose db service failed"
                return 1
            fi
            ;;
        external)
            if ! read_app_database_dsn; then
                CHECKPOINT_FAILURE_REASON="DB_CHECKPOINT_MODE=external but the app container is not running, so its DATABASE_* environment could not be read (the DSN is never parsed out of .env - see docs/RDS_MIGRATION.md)"
                return 1
            fi
            CHECKPOINT_SOURCE="app container DATABASE_*"
            CHECKPOINT_HOST="$DATABASE_HOST"
            if ! dump_external_database "$filename"; then
                rm -f "$candidate"
                [ -n "$CHECKPOINT_FAILURE_REASON" ] || \
                    CHECKPOINT_FAILURE_REASON="pg_dump against ${DATABASE_HOST}:${DATABASE_PORT} failed"
                return 1
            fi
            ;;
        missing)
            CHECKPOINT_FAILURE_REASON="the compose stack has no running 'db' service"
            return 1
            ;;
        none)
            CHECKPOINT_FAILURE_REASON="DB_CHECKPOINT_MODE=none (the database has managed backups)"
            return 1
            ;;
        *)
            CHECKPOINT_FAILURE_REASON="DB_CHECKPOINT_MODE='${DB_CHECKPOINT_MODE}' is not one of auto|compose|external|none"
            return 1
            ;;
    esac

    # An empty dump is worse than no dump: it looks like a restore point and is not
    # one, and pg_dump can exit 0 having written nothing when it is interrupted. It
    # also applies here because `docker run` writes through a bind mount.
    if [ ! -s "$candidate" ]; then
        rm -f "$candidate"
        CHECKPOINT_FAILURE_REASON="pg_dump exited 0 but wrote an empty file"
        return 1
    fi

    CHECKPOINT="$candidate"
    return 0
}
