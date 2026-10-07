#!/bin/bash
# Container entrypoint: prepare the release, then hand over to CMD.
#
# Order mirrors docs/RELEASE_PROCEDURE.md §6.2, and each step is here rather than
# in CI so that any way of starting the container - `docker compose up`, a manual
# `docker run`, a rollback - leaves the app in a consistent state:
#
#   1. migrate        Schema before the first request is served. Migrations are
#                     committed source; this script never runs `makemigrations`,
#                     which would create schema drift no tag can reproduce.
#   2. collectstatic  Production uses WhiteNoise's
#                     CompressedManifestStaticFilesStorage, so templates reference
#                     hashed filenames that must exist before serving traffic.
#   3. exec "$@"      exec, not a plain call: gunicorn must become PID 1 so it
#                     receives SIGTERM directly. Without exec, Docker's stop
#                     signal goes to this shell and every deploy waits out the
#                     grace period before being SIGKILLed.
#
# Set WAYPOST_SKIP_PREPARE=1 to bypass steps 1-2 for one-off commands:
#   docker compose run --rm -e WAYPOST_SKIP_PREPARE=1 app python manage.py shell
set -euo pipefail

if [ "${WAYPOST_SKIP_PREPARE:-0}" = "1" ]; then
    echo "[entrypoint] WAYPOST_SKIP_PREPARE=1 - skipping migrate and collectstatic"
else
    echo "[entrypoint] applying migrations"
    python manage.py migrate --noinput

    echo "[entrypoint] collecting static files"
    python manage.py collectstatic --noinput --clear
fi

echo "[entrypoint] version=${WAYPOST_VERSION:-unset} revision=${WAYPOST_GIT_SHA:-unset}"
echo "[entrypoint] starting: $*"
exec "$@"
