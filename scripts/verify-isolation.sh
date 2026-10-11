#!/usr/bin/env bash
# Prove the isolation: run this after installing, after every update, and
# before importing private photos. Exit code 0 only if every check passes.
#
# Usage: sudo ./scripts/verify-isolation.sh
#
# Reads all paths, user names and ports from $CLOUDBUSTER_CONFIG_DIR/.env and
# config.yaml, so it works on any installation. Prints no secrets.
# shellcheck source=lib.sh
. "$(dirname "$0")/lib.sh"
cb_require_root
cb_load_env
set +e

U="$AGENT_USER"
PASS=0; FAIL=0; WARN=0
ok()   { printf '  \e[32mPASS\e[0m  %s\n' "$1"; PASS=$((PASS+1)); }
bad()  { printf '  \e[31mFAIL\e[0m  %s\n' "$1"; FAIL=$((FAIL+1)); }
warn() { printf '  \e[33mWARN\e[0m  %s\n' "$1"; WARN=$((WARN+1)); }
# expect_fail "description" command...   (run as the agent; must NOT succeed)
expect_fail() { local d="$1"; shift; if runuser -u "$U" -- "$@" >/dev/null 2>&1; then bad "$d"; else ok "$d"; fi; }
expect_ok()   { local d="$1"; shift; if runuser -u "$U" -- "$@" >/dev/null 2>&1; then ok "$d"; else bad "$d"; fi; }
tcp() { timeout 5 bash -c "exec 3<>/dev/tcp/$1/$2" 2>/dev/null; }
agent_tcp() { runuser -u "$U" -- timeout 5 bash -c "exec 3<>/dev/tcp/$1/$2" >/dev/null 2>&1; }
container_ip() { docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}' "$1" 2>/dev/null | awk '{print $1}'; }

id "$U" >/dev/null 2>&1 || cb_die "agent user $U does not exist"
IMMICH_PORT="${IMMICH_PORT:-2283}"; NC_PORT="${NEXTCLOUD_PORT:-8080}"
NC="http://127.0.0.1:$NC_PORT"; IM="http://127.0.0.1:$IMMICH_PORT/api"

echo "1. Linux user $U"
groups_of="$(id -nG "$U")"
if [[ "$groups_of" == "$(id -gn "$U")" ]]; then
  ok "member of its own group only"
else
  bad "member of its own group only (also in: $groups_of)"
fi
expect_fail "sudo is not possible" sudo -n true
expect_fail "cannot use the Docker socket" test -w /var/run/docker.sock
expect_fail "docker commands fail" docker ps

echo "2. Files the agent must not reach"
expect_fail "cannot list the data directory ($CLOUDBUSTER_DATA_DIR)" ls "$CLOUDBUSTER_DATA_DIR"
expect_fail "cannot list Immich photos" ls "$CLOUDBUSTER_DATA_DIR/immich/library"
expect_fail "cannot list Nextcloud data" ls "$CLOUDBUSTER_DATA_DIR/nextcloud/data"
expect_fail "cannot list the databases" ls "$CLOUDBUSTER_DATA_DIR/immich/postgres"
expect_fail "cannot list the config directory" ls "$CLOUDBUSTER_CONFIG_DIR"
expect_fail "cannot read .env" cat "$CB_ENV_FILE"
expect_fail "cannot read config.yaml" cat "$CLOUDBUSTER_CONFIG_DIR/config.yaml"
expect_fail "cannot list Docker's data" ls /var/lib/docker
if [[ -n "${OWNER_USER:-}" ]]; then
  OWNER_HOME="$(getent passwd "$OWNER_USER" | cut -d: -f6)"
  expect_fail "cannot list your home ($OWNER_HOME)" ls "$OWNER_HOME"
else
  warn "OWNER_USER not set in .env; skipped the home directory check"
fi
for h in /home/*; do
  [[ "$h" == "$(getent passwd "$U" | cut -d: -f6)" || ! -d "$h" ]] && continue
  if runuser -u "$U" -- ls "$h" >/dev/null 2>&1; then bad "can list $h (run: sudo chmod 700 $h)"; fi
done

echo "3. The agent's own folder"
probe="$AGENT_SYNC_DIR/.cloudbuster-isolation-test"
expect_ok "can write in $AGENT_SYNC_DIR" bash -c "echo test > '$probe'"
expect_ok "can read in $AGENT_SYNC_DIR" cat "$probe"
expect_ok "can delete in $AGENT_SYNC_DIR" rm -f "$probe"

echo "4. Network"
if nft list table inet cloudbuster_agent >/dev/null 2>&1; then ok "agent firewall table is loaded"; else bad "agent firewall table is loaded (run scripts/agent-firewall.sh)"; fi
if tcp 127.0.0.1 "$IMMICH_PORT"; then
  if agent_tcp 127.0.0.1 "$IMMICH_PORT"; then bad "agent cannot reach Immich on 127.0.0.1:$IMMICH_PORT"; else ok "agent cannot reach Immich on 127.0.0.1:$IMMICH_PORT"; fi
else
  warn "Immich is not listening on 127.0.0.1:$IMMICH_PORT; skipped"
fi
for c in cloudbuster-immich-server:2283 cloudbuster-immich-postgres:5432 cloudbuster-nextcloud-postgres:5432 cloudbuster-nextcloud:80; do
  ip="$(container_ip "${c%%:*}")"; port="${c##*:}"
  [[ -n "$ip" ]] || { warn "${c%%:*} is not running; skipped"; continue; }
  if agent_tcp "$ip" "$port"; then bad "agent cannot reach ${c%%:*} directly"; else ok "agent cannot reach ${c%%:*} directly"; fi
done
for addr in $(hostname -I 2>/dev/null); do
  [[ "$addr" == *:* ]] && continue
  if agent_tcp "$addr" 22 || agent_tcp "$addr" 443 || agent_tcp "$addr" 8443; then
    bad "agent cannot reach this machine on $addr"
  else
    ok "agent cannot reach this machine on $addr"
  fi
done
if agent_tcp 127.0.0.1 "$NC_PORT"; then ok "agent's sync client can reach Nextcloud (needed)"; else bad "agent's sync client can reach Nextcloud on 127.0.0.1:$NC_PORT"; fi
if runuser -u "$U" -- timeout 8 bash -c 'exec 3<>/dev/tcp/api.anthropic.com/443' >/dev/null 2>&1; then
  ok "agent can reach the internet (its AI service)"
else
  warn "agent cannot reach api.anthropic.com:443 (no internet, or DNS?)"
fi

echo "5. Nextcloud accounts"
AGENT_HOME="$(getent passwd "$U" | cut -d: -f6)"
nc_root_listing() {  # user password -> names in the account's root folder
  curl -s -u "$1:$2" -X PROPFIND -H 'Depth: 1' "$NC/remote.php/dav/files/$1/" |
    python3 -c '
import sys, urllib.parse, xml.etree.ElementTree as ET
ns = {"d": "DAV:"}
try:
    hrefs = [urllib.parse.unquote(h.text) for h in ET.fromstring(sys.stdin.read()).findall(".//d:href", ns)]
except ET.ParseError:
    sys.exit(1)
print("\n".join(sorted({h.rstrip("/").rsplit("/", 1)[-1] for h in hrefs[1:]})))'
}
check_nc_account() {  # user password expected_folder
  local listing expected="${3#/}"
  listing="$(nc_root_listing "$1" "$2")" || { bad "$1: could not log in over WebDAV"; return; }
  if [[ "$listing" == "$expected" ]]; then ok "$1 sees only '$expected'"; else bad "$1 sees only '$expected' (it sees: $(echo "$listing" | tr '\n' ' '))"; fi
  local code
  code="$(curl -s -u "$1:$2" -H 'OCS-APIRequest: true' -X POST "$NC/ocs/v2.php/apps/files_sharing/api/v1/shares?format=json" \
    --data-urlencode "path=/$expected" -d shareType=3 | python3 -c 'import json,sys; print(json.load(sys.stdin)["ocs"]["meta"]["statuscode"])' 2>/dev/null)"
  if [[ "$code" == "200" ]]; then bad "$1 cannot create public links (it just did; delete that link!)"; else ok "$1 cannot create public links"; fi
  code="$(curl -s -o /dev/null -w '%{http_code}' -u "$1:$2" -H 'OCS-APIRequest: true' "$NC/ocs/v2.php/cloud/users")"
  if [[ "$code" == "200" ]]; then bad "$1 cannot list users"; else ok "$1 cannot list users"; fi
}
if [[ -r "$AGENT_HOME/.netrc" ]]; then
  read -r _ _ _ nc_user _ nc_pass < "$AGENT_HOME/.netrc"
  check_nc_account "$nc_user" "$nc_pass" "${AGENT_REMOTE_FOLDER:?}"
  unset nc_pass
else
  warn "no $AGENT_HOME/.netrc; skipped the agent account checks"
fi
SYNC_FOLDER="$(python3 -c 'import sys,yaml; print(yaml.safe_load(open(sys.argv[1]))["sync"]["nextcloud_folder"])' "$CLOUDBUSTER_CONFIG_DIR/config.yaml" 2>/dev/null ||
  awk -F'"' '/nextcloud_folder:/ {print $2}' "$CLOUDBUSTER_CONFIG_DIR/config.yaml")"
if [[ -n "${SYNC_NEXTCLOUD_APP_PASSWORD:-}" ]]; then
  check_nc_account "$SYNC_NEXTCLOUD_USER" "$SYNC_NEXTCLOUD_APP_PASSWORD" "$SYNC_FOLDER"
else
  warn "SYNC_NEXTCLOUD_APP_PASSWORD not set; skipped the sync account checks"
fi

echo "6. Immich sync key"
if [[ -n "${SYNC_IMMICH_API_KEY:-}" ]]; then
  ALBUM="$(awk -F'"' '/album_name:/ {print $2}' "$CLOUDBUSTER_CONFIG_DIR/config.yaml")"
  imget() { curl -s -H "x-api-key: $SYNC_IMMICH_API_KEY" "$IM$1"; }
  imget /api-keys/me | python3 -c '
import json, sys
allowed = {"album.read","asset.read","asset.download","asset.upload","albumAsset.create","albumAsset.delete","user.read"}
perms = set(json.load(sys.stdin).get("permissions", []))
extra = perms - allowed
print("ok" if perms and not extra else "extra:" + ",".join(sorted(extra or {"<none or all>"})))' |
    { read -r r; if [[ "$r" == ok ]]; then ok "API key has only the needed permissions"; else bad "API key has only the needed permissions ($r)"; fi; }
  albums="$(imget /albums | python3 -c 'import json,sys; print("\n".join(sorted(a["albumName"] for a in json.load(sys.stdin))))' 2>/dev/null)"
  if [[ "$albums" == "$ALBUM" ]]; then ok "sync user sees only the album '$ALBUM'"; else bad "sync user sees only the album '$ALBUM' (sees: $(echo "$albums" | tr '\n' ' '))"; fi
  me="$(imget /users/me | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])' 2>/dev/null)"
  foreign="$(curl -s -H "x-api-key: $SYNC_IMMICH_API_KEY" -H 'Content-Type: application/json' -X POST "$IM/search/metadata" \
    -d '{"size":1000}' | python3 -c "import json,sys; d=json.load(sys.stdin); print(sum(1 for a in d['assets']['items'] if a['ownerId'] != '$me'))" 2>/dev/null)"
  if [[ "$foreign" == "0" ]]; then ok "outside the album, the sync user sees none of your photos (partner sharing off)"
  else bad "outside the album, the sync user sees none of your photos (it sees ${foreign:-?}; turn off partner sharing)"; fi
else
  warn "SYNC_IMMICH_API_KEY not set; skipped the Immich checks"
fi

echo
echo "Result: $PASS passed, $FAIL failed, $WARN warnings"
[[ $FAIL -eq 0 ]]
