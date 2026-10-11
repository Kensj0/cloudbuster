#!/usr/bin/env bash
# Back up both databases and all files.
#
# Usage: sudo ./scripts/backup.sh
#
# Copy 1 is the live data, copy 2 goes to $CLOUDBUSTER_BACKUP_DIR (use a
# different disk). For the off-site copy (the "1" in 3-2-1), set
# RESTIC_REPOSITORY and RESTIC_PASSWORD_FILE in your private .env and restic
# is run after the local copy. See docs/operations.md.
#
# The backup contains your private .env (passwords) and all photos, so it is
# kept root-only (0700) like the data itself.
# shellcheck source=lib.sh
. "$(dirname "$0")/lib.sh"
cb_require_root
cb_load_env

B="${CLOUDBUSTER_BACKUP_DIR:?set CLOUDBUSTER_BACKUP_DIR in .env}"
D="$CLOUDBUSTER_DATA_DIR"
STAMP="$(date +%Y-%m-%d_%H%M)"
KEEP_DUMPS_DAYS="${CLOUDBUSTER_KEEP_DUMPS_DAYS:-14}"
install -d -o root -g root -m 0700 "$B" "$B/dumps" "$B/files"

maintenance() { cb_compose exec -T -u www-data nextcloud php occ maintenance:mode "--$1" >/dev/null; }
trap 'maintenance off || true' EXIT

cb_info "Nextcloud maintenance mode on"
maintenance on

cb_info "dumping databases"
cb_compose exec -T nextcloud-database pg_dump -U nextcloud -d nextcloud --clean --if-exists |
  gzip > "$B/dumps/nextcloud-$STAMP.sql.gz"
cb_compose exec -T immich-database pg_dumpall -U postgres --clean --if-exists |
  gzip > "$B/dumps/immich-$STAMP.sql.gz"

cb_info "copying files (only changes are transferred)"
# Immich: originals and uploads. Thumbnails and encoded videos are regenerated
# by Immich and are left out to save space.
rsync -a --delete --exclude 'thumbs/' --exclude 'encoded-video/' "$D/immich/library/" "$B/files/immich-library/"
rsync -a --delete "$D/nextcloud/data/" "$B/files/nextcloud-data/"
rsync -a --delete "$D/nextcloud/html/config/" "$B/files/nextcloud-config/"
rsync -a --delete "$D/sync/" "$B/files/sync-state/"
rsync -a --delete "$CLOUDBUSTER_CONFIG_DIR/" "$B/files/config/"

maintenance off
trap - EXIT
cb_info "Nextcloud maintenance mode off"

find "$B/dumps" -name '*.sql.gz' -mtime +"$KEEP_DUMPS_DAYS" -delete
chmod -R go-rwx "$B"

if [[ -n "${RESTIC_REPOSITORY:-}" ]]; then
  command -v restic >/dev/null || cb_die "RESTIC_REPOSITORY is set but restic is not installed"
  cb_info "off-site copy with restic"
  restic backup --quiet --tag cloudbuster "$B/dumps" "$B/files"
  restic forget --quiet --tag cloudbuster --keep-daily 7 --keep-weekly 5 --keep-monthly 12 --prune
fi

cb_info "backup done: $B"
