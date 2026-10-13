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
#   0. resolve the ref and obtain its tree               (git fetch, or the
#                                                         codeload fallback)
#   1. alias the running image as waypost-app:rollback   (the rollback target)
#   2. pg_dump checkpoint                                (the data safety net)
#   3. git checkout the ref
#   4. build + up                                        (the entrypoint runs
#                                                         migrate/collectstatic)
#   5. health gate: loopback, then the public URL through Nginx
#   6. on failure: restore the previous image and tag, re-gate, exit non-zero
#
# Step 0 has two sources. `git fetch` is the normal one, but it needs github.com's
# apex host, which from this network is intermittently unreachable while
# api.github.com and codeload.github.com are fine. When the fetch fails the script
# falls back to the codeload tarball - after proving, by recomputing its git tree
# hash against the tree SHA the API reports for the tag, that the tarball IS that
# commit. deploy.log's kind column ends in "+tarball" when that path was used.
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

# How many times to retry `git fetch`. The default of 4 is derived from the deploy
# job's timeout-minutes: 25 - a failed attempt can burn ~132s in the connect phase
# alone, so four cost at most ~11 minutes and still leave room for the build and
# the health gates. Lower it to reach the codeload fallback quickly in a drill:
#     FETCH_ATTEMPTS=1 FORCE_TARBALL_SOURCE=0 ci-deploy.sh v0.2.0
FETCH_ATTEMPTS="${FETCH_ATTEMPTS:-4}"

# Set to 1 to skip `git fetch` and take the codeload tarball path deliberately.
# An outage cannot be scheduled, and an undrilled fallback is not a fallback.
FORCE_TARBALL_SOURCE="${FORCE_TARBALL_SOURCE:-0}"

# The fallback talks to two hosts that stay reachable when the apex does not.
GITHUB_API_URL="${GITHUB_API_URL:-https://api.github.com}"
CODELOAD_URL="${CODELOAD_URL:-https://codeload.github.com}"

# Which source produced the tree: "git" (normal) or "tarball" (fallback). Recorded
# in deploy.log's kind column so an audit can tell how a release reached the host.
SOURCE_MODE="git"
SRC_TMP=""
REPO_SLUG=""
CODELOAD_REF=""
API_BODY=""
API_CODE=""
API_COMMIT=""
API_TREE=""

# Argument parsing is order-independent so `--allow-untagged` can be given before
# or after the ref/sha: a tag deploy (the CD path) and a verification deploy of a
# branch (scripts/deploy-dev-to-ecs.ps1) share this one code path on purpose, so
# there are not two deploy scripts to drift apart.
REF=""
SHA=""
ALLOW_UNTAGGED=0
# "unresolved" until the pre-flight works out what was asked for. An abort before
# that point (a failed fetch, say) must not claim kind=tag for a branch.
KIND="unresolved"
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

# The tarball fallback downloads into a temp dir; never leave it behind, including
# on the die/fail paths.
cleanup() {
    if [ -n "$SRC_TMP" ] && [ -d "$SRC_TMP" ]; then
        rm -rf "$SRC_TMP"
    fi
}
trap cleanup EXIT

# github.com is intermittently unreachable from this network - observed during
# bring-up as `curl 28 Failed to connect to github.com port 443 after 135163 ms`,
# with the very next request succeeding. A single attempt therefore makes release
# outcome depend on a transient blip. Retry, and bound the transfer so a stalled
# connection fails in a minute instead of hanging for the whole SSH session.
fetch_with_retry() {
    local attempt waited start
    # github.com is intermittently unreachable from this network. Observed during
    # bring-up, and again on 2026-10-12 when a dispatched release failed all three
    # of the original attempts (curl 28 "Operation too slow", then two 132s connect
    # timeouts) and had to be re-run by hand seven hours later.
    #
    # The attempt count is bounded by the workflow's timeout-minutes: 25 for the
    # deploy job, of which the build and the health gates need about 4. A failed
    # attempt can burn ~132s in the connect phase alone, so four attempts with
    # escalating backoff cost at most ~11 minutes and still leave headroom. Going
    # further would risk the job being killed mid-deploy, which is worse than
    # failing cleanly before anything was touched.
    for attempt in $(seq 1 "$FETCH_ATTEMPTS"); do
        start=$(date +%s)
        if git -c http.lowSpeedLimit=1000 -c http.lowSpeedTime=60 \
               fetch --tags --force origin; then
            log "git fetch succeeded on attempt ${attempt}/${FETCH_ATTEMPTS} ($(( $(date +%s) - start ))s)"
            return 0
        fi
        waited=$(( attempt * 20 ))
        if [ "$waited" -gt 60 ]; then
            waited=60
        fi
        log "git fetch attempt ${attempt}/${FETCH_ATTEMPTS} failed after $(( $(date +%s) - start ))s"
        if [ "$attempt" -lt "$FETCH_ATTEMPTS" ]; then
            log "  retrying in ${waited}s"
            sleep "$waited"
        fi
    done
    return 1
}

record() {
    mkdir -p "$BACKUP_DIR"
    # TAG is only known once the pre-flight has resolved the ref, so fall back to
    # the raw ref: without it, an abort during fetch logged "none none ABORTED" and
    # the durable host-side record could not say which release was attempted.
    printf '%s\t%s\t%s\t%s\t%s\n' "$(date -Is)" "${KIND}" "${TAG:-${REF:-none}}" "${SHA:-none}" "${1:-unknown}" >> "$DEPLOY_LOG"
}

is_release() {
    # KIND carries the source suffix ("tag+tarball"), so "is this a release?" is a
    # PREFIX test, not an equality test. Getting that wrong recorded a real v0.2.0
    # tag deploy as DEV_OK during the fallback drill: the kind column said
    # tag+tarball while the result column claimed it was only a verification
    # deploy. Anything that branches on the kind must go through here.
    case "$KIND" in
        tag*) return 0 ;;
        *)    return 1 ;;
    esac
}

# --- codeload fallback -------------------------------------------------------
# `git fetch` needs github.com's APEX host, and from this network that host is
# intermittently unreachable while its subdomains stay fine. Measured 2026-10-13:
# apex 5/5 curl timeouts (one with TLS complete at 0.199s but no HTTP response ever
# arriving) and `git ls-remote` 2/2 failed, while api.github.com answered 200 in
# 0.33s and codeload.github.com served the v0.2.0 tarball - 7.3 MB in 1.4s. A
# release should not have to wait for that to clear.
#
# The fallback is only acceptable because the tarball can be tied to the exact
# tagged commit: api.github.com reports the commit's tree SHA, and recomputing the
# git tree hash of the extracted files must reproduce it. Verified on the host for
# v0.2.0 - both sides gave 1523d940f618c5b02072c73f093b9f7840073198. A tree that
# does not match is refused, not deployed: TLS to codeload alone would only prove
# the bytes came from GitHub, not that they are the tag.

repo_slug() {
    # Local operation: reads the remote URL, no network. Accepts the https form
    # (https://github.com/owner/repo.git) and the scp form (git@github.com:owner/repo.git).
    local url
    url="$(git -C "$APP_DIR" remote get-url origin 2>/dev/null)" || return 1
    [ -n "$url" ] || return 1
    printf '%s' "$url" | sed -E 's#^https?://[^/]+/##; s#^[^@/]+@[^:]+:##; s#\.git$##; s#/$##'
}

api_get() {
    # $1 = path under /repos/<slug>. Sets API_BODY and API_CODE.
    # Returns 0 on 2xx, 2 on 404 (a meaningful "no such ref"), 1 on anything else -
    # the distinction matters, because treating a network failure as "not a tag"
    # would let a real release be deployed under a dev image tag.
    local path="$1" attempt out
    for attempt in 1 2 3; do
        if out="$(curl -sS --max-time 30 -w '\n%{http_code}' \
                       -H 'Accept: application/vnd.github+json' \
                       -H 'User-Agent: waypost-ci-deploy' \
                       "${GITHUB_API_URL}/repos/${REPO_SLUG}${path}" 2>/dev/null)"; then
            API_CODE="${out##*$'\n'}"
            API_BODY="${out%$'\n'*}"
            case "$API_CODE" in
                2*)   return 0 ;;
                404)  return 2 ;;
                401|403)
                    log "api.github.com refused the request (HTTP ${API_CODE}); is the repo private or the rate limit hit?"
                    return 1 ;;
                *)    log "api.github.com returned HTTP ${API_CODE} for ${path} (attempt ${attempt}/3)" ;;
            esac
        else
            log "api.github.com request for ${path} failed (attempt ${attempt}/3)"
        fi
        sleep 5
    done
    return 1
}

resolve_via_api() {
    # Works out KIND/TAG/SHA/API_COMMIT/API_TREE/CODELOAD_REF without touching git's
    # refs, which are stale precisely because the fetch failed.
    local name rc
    command -v jq >/dev/null 2>&1 || { log "the codeload fallback needs jq (sudo apt install jq)"; return 1; }
    REPO_SLUG="$(repo_slug)" || { log "cannot derive owner/repo from the origin remote URL"; return 1; }
    log "codeload fallback: repository ${REPO_SLUG}, api ${GITHUB_API_URL}"

    name="$REF"
    case "$name" in origin/*) name="${name#origin/}" ;; esac

    api_get "/git/refs/tags/${name}"; rc=$?
    if [ "$rc" -eq 0 ]; then
        KIND="tag"
        TAG="$name"
    elif [ "$rc" -eq 2 ]; then
        if [ "$ALLOW_UNTAGGED" -ne 1 ]; then
            log "${REF} is not a tag in ${REPO_SLUG} (pass --allow-untagged to deploy a branch for verification)"
            return 1
        fi
        KIND="untagged-ref"
        TAG="dev-$(printf '%s' "${name}" | tr -c 'A-Za-z0-9._-' '-')"
        log "WARNING: ${REF} is NOT a tag - this is a verification deploy, not a release"
    else
        log "cannot determine whether ${name} is a tag (api.github.com unreachable)"
        return 1
    fi

    # /commits/<ref> resolves tags, branches and bare SHAs alike, and reports the
    # commit together with its tree - the tree is what the tarball is checked against.
    api_get "/commits/${name}" || { log "api.github.com cannot resolve ${name}"; return 1; }
    API_COMMIT="$(printf '%s' "$API_BODY" | jq -r '.sha // empty')"
    API_TREE="$(printf '%s' "$API_BODY" | jq -r '.commit.tree.sha // empty')"
    if [ -z "$API_COMMIT" ] || [ -z "$API_TREE" ]; then
        log "unexpected api.github.com response for ${name}"
        return 1
    fi
    SHA="${API_COMMIT:0:12}"

    if [ "$KIND" = "tag" ]; then
        CODELOAD_REF="refs/tags/${name}"
    else
        api_get "/git/refs/heads/${name}"; rc=$?
        if [ "$rc" -eq 0 ]; then
            CODELOAD_REF="refs/heads/${name}"
        elif [ "$rc" -eq 2 ]; then
            CODELOAD_REF="${API_COMMIT}"
        else
            log "cannot tell whether ${name} is a branch (api.github.com unreachable)"
            return 1
        fi
    fi
    log "resolved ${REF} -> commit ${API_COMMIT:0:12}, tree ${API_TREE:0:12} (${KIND}, codeload ${CODELOAD_REF})"
    return 0
}

verify_tree() {
    # Recompute the git tree hash of a directory using a throwaway index, so the
    # checkout's real index is never touched. Going through the repo's object store
    # means its .gitattributes apply, which is what makes the result comparable to
    # the tree SHA the API reports.
    local dir="$1" idx="$2" tree
    rm -f "$idx"
    if ! GIT_INDEX_FILE="$idx" git --git-dir="${APP_DIR}/.git" --work-tree="$dir" \
            add -A >/dev/null 2>&1; then
        log "tree verification: git add failed for ${dir}"
        rm -f "$idx"
        return 1
    fi
    tree="$(GIT_INDEX_FILE="$idx" git --git-dir="${APP_DIR}/.git" --work-tree="$dir" \
            write-tree 2>/dev/null)"
    rm -f "$idx"
    [ -n "$tree" ] || { log "tree verification: write-tree produced nothing"; return 1; }
    printf '%s' "$tree"
}

fetch_tree_via_tarball() {
    local url got
    SRC_TMP="$(mktemp -d /tmp/waypost-src-XXXXXX)" || { log "mktemp failed"; return 1; }
    url="${CODELOAD_URL}/${REPO_SLUG}/tar.gz/${CODELOAD_REF}"
    log "downloading ${url}"
    curl -fsSL --max-time 300 "$url" -o "${SRC_TMP}/src.tgz" || { log "codeload download failed"; return 1; }
    gzip -t "${SRC_TMP}/src.tgz" || { log "downloaded file is not valid gzip"; return 1; }
    log "downloaded $(du -h "${SRC_TMP}/src.tgz" | cut -f1)"
    mkdir -p "${SRC_TMP}/tree"
    tar xzf "${SRC_TMP}/src.tgz" -C "${SRC_TMP}/tree" --strip-components=1 \
        || { log "tar extraction failed"; return 1; }
    log "extracted $(find "${SRC_TMP}/tree" -type f | wc -l) files"

    got="$(verify_tree "${SRC_TMP}/tree" "${SRC_TMP}/verify.idx")" || return 1
    if [ "$got" != "$API_TREE" ]; then
        log "INTEGRITY FAILURE: tarball tree ${got:-<none>} != ${API_TREE} expected for ${API_COMMIT:0:12}"
        log "refusing to deploy content that cannot be tied to the tag"
        return 1
    fi
    log "integrity verified: tree ${got} matches commit ${API_COMMIT:0:12}"
    return 0
}

sync_tree_into_checkout() {
    local got
    command -v rsync >/dev/null 2>&1 || { log "the codeload fallback needs rsync (sudo apt install rsync)"; return 1; }

    # .env is gitignored and lives inside the checkout, and compose reads it from
    # there. Copy it outside the tree before anything can be deleted.
    cp -p "${APP_DIR}/.env" "${BACKUP_DIR}/env.pre-tarball-${STAMP}" \
        || log "WARNING: could not back up .env before syncing"

    # --delete removes what the new tree does not have, which is the equivalent of
    # git mode's `git clean -ffd`. The `:- .gitignore` filter makes rsync honour
    # per-directory .gitignore files, so every ignored path is protected from
    # deletion. `.env` and `.git/` get explicit excludes on top of that: .env is
    # only ignored by way of a `*.env` pattern, and losing it would stop the app
    # from booting at all - too severe to leave resting on one line of .gitignore.
    rsync -a --delete --exclude='.git/' --exclude='.env' --filter=':- .gitignore' \
          "${SRC_TMP}/tree/" "${APP_DIR}/" || { log "rsync into ${APP_DIR} failed"; return 1; }

    # Belt and braces: the file the whole stack depends on must still be there.
    [ -s "${APP_DIR}/.env" ] || { log ".env is missing or empty after the sync - aborting"; return 1; }

    # Re-verify in place: what is now on disk must still hash to the expected tree.
    got="$(verify_tree "$APP_DIR" "${SRC_TMP}/verify2.idx")" || return 1
    if [ "$got" != "$API_TREE" ]; then
        log "INTEGRITY FAILURE after sync: checkout tree ${got:-<none>} != ${API_TREE}"
        log "the checkout may be inconsistent; reconcile it with: git -C ${APP_DIR} checkout -f <ref> && git -C ${APP_DIR} clean -ffd"
        return 1
    fi
    log "checkout synced from the tarball and re-verified (tree ${got})"
    return 0
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
            # Needed when the failed deploy came in as a tarball: that path leaves
            # files behind that the previous ref does not have, and compose reads
            # docker-compose.yml from this directory, so a stale extra file could
            # change what the rollback starts. -ffd, never -x: .env must survive.
            git clean -ffd || log "git clean during rollback failed; source tree may hold files from the failed release"
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
# branch deploy is meaningless against a stale remote-tracking ref. If the fetch
# cannot be made to work, fall back to codeload (see the note above repo_slug()).
fetch_ok=1
if [ "$FORCE_TARBALL_SOURCE" = "1" ]; then
    log "FORCE_TARBALL_SOURCE=1 - skipping git fetch and taking the codeload path (drill mode)"
    fetch_ok=0
else
    fetch_with_retry || fetch_ok=0
fi

if [ "$fetch_ok" -eq 1 ]; then
    SOURCE_MODE="git"
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
else
    SOURCE_MODE="tarball"
    log "git fetch failed after ${FETCH_ATTEMPTS} attempt(s) - falling back to codeload.github.com"
    resolve_via_api || die "cannot deploy ${REF} via the codeload fallback - see the reason logged above (git fetch had already failed)"
    fetch_tree_via_tarball || die "the codeload fallback did not produce a tree verified against ${REF}"
    # Recorded in deploy.log so an audit can tell how a release reached this host.
    KIND="${KIND}+tarball"
fi

log "=== deploying ${REF} as image ${IMAGE}:${TAG} (${KIND}, source ${SOURCE_MODE}) as $(id -un) ==="

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

# --- 3. put the released tree into the checkout ------------------------------
if [ "$SOURCE_MODE" = "git" ]; then
    # (fetched and verified in the pre-flight above)
    git checkout -f "$REF" || fail "git checkout ${REF} failed"
    # -ffd, never -x: .env is gitignored and must survive every deploy.
    git clean -ffd || fail "git clean failed"
    if [ -z "$SHA" ]; then
        SHA="$(git rev-parse --short "${REF}^{commit}" 2>/dev/null || git rev-parse --short HEAD)"
    fi
    log "checked out $(git describe --tags 2>/dev/null || git rev-parse --short HEAD) (${SHA})"
else
    sync_tree_into_checkout || fail "syncing the verified tarball into ${APP_DIR} failed"
    # The files are the tag's, but .git still points wherever the last git-mode
    # deploy left it, so `git describe` below is stale BY DESIGN. The truth is
    # recorded twice: /healthz/ reports GIT_SHA (exported in step 4), and
    # deploy.log's kind column ends in "+tarball". The next git-mode deploy
    # reconciles the checkout with `git checkout -f` + `git clean -ffd`.
    log "checkout holds ${REF} (${SHA}) from the codeload tarball"
    log "  git metadata still reports $(git describe --tags 2>/dev/null || git rev-parse --short HEAD 2>/dev/null || echo unknown) - expected after a tarball deploy"
fi

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
    # -L is load-bearing. A bare /accounts/login/ legitimately 302s to the default
    # language prefix (/en-us/accounts/login/), so demanding 200 on the first hop
    # fails EVERY deploy even when the edge is fine - it marked a healthy deploy
    # FAILED_EDGE during bring-up. Following redirects tests the whole path instead:
    # Nginx, TLS, the locale redirect and the rendered login page.
    login_code="$(curl -sL -o /dev/null -w '%{http_code}' --max-time 20 "$PUBLIC_LOGIN_URL" || true)"
    if [ "$login_code" != "200" ]; then
        log "public login page returned ${login_code:-none} after following redirects (expected 200): ${PUBLIC_LOGIN_URL}"
        edge_ok=0
    else
        log "health gate passed (public login, redirects followed): ${PUBLIC_LOGIN_URL}"
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
if is_release; then
    log "=== ${TAG} deployed and healthy ==="
else
    log "=== ${REF} deployed and healthy (image ${IMAGE}:${TAG}, NOT a release) ==="
fi
docker compose ps || true
curl -s --max-time 10 "$PUBLIC_HEALTH_URL" || curl -s --max-time 10 "$LOCAL_HEALTH_URL" || true
echo
if is_release; then record "OK"; else record "DEV_OK"; fi
