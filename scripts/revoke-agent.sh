#!/usr/bin/env bash
# Emergency stop: cut the AI agent off immediately. Safe to run again.
#
# Usage: sudo ./scripts/revoke-agent.sh
#
#   1. kills every process of $AGENT_USER and locks the account (no sudo -iu)
#   2. stops its folder sync timer
#   3. revokes all app passwords of the agent's Nextcloud account and disables it
# To undo: see docs/operations.md ("Give the agent access again").
# Removing the folder share is done in the Nextcloud web UI (Sharing tab).
# shellcheck source=lib.sh
. "$(dirname "$0")/lib.sh"
cb_require_root
cb_load_env

U="$AGENT_USER"
if id "$U" >/dev/null 2>&1; then
  usermod --expiredate 1 --shell /usr/sbin/nologin "$U"
  pkill -KILL -u "$U" || true
  loginctl disable-linger "$U" 2>/dev/null || true
  loginctl terminate-user "$U" 2>/dev/null || true
  cb_info "Linux user $U locked and all its processes stopped"
fi

occ() { cb_compose exec -T -u www-data nextcloud php occ "$@"; }
NCU="${AGENT_NEXTCLOUD_USER:?missing in .env}"
if occ user:info "$NCU" >/dev/null 2>&1; then
  for id in $(occ user:auth-tokens:list "$NCU" --output=json 2>/dev/null | python3 -c 'import json,sys; print(" ".join(str(t["id"]) for t in json.load(sys.stdin)))'); do
    occ user:auth-tokens:delete "$NCU" "$id" >/dev/null && cb_info "revoked app password $id"
  done
  occ user:disable "$NCU" >/dev/null && cb_info "Nextcloud account $NCU disabled"
fi

cb_info "done. Also remove the share with $NCU in the Nextcloud web UI if you want it gone for good."
