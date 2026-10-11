#!/usr/bin/env bash
# Create (or re-check) the Linux user that runs the AI agent, and give it
# exactly one folder that a Nextcloud sync client keeps in sync.
#
# Usage: sudo ./scripts/create-agent-user.sh
#
# What it does (safe to run again):
#   * creates $AGENT_USER with a locked password (you enter it with `sudo -iu`)
#   * removes it from every privileged group (sudo, docker, adm, ...)
#   * home directory 0700, sync folder $AGENT_SYNC_DIR 0700
#   * installs the Claude Code settings (second layer) and a nextcloudcmd timer
# It does NOT create the agent's Nextcloud app password; see scripts/nextcloud-setup.sh.
# shellcheck source=lib.sh
. "$(dirname "$0")/lib.sh"
cb_require_root
cb_load_env

U="$AGENT_USER"
[[ "$U" =~ ^[a-z_][a-z0-9_-]{0,30}$ ]] || cb_die "AGENT_USER '$U' is not a valid user name"
[[ "$U" != "root" && "$U" != "${OWNER_USER:-}" ]] || cb_die "AGENT_USER must be a separate, new user"

if id "$U" >/dev/null 2>&1; then
  cb_info "user $U exists, re-checking it"
else
  useradd --create-home --shell /bin/bash --user-group "$U"
  cb_info "created user $U"
fi
passwd -l "$U" >/dev/null   # no password login; only `sudo -iu $U` from an admin

HOME_DIR="$(getent passwd "$U" | cut -d: -f6)"
case "$AGENT_SYNC_DIR" in "$HOME_DIR"/*) ;; *) cb_die "AGENT_SYNC_DIR must be inside $HOME_DIR";; esac

# Remove from every group except its own primary group.
for g in $(id -nG "$U"); do
  [[ "$g" == "$(id -gn "$U")" ]] && continue
  gpasswd -d "$U" "$g" >/dev/null && cb_info "removed $U from group $g"
done
# Make sure no sudoers rule grants it anything.
if command -v sudo >/dev/null && ! sudo -l -U "$U" 2>&1 | grep -q "not allowed to run sudo"; then
  cb_die "a sudoers rule lets $U run sudo (see: sudo -l -U $U). Remove it, then run again."
fi

chmod 0700 "$HOME_DIR"
install -d -o "$U" -g "$U" -m 0700 "$AGENT_SYNC_DIR"

# ---- Claude Code settings (second layer, see docs/security-model.md) ---------
# A root-owned copy the agent cannot change, used through an alias...
install -d -o root -g root -m 0755 /etc/cloudbuster-agent
sed "s#@AGENT_SYNC_DIR@#$AGENT_SYNC_DIR#g; s#@CONFIG_DIR@#$CLOUDBUSTER_CONFIG_DIR#g; s#@DATA_DIR@#$CLOUDBUSTER_DATA_DIR#g" \
  "$CB_REPO_DIR/agent/claude-settings.json" > /etc/cloudbuster-agent/claude-settings.json
chmod 0644 /etc/cloudbuster-agent/claude-settings.json
# ...and the same file as the user's settings for tools that ignore the alias.
install -d -o "$U" -g "$U" -m 0700 "$HOME_DIR/.claude"
install -o root -g root -m 0644 /etc/cloudbuster-agent/claude-settings.json "$HOME_DIR/.claude/settings.json"

BASHRC="$HOME_DIR/.bashrc"
MARK="# cloudbuster: agent settings"
if ! grep -qF "$MARK" "$BASHRC" 2>/dev/null; then
  {
    echo "$MARK"
    echo "alias claude='claude --settings /etc/cloudbuster-agent/claude-settings.json'"
    echo "umask 077"
  } >> "$BASHRC"
  chown "$U:$U" "$BASHRC"
fi

# ---- Nextcloud sync client (nextcloudcmd) as a systemd user timer ------------
if ! command -v nextcloudcmd >/dev/null; then
  echo "note: nextcloudcmd is not installed. On Debian/Ubuntu: sudo apt install nextcloud-desktop-cmd"
fi
UNIT_DIR="$HOME_DIR/.config/systemd/user"
install -d -o "$U" -g "$U" -m 0700 "$HOME_DIR/.config" "$HOME_DIR/.config/systemd" "$UNIT_DIR"
sed "s#@AGENT_SYNC_DIR@#$AGENT_SYNC_DIR#g; s#@AGENT_REMOTE_FOLDER@#${AGENT_REMOTE_FOLDER:?missing in .env}#g; s#@NEXTCLOUD_PORT@#${NEXTCLOUD_PORT:-8080}#g" \
  "$CB_REPO_DIR/agent/cloudbuster-agent-sync.service" > "$UNIT_DIR/cloudbuster-agent-sync.service"
cp "$CB_REPO_DIR/agent/cloudbuster-agent-sync.timer" "$UNIT_DIR/"
chown "$U:$U" "$UNIT_DIR"/cloudbuster-agent-sync.*
chmod 0600 "$UNIT_DIR"/cloudbuster-agent-sync.*
loginctl enable-linger "$U" || echo "note: could not enable lingering (is systemd running?)"

if [[ -s "$HOME_DIR/.netrc" ]]; then
  runuser -u "$U" -- env XDG_RUNTIME_DIR="/run/user/$(id -u "$U")" systemctl --user daemon-reload || true
  runuser -u "$U" -- env XDG_RUNTIME_DIR="/run/user/$(id -u "$U")" systemctl --user enable --now cloudbuster-agent-sync.timer \
    && cb_info "folder sync timer enabled"
else
  cb_info "no ~/.netrc yet for $U; run scripts/nextcloud-setup.sh, then this script again to start the sync timer"
fi

cb_info "done. Start the agent with:  sudo -iu $U  then  cd $AGENT_SYNC_DIR && claude"
