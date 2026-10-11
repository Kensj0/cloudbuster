#!/usr/bin/env bash
# Create the private config and data directories with strict ownership.
# Safe to run again; it only tightens permissions and creates what is missing.
#
# Usage: sudo ./scripts/prepare-host.sh
#
# Layout (owner, mode):
#   $CLOUDBUSTER_CONFIG_DIR            root:root 0700   private settings
#     .env                             root:root 0600
#     config.yaml                      root:root 0600
#     docker-compose.override.yml      root:root 0600   (optional, your own additions)
#   $CLOUDBUSTER_DATA_DIR              root:root 0700   nobody but root can even list it
#     immich/library  immich/postgres  nextcloud/html  nextcloud/data  nextcloud/postgres
#     sync                             10001:10001 0700 (the sync container's uid)
#     caddy/                           (only with compose/caddy.yml)
# shellcheck source=lib.sh
. "$(dirname "$0")/lib.sh"
cb_require_root

install -d -o root -g root -m 0700 "$CLOUDBUSTER_CONFIG_DIR"
if [[ ! -f "$CB_ENV_FILE" ]]; then
  install -o root -g root -m 0600 "$CB_REPO_DIR/.env.example" "$CB_ENV_FILE"
  cb_info "created $CB_ENV_FILE from the template. Edit it, then run this script again."
  exit 0
fi
if [[ ! -f "$CLOUDBUSTER_CONFIG_DIR/config.yaml" ]]; then
  install -o root -g root -m 0600 "$CB_REPO_DIR/config.example.yaml" "$CLOUDBUSTER_CONFIG_DIR/config.yaml"
  cb_info "created $CLOUDBUSTER_CONFIG_DIR/config.yaml from the template"
fi
chown -R root:root "$CLOUDBUSTER_CONFIG_DIR"
chmod 0700 "$CLOUDBUSTER_CONFIG_DIR"
find "$CLOUDBUSTER_CONFIG_DIR" -type f -exec chmod 0600 {} +

cb_load_env
grep -q 'change-me' "$CB_ENV_FILE" && cb_die "$CB_ENV_FILE still contains 'change-me' placeholders"

D="$CLOUDBUSTER_DATA_DIR"
install -d -o root -g root -m 0700 "$D"
for sub in immich immich/library immich/postgres nextcloud nextcloud/html nextcloud/data nextcloud/postgres; do
  # The containers set ownership inside these on first start (postgres, www-data).
  [[ -d "$D/$sub" ]] || install -d -o root -g root -m 0700 "$D/$sub"
done
install -d -o 10001 -g 10001 -m 0700 "$D/sync"
chmod 0700 "$D" "$D/immich" "$D/nextcloud"

if [[ -n "${CLOUDBUSTER_BACKUP_DIR:-}" ]]; then
  install -d -o root -g root -m 0700 "$CLOUDBUSTER_BACKUP_DIR" 2>/dev/null || \
    echo "note: could not create $CLOUDBUSTER_BACKUP_DIR (is the backup disk mounted?)"
fi

cb_info "config: $CLOUDBUSTER_CONFIG_DIR  data: $D  (both root-only)"
