from cloudbuster_sync.planner import Asset, Kind, NcFile, Policy, Record, plan, unique_name

P = Policy()


def kinds(p):
    return sorted(a.kind.value for a in p.actions)


def test_first_run_merges_without_deleting():
    album = {"a1": Asset("a1", "s1", "kvitto.jpg"), "a2": Asset("a2", "s2", "same.jpg")}
    files = {"same-name-other.jpg": NcFile("same-name-other.jpg", "e1", "s2"),
             "agent.png": NcFile("agent.png", "e2", "s3")}
    p = plan(album, files, [], P)
    assert kinds(p) == ["download", "link", "upload"]
    assert not p.deletions()


def test_in_sync_is_noop():
    album = {"a1": Asset("a1", "s1", "x.jpg")}
    files = {"x.jpg": NcFile("x.jpg", "e1")}
    p = plan(album, files, [Record("a1", "x.jpg", "s1", "e1")], P)
    assert p.actions == [] and p.needs_hash == []


def test_removed_from_album_trashes_file():
    files = {"x.jpg": NcFile("x.jpg", "e1")}
    p = plan({}, files, [Record("a1", "x.jpg", "s1", "e1")], P)
    assert kinds(p) == ["trash_file"]


def test_removed_from_album_ignore_policy_only_forgets():
    files = {"x.jpg": NcFile("x.jpg", "e1")}
    p = plan({}, files, [Record("a1", "x.jpg", "s1", "e1")], Policy(on_album_remove="ignore"))
    assert kinds(p) == ["forget"]


def test_deleted_in_nextcloud_removes_from_album_only():
    album = {"a1": Asset("a1", "s1", "x.jpg")}
    p = plan(album, {}, [Record("a1", "x.jpg", "s1", "e1")], P)
    assert kinds(p) == ["remove_from_album"]


def test_gone_on_both_sides_forgets():
    p = plan({}, {}, [Record("a1", "x.jpg", "s1", "e1")], P)
    assert kinds(p) == ["forget"]


def test_changed_etag_needs_hash_then_same_content_updates_etag():
    album = {"a1": Asset("a1", "s1", "x.jpg")}
    rec = [Record("a1", "x.jpg", "s1", "e1")]
    p = plan(album, {"x.jpg": NcFile("x.jpg", "e2")}, rec, P)
    assert [f.path for f in p.needs_hash] == ["x.jpg"] and p.actions == []
    p = plan(album, {"x.jpg": NcFile("x.jpg", "e2", "s1")}, rec, P)
    assert kinds(p) == ["update_etag"]


def test_changed_content_replaces():
    album = {"a1": Asset("a1", "s1", "x.jpg")}
    p = plan(album, {"x.jpg": NcFile("x.jpg", "e2", "s9")}, [Record("a1", "x.jpg", "s1", "e1")], P)
    assert kinds(p) == ["replace"]


def test_unknown_etag_after_download_does_not_loop():
    # A file we wrote without getting an etag back: next run hashes it, finds
    # identical content and only records the etag. No upload, no download.
    album = {"a1": Asset("a1", "s1", "x.jpg")}
    rec = [Record("a1", "x.jpg", "s1", None)]
    p = plan(album, {"x.jpg": NcFile("x.jpg", "e1", "s1")}, rec, P)
    assert kinds(p) == ["update_etag"]


def test_name_conflict_renames_never_overwrites():
    album = {"a1": Asset("a1", "s1", "kvitto.jpg")}
    files = {"kvitto.jpg": NcFile("kvitto.jpg", "e1", "other")}
    p = plan(album, files, [], P)
    targets = {a.target_path for a in p.actions if a.kind == Kind.DOWNLOAD_TO_NEXTCLOUD}
    assert targets == {"kvitto (1).jpg"}


def test_two_album_assets_same_name_get_distinct_names():
    album = {"a1": Asset("a1", "s1", "IMG.jpg"), "a2": Asset("a2", "s2", "img.jpg")}
    p = plan(album, {}, [], P)
    assert sorted(a.target_path for a in p.actions) == ["IMG (1).jpg", "img.jpg"] or \
        sorted(a.target_path for a in p.actions) == ["IMG.jpg", "img (1).jpg"]


def test_copy_of_tracked_asset_is_linked_not_uploaded():
    album = {"a1": Asset("a1", "s1", "x.jpg")}
    files = {"x.jpg": NcFile("x.jpg", "e1"), "copy.jpg": NcFile("copy.jpg", "e5", "s1")}
    p = plan(album, files, [Record("a1", "x.jpg", "s1", "e1")], P)
    assert kinds(p) == ["link"]


def test_deleting_one_of_two_identical_files_keeps_asset_in_album():
    album = {"a1": Asset("a1", "s1", "x.jpg")}
    recs = [Record("a1", "x.jpg", "s1", "e1"), Record("a1", "copy.jpg", "s1", "e5")]
    p = plan(album, {"copy.jpg": NcFile("copy.jpg", "e5")}, recs, P)
    assert kinds(p) == ["forget"]


def test_files_in_subfolders_are_uploaded():
    p = plan({}, {"kvitton/2026/a.jpg": NcFile("kvitton/2026/a.jpg", "e", "s")}, [], P)
    assert kinds(p) == ["upload"]


def test_unique_name():
    assert unique_name("a.jpg", {"A.JPG", "a (1).jpg"}) == "a (2).jpg"
    assert unique_name("README", {"README"}) == "README (1)"
