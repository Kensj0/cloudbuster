"""Minimal Nextcloud WebDAV client, confined to one folder of one account."""

from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

from .http import session

NS = {"d": "DAV:", "oc": "http://owncloud.org/ns"}

PROPFIND_BODY = """<?xml version="1.0"?>
<d:propfind xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">
  <d:prop>
    <d:getetag/><d:getlastmodified/><d:getcontentlength/><d:resourcetype/>
    <oc:checksums/>
  </d:prop>
</d:propfind>"""


class NextcloudError(RuntimeError):
    pass


@dataclass(frozen=True)
class RemoteEntry:
    path: str                 # relative to the synced folder
    is_dir: bool
    etag: str
    size: int
    modified: datetime | None
    sha1: str | None          # from oc:checksums when the uploader provided one


class Nextcloud:
    def __init__(self, base_url: str, user: str, app_password: str, folder: str, timeout: float = 120.0):
        self.http = session(timeout)
        self.http.auth = (user, app_password)
        self.http.headers["OCS-APIRequest"] = "true"
        folder = "/" + folder.strip("/")
        self.root_url = f"{base_url.rstrip('/')}/remote.php/dav/files/{quote(user)}{quote(folder)}"
        self.root_path = unquote(urlparse(self.root_url).path).rstrip("/")

    def _url(self, rel: str) -> str:
        rel = rel.strip("/")
        if ".." in rel.split("/"):
            raise NextcloudError("refusing a path outside the synced folder")
        return f"{self.root_url}/{quote(rel)}" if rel else self.root_url

    def _check(self, resp, what: str):
        if resp.status_code >= 400:
            raise NextcloudError(f"{what} failed: HTTP {resp.status_code}")
        return resp

    def folder_exists(self) -> bool:
        resp = self.http.request("PROPFIND", self.root_url, headers={"Depth": "0"}, data=PROPFIND_BODY)
        if resp.status_code == 404:
            return False
        self._check(resp, "check folder")
        return True

    def list_files(self) -> list[RemoteEntry]:
        """All files below the folder (recursively, one PROPFIND per directory)."""
        files: list[RemoteEntry] = []
        pending = [""]
        while pending:
            rel_dir = pending.pop()
            for entry in self._propfind(rel_dir):
                if entry.path == rel_dir:
                    continue
                if entry.is_dir:
                    pending.append(entry.path)
                else:
                    files.append(entry)
        return files

    def _propfind(self, rel_dir: str) -> list[RemoteEntry]:
        resp = self.http.request(
            "PROPFIND", self._url(rel_dir) + "/", headers={"Depth": "1"}, data=PROPFIND_BODY)
        self._check(resp, "list folder")
        out = []
        for r in ET.fromstring(resp.content).findall("d:response", NS):
            href = unquote(urlparse(r.findtext("d:href", "", NS)).path).rstrip("/")
            if not href.startswith(self.root_path):
                continue
            rel = href[len(self.root_path):].strip("/")
            prop = r.find("d:propstat/d:prop", NS)
            if prop is None:
                continue
            is_dir = prop.find("d:resourcetype/d:collection", NS) is not None
            modified = prop.findtext("d:getlastmodified", None, NS)
            out.append(RemoteEntry(
                path=rel,
                is_dir=is_dir,
                etag=(prop.findtext("d:getetag", "", NS) or "").strip('"'),
                size=int(prop.findtext("d:getcontentlength", "0", NS) or 0),
                modified=parsedate_to_datetime(modified) if modified else None,
                sha1=_sha1_from_checksums(prop.findtext("oc:checksums/oc:checksum", "", NS)),
            ))
        return out

    def download(self, rel: str, dest: Path) -> str:
        h = hashlib.sha1()
        with self.http.get(self._url(rel), stream=True) as resp:
            self._check(resp, "download file")
            with open(dest, "wb") as out:
                for chunk in resp.iter_content(1024 * 1024):
                    h.update(chunk)
                    out.write(chunk)
        return h.hexdigest()

    def exists(self, rel: str) -> bool:
        resp = self.http.head(self._url(rel))
        if resp.status_code == 404:
            return False
        self._check(resp, "check file")
        return True

    def upload(self, rel: str, src: Path, sha1: str, mtime: datetime | None) -> str | None:
        """Create a new file. Never overwrites: fails if the name is taken. Returns the new etag."""
        headers = {"OC-Checksum": f"SHA1:{sha1}", "If-None-Match": "*"}
        if mtime:
            headers["X-OC-Mtime"] = str(int(mtime.timestamp()))
        with open(src, "rb") as fh:
            resp = self.http.put(self._url(rel), data=fh, headers=headers)
        if resp.status_code == 412:
            raise NextcloudError("file appeared while uploading; will retry next run")
        self._check(resp, "upload file")
        etag = resp.headers.get("OC-ETag") or resp.headers.get("ETag")
        return etag.strip('"') if etag else None

    def trash(self, rel: str) -> None:
        """DELETE over WebDAV moves the file to the Nextcloud trash bin (files_trashbin app)."""
        resp = self.http.delete(self._url(rel))
        if resp.status_code == 404:
            return
        self._check(resp, "delete file")


def _sha1_from_checksums(value: str) -> str | None:
    for part in (value or "").split():
        algo, _, digest = part.partition(":")
        if algo.upper() == "SHA1" and len(digest) == 40:
            return digest.lower()
    return None
