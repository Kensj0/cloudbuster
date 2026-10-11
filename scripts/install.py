#!/usr/bin/env python3
"""cloudbuster guided installer.

Asks a few questions, then runs the existing scripts in scripts/ in the right
order, one step at a time. It has no behaviour of its own: every system change
is a command you can read in scripts/ or in docs/install.md. Steps that need a
web browser are shown as checklists and wait for you.

Usage, from your normal login:

    cd /opt/cloudbuster
    sudo ./scripts/install.py                 # guided installation
    sudo ./scripts/install.py --dry-run       # show every command, change nothing
    sudo ./scripts/install.py --list          # list the step names
    sudo ./scripts/install.py --from immich   # resume at a step

Running it again is safe: answers and secrets already in /etc/cloudbuster/.env
are kept, and every step can be repeated.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import pwd
import re
import secrets
import shlex
import shutil
import subprocess
import tempfile
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

REPO = Path(__file__).resolve().parent.parent
CONFIG_DIR = Path(os.environ.get("CLOUDBUSTER_CONFIG_DIR", "/etc/cloudbuster"))
ENV_FILE = CONFIG_DIR / ".env"
CONFIG_YAML = CONFIG_DIR / "config.yaml"
ENV_TEMPLATE = REPO / ".env.example"
YAML_TEMPLATE = REPO / "config.example.yaml"

AGENT_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,30}$")
HOST_RE = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)+$")
FOLDER_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
ALBUM_RE = re.compile(r"^[^\"\\\n\r\x00]{1,60}$")
TZ_RE = re.compile(r"^[A-Za-z_]+(/[A-Za-z0-9_+-]+)*$")
ADMIN_RE = re.compile(r"^[A-Za-z0-9_.-]{2,64}$")
ENV_KEY_RE = re.compile(r"^([A-Z][A-Z0-9_]*)=")
YAML_KEY_RE = re.compile(r"^(\s+)([a-z_]+):(\s|$)")

# Passwords the installer generates itself (never shown, never printed).
GENERATED_SECRETS = ("IMMICH_DB_PASSWORD", "NEXTCLOUD_DB_PASSWORD", "NEXTCLOUD_REDIS_PASSWORD")
YAML_STRING_KEYS = {"album_name", "nextcloud_folder"}


class Stop(Exception):
    """The installer stops here. The message says what to fix."""


# ---------------------------------------------------------------- pure helpers
# These functions have no side effects and are covered by tests/test_install.py.

def parse_env(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        match = ENV_KEY_RE.match(line)
        if match:
            values[match.group(1)] = line.split("=", 1)[1]
    return values


def update_env_text(text: str, updates: dict[str, str], remove: tuple[str, ...] = ()) -> str:
    """Replace, append or drop KEY=value lines and keep everything else (comments, order)."""
    out: list[str] = []
    seen: set[str] = set()
    for line in text.splitlines():
        match = ENV_KEY_RE.match(line)
        key = match.group(1) if match else None
        if key in remove:
            continue
        if key in updates:
            seen.add(key)
            out.append(f"{key}={updates[key]}")
        else:
            out.append(line)
    extra = [k for k in updates if k not in seen]
    if extra:
        out += ["", "# added by scripts/install.py"] + [f"{k}={updates[k]}" for k in extra]
    return "\n".join(out) + "\n"


def render_config(text: str, values: dict[str, str]) -> str:
    """Set scalar values in config.yaml, keeping comments and every other key."""
    out: list[str] = []
    for line in text.splitlines():
        match = YAML_KEY_RE.match(line)
        if match and match.group(2) in values:
            key, value = match.group(2), values[match.group(2)]
            if key in YAML_STRING_KEYS:
                value = f'"{value}"'
            out.append(f"{match.group(1)}{key}: {value}")
        else:
            out.append(line)
    return "\n".join(out) + "\n"


def ok_if(condition: object, message: str) -> Optional[str]:
    return None if condition else message


def check_host(value: str) -> Optional[str]:
    return ok_if(HOST_RE.match(value), "use a name like myserver.tailnet-name.ts.net or cloud.example.com")


def check_agent(value: str) -> Optional[str]:
    return ok_if(AGENT_RE.match(value) and value != "root", "use a lowercase Linux user name, e.g. agent")


def check_folder(value: str) -> Optional[str]:
    return ok_if(FOLDER_RE.match(value) and value not in (".", ".."),
                 "use letters, digits, dot, underscore or dash only (no spaces)")


def check_album(value: str) -> Optional[str]:
    return ok_if(ALBUM_RE.match(value), "1-60 characters, no quotes or backslashes")


def check_timezone(value: str) -> Optional[str]:
    return ok_if(TZ_RE.match(value), "use an IANA time zone such as Europe/Stockholm or Etc/UTC")


def check_absolute(value: str) -> Optional[str]:
    return ok_if(value.startswith("/") and " " not in value, "use an absolute path without spaces")


def check_owner(value: str) -> Optional[str]:
    try:
        pwd.getpwnam(value)
    except KeyError:
        return f"there is no Linux user called {value!r}"
    return None


@dataclass
class Answers:
    access: str = "tailscale"          # "tailscale" or "caddy"
    host: str = "myserver.tailnet-name.ts.net"  # Tailscale name, or the Nextcloud domain with caddy
    immich_host: str = ""              # caddy only
    owner: str = "owner"               # your normal Linux login
    agent: str = "agent"               # Linux user for the AI agent
    folder: str = "agent-folder"       # the one folder the agent may use
    album: str = "Nextcloud"           # Immich album that is synced
    timezone: str = "Etc/UTC"
    backup_dir: str = "/mnt/backup/cloudbuster"
    data_dir: str = "/srv/cloudbuster"
    agent_nc_user: str = "ai-agent"
    sync_user: str = "nextcloud-sync"
    admin_user: str = ""               # only for a fresh Nextcloud install
    admin_password: str = ""

    @property
    def agent_sync_dir(self) -> str:
        return f"/home/{self.agent}/{self.folder}"

    @property
    def nc_folder(self) -> str:
        return "/" + self.folder

    @property
    def nc_url(self) -> str:
        return f"https://{self.host}"

    @property
    def immich_url(self) -> str:
        return f"https://{self.host}:8443" if self.access == "tailscale" else f"https://{self.immich_host}"


def check_answers(a: Answers) -> list[str]:
    errors: list[str] = []
    if a.access not in ("tailscale", "caddy"):
        errors.append("access must be tailscale or caddy")
    if not HOST_RE.match(a.host):
        errors.append(f"{a.host!r} is not a valid host name")
    if a.access == "caddy":
        if not HOST_RE.match(a.immich_host):
            errors.append(f"{a.immich_host!r} is not a valid domain for Immich")
        elif a.immich_host == a.host:
            errors.append("Nextcloud and Immich need two different domains")
    if not AGENT_RE.match(a.agent) or a.agent == "root":
        errors.append(f"{a.agent!r} is not a valid agent user name")
    if a.agent == a.owner:
        errors.append("the agent user must be a different user from your own login")
    for problem in (check_folder(a.folder), check_album(a.album), check_timezone(a.timezone)):
        if problem:
            errors.append(problem)
    if check_absolute(a.backup_dir):
        errors.append("the backup directory must be an absolute path")
    if a.data_dir == a.backup_dir or a.backup_dir.startswith(a.data_dir.rstrip("/") + "/"):
        errors.append("the backup directory must be on another disk, not inside the data directory")
    if a.admin_password:
        if not ADMIN_RE.match(a.admin_user):
            errors.append("the Nextcloud admin name has invalid characters")
        if len(a.admin_password) < 12:
            errors.append("the Nextcloud admin password must be at least 12 characters")
    return errors


def env_updates(a: Answers, current: dict[str, str]) -> dict[str, str]:
    """Values written to .env. Existing secrets are kept; placeholders get new random ones."""
    updates = {
        "CLOUDBUSTER_CONFIG_DIR": str(CONFIG_DIR),
        "CLOUDBUSTER_DATA_DIR": a.data_dir,
        "CLOUDBUSTER_BACKUP_DIR": a.backup_dir,
        "CLOUDBUSTER_EXTRA_COMPOSE": "compose/caddy.yml" if a.access == "caddy" else "",
        "TZ": a.timezone,
        "NEXTCLOUD_PUBLIC_HOST": a.host,
        "NEXTCLOUD_OVERWRITE_PROTOCOL": "https",
        "NEXTCLOUD_DOMAIN": a.host if a.access == "caddy" else "",
        "IMMICH_DOMAIN": a.immich_host if a.access == "caddy" else "",
        "OWNER_USER": a.owner,
        "AGENT_USER": a.agent,
        "AGENT_SYNC_DIR": a.agent_sync_dir,
        "AGENT_NEXTCLOUD_USER": a.agent_nc_user,
        "AGENT_REMOTE_FOLDER": a.nc_folder,
        "SYNC_NEXTCLOUD_USER": a.sync_user,
    }
    for key in GENERATED_SECRETS:
        if not current.get(key) or current[key].startswith("change-me"):
            updates[key] = secrets.token_hex(24)
    if a.admin_password:
        updates["NEXTCLOUD_ADMIN_USER"] = a.admin_user
        updates["NEXTCLOUD_ADMIN_PASSWORD"] = a.admin_password
    return updates


def http_ok(url: str) -> bool:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # talk to 127.0.0.1 directly
    try:
        with opener.open(url, timeout=5) as response:
            return response.status == 200
    except Exception:
        return False


# ------------------------------------------------------------- I/O and runner

@dataclass
class Context:
    dry: bool
    yes: bool
    answers: Answers = None  # type: ignore[assignment]

    def say(self, message: str) -> None:
        print(f"   {message}")


def display(cmd: list[str]) -> str:
    text = " ".join(shlex.quote(part) for part in cmd)
    return text.replace(str(REPO) + "/", "")


def run(ctx: Context, cmd: list[str], check: bool = True) -> None:
    print(f"   $ {display(cmd)}")
    if ctx.dry:
        return
    result = subprocess.run(cmd, cwd=REPO)
    if check and result.returncode != 0:
        raise Stop(f"the command failed (exit {result.returncode}): {display(cmd)}")


def manual(ctx: Context, lines: list[str], prompt: str = "Press Enter when this is done (Ctrl+C stops)") -> str:
    print()
    for line in lines:
        print(f"   [ ] {line}")
    if ctx.dry:
        return ""
    return input(f"   {prompt}: ").strip().lower()


def ask_yes(ctx: Context, question: str, default_yes: bool = False) -> bool:
    if ctx.yes:
        return default_yes  # unattended runs never start the sync on their own
    hint = "[Y/n]" if default_yes else "[y/N]"
    answer = input(f"   {question} {hint}: ").strip().lower()
    if not answer:
        return default_yes
    return answer in ("y", "yes")


def wait_until(description: str, check: Callable[[], bool], timeout_s: int = 900, every_s: int = 10) -> None:
    print(f"   waiting for {description} ...", flush=True)
    started = time.monotonic()
    while time.monotonic() - started < timeout_s:
        if check():
            print("   ready")
            return
        time.sleep(every_s)
    raise Stop(f"timed out waiting for {description}; look at the logs with: "
               "sudo ./bin/cb-compose logs --tail 100")


def write_private(ctx: Context, path: Path, text: str) -> None:
    if ctx.dry:
        ctx.say(f"would write {path} (contents are not shown)")
        return
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    ctx.say(f"wrote {path} (mode 600, contents not shown)")


def current_env() -> dict[str, str]:
    return parse_env((ENV_FILE if ENV_FILE.exists() else ENV_TEMPLATE).read_text(encoding="utf-8"))


def update_env(ctx: Context, updates: dict[str, str], remove: tuple[str, ...] = ()) -> None:
    base = (ENV_FILE if ENV_FILE.exists() else ENV_TEMPLATE).read_text(encoding="utf-8")
    write_private(ctx, ENV_FILE, update_env_text(base, updates, remove))


def port(name: str, default: str) -> int:
    return int(current_env().get(name) or default)


def nextcloud_installed() -> bool:
    result = subprocess.run(
        [str(REPO / "bin" / "cb-compose"), "exec", "-T", "-u", "www-data", "nextcloud",
         "php", "occ", "status", "--output=json"],
        cwd=REPO, capture_output=True, text=True)
    if result.returncode != 0:
        return False
    try:
        return bool(json.loads(result.stdout[result.stdout.index("{"):]).get("installed"))
    except (ValueError, json.JSONDecodeError):
        return False


# -------------------------------------------------------------- questions

def ask(ctx: Context, label: str, default: str, check: Callable[[str], Optional[str]]) -> str:
    while True:
        if ctx.yes:
            value = default
        else:
            hint = f" [{default}]" if default else ""
            value = input(f"{label}{hint}: ").strip() or default
        error = "a value is required" if not value else check(value)
        if not error:
            return value
        if ctx.yes:
            raise Stop(f"{label}: {error}. Run without --yes to answer it, or fix the config.")
        print(f"   {error}")


def ask_admin_password(ctx: Context) -> tuple[str, str]:
    user = ask(ctx, "Nextcloud admin account name", "admin",
               lambda v: ok_if(ADMIN_RE.match(v), "use 2-64 letters, digits, dot, underscore or dash"))
    if ctx.yes:
        password = os.environ.get("CLOUDBUSTER_ADMIN_PASSWORD", "")
        if len(password) < 12:
            raise Stop("for an unattended install set CLOUDBUSTER_ADMIN_PASSWORD (at least 12 characters)")
        return user, password
    while True:
        password = getpass.getpass("   Nextcloud admin password (at least 12 characters, hidden): ")
        if len(password) < 12:
            print("   too short")
            continue
        if getpass.getpass("   Repeat it (hidden): ") != password:
            print("   the two entries differ")
            continue
        return user, password


def local_timezone() -> str:
    try:
        return Path("/etc/timezone").read_text(encoding="utf-8").strip() or "Etc/UTC"
    except OSError:
        return "Etc/UTC"


def gather(ctx: Context) -> Answers:
    cur = current_env()
    cfg_text = (CONFIG_YAML if CONFIG_YAML.exists() else YAML_TEMPLATE).read_text(encoding="utf-8")
    album_match = re.search(r'album_name:\s*"([^"]+)"', cfg_text)

    print("\nA few questions. Press Enter to accept the value in brackets.\n")
    access = ask(ctx, "Reach it from outside your home: tailscale or caddy",
                 "caddy" if cur.get("CLOUDBUSTER_EXTRA_COMPOSE") else "tailscale",
                 lambda v: ok_if(v in ("tailscale", "caddy"), "type tailscale or caddy"))
    host_default = cur.get("NEXTCLOUD_DOMAIN" if access == "caddy" else "NEXTCLOUD_PUBLIC_HOST", "")
    if access == "tailscale":
        host = ask(ctx, "Your Tailscale machine name (e.g. myserver.tailnet-name.ts.net)", host_default, check_host)
        immich_host = ""
    else:
        host = ask(ctx, "Domain for Nextcloud (e.g. cloud.example.com)", host_default, check_host)
        immich_host = ask(ctx, "Domain for Immich (e.g. photos.example.com)", cur.get("IMMICH_DOMAIN", ""), check_host)

    owner = ask(ctx, "Your normal Linux login name", cur.get("OWNER_USER") or os.environ.get("SUDO_USER", ""),
                check_owner)
    agent = ask(ctx, "Linux user for the AI agent (new or existing)", cur.get("AGENT_USER") or "agent", check_agent)
    folder_default = (cur.get("AGENT_REMOTE_FOLDER") or "").strip("/") or "agent-folder"
    folder = ask(ctx, "Name of the one folder the agent may use", folder_default, check_folder)
    album = ask(ctx, "Name of the Immich album that is synced", album_match.group(1) if album_match else "Nextcloud",
                check_album)
    timezone = ask(ctx, "Time zone", cur.get("TZ") or local_timezone(), check_timezone)
    backup_dir = ask(ctx, "Backup directory (on another disk)", cur.get("CLOUDBUSTER_BACKUP_DIR") or
                     "/mnt/backup/cloudbuster", check_absolute)

    admin_user, admin_password = "", ""
    if cur.get("NEXTCLOUD_ADMIN_PASSWORD", "").startswith("change-me"):
        admin_user, admin_password = ask_admin_password(ctx)

    answers = Answers(
        access=access, host=host, immich_host=immich_host, owner=owner, agent=agent, folder=folder,
        album=album, timezone=timezone, backup_dir=backup_dir,
        data_dir=cur.get("CLOUDBUSTER_DATA_DIR") or "/srv/cloudbuster",
        agent_nc_user=cur.get("AGENT_NEXTCLOUD_USER") or "ai-agent",
        sync_user=cur.get("SYNC_NEXTCLOUD_USER") or "nextcloud-sync",
        admin_user=admin_user, admin_password=admin_password,
    )
    errors = check_answers(answers)
    if errors:
        raise Stop("; ".join(errors))

    print("\nThis is what will be set up (no passwords are shown):")
    rows = [
        ("Reach it via", f"{access}: {answers.host}" + (f", {answers.immich_host}" if immich_host else "")),
        ("Your login", answers.owner),
        ("Agent Linux user", f"{answers.agent} (own home, no sudo, no docker group)"),
        ("The one folder", f"{answers.nc_folder} in Nextcloud  <->  {answers.agent_sync_dir}"),
        ("Immich album", answers.album),
        ("Time zone", answers.timezone),
        ("Backups", answers.backup_dir),
        ("Data directory", answers.data_dir),
    ]
    for label, value in rows:
        print(f"   {label:<18} {value}")
    if not ctx.yes and not ask_yes(ctx, "Are these answers right?", default_yes=True):
        raise Stop("answers rejected; run the installer again")
    return answers


# ----------------------------------------------------------------- preflight

def preflight(ctx: Context) -> None:
    problems: list[str] = []
    warnings: list[str] = []
    if os.geteuid() != 0:
        problems.append("run it with sudo: sudo ./scripts/install.py")
    tools = {
        "docker": "install Docker Engine: https://docs.docker.com/engine/install/ubuntu/",
        "nft": "sudo apt install nftables",
        "nextcloudcmd": "sudo apt install nextcloud-desktop-cmd",
        "rsync": "sudo apt install rsync",
        "openssl": "sudo apt install openssl",
    }
    for tool, hint in tools.items():
        if not shutil.which(tool):
            problems.append(f"{tool} is missing: {hint}")
    if shutil.which("docker"):
        if subprocess.run(["docker", "compose", "version"], capture_output=True).returncode != 0:
            problems.append("the Docker Compose plugin is missing (docker compose version fails)")
        if subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
            problems.append("the Docker daemon is not running: sudo systemctl start docker")
    owner = os.environ.get("SUDO_USER", "")
    if not owner:
        warnings.append("run it with sudo from your own login, so the installer knows your user name")
    else:
        groups = subprocess.run(["id", "-nG", owner], capture_output=True, text=True).stdout.split()
        if "docker" in groups:
            warnings.append(f"{owner} is in the docker group, which is root in practice. Remove it with: "
                            f"sudo gpasswd -d {owner} docker  (then log out and in)")
    if problems:
        raise Stop("\n  - " + "\n  - ".join(problems))
    for warning in warnings:
        print(f"WARNING: {warning}")
    if warnings and not ctx.yes and not ask_yes(ctx, "Continue anyway?"):
        raise Stop("stopped; fix the warning and run again")


# --------------------------------------------------------------------- steps

def step_config(ctx: Context) -> None:
    a = ctx.answers
    update_env(ctx, env_updates(a, current_env()))
    cfg_base = (CONFIG_YAML if CONFIG_YAML.exists() else YAML_TEMPLATE).read_text(encoding="utf-8")
    write_private(ctx, CONFIG_YAML, render_config(cfg_base, {"album_name": a.album, "nextcloud_folder": a.nc_folder}))


def step_host(ctx: Context) -> None:
    run(ctx, [str(REPO / "scripts" / "prepare-host.sh")])


def step_start(ctx: Context) -> None:
    run(ctx, [str(REPO / "bin" / "cb-compose"), "pull"])
    run(ctx, [str(REPO / "bin" / "cb-compose"), "up", "-d"])


def step_access(ctx: Context) -> None:
    a = ctx.answers
    if a.access == "tailscale":
        if not ctx.dry and not shutil.which("tailscale"):
            raise Stop("tailscale is not installed: https://tailscale.com/download, then run: sudo tailscale up")
        run(ctx, ["tailscale", "serve", "--bg", "--https=443", f"http://127.0.0.1:{port('NEXTCLOUD_PORT', '8080')}"])
        run(ctx, ["tailscale", "serve", "--bg", "--https=8443", f"http://127.0.0.1:{port('IMMICH_PORT', '2283')}"])
        run(ctx, ["tailscale", "serve", "status"], check=False)
        manual(ctx, [
            "In the Tailscale admin console, MagicDNS and HTTPS certificates must be enabled.",
            "On your phone, install Tailscale and log in with the same account.",
        ])
    else:
        manual(ctx, [
            f"Point DNS for {a.host} and {a.immich_host} at this home's public IP.",
            "Forward ports 80 and 443 from your router to this machine.",
            "Caddy fetches its own certificates once DNS and the ports are in place.",
        ])


def step_nextcloud_wait(ctx: Context) -> None:
    if ctx.dry:
        ctx.say("would wait until Nextcloud reports installed, then remove the one-time admin settings from .env")
        return
    wait_until("Nextcloud to finish installing", nextcloud_installed)
    update_env(ctx, {}, remove=("NEXTCLOUD_ADMIN_USER", "NEXTCLOUD_ADMIN_PASSWORD"))
    ctx.say(f"the one-time admin settings are removed from .env; open {ctx.answers.nc_url} and log in to check")


def step_accounts(ctx: Context) -> None:
    # The agent user must exist before its Nextcloud accounts can be set up.
    run(ctx, [str(REPO / "scripts" / "create-agent-user.sh")])
    run(ctx, [str(REPO / "scripts" / "nextcloud-setup.sh")])


def step_folder(ctx: Context) -> None:
    a = ctx.answers
    manual(ctx, [
        f"Open {a.nc_url} and log in with your normal (non-admin) account.",
        f"Create a top-level folder named exactly: {a.folder}",
        f"Open its Sharing menu and share it with '{a.agent_nc_user}': allow editing, untick 'Allow resharing'.",
        f"Share it the same way with '{a.sync_user}'.",
    ])


def step_immich(ctx: Context) -> None:
    a = ctx.answers
    if ctx.dry:
        ctx.say("would wait for Immich, show the checklist, and store the sync user's API key in .env")
        return
    if current_env().get("SYNC_IMMICH_API_KEY"):
        ctx.say("the sync API key is already in .env, skipping")
        return
    ping = f"http://127.0.0.1:{port('IMMICH_PORT', '2283')}/api/server/ping"
    wait_until("Immich to start", lambda: http_ok(ping), timeout_s=900)
    if ctx.yes:
        raise Stop("the Immich step needs your API key: run without --yes, e.g. sudo ./scripts/install.py --from immich")
    manual(ctx, [
        f"Open {a.immich_url} and create the first account (it is the Immich administrator).",
        f"Administration -> Users -> create the user '{a.sync_user}' with a long random password.",
        f"In a private window, log in as '{a.sync_user}'. Account settings -> API keys -> New API key.",
        "Tick ONLY: album.read, asset.read, asset.download, asset.upload, albumAsset.create, "
        "albumAsset.delete, user.read. Keep the key for the next question.",
        f"As '{a.sync_user}', create an album named exactly: {a.album}",
        "Share that album with YOUR account as Editor. Do not turn on partner sharing.",
        "Log out of the sync user.",
    ])
    key = getpass.getpass("   Paste the sync user's API key (hidden): ").strip()
    if len(key) < 20 or " " in key:
        raise Stop("that does not look like an Immich API key; run the step again with: --from immich")
    update_env(ctx, {"SYNC_IMMICH_API_KEY": key})


def step_agent(ctx: Context) -> None:
    # Second pass: now the Nextcloud password file exists, so the folder sync timer starts.
    run(ctx, [str(REPO / "scripts" / "create-agent-user.sh")])
    run(ctx, [str(REPO / "scripts" / "agent-firewall.sh")])


def step_claude(ctx: Context) -> None:
    a = ctx.answers
    answer = manual(ctx, [
        f"In a new terminal, switch to the agent user:  sudo -iu {a.agent}",
        "Install Claude Code (official installer):  curl -fsSL https://claude.ai/install.sh | bash",
        f"Start it once from the folder:  cd ~/{a.folder} && claude   and log in with the agent's own account.",
        "Leave with 'exit' and return to your own login.",
    ], prompt="Press Enter when this is done, or type s to skip for now")
    if answer == "s":
        ctx.say(f"skipped. Do it later with: sudo -iu {a.agent}")


def step_sync(ctx: Context) -> None:
    compose = str(REPO / "bin" / "cb-compose")
    run(ctx, [compose, "--profile", "sync", "build", "sync"])
    run(ctx, [compose, "--profile", "sync", "run", "--rm", "sync",
              "--config", "/config/config.yaml", "--once", "--dry-run"])
    if ctx.dry:
        return
    if not ask_yes(ctx, "Did the dry run list only what you expect (e.g. your test photo)?"):
        raise Stop("the sync is not started. Fix the album or folder, then run: --from sync")
    run(ctx, [compose, "--profile", "sync", "up", "-d", "sync"])


def step_verify(ctx: Context) -> None:
    try:
        run(ctx, [str(REPO / "scripts" / "verify-isolation.sh")])
    except Stop:
        raise Stop("the isolation test failed. Do NOT import private photos yet. Fix each FAIL line, "
                   "then run: sudo ./scripts/install.py --from verify")


def step_finish(ctx: Context) -> None:
    a = ctx.answers
    print(f"""
   Installed. Next:
   - Nextcloud:  {a.nc_url}
   - Immich:     {a.immich_url}
   - Phone:      docs/clients.md (Immich app, automatic backup, Tailscale on the phone)
   - Migration:  docs/migration.md (Google Photos, Drive, local folders), only now
   - Backups:    add the cron line from docs/operations.md with: sudo crontab -e
   - Updates:    docs/operations.md
   The private settings are in {CONFIG_DIR} and are not part of the repository.
""")


STEPS: list[tuple[str, str, Callable[[Context], None]]] = [
    ("config", "Write the private configuration", step_config),
    ("host", "Create the data directories (root only)", step_host),
    ("start", "Start Immich and Nextcloud", step_start),
    ("access", "Make the web interfaces reachable", step_access),
    ("nextcloud", "Wait for Nextcloud to finish installing", step_nextcloud_wait),
    ("accounts", "Create the agent user and its Nextcloud accounts", step_accounts),
    ("folder", "Share the one folder (web interface)", step_folder),
    ("immich", "Create the sync user and the album in Immich", step_immich),
    ("agent", "Start the folder sync and the agent's network rules", step_agent),
    ("claude", "Install Claude Code for the agent user", step_claude),
    ("sync", "Start the album sync (dry run first)", step_sync),
    ("verify", "Verify the isolation", step_verify),
    ("finish", "Summary", step_finish),
]


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Guided installation of cloudbuster.")
    parser.add_argument("--dry-run", action="store_true", help="show every command, change nothing")
    parser.add_argument("--yes", action="store_true",
                        help="do not ask per step; use defaults and CLOUDBUSTER_ADMIN_PASSWORD")
    parser.add_argument("--from", dest="start", metavar="STEP", help="resume at this step (see --list)")
    parser.add_argument("--list", action="store_true", help="list the steps and exit")
    args = parser.parse_args(argv)

    if args.list:
        for step_id, title, _ in STEPS:
            print(f"{step_id:<10} {title}")
        return 0
    ids = [step_id for step_id, _, _ in STEPS]
    if args.start and args.start not in ids:
        parser.error(f"unknown step {args.start!r}; choose one of: {', '.join(ids)}")

    ctx = Context(dry=args.dry_run, yes=args.yes)
    try:
        preflight(ctx)
        ctx.answers = gather(ctx)
        first = ids.index(args.start) if args.start else 0
        for step_id, title, func in STEPS[first:]:
            print(f"\n== {title}  [{step_id}]")
            if not ctx.yes:
                choice = input("   Run this step? [Y/n/q]: ").strip().lower()
                if choice in ("q", "quit"):
                    raise Stop(f"stopped before '{step_id}'. Resume with: sudo ./scripts/install.py --from {step_id}")
                if choice in ("n", "no"):
                    print("   skipped")
                    continue
            func(ctx)
        print("\nDone.")
        return 0
    except Stop as stop:
        print(f"\nSTOPPED: {stop}")
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted; nothing else was started")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
