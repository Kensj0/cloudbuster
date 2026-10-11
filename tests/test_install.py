"""Tests for the pure parts of scripts/install.py. No system changes, no network."""

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("install", ROOT / "scripts" / "install.py")
install = importlib.util.module_from_spec(spec)
# Register before executing: dataclasses look the module up in sys.modules.
sys.modules["install"] = install
spec.loader.exec_module(install)

Answers = install.Answers


def test_update_env_replaces_keeps_comments_and_appends():
    text = "# keep me\nA=1\nB=2\n"
    out = install.update_env_text(text, {"A": "x", "C": "3"})
    lines = out.splitlines()
    assert lines[0] == "# keep me"
    assert "A=x" in lines and "B=2" in lines and "C=3" in lines


def test_update_env_removes_keys():
    out = install.update_env_text("A=1\nB=2\n", {}, remove=("A",))
    assert "A=" not in out and "B=2" in out


def test_parse_env_ignores_comments_and_blank_lines():
    values = install.parse_env("# x\n\nKEY=value=with=equals\nlower=no\n")
    assert values == {"KEY": "value=with=equals"}


def test_render_config_sets_strings_and_keeps_policies():
    text = 'sync:\n  album_name: "Nextcloud"\n  on_album_remove: trash_file\n  # comment\n'
    out = install.render_config(text, {"album_name": "Mitt album", "nextcloud_folder": "/agent-folder"})
    assert 'album_name: "Mitt album"' in out
    assert "on_album_remove: trash_file" in out
    assert "# comment" in out


def test_default_answers_are_valid():
    assert install.check_answers(Answers()) == []


@pytest.mark.parametrize("changes, fragment", [
    ({"agent": "root"}, "agent user name"),
    ({"agent": "owner", "owner": "owner"}, "different user"),
    ({"folder": "my folder"}, "letters, digits"),
    ({"album": 'bad"name'}, "quotes"),
    ({"timezone": "Europe Stockholm"}, "time zone"),
    ({"backup_dir": "relative"}, "absolute"),
    ({"backup_dir": "/srv/cloudbuster/backup"}, "another disk"),
    ({"host": "not a host"}, "not a valid host"),
    ({"access": "caddy", "immich_host": "cloud.example.com", "host": "cloud.example.com"}, "different domains"),
    ({"admin_password": "short", "admin_user": "admin"}, "at least 12"),
])
def test_invalid_answers_are_rejected(changes, fragment):
    errors = install.check_answers(Answers(**changes))
    assert errors, changes
    assert any(fragment in e for e in errors), errors


def test_agent_sync_dir_is_inside_the_agent_home():
    a = Answers(agent="robot", folder="agent-folder")
    assert a.agent_sync_dir == "/home/robot/agent-folder"
    assert a.nc_folder == "/agent-folder"
    assert install.check_answers(a) == []


def test_tailscale_urls():
    a = Answers(access="tailscale", host="myserver.tailnet-name.ts.net")
    assert a.nc_url == "https://myserver.tailnet-name.ts.net"
    assert a.immich_url == "https://myserver.tailnet-name.ts.net:8443"


def test_caddy_urls_and_env():
    a = Answers(access="caddy", host="cloud.example.com", immich_host="photos.example.com")
    assert a.immich_url == "https://photos.example.com"
    u = install.env_updates(a, {})
    assert u["CLOUDBUSTER_EXTRA_COMPOSE"] == "compose/caddy.yml"
    assert u["NEXTCLOUD_DOMAIN"] == "cloud.example.com"
    assert u["IMMICH_DOMAIN"] == "photos.example.com"


def test_tailscale_env_clears_caddy_settings():
    u = install.env_updates(Answers(access="tailscale"), {})
    assert u["CLOUDBUSTER_EXTRA_COMPOSE"] == ""
    assert u["NEXTCLOUD_DOMAIN"] == ""


def test_generated_secrets_are_created_once_and_kept():
    current = {"IMMICH_DB_PASSWORD": "existing-value-kept", "NEXTCLOUD_DB_PASSWORD": "change-me",
               "NEXTCLOUD_REDIS_PASSWORD": ""}
    u = install.env_updates(Answers(), current)
    assert "IMMICH_DB_PASSWORD" not in u
    assert len(u["NEXTCLOUD_DB_PASSWORD"]) == 48 and "change-me" not in u["NEXTCLOUD_DB_PASSWORD"]
    assert len(u["NEXTCLOUD_REDIS_PASSWORD"]) == 48


def test_admin_credentials_only_when_given():
    assert "NEXTCLOUD_ADMIN_PASSWORD" not in install.env_updates(Answers(), {})
    u = install.env_updates(Answers(admin_user="admin", admin_password="x" * 12), {})
    assert u["NEXTCLOUD_ADMIN_USER"] == "admin" and u["NEXTCLOUD_ADMIN_PASSWORD"] == "x" * 12


def test_every_key_the_installer_writes_exists_in_the_template():
    """If the template and the installer drift apart, the setting would silently not be used."""
    template = install.parse_env((ROOT / ".env.example").read_text(encoding="utf-8"))
    written = install.env_updates(Answers(access="caddy", host="cloud.example.com",
                                          immich_host="photos.example.com",
                                          admin_user="admin", admin_password="x" * 12), {})
    missing = set(written) - set(template)
    assert not missing, f"keys missing from .env.example: {sorted(missing)}"


def test_template_defaults_are_consistent_with_installer_defaults():
    template = install.parse_env((ROOT / ".env.example").read_text(encoding="utf-8"))
    assert template["AGENT_REMOTE_FOLDER"] == "/" + Answers().folder
    assert template["AGENT_SYNC_DIR"] == f"/home/{template['AGENT_USER']}/{Answers().folder}"
    assert template["SYNC_NEXTCLOUD_USER"] == Answers().sync_user
    assert template["AGENT_NEXTCLOUD_USER"] == Answers().agent_nc_user


def test_config_template_folder_matches_installer_default():
    text = (ROOT / "config.example.yaml").read_text(encoding="utf-8")
    assert f'nextcloud_folder: "{Answers().nc_folder}"' in text
