#!/usr/bin/env bash
# Off-site copy of DigiClinic backups (07 §7.5). The files are already encrypted (Fernet) by `manage.py backup`,
# so the remote only ever stores ciphertext. Run daily from cron (see deploy/cron), after the scheduler's backup.
#
#   RCLONE_REMOTE=gdrive:digiclinic-backups /opt/digiclinic/deploy/offsite-backup.sh
set -euo pipefail

cd "$(dirname "$0")"
LOCAL_DIR="${LOCAL_DIR:-/var/backups/digiclinic}"
REMOTE="${RCLONE_REMOTE:?set RCLONE_REMOTE, e.g. gdrive:digiclinic-backups}"
KEEP_LOCAL_DAYS="${KEEP_LOCAL_DAYS:-30}"

mkdir -p "$LOCAL_DIR"
# 1) copy the newest backups out of the container volume
docker compose -f compose.yml --env-file .env cp scheduler:/data/backups/. "$LOCAL_DIR/"
# 2) push to the off-site remote (never deletes anything remotely)
rclone copy "$LOCAL_DIR" "$REMOTE" --include "digiclinic-*.dcbak" --max-age 72h
# 3) keep the host copy bounded
find "$LOCAL_DIR" -name "digiclinic-*.dcbak" -mtime +"$KEEP_LOCAL_DAYS" -delete
echo "$(date -Is) off-site backup OK"
