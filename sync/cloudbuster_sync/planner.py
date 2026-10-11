"""Pure sync planning: decide what to do, without touching the network.

The planner compares three views:

* the assets currently in the Immich album,
* the files currently in the Nextcloud folder,
* the state database (what was in sync after the previous run).

It returns a list of actions. Nothing here performs I/O, which keeps the
rules easy to review and to unit-test.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Kind(str, Enum):
    LINK = "link"                          # same content on both sides, start tracking it
    DOWNLOAD_TO_NEXTCLOUD = "download"     # album asset -> new file in Nextcloud
    UPLOAD_TO_IMMICH = "upload"            # new Nextcloud file -> Immich + album
    REPLACE_IN_IMMICH = "replace"          # Nextcloud file changed -> new asset, old one leaves the album
    TRASH_NEXTCLOUD_FILE = "trash_file"    # asset left the album -> file to Nextcloud trash
    REMOVE_FROM_ALBUM = "remove_from_album"  # file deleted in Nextcloud -> asset leaves the album
    UPDATE_ETAG = "update_etag"            # file touched but content identical
    FORGET = "forget"                      # stop tracking (gone on both sides, or policy says ignore)


DELETING_KINDS = {Kind.TRASH_NEXTCLOUD_FILE, Kind.REMOVE_FROM_ALBUM, Kind.REPLACE_IN_IMMICH}


@dataclass(frozen=True)
class Asset:
    id: str
    sha1: str               # lowercase hex
    filename: str


@dataclass(frozen=True)
class NcFile:
    path: str               # relative to the synced folder, e.g. "receipt.jpg" or "sub/a.png"
    etag: str
    sha1: str | None = None  # known content hash, if any


@dataclass(frozen=True)
class Record:
    asset_id: str
    nc_path: str
    sha1: str
    etag: str | None


@dataclass
class Action:
    kind: Kind
    asset: Asset | None = None
    file: NcFile | None = None
    record: Record | None = None
    target_path: str | None = None   # for downloads: the Nextcloud name to write

    def describe(self) -> str:
        name = self.target_path or (self.file.path if self.file else None) or (
            self.record.nc_path if self.record else None) or (self.asset.filename if self.asset else "?")
        return f"{self.kind.value}: {name}"


@dataclass
class Policy:
    on_album_remove: str = "trash_file"            # "trash_file" | "ignore"
    on_nextcloud_delete: str = "remove_from_album"  # "remove_from_album" | "ignore"


@dataclass
class Plan:
    actions: list[Action] = field(default_factory=list)
    needs_hash: list[NcFile] = field(default_factory=list)  # changed files whose hash must be computed first

    def deletions(self) -> list[Action]:
        return [a for a in self.actions if a.kind in DELETING_KINDS]


def unique_name(wanted: str, taken: set[str]) -> str:
    """Return `wanted`, or `name (1).ext`, `name (2).ext`... if it is taken (case-insensitive)."""
    lower = {t.lower() for t in taken}
    if wanted.lower() not in lower:
        return wanted
    stem, dot, ext = wanted.rpartition(".")
    if not dot:
        stem, ext = wanted, ""
    n = 1
    while True:
        candidate = f"{stem} ({n}){dot}{ext}"
        if candidate.lower() not in lower:
            return candidate
        n += 1


def plan(
    album: dict[str, Asset],
    files: dict[str, NcFile],
    records: list[Record],
    policy: Policy,
) -> Plan:
    """Compute the actions for one sync run.

    Untracked Nextcloud files must already carry `sha1` (the engine hashes
    them before calling this). Tracked files whose etag changed and that have
    no `sha1` are returned in `Plan.needs_hash`; the engine hashes them and
    calls `plan` again.
    """
    result = Plan()
    tracked_assets = {r.asset_id for r in records}
    tracked_paths = {r.nc_path for r in records}
    claimed_assets: set[str] = set()
    claimed_paths: set[str] = set()

    # 1. Things we already track.
    for rec in records:
        asset = album.get(rec.asset_id)
        f = files.get(rec.nc_path)
        claimed_assets.add(rec.asset_id)
        claimed_paths.add(rec.nc_path)

        if asset and f:
            if rec.etag is not None and f.etag == rec.etag:
                continue
            if f.sha1 is None:
                result.needs_hash.append(f)
                continue
            if f.sha1 == rec.sha1:
                result.actions.append(Action(Kind.UPDATE_ETAG, asset=asset, file=f, record=rec))
            else:
                result.actions.append(Action(Kind.REPLACE_IN_IMMICH, asset=asset, file=f, record=rec))
        elif f and not asset:
            if policy.on_album_remove == "trash_file":
                result.actions.append(Action(Kind.TRASH_NEXTCLOUD_FILE, file=f, record=rec))
            else:
                result.actions.append(Action(Kind.FORGET, record=rec))
        elif asset and not f:
            # Another file with identical content may still point at this asset.
            still_used = any(
                o.asset_id == rec.asset_id and o.nc_path != rec.nc_path and o.nc_path in files
                for o in records
            )
            if still_used:
                result.actions.append(Action(Kind.FORGET, record=rec))
            elif policy.on_nextcloud_delete == "remove_from_album":
                result.actions.append(Action(Kind.REMOVE_FROM_ALBUM, asset=asset, record=rec))
            else:
                result.actions.append(Action(Kind.FORGET, record=rec))
        else:
            result.actions.append(Action(Kind.FORGET, record=rec))

    # 2. New Nextcloud files: link to an identical album asset, or upload.
    new_assets_by_sha = {}
    for a in album.values():
        if a.id not in tracked_assets:
            new_assets_by_sha.setdefault(a.sha1, a)

    for path in sorted(files):
        if path in tracked_paths:
            continue
        f = files[path]
        if f.sha1 is None:
            raise ValueError(f"untracked file without hash: {path}")
        match = new_assets_by_sha.pop(f.sha1, None)
        if match:
            claimed_assets.add(match.id)
            result.actions.append(Action(Kind.LINK, asset=match, file=f))
        else:
            # Same content as an asset that is already tracked under another
            # name: link to that asset instead of uploading a duplicate.
            dup = next((a for a in album.values() if a.sha1 == f.sha1), None)
            if dup:
                result.actions.append(Action(Kind.LINK, asset=dup, file=f))
            else:
                result.actions.append(Action(Kind.UPLOAD_TO_IMMICH, file=f))
        claimed_paths.add(path)

    # 3. New album assets: download into the folder root under a free name.
    taken = set(files) | tracked_paths
    for a in sorted(album.values(), key=lambda x: x.filename.lower()):
        if a.id in claimed_assets:
            continue
        name = unique_name(a.filename, taken)
        taken.add(name)
        result.actions.append(Action(Kind.DOWNLOAD_TO_NEXTCLOUD, asset=a, target_path=name))

    return result
