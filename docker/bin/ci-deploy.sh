#!/usr/bin/env bash
# Production deploy for Waypost. Tag-promoted; a branch can be deployed only when
# explicitly asked for.
#
# Invoked over SSH by .github/workflows/deploy-ecs.yml:
#
#     ci-deploy.sh <git-tag> [<git-sha>]
#
# The SHA is optional and only used for logging and /healthz/; when omitted it is
# resolved from the tag, which is the more trustworthy source anyway.
#
# Invoked by an operator through scripts/deploy-dev-to-ecs.ps1:
#
#     ci-deploy.sh origin/main --allow-untagged
#
# Without --allow-untagged a non-tag ref is refused, so the CD path can never
# deploy a branch by accident. An untagged deploy is built as waypost-app:dev-<ref>,
# logged with kind=untagged-ref and result DEV_OK, and is a VERIFICATION deploy:
# it is not a release, it gets no CHANGELOG entry and no tag. Releases still go
# through a tag (RELEASE_PROCEDURE §6.3) so that "what is in production" is always
# a named, immutable commit.
#
# Runs as the `deploy` user on the ECS. That user owns this directory tree and
# its own rootless Docker daemon, which is a separate daemon from the one running
# the Helpdesk stack - so nothing this script does can touch that application,
# and it has no sudo rights either.
#
# Sequence:
#   0. fetch, then resolve the ref to an image tag       (tag, or dev-<ref>)
#   1. alias the running image as waypost-app:rollback   (the rollback target)
#   2. pg_dump checkpoint                                (the data safety net)
#   3. git checkout the ref
#   4. build + up                                        (the entrypoint runs
#                                                         migrate/collectstatic)
#   5. health gate: loopback, then the public URL through Nginx
#   6. on failure: restore the previous image and tag, re-gate, exit non-zero
#
# The database is NEVER restored automatically. A release may contain a
# one-way-door migration (accounts/0019, products/0004, quotations/0007 - see
# RELEASE_PROCEDURE §9.2), so restoring over a live database is a human decision.
# This script prints the checkpoint path and stops.
#
# NOTE for untagged deploys: `main` may contain migrations, and those are applied
# to the SAME production database a release would use. There is no separate dev
# database on this host, so a verification deploy of main is not risk-free - take
# the checkpoint (automatic) and read the migration list before running it.
set -euo pipefail

# Docker access must not depend on how this account's CLI context happens to be
# configured, nor on ~/.bashrc being sourced: a non-interactive `ssh host cmd`
# does not source it, and the CI authorized_keys entry is `restrict`ed (no user
# rc). Point straight at this user's own rootless socket instead.
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export DOCKER_HOST="${DOCKER_HOST:-unix://${XDG_RUNTIME_DIR}/docker.sock}"
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:${PATH}"

APP_DIR="${APP_DIR:-/srv/waypost/app}"
BACKUP_DIR="${BACKUP_DIR:-/srv/waypost/backups}"
IMAGE="waypost-app"
LOCAL_HEALTH_URL="http://127.0.0.1:8000/healthz/"
PUBLIC_HEALTH_URL="${PUBLIC_HEALTH_URL:-https://ams.istore-tech.cn/healthz/}"
PUBLIC_LOGIN_URL="${PUBLIC_LOGIN_URL:-https://ams.istore-tech.cn/accounts/login/}"
KEEP_CHECKPOINTS="${KEEP_CHECKPOINTS:-14}"
HEALTH_TIMEOUT_SECONDS="${HEALTH_TIMEOUT_SECONDS:-180}"
DEPLOY_LOG="${BACKUP_DIR}/deploy.log"
STAMP="$(date +%F-%H%M%S)"

# Argument parsing is order-independent so `--allow-untagged` can be given before
# or after the ref/sha: a tag deploy (the CD path) and a verification deploy of a
# branch (scripts/deploy-dev-to-ecs.ps1) share this one code path on purpose, so
# there are not two deploy scripts to drift apart.
REF=""
SHA=""
ALLOW_UNTAGGED=0
KIND="tag"
TAG=""
PREV_REF=""
CHECKPOINT=""
ROLLED_BACK=0

for arg in "$@"; do
    case "$arg" in
        --allow-untagged) ALLOW_UNTAGGED=1 ;;
        *)
            if [ -z "$REF" ]; then REF="$arg"; elif [ -z "$SHA" ]; then SHA="$arg"; fi
            ;;
    esac
done

log()  { printf '%s %s\n' "$(date -Is)" "$*"; }
die()  { log "FATAL: $*" >&2; record "ABORTED"; exit 1; }

# github.com is intermittently unreachable from this network - observed during
# bring-up as `curl 28 Failed to connect to github.com port 443 after 135163 ms`,
# with the very next request succeeding. A single attempt therefore makes release
# outcome depend on a transient blip. Retry, and bound the transfer so a stalled
# connection fails in a minute instead of hanging for the whole SSH session.
fetch_with_retry() {
    local attempt
    for attempt in 1 2 3; do
        if git -c http.lowSpeedLimit=1000 -c http.lowSpeedTime=60 \
               fetch --tags --force origin; then
            return 0
        fi
        log "git fetch attempt ${attempt}/3 failed; retrying in 15s"
        sleep 15
    done
    return 1
}

record() {
    mkdir -p "$BACKUP_DIR"
    printf '%s\t%s\t%s\t%s\t%s\n' "$(date -Is)" "${KIND}" "${TAG:-none}" "${SHA:-none}" "${1:-unknown}" >> "$DEPLOY_LOG"
}

# Dump the app container's recent logs so a failed deploy is diagnosable straight
# from the GitHub Actions output, without an SSH session.
dump_logs() {
    log "--- last 120 log lines from the app container ---"
    docker compose logs --tail=120 app || true
    log "--- container state ---"
    docker compose ps || true
}

wait_for_health() {
    local url="$1" label="$2" deadline code=""
    deadline=$(( $(date +%s) + HEALTH_TIMEOUT_SECONDS ))
    while [ "$(date +%s)" -lt "$deadline" ]; do
        code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$url" || true)"
        if [ "$code" = "200" ]; then
            log "health gate passed (${label}): ${url}"
            return 0
        fi
        sleep 3
    done
    log "health gate FAILED (${label}): ${url} last status ${code:-none}"
    return 1
}

rollback() {
    local reason="$1"
    [ "$ROLLED_BACK" -eq 1 ] && return 0
    ROLLED_BACK=1
    log "ROLLING BACK: ${reason}"

    if docker image inspect "${IMAGE}:rollback" >/dev/null 2>&1; then
        if [ -n "$PREV_REF" ]; then
            git checkout -f "$PREV_REF" || log "could not check out ${PREV_REF}; source tree may not match the running image"
        fi
        # The compose file resolves `image: waypost-app:${TAG:-latest}`, so
        # re-running with TAG=rollback starts the aliased previous image without
        # rebuilding anything.
        TAG=rollback docker compose up -d --no-build app || log "rollback 'up' failed - manual intervention required"
        wait_for_health "$LOCAL_HEALTH_URL" "post-rollback loopback" || log "still unhealthy after rollback - MANUAL INTERVENTION REQUIRED"
    else
        log "no ${IMAGE}:rollback image to return to (first deploy?); leaving the failed release in place"
    fi

    log "database checkpoint (NOT restored - see RELEASE_PROCEDURE §8/§9): ${CHECKPOINT:-none}"
    log "to restore it manually: docker compose exec -T db pg_restore --clean --if-exists --no-owner -U <user> -d <db> < <checkpoint>"
    dump_logs
}

fail() {
    rollback "$1"
    record "FAILED"
    exit 1
}

# --- pre-flight -------------------------------------------------------------
[ -n "$REF" ] || die "usage: ci-deploy.sh <git-tag|origin/branch> [--allow-untagged] [<git-sha>]"
command -v docker >/dev/null 2>&1 || die "docker CLI not found for $(id -un)"
docker compose version >/dev/null 2>&1 || die "'docker compose' plugin unavailable"
docker info >/dev/null 2>&1 || die "cannot reach this user's Docker daemon (is it rootless and running? try: systemctl --user status docker)"
[ -d "$APP_DIR" ] || die "APP_DIR missing: $APP_DIR"
cd "$APP_DIR"
[ -f .env ] || die ".env missing in $APP_DIR - the app cannot boot without it (see docs/DEPLOYMENT.md)"
[ -f docker-compose.yml ] || die "docker-compose.yml missing in $APP_DIR"
[ -d "${BACKUP_DIR}" ] || mkdir -p "${BACKUP_DIR}/pg"

# Fetch first: the ref must be resolved against what origin actually has, and a
# branch deploy is meaningless against a stale remote-tracking ref.
fetch_with_retry || die "git fetch failed after 3 attempts (is github.com reachable from this host?)"

if git rev-parse -q --verify "refs/tags/${REF}" >/dev/null 2>&1; then
    KIND="tag"
    TAG="$REF"
elif [ "$ALLOW_UNTAGGED" -eq 1 ] && git rev-parse -q --verify "${REF}^{commit}" >/dev/null 2>&1; then
    KIND="untagged-ref"
    # Compose interpolates this into `image: waypost-app:${TAG:-latest}`, so it
    # must be a valid image tag: strip the remote prefix and anything Docker
    # would reject. `dev-main` reads clearly in `docker images` next to `v0.2.0`.
    TAG="dev-$(printf '%s' "${REF##*/}" | tr -c 'A-Za-z0-9._-' '-')"
    log "WARNING: ${REF} is NOT a tag - this is a verification deploy, not a release"
else
    die "${REF} is not a tag in this repository (pass --allow-untagged to deploy a branch for verification)"
fi

log "=== deploying ${REF} as image ${IMAGE}:${TAG} (${KIND}) as $(id -un) ==="

# --- 1. remember what is running now ---------------------------------------
PREV_REF="$(git describe --tags --abbrev=0 HEAD 2>/dev/null || true)"
CURRENT_IMAGE_ID="$(docker inspect -f '{{.Image}}' waypost_app 2>/dev/null || true)"
if [ -n "$CURRENT_IMAGE_ID" ]; then
    docker tag "$CURRENT_IMAGE_ID" "${IMAGE}:rollback"
    log "aliased running image ${CURRENT_IMAGE_ID:0:19} as ${IMAGE}:rollback (previous ref: ${PREV_REF:-none})"
else
    log "no running waypost_app container - this is a first deploy, there is nothing to roll back to"
fi

# --- 2. pre-deploy database checkpoint --------------------------------------
# Automates the non-negotiable backup in RELEASE_PROCEDURE §6.1. Skipped only
# when the database is not up yet (first deploy), because there is nothing to
# lose at that point.
mkdir -p "${BACKUP_DIR}/pg"
if docker compose ps --status running --services 2>/dev/null | grep -qx db; then
    CHECKPOINT="${BACKUP_DIR}/pg/pre-${TAG}-${STAMP}.dump"
    # The DSN is read from the db container's own environment rather than parsed
    # out of .env here, so this cannot disagree with what Postgres was actually
    # initialised with.
    if docker compose exec -T db sh -c 'pg_dump -Fc -U "$POSTGRES_USER" "$POSTGRES_DB"' > "$CHECKPOINT"; then
        log "database checkpoint written: $CHECKPOINT ($(du -h "$CHECKPOINT" | cut -f1))"
    else
        rm -f "$CHECKPOINT"
        CHECKPOINT=""
        log "WARNING: pg_dump checkpoint failed; continuing without a pre-deploy restore point"
    fi
    # Keep the newest N pre-deploy checkpoints. Nightly backups are pruned by the
    # cron job instead (see docs/BACKUP_RESTORE.md).
    { ls -1t "${BACKUP_DIR}"/pg/pre-*.dump 2>/dev/null || true; } | tail -n +$(( KEEP_CHECKPOINTS + 1 )) | xargs -r rm -f
else
    log "database container not running yet - skipping the pre-deploy checkpoint"
fi

# --- 3. check out the released ref ------------------------------------------
# (fetched and verified in the pre-flight above)
git checkout -f "$REF" || fail "git checkout ${REF} failed"
# -ffd, never -x: .env is gitignored and must survive every deploy.
git clean -ffd || fail "git clean failed"
if [ -z "$SHA" ]; then
    SHA="$(git rev-parse --short "${REF}^{commit}" 2>/dev/null || git rev-parse --short HEAD)"
fi
log "checked out $(git describe --tags 2>/dev/null || git rev-parse --short HEAD) (${SHA})"

# --- 4. build and start -----------------------------------------------------
export TAG SHA GIT_SHA="$SHA"
# --pull re-pulls the base image, so a deploy can pick up a newer
# python:3.12-slim-bookworm (Debian security fixes) with no code change. That is
# deliberate: the health gate below plus the rollback alias are what make it
# safe. For a byte-for-byte reproducible rebuild, pin BASE_IMAGE to a digest.
docker compose build --pull app || fail "image build failed"
docker compose up -d || fail "'docker compose up -d' failed"

# --- 5. health gates --------------------------------------------------------
if ! wait_for_health "$LOCAL_HEALTH_URL" "loopback"; then
    fail "the new release never became healthy on the loopback interface"
fi

# A container that answers locally but not publicly means the edge is broken
# (Nginx site, certificate, DNS), not the release. Rolling back cannot fix that,
# so this path reports and exits non-zero without touching the running app.
if [ "${SKIP_PUBLIC_GATE:-0}" != "1" ]; then
    edge_ok=1
    wait_for_health "$PUBLIC_HEALTH_URL" "public healthz" || edge_ok=0
    login_code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 "$PUBLIC_LOGIN_URL" || true)"
    if [ "$login_code" != "200" ]; then
        log "public login page returned ${login_code:-none} (expected 200): ${PUBLIC_LOGIN_URL}"
        edge_ok=0
    else
        log "health gate passed (public login): ${PUBLIC_LOGIN_URL}"
    fi

    if [ "$edge_ok" -ne 1 ]; then
        log "app is healthy locally but NOT through Nginx - suspect the edge, not this release:"
        log "  sudo nginx -t && sudo systemctl status nginx --no-pager"
        log "  sudo systemctl reload nginx"
        log "  openssl x509 -in <fullchain.pem> -noout -dates -ext subjectAltName"
        log "  curl -sv https://ams.istore-tech.cn/healthz/ 2>&1 | tail -20"
        log "leaving ${TAG} running (a rollback cannot repair the edge)"
        dump_logs
        record "FAILED_EDGE"
        exit 1
    fi
fi

# --- 6. done ----------------------------------------------------------------
if [ "$KIND" = "tag" ]; then
    log "=== ${TAG} deployed and healthy ==="
else
    log "=== ${REF} deployed and healthy (image ${IMAGE}:${TAG}, NOT a release) ==="
fi
docker compose ps || true
curl -s --max-time 10 "$PUBLIC_HEALTH_URL" || curl -s --max-time 10 "$LOCAL_HEALTH_URL" || true
echo
if [ "$KIND" = "tag" ]; then record "OK"; else record "DEV_OK"; fi
