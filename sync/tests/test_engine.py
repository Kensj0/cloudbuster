"""Engine tests with in-memory fakes for Immich and Nextcloud (no network)."""

import hashlib
from pathlib import Path

import pytest

from cloudbuster_sync.config import Config
from cloudbuster_sync.engine import Engine, SyncAbort
from cloudbuster_sync.immich import UploadResult
from cloudbuster_sync.nextcloud import RemoteEntry
from cloudbuster_sync.planner import Asset
from cloudbuster_sync.state import State


def sha1(b: bytes) -> str:
    return hashlib.sha1(b).hexdigest()


class FakeImmich:
    def __init__(self):
        self.album_id = "album-1"
        self.blobs: dict[str, bytes] = {}      # asset id -> content
        self.names: dict[str, str] = {}
        self.album: set[str] = set()
        self.uploads = 0
        self.n = 0

    def add_asset(self, data: bytes, name: str, in_album=True) -> str:
        self.n += 1
        aid = f"asset-{self.n}"
        self.blobs[aid], self.names[aid] = data, name
        if in_album:
            self.album.add(aid)
        return aid

    def find_album(self, name):
        return {"id": self.album_id, "albumName": name}

    def album_assets(self, album_id):
        return {a: Asset(a, sha1(self.blobs[a]), self.names[a]) for a in self.album}

    def download(self, aid, dest: Path):
        dest.write_bytes(self.blobs[aid])
        return sha1(self.blobs[aid])

    def upload(self, path: Path, filename, modified):
        data = path.read_bytes()
        for aid, blob in self.blobs.items():
            if blob == data:
                return UploadResult(aid, True)
        self.uploads += 1
        return UploadResult(self.add_asset(data, filename, in_album=False), False)

    def add_to_album(self, album_id, ids):
        self.album.update(ids)

    def remove_from_album(self, album_id, ids):
        self.album.difference_update(ids)


class FakeNextcloud:
    def __init__(self):
        self.files: dict[str, bytes] = {}
        self.etags: dict[str, int] = {}
        self.trashed: list[str] = []
        self.missing = False

    def put(self, path, data: bytes):
        self.files[path] = data
        self.etags[path] = self.etags.get(path, 0) + 1

    def folder_exists(self):
        return not self.missing

    def list_files(self):
        return [RemoteEntry(p, False, f"e{self.etags[p]}", len(d), None, None) for p, d in self.files.items()]

    def download(self, path, dest: Path):
        dest.write_bytes(self.files[path])
        return sha1(self.files[path])

    def upload(self, path, src: Path, sha, mtime):
        assert path not in self.files, "must never overwrite"
        self.put(path, src.read_bytes())
        return f"e{self.etags[path]}"

    def trash(self, path):
        self.trashed.append(path)
        del self.files[path]


@pytest.fixture
def env(tmp_path):
    cfg = Config(immich_url="", immich_api_key="k", nextcloud_url="", nextcloud_user="u",
                 nextcloud_app_password="p", state_path=str(tmp_path / "s.db"), max_deletions_per_run=3)
    im, nc = FakeImmich(), FakeNextcloud()
    eng = Engine(cfg, im, nc, State(cfg.state_path))
    return eng, im, nc


def test_full_workflow(env):
    eng, im, nc = env
    # 1-3: photo added to the album -> appears in Nextcloud
    im.add_asset(b"receipt", "kvitto.jpg")
    eng.run()
    assert nc.files == {"kvitto.jpg": b"receipt"}

    # Second run: nothing to do (no loop)
    assert eng.run().planned == 0

    # 5: the agent writes a new image -> uploaded and added to the album
    nc.put("summary.png", b"chart")
    eng.run()
    assert any(im.blobs[a] == b"chart" for a in im.album)
    assert eng.run().planned == 0

    # The agent writes a text file -> ignored by the Immich sync
    nc.put("notes.md", b"text")
    assert eng.run().planned == 0

    # File deleted in Nextcloud -> removed from the album, asset kept in the library
    del nc.files["kvitto.jpg"]
    eng.run()
    assert not any(im.blobs[a] == b"receipt" for a in im.album)
    assert any(b == b"receipt" for b in im.blobs.values())

    # Removed from the album in Immich -> file goes to the Nextcloud trash
    chart = next(a for a in im.album if im.blobs[a] == b"chart")
    im.album.discard(chart)
    eng.run()
    assert nc.trashed == ["summary.png"]
    assert "summary.png" not in nc.files


def test_duplicate_content_is_not_uploaded_twice(env):
    eng, im, nc = env
    im.add_asset(b"same", "a.jpg")
    nc.put("b.jpg", b"same")
    eng.run()
    assert im.uploads == 0
    assert set(nc.files) == {"b.jpg"}   # linked, not downloaded again


def test_dry_run_changes_nothing(env):
    eng, im, nc = env
    im.add_asset(b"x", "x.jpg")
    nc.put("y.jpg", b"y")
    res = eng.run(dry_run=True)
    assert res.planned == 2 and res.applied == 0
    assert set(nc.files) == {"y.jpg"} and im.uploads == 0
    assert eng.state.records() == []


def test_mass_delete_guard(env):
    eng, im, nc = env
    for i in range(5):
        im.add_asset(f"img{i}".encode(), f"{i}.jpg")
    eng.run()
    im.album.clear()  # e.g. album emptied by mistake
    res = eng.run()
    assert res.deletions_blocked == 5 and nc.trashed == []
    res = eng.run(allow_mass_delete=True)
    assert len(nc.trashed) == 5


def test_missing_folder_aborts(env):
    eng, im, nc = env
    nc.missing = True
    with pytest.raises(SyncAbort):
        eng.run()


def test_album_id_change_aborts(env):
    eng, im, nc = env
    im.add_asset(b"x", "x.jpg")
    eng.run()
    im.album_id = "album-2"
    with pytest.raises(SyncAbort):
        eng.run()


def test_modified_file_replaces_asset(env):
    eng, im, nc = env
    nc.put("x.png", b"v1")
    eng.run()
    nc.put("x.png", b"v2")
    eng.run()
    contents = sorted(im.blobs[a] for a in im.album)
    assert contents == [b"v2"]
