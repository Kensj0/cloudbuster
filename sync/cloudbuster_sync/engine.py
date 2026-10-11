"""One sync run: gather both sides, plan, check the safety limits, apply."""

from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path

from .config import Config
from .immich import Immich
from .nextcloud import Nextcloud, RemoteEntry
from .planner import Action, Kind, NcFile, Plan, Policy, Record, plan
from .state import State

log = logging.getLogger("cloudbuster.sync")


class SyncAbort(RuntimeError):
    """Something looks wrong enough that nothing should be changed this run."""


@dataclass
class RunResult:
    planned: int = 0
    applied: int = 0
    failed: int = 0
    deletions_blocked: int = 0


class Engine:
    def __init__(self, cfg: Config, immich: Immich, nc: Nextcloud, state: State):
        self.cfg = cfg
        self.immich = immich
        self.nc = nc
        self.state = state
        self.policy = Policy(on_album_remove=cfg.on_album_remove, on_nextcloud_delete=cfg.on_nextcloud_delete)

    # ---- gathering -------------------------------------------------------

    def _album_id(self) -> str:
        album = self.immich.find_album(self.cfg.album_name)
        if not album:
            raise SyncAbort(f"Immich album {self.cfg.album_name!r} not found or not shared with the sync user")
        known = self.state.get_meta("album_id")
        if known and known != album["id"]:
            raise SyncAbort("the album id changed since the last run; refusing to sync. "
                            "If you recreated the album on purpose, stop the service and remove the state file.")
        return album["id"]

    def run(self, dry_run: bool = False, allow_mass_delete: bool = False) -> RunResult:
        album_id = self._album_id()
        if not self.nc.folder_exists():
            raise SyncAbort("the Nextcloud folder is missing or not shared with the sync user")

        assets = self.immich.album_assets(album_id)
        entries = {e.path: e for e in self.nc.list_files() if self.cfg.is_media(e.path)}
        records = self.state.records()
        tracked = {r.nc_path for r in records}

        with tempfile.TemporaryDirectory(prefix="cloudbuster-") as tmp:
            cache: dict[str, Path] = {}
            files = {p: NcFile(path=p, etag=e.etag, sha1=e.sha1) for p, e in entries.items()}

            # Untracked files must be hashed before planning (to find matches).
            for p, f in files.items():
                if p not in tracked and f.sha1 is None:
                    files[p] = replace(f, sha1=self._fetch(p, tmp, cache))

            result_plan = plan(assets, files, records, self.policy)
            if result_plan.needs_hash:
                for f in result_plan.needs_hash:
                    files[f.path] = replace(f, sha1=self._fetch(f.path, tmp, cache))
                result_plan = plan(assets, files, records, self.policy)

            res = RunResult(planned=len(result_plan.actions))
            actions = self._guard(result_plan, allow_mass_delete, res)

            if not dry_run and not self.state.get_meta("album_id"):
                self.state.set_meta("album_id", album_id)

            for action in actions:
                if dry_run:
                    log.info("[dry-run] would %s", action.describe())
                    continue
                try:
                    self._apply(action, album_id, entries, tmp, cache, records)
                    res.applied += 1
                    log.info("%s", action.describe())
                except Exception as exc:  # one failing item must not stop the others
                    res.failed += 1
                    log.error("%s failed: %s", action.describe(), exc)
            return res

    def _fetch(self, path: str, tmp: str, cache: dict[str, Path]) -> str:
        dest = Path(tmp) / f"nc-{len(cache)}"
        sha1 = self.nc.download(path, dest)
        cache[path] = dest
        return sha1

    def _guard(self, p: Plan, allow_mass_delete: bool, res: RunResult) -> list[Action]:
        deletions = p.deletions()
        limit = self.cfg.max_deletions_per_run
        if len(deletions) > limit and not allow_mass_delete:
            res.deletions_blocked = len(deletions)
            log.error(
                "safety stop: %d deletions planned, limit is %d. Nothing was deleted. "
                "If this is intended, run once with --allow-mass-delete (try --dry-run first).",
                len(deletions), limit)
            return [a for a in p.actions if a.kind not in {Kind.TRASH_NEXTCLOUD_FILE, Kind.REMOVE_FROM_ALBUM,
                                                              Kind.REPLACE_IN_IMMICH}]
        return p.actions

    # ---- applying --------------------------------------------------------

    def _apply(self, a: Action, album_id: str, entries: dict[str, RemoteEntry], tmp: str,
               cache: dict[str, Path], records: list[Record]) -> None:
        k = a.kind
        if k == Kind.LINK:
            self.state.upsert(Record(a.asset.id, a.file.path, a.file.sha1, a.file.etag))

        elif k == Kind.DOWNLOAD_TO_NEXTCLOUD:
            dest = Path(tmp) / f"im-{a.asset.id}"
            sha1 = self.immich.download(a.asset.id, dest)
            etag = self.nc.upload(a.target_path, dest, sha1, None)
            self.state.upsert(Record(a.asset.id, a.target_path, sha1, etag))

        elif k in (Kind.UPLOAD_TO_IMMICH, Kind.REPLACE_IN_IMMICH):
            path = a.file.path
            src = cache.get(path)
            if src is None:
                src = Path(tmp) / f"nc-up-{len(cache)}"
                self.nc.download(path, src)
            entry = entries.get(path)
            uploaded = self.immich.upload(src, path.rsplit("/", 1)[-1], entry.modified if entry else None)
            self.immich.add_to_album(album_id, [uploaded.asset_id])
            if k == Kind.REPLACE_IN_IMMICH and self.policy.on_nextcloud_delete == "remove_from_album":
                old = a.record.asset_id
                if old != uploaded.asset_id and not any(
                        r.asset_id == old and r.nc_path != path for r in records):
                    self.immich.remove_from_album(album_id, [old])
            self.state.upsert(Record(uploaded.asset_id, path, a.file.sha1, a.file.etag))

        elif k == Kind.TRASH_NEXTCLOUD_FILE:
            self.nc.trash(a.record.nc_path)
            self.state.delete(a.record.nc_path)

        elif k == Kind.REMOVE_FROM_ALBUM:
            self.immich.remove_from_album(album_id, [a.record.asset_id])
            self.state.delete(a.record.nc_path)

        elif k == Kind.UPDATE_ETAG:
            self.state.upsert(replace(a.record, etag=a.file.etag))

        elif k == Kind.FORGET:
            self.state.delete(a.record.nc_path)
