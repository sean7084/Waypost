#!/usr/bin/env bash
# Nightly Waypost backup. Installed on the host as
# /srv/waypost/bin/waypost-backup.sh (mode 750, owner deploy) and run from that
# account's crontab; this file in the repository is the authoritative copy, so a
# host-side change is a reviewable diff rather than an edit nobody sees.
#
# This script is the docker/bin/ci-deploy.sh checkpoint with the retention policy
# of docs/BACKUP_RESTORE.md, and both dump the database through
# docker/bin/_pg_checkpoint.sh so they cannot disagree about which database is
# being protected. See docs/BACKUP_RESTORE.md §2b and docs/RDS_MIGRATION.md.
set -euo pipefail

# Cron has no login shell, so this account's Docker socket must be located the same
# way ci-deploy.sh locates it: straight at the rootless socket, not via whatever the
# CLI context happens to point at, and never via ~/.bashrc.
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export DOCKER_HOST="${DOCKER_HOST:-unix://${XDG_RUNTIME_DIR}/docker.sock}"
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:${PATH}"

BACKUP_DIR="/srv/waypost/backups"
DATA_DIR="${WAYPOST_DATA_DIR:-/home/deploy/waypost-data}"
STAMP=$(date +%Y%m%d_%H%M%S)
APP_DIR="/srv/waypost/app"

mkdir -p "${BACKUP_DIR}/pg"
cd "$APP_DIR"

# `$0` is a bare name when cron or an operator runs this from its own directory, so
# resolve the directory rather than trusting ${0%/*}.
_BIN_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=/dev/null
. "${_BIN_DIR}/_pg_checkpoint.sh"

# 1) Database. The mode - compose db service, the DSN the app container carries, or
#    none - is resolved by _pg_checkpoint.sh, so this is the same rule the pre-deploy
#    checkpoint uses. A dump that cannot be written is a failed backup, not a warning:
#    the whole point of this job is the restore point. `none` is the one case that is
#    a decision rather than a failure, so it is reported and the rest continues.
CHECKPOINT_MODE="$(db_checkpoint_mode)"
case "$CHECKPOINT_MODE" in
    invalid)
        echo "$(date -Is) FATAL DB_CHECKPOINT_MODE='${DB_CHECKPOINT_MODE}' is not one of auto|compose|external|none" >&2
        exit 1
        ;;
    none)
        echo "$(date -Is) database dump skipped (DB_CHECKPOINT_MODE=none - the database has managed backups)"
        ;;
    *)
        if ! take_database_checkpoint "db_${STAMP}.dump" "$CHECKPOINT_MODE"; then
            echo "$(date -Is) FAIL database dump: ${CHECKPOINT_FAILURE_REASON}" >&2
            exit 1
        fi
        echo "$(date -Is) database dump written from ${CHECKPOINT_SOURCE} (host ${CHECKPOINT_HOST}): ${CHECKPOINT}"
        ;;
esac

# 2) Media uploads - gated, not deleted. While media is still on the bind mount this
#    tar is the ONLY copy of the inspection photos, so flipping the switch has to
#    follow a verified OSS backfill (docs/OSS_STORAGE.md §8-§9), not precede it.
#    Default 0 = runs exactly as it always did.
if [ "${WAYPOST_MEDIA_IN_OSS:-0}" != "1" ]; then
    tar czf "${BACKUP_DIR}/media_${STAMP}.tar.gz" -C "$DATA_DIR" media
else
    echo "$(date -Is) media tar skipped (WAYPOST_MEDIA_IN_OSS=1)"
fi

# 3) Document templates - unconditional. A read-only bind mount the app only fills
#    copies of; it is not going to object storage and it is not in git.
tar czf "${BACKUP_DIR}/template_files_${STAMP}.tar.gz" -C "$DATA_DIR" template_files

# 4) Configuration. .env holds DJANGO_FIELD_ENCRYPTION_KEY, without which stored
#    mailbox credentials cannot be decrypted even from a good database dump.
#    It is copied at mode 600 rather than gpg'd against a passphrase stored next
#    to the ciphertext, which would be security theatre: an attacker who can read
#    the backup directory could read the passphrase too. Anything that leaves this
#    host (object storage, a laptop) must be encrypted in transit/at rest there.
#
#    WARNING: that copy is on the SAME host as the database. There is no off-host
#    copy of DJANGO_FIELD_ENCRYPTION_KEY, so a total host loss takes the key with it
#    and the restored database then decrypts nothing. Until key governance exists
#    (out of scope here), the second copy has to be made by hand, from this file,
#    into a password manager - this is where the next reader will look.
install -m 600 "${APP_DIR}/.env" "${BACKUP_DIR}/env_${STAMP}"

# 5) Retention
find "${BACKUP_DIR}/pg" -name 'db_*.dump' -mtime +7 -delete
if [ "${WAYPOST_MEDIA_IN_OSS:-0}" != "1" ]; then
    find "${BACKUP_DIR}" -maxdepth 1 -name 'media_*.tar.gz' -mtime +"${MEDIA_TAR_RETENTION_DAYS:-28}" -delete
fi
find "${BACKUP_DIR}" -maxdepth 1 -name 'template_files_*.tar.gz' -mtime +28 -delete
find "${BACKUP_DIR}" -maxdepth 1 -name 'env_*'                   -mtime +28 -delete

echo "$(date -Is) ok ${STAMP}"
