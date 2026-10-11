# Shared helpers for cloudbuster scripts. Source it, don't run it.
# shellcheck shell=bash

set -euo pipefail

CB_REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLOUDBUSTER_CONFIG_DIR="${CLOUDBUSTER_CONFIG_DIR:-/etc/cloudbuster}"
CB_ENV_FILE="$CLOUDBUSTER_CONFIG_DIR/.env"

cb_die()  { echo "error: $*" >&2; exit 1; }
cb_info() { echo "==> $*"; }

cb_require_root() {
  [[ $EUID -eq 0 ]] || cb_die "run this with sudo (it needs to read $CB_ENV_FILE)"
}

# Load the private .env without printing anything from it.
cb_load_env() {
  [[ -r "$CB_ENV_FILE" ]] || cb_die "cannot read $CB_ENV_FILE (copy .env.example there first, see docs/install.md)"
  set -a
  # shellcheck disable=SC1090
  . "$CB_ENV_FILE"
  set +a
  : "${CLOUDBUSTER_DATA_DIR:?missing in .env}"
  : "${AGENT_USER:?missing in .env}"
  : "${AGENT_SYNC_DIR:?missing in .env}"
}

# docker compose with the private env file and optional extra/override files.
cb_compose() {
  local args=(--project-directory "$CB_REPO_DIR" --env-file "$CB_ENV_FILE" -f "$CB_REPO_DIR/docker-compose.yml")
  local extra
  for extra in ${CLOUDBUSTER_EXTRA_COMPOSE:-}; do
    [[ "$extra" = /* ]] || extra="$CB_REPO_DIR/$extra"
    args+=(-f "$extra")
  done
  if [[ -f "$CLOUDBUSTER_CONFIG_DIR/docker-compose.override.yml" ]]; then
    args+=(-f "$CLOUDBUSTER_CONFIG_DIR/docker-compose.override.yml")
  fi
  docker compose "${args[@]}" "$@"
}

# Run a command as the agent user, with a clean login environment.
cb_as_agent() {
  runuser -l "$AGENT_USER" -s /bin/bash -c "$*"
}
