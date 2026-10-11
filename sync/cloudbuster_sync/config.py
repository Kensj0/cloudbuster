"""Configuration: behaviour from config.yaml, secrets from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import yaml

DEFAULT_MEDIA_EXTENSIONS = [
    "jpg", "jpeg", "png", "heic", "heif", "webp", "gif", "tif", "tiff", "avif", "jxl", "bmp",
    "dng", "cr2", "cr3", "nef", "arw", "orf", "raf", "rw2",
    "mp4", "mov", "m4v", "3gp", "mkv", "webm", "avi", "mts", "m2ts",
]


@dataclass
class Config:
    immich_url: str
    immich_api_key: str
    nextcloud_url: str
    nextcloud_user: str
    nextcloud_app_password: str
    album_name: str = "Nextcloud"
    nextcloud_folder: str = "/agent-folder"
    interval_seconds: int = 60
    on_album_remove: str = "trash_file"
    on_nextcloud_delete: str = "remove_from_album"
    max_deletions_per_run: int = 10
    state_path: str = "/state/sync.db"
    media_extensions: list[str] = field(default_factory=lambda: list(DEFAULT_MEDIA_EXTENSIONS))
    log_level: str = "INFO"

    def is_media(self, path: str) -> bool:
        name = path.rsplit("/", 1)[-1]
        if name.startswith("."):
            return False
        ext = name.rpartition(".")[2].lower() if "." in name else ""
        return ext in {e.lower() for e in self.media_extensions}


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"missing required environment variable {name}")
    return value


def load(path: str | None) -> Config:
    data: dict = {}
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            data = (yaml.safe_load(fh) or {}).get("sync", {}) or {}

    cfg = Config(
        immich_url=os.environ.get("IMMICH_URL", "http://immich-server:2283"),
        immich_api_key=_require("SYNC_IMMICH_API_KEY"),
        nextcloud_url=os.environ.get("NEXTCLOUD_URL", "http://nextcloud"),
        nextcloud_user=os.environ.get("SYNC_NEXTCLOUD_USER", "nextcloud-sync"),
        nextcloud_app_password=_require("SYNC_NEXTCLOUD_APP_PASSWORD"),
    )
    for key in ("album_name", "nextcloud_folder", "interval_seconds", "on_album_remove",
                "on_nextcloud_delete", "max_deletions_per_run", "state_path", "media_extensions", "log_level"):
        if key in data and data[key] is not None:
            setattr(cfg, key, data[key])

    if cfg.on_album_remove not in ("trash_file", "ignore"):
        raise SystemExit("sync.on_album_remove must be 'trash_file' or 'ignore'")
    if cfg.on_nextcloud_delete not in ("remove_from_album", "ignore"):
        raise SystemExit("sync.on_nextcloud_delete must be 'remove_from_album' or 'ignore'")
    if int(cfg.interval_seconds) < 10:
        raise SystemExit("sync.interval_seconds must be at least 10")
    cfg.interval_seconds = int(cfg.interval_seconds)
    cfg.max_deletions_per_run = int(cfg.max_deletions_per_run)
    return cfg
