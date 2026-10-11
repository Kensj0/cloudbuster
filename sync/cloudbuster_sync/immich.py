"""Minimal Immich client: only the endpoints the album sync needs.

Endpoints used (Immich v3) and the API key permission each needs:

    GET    /api/albums?name=...          album.read
    POST   /api/search/metadata          asset.read      (always filtered by albumIds)
    GET    /api/assets/{id}/original     asset.download
    POST   /api/assets                   asset.upload
    PUT    /api/albums/{id}/assets       albumAsset.create
    DELETE /api/albums/{id}/assets       albumAsset.delete
    GET    /api/users/me                 user.read       (startup check only)
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .http import session
from .planner import Asset


class ImmichError(RuntimeError):
    pass


def b64_to_hex(checksum: str) -> str:
    return base64.b64decode(checksum).hex()


@dataclass
class UploadResult:
    asset_id: str
    duplicate: bool


class Immich:
    def __init__(self, base_url: str, api_key: str, timeout: float = 120.0):
        self.base = base_url.rstrip("/") + "/api"
        self.http = session(timeout)
        self.http.headers["x-api-key"] = api_key
        self.http.headers["Accept"] = "application/json"

    def _check(self, resp, what: str):
        if resp.status_code >= 400:
            # Never include headers or the request body in errors (they hold the key).
            raise ImmichError(f"{what} failed: HTTP {resp.status_code}")
        return resp

    def me(self) -> dict:
        return self._check(self.http.get(f"{self.base}/users/me"), "get current user").json()

    def find_album(self, name: str) -> dict | None:
        resp = self._check(self.http.get(f"{self.base}/albums", params={"name": name}), "list albums")
        albums = [a for a in resp.json() if a.get("albumName") == name]
        if len(albums) > 1:
            raise ImmichError(f"more than one album is called {name!r}; rename one of them")
        return albums[0] if albums else None

    def album_assets(self, album_id: str, page_size: int = 500) -> dict[str, Asset]:
        """All non-trashed assets in the album, keyed by asset id.

        Uses the v3.2+ filter/cursor shape. The filter is confined to the album,
        so the server never searches the rest of anyone's library.
        """
        result: dict[str, Asset] = {}
        cursor = None
        while True:
            body = {
                "filter": {"albumIds": {"any": [album_id]}, "trashedAt": {"eq": None}},
                "size": page_size,
            }
            if cursor:
                body["cursor"] = cursor
            resp = self._check(self.http.post(f"{self.base}/search/metadata", json=body), "list album assets")
            data = resp.json()["assets"]
            for item in data["items"]:
                if item.get("isTrashed"):
                    continue
                result[item["id"]] = Asset(
                    id=item["id"],
                    sha1=b64_to_hex(item["checksum"]),
                    filename=item.get("originalFileName") or f"{item['id']}",
                )
            cursor = data.get("nextCursor")
            if not cursor:
                return result

    def download(self, asset_id: str, dest: Path) -> str:
        """Stream the original file to `dest`; return its sha1 (hex)."""
        h = hashlib.sha1()
        with self.http.get(f"{self.base}/assets/{asset_id}/original", stream=True) as resp:
            self._check(resp, "download asset")
            with open(dest, "wb") as out:
                for chunk in resp.iter_content(1024 * 1024):
                    h.update(chunk)
                    out.write(chunk)
        return h.hexdigest()

    def upload(self, path: Path, filename: str, modified: datetime | None) -> UploadResult:
        when = (modified or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
        with open(path, "rb") as fh:
            resp = self.http.post(
                f"{self.base}/assets",
                data={"fileCreatedAt": when, "fileModifiedAt": when, "filename": filename},
                files={"assetData": (filename, fh, "application/octet-stream")},
            )
        self._check(resp, "upload asset")
        data = resp.json()
        return UploadResult(asset_id=data["id"], duplicate=data.get("status") == "duplicate")

    def add_to_album(self, album_id: str, asset_ids: list[str]) -> None:
        resp = self._check(
            self.http.put(f"{self.base}/albums/{album_id}/assets", json={"ids": asset_ids}), "add to album")
        for item in resp.json():
            if not item.get("success") and item.get("error") != "duplicate":
                raise ImmichError(f"add to album refused: {item.get('error')}")

    def remove_from_album(self, album_id: str, asset_ids: list[str]) -> None:
        resp = self._check(
            self.http.delete(f"{self.base}/albums/{album_id}/assets", json={"ids": asset_ids}),
            "remove from album")
        for item in resp.json():
            err = item.get("error")
            if not item.get("success") and err != "not_found":
                hint = " (the sync user must own the album, see docs/install.md)" if err == "no_permission" else ""
                raise ImmichError(f"remove from album refused: {err}{hint}")
