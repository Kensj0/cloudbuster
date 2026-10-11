#!/usr/bin/env bash
# Create and lock down the two service accounts in Nextcloud. Safe to run again.
#
# Usage: sudo ./scripts/nextcloud-setup.sh
#
#   $AGENT_NEXTCLOUD_USER   used by the agent's sync client
#   $SYNC_NEXTCLOUD_USER    used by the sync service
# Both: not admin, random login password nobody knows, only an app password,
# member of one restriction group "cloudbuster-restricted" that is excluded
# from all sharing (no public links, no re-sharing, no user enumeration).
#
# App passwords are written straight to where they are used and never printed:
#   agent  -> ~$AGENT_USER/.netrc (0600, owned by the agent)
#   sync   -> SYNC_NEXTCLOUD_APP_PASSWORD in $CLOUDBUSTER_CONFIG_DIR/.env
#
# Sharing the folder itself is done by you in the web UI (docs/install.md, step 6).
# shellcheck source=lib.sh
. "$(dirname "$0")/lib.sh"
cb_require_root
cb_load_env

GROUP=cloudbuster-restricted
occ() { cb_compose exec -T -u www-data nextcloud php occ "$@"; }

occ status >/dev/null || cb_die "Nextcloud is not running or not installed yet"

# ---- instance-wide hardening -------------------------------------------------
occ group:add "$GROUP" >/dev/null 2>&1 || true
occ config:app:set core shareapi_exclude_groups --value=yes >/dev/null
occ config:app:set core shareapi_exclude_groups_list --value="[\"$GROUP\"]" >/dev/null
occ config:app:set core shareapi_allow_links_exclude_groups --value="[\"$GROUP\"]" >/dev/null
occ config:app:set core shareapi_allow_share_dialog_user_enumeration --value=no >/dev/null
occ config:app:set core shareapi_restrict_user_enumeration_full_match --value=no >/dev/null
cb_info "sharing disabled for group $GROUP; user enumeration off"

# No external AI integrations. Disable the common ones if they are installed.
for app in assistant integration_openai integration_replicate integration_watsonx llm2 \
           context_chat context_chat_backend stt_whisper2 text2image_stablediffusion2 \
           translate2 summary_bot; do
  if occ app:list --output=json 2>/dev/null | grep -q "\"$app\""; then
    occ app:disable "$app" >/dev/null 2>&1 && cb_info "disabled app $app"
  fi
done

# ---- accounts ------------------------------------------------------------------
create_user() {
  local user="$1" display="$2"
  if occ user:info "$user" >/dev/null 2>&1; then
    cb_info "account $user exists"
  else
    NC_PASS="$(openssl rand -base64 36)" cb_compose exec -T -e NC_PASS -u www-data nextcloud \
      php occ user:add --password-from-env --display-name="$display" "$user" >/dev/null
    cb_info "created account $user"
  fi
  occ group:adduser "$GROUP" "$user" >/dev/null 2>&1 || true
  # Not admin, no other groups.
  local g
  for g in $(occ user:info "$user" --output=json | python3 -c 'import json,sys; print(" ".join(json.load(sys.stdin).get("groups", [])))'); do
    [[ "$g" == "$GROUP" ]] && continue
    occ group:removeuser "$g" "$user" >/dev/null && cb_info "removed $user from group $g"
  done
}

new_app_password() {
  # Prints only the token, for capture by the caller. Never echoed to the terminal.
  # occ prints "app password:" and the token on the next line. Without the login
  # password the token cannot change account settings, which is what we want.
  occ user:auth-tokens:add --no-interaction --name="$2" "$1" | awk 'f {print; exit} /^app password:/ {f=1}' | tr -d '\r'
}

create_user "${AGENT_NEXTCLOUD_USER:?missing in .env}" "AI agent (one folder)"
create_user "${SYNC_NEXTCLOUD_USER:?missing in .env}" "Immich album sync"

# Agent app password -> agent's ~/.netrc
AGENT_HOME="$(getent passwd "$AGENT_USER" | cut -d: -f6)"
[[ -n "$AGENT_HOME" ]] || cb_die "run create-agent-user.sh first"
if [[ -s "$AGENT_HOME/.netrc" ]]; then
  cb_info "agent ~/.netrc exists, keeping it"
else
  token="$(new_app_password "$AGENT_NEXTCLOUD_USER" cloudbuster-agent)"
  [[ -n "$token" ]] || cb_die "could not create an app password for $AGENT_NEXTCLOUD_USER"
  install -o "$AGENT_USER" -g "$AGENT_USER" -m 0600 /dev/null "$AGENT_HOME/.netrc"
  printf 'machine 127.0.0.1 login %s password %s\n' "$AGENT_NEXTCLOUD_USER" "$token" > "$AGENT_HOME/.netrc"
  unset token
  cb_info "wrote the agent's app password to $AGENT_HOME/.netrc"
fi

# Sync app password -> .env
if [[ -n "${SYNC_NEXTCLOUD_APP_PASSWORD:-}" ]]; then
  cb_info "SYNC_NEXTCLOUD_APP_PASSWORD already set, keeping it"
else
  token="$(new_app_password "$SYNC_NEXTCLOUD_USER" cloudbuster-sync)"
  [[ -n "$token" ]] || cb_die "could not create an app password for $SYNC_NEXTCLOUD_USER"
  tmp="$(mktemp "$CLOUDBUSTER_CONFIG_DIR/.env.XXXXXX")"
  awk -v t="$token" 'BEGIN{done=0} /^SYNC_NEXTCLOUD_APP_PASSWORD=/ {print "SYNC_NEXTCLOUD_APP_PASSWORD=" t; done=1; next} {print} END{if(!done) print "SYNC_NEXTCLOUD_APP_PASSWORD=" t}' \
    "$CB_ENV_FILE" > "$tmp"
  chmod 0600 "$tmp"
  mv "$tmp" "$CB_ENV_FILE"
  unset token
  cb_info "wrote the sync app password to $CB_ENV_FILE"
fi

cb_info "next: share your folder with $AGENT_NEXTCLOUD_USER and $SYNC_NEXTCLOUD_USER in the web UI (docs/install.md)"
