from __future__ import annotations

import fcntl
import json
import os
import stat
import subprocess
import sys
import threading
import time

import pytest
from cowtree import cache as cache_module
from cowtree.cache import Cache, SharedRoots, _lease_state, mount_holders, shared_roots, tree_manifest, validate_key
from cowtree.errors import BackendFailed, CacheBusy, KeyMissing, PathRefused, RootInvalid, TreeInvalid

KEY = "a" * 16
KEY2 = "b" * 16


def _build(tree):
    os.makedirs(os.path.join(tree, "pkg"))
    with open(os.path.join(tree, "pkg", "mod.py"), "w", encoding="utf-8") as handle:
        handle.write("x = 1\n")
    os.symlink("pkg/mod.py", os.path.join(tree, "link"))


def _root(tmp_path, **kwargs):
    root = tmp_path / "cache"
    root.mkdir(mode=0o770, exist_ok=True)
    return Cache(str(root), mountinfo=lambda: _mountinfo(str(root)), **kwargs)


def _mountinfo(root, extra=""):
    return f"1 0 8:1 / / rw - ext4 /dev/sda1 rw\n2 1 0:50 / {root} rw - tmpfs tmpfs rw\n{extra}"


def test_validate_key():
    assert validate_key(KEY) == KEY
    for bad in ("short", "a.b" + "c" * 10, "../" + "a" * 10, "", None, "a" * 129):
        with pytest.raises(PathRefused):
            validate_key(bad)


def test_shared_roots(tmp_path):
    assert shared_roots({}) is None
    assert shared_roots({"MDT_TREE_CACHE_ROOT": ""}) is None
    cache_dir = tmp_path / "c"
    upper_dir = tmp_path / "u"
    cache_dir.mkdir(mode=0o770)
    upper_dir.mkdir(mode=0o770)
    roots = shared_roots({"MDT_TREE_CACHE_ROOT": str(cache_dir)})
    assert roots == SharedRoots(str(cache_dir), None) and not roots.overlay_ready
    roots = shared_roots({"MDT_TREE_CACHE_ROOT": str(cache_dir), "MDT_TREE_UPPER_ROOT": str(upper_dir)})
    assert roots.overlay_ready
    os.environ["MDT_TREE_CACHE_ROOT"] = str(cache_dir)
    try:
        assert shared_roots().cache_root == str(cache_dir)
    finally:
        del os.environ["MDT_TREE_CACHE_ROOT"]


def test_shared_roots_refuses_invalid(tmp_path):
    with pytest.raises(RootInvalid):
        shared_roots({"MDT_TREE_CACHE_ROOT": "relative"})
    target = tmp_path / "t"
    target.mkdir()
    link = tmp_path / "l"
    link.symlink_to(target)
    with pytest.raises(RootInvalid, match="symlinks"):
        shared_roots({"MDT_TREE_CACHE_ROOT": str(link)})
    with pytest.raises(RootInvalid):
        shared_roots({"MDT_TREE_CACHE_ROOT": str(tmp_path / "missing")})
    open_dir = tmp_path / "w"
    open_dir.mkdir()
    open_dir.chmod(0o777)
    with pytest.raises(RootInvalid, match="world-writable"):
        shared_roots({"MDT_TREE_CACHE_ROOT": str(open_dir)})
    good = tmp_path / "g"
    good.mkdir(mode=0o770)
    with pytest.raises(RootInvalid):
        shared_roots({"MDT_TREE_CACHE_ROOT": str(good), "MDT_TREE_UPPER_ROOT": "rel"})


def test_ensure_publishes_atomically_and_reuses(tmp_path):
    cache = _root(tmp_path)
    first = cache.ensure(KEY, _build)
    assert first.built and first.files == 1 and first.bytes == 6 and first.lease is None
    assert os.path.isfile(os.path.join(first.tree, "pkg", "mod.py"))
    assert not [name for name in os.listdir(cache.root) if ".tmp-" in name]
    again = cache.ensure(KEY, lambda tree: pytest.fail("must not rebuild"))
    assert not again.built and again.digest == first.digest
    assert cache.verify(KEY)["digest"] == first.digest
    assert cache.verify(KEY, deep=False)["key"] == KEY
    assert cache.require_complete(KEY) == first.tree


def test_ensure_seal_makes_tree_read_only_and_gc_can_remove_it(tmp_path):
    cache = _root(tmp_path)
    published = cache.ensure(KEY, _build, seal=True)
    assert stat.S_IMODE(os.stat(os.path.join(published.tree, "pkg", "mod.py")).st_mode) & 0o222 == 0
    assert stat.S_IMODE(os.stat(published.tree).st_mode) & 0o222 == 0
    cache.verify(KEY)
    cache.lease(KEY, "o").release()
    items = cache.gc(0)
    assert [(item.name, item.status) for item in items] == [(KEY, "removed")]
    assert not os.path.exists(published.path) and not os.path.exists(cache._state(KEY))


def test_build_failure_removes_scratch_and_propagates(tmp_path):
    cache = _root(tmp_path)

    def boom(tree):
        open(os.path.join(tree, "partial"), "w").close()
        raise OSError(28, "No space left on device")

    with pytest.raises(OSError):
        cache.ensure(KEY, boom)
    assert os.listdir(cache.root) == [".locks", ".state"] or sorted(os.listdir(cache.root)) == [".locks", ".state"]
    with pytest.raises(KeyMissing):
        cache.require_complete(KEY)


def test_ensure_replaces_incomplete_key_dir_and_refuses_file(tmp_path):
    cache = _root(tmp_path)
    os.makedirs(os.path.join(cache.key_path(KEY), "tree"))
    assert cache.ensure(KEY, _build).built
    cache2 = _root(tmp_path)
    open(cache2.key_path(KEY2), "w").close()
    with pytest.raises(PathRefused):
        cache2.ensure(KEY2, _build)


def test_unsupported_entry_type_is_refused(tmp_path):
    cache = _root(tmp_path)

    def build(tree):
        os.mkfifo(os.path.join(tree, "fifo"))

    with pytest.raises(PathRefused, match="unsupported"):
        cache.ensure(KEY, build)
    assert sorted(os.listdir(cache.root)) == [".locks", ".state"]


def test_complete_marker_validation(tmp_path):
    cache = _root(tmp_path)
    cache.ensure(KEY, _build)
    marker = os.path.join(cache.key_path(KEY), "COMPLETE")
    good = json.load(open(marker))
    for bad in (
        "{", "[]", json.dumps({**good, "format": "x"}), json.dumps({**good, "key": KEY2}),
        json.dumps({**good, "files": "1"}), json.dumps({**good, "digest": 5}),
    ):
        with open(marker, "w") as handle:
            handle.write(bad)
        assert cache._complete(KEY) is None
    with pytest.raises(KeyMissing):
        cache.verify(KEY)
    os.unlink(marker)
    assert cache._complete(KEY) is None


def test_verify_detects_tamper(tmp_path):
    cache = _root(tmp_path)
    published = cache.ensure(KEY, _build)
    with open(os.path.join(published.tree, "pkg", "mod.py"), "a") as handle:
        handle.write("evil\n")
    with pytest.raises(TreeInvalid):
        cache.verify(KEY)
    assert cache.verify(KEY, deep=False)


def test_manifest_covers_mode_target_and_content(tmp_path):
    tree = tmp_path / "t"
    tree.mkdir()
    (tree / "f").write_text("a")
    base = tree_manifest(str(tree))
    (tree / "f").chmod(0o600)
    assert tree_manifest(str(tree))["digest"] != base["digest"]
    (tree / "f").write_text("b")
    assert tree_manifest(str(tree))["bytes"] == 1


def test_concurrent_ensure_builds_once(tmp_path):
    cache = _root(tmp_path)
    builds = []
    gate = threading.Event()

    def slow(tree):
        builds.append(1)
        gate.wait(5)
        _build(tree)

    results = []
    first = threading.Thread(target=lambda: results.append(cache.ensure(KEY, slow)))
    first.start()
    while not builds:
        time.sleep(0.01)
    second = threading.Thread(target=lambda: results.append(cache.ensure(KEY, slow, lock_timeout=10)))
    second.start()
    time.sleep(0.2)
    with pytest.raises(CacheBusy):
        cache.ensure(KEY, slow, lock_timeout=0)
    gate.set()
    first.join()
    second.join()
    assert len(builds) == 1 and sorted(r.built for r in results) == [False, True]


def test_ensure_with_owner_takes_lease_before_gc_can_run(tmp_path):
    cache = _root(tmp_path)
    published = cache.ensure(KEY, _build, owner="lane-1")
    assert published.lease is not None and published.lease.owner == "lane-1"
    assert [(i.status) for i in cache.gc(0)] == ["leased"]
    published.lease.release()
    assert [(i.status) for i in cache.gc(0)] == ["removed"]


def test_lease_flock_lifecycle_and_context_manager(tmp_path):
    cache = _root(tmp_path)
    cache.ensure(KEY, _build)
    with cache.lease(KEY, "o") as lease:
        lease.heartbeat()
        assert _lease_state(lease.path) is True
        document = json.load(open(lease.path))
        assert document["mode"] == "flock" and document["pid"] == os.getpid() and document["owner"] == "o"
        assert document["pid_starttime"] > 0 and document["boot_id"]
        assert cache.survey()[0]["live_leases"] == 1
    assert not os.path.exists(lease.path)
    lease.release()  # idempotent
    assert cache.survey()[0]["live_leases"] == 0


def test_lease_requires_published_key(tmp_path):
    cache = _root(tmp_path)
    with pytest.raises(KeyMissing):
        cache.lease(KEY, "o")


def test_lease_waits_for_build_lock_then_times_out(tmp_path):
    cache = _root(tmp_path)
    descriptor = cache._lock(KEY, fcntl.LOCK_EX, 0)
    try:
        with pytest.raises(CacheBusy):
            cache.lease(KEY, "o", lock_timeout=0.1)
    finally:
        os.close(descriptor)


def test_flock_lease_of_dead_process_is_stale_and_pruned(tmp_path):
    cache = _root(tmp_path)
    cache.ensure(KEY, _build)
    code = (
        "import sys; sys.path.insert(0, 'src'); from cowtree.cache import Cache; "
        f"c = Cache({cache.root!r}); l = c.lease({KEY!r}, 'child'); print(l.path, flush=True)"
    )
    path = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True,
                          env={**os.environ, "PYTHONPATH": "src"}).stdout.strip()
    assert os.path.exists(path) and _lease_state(path) is False
    assert cache.gc(10**9)[0].status == "kept"
    assert not os.path.exists(path)


def test_lease_state_edges(tmp_path):
    assert _lease_state(str(tmp_path / "gone")) is False
    junk = tmp_path / "junk"
    junk.write_text("not json")
    assert _lease_state(str(junk)) is False
    wrong = tmp_path / "wrong"
    wrong.write_text("[]")
    assert _lease_state(str(wrong)) is False


def test_valid_lease_rejects_bad_documents():
    good = {"format": cache_module.LEASE_FORMAT, "mode": "pid", "pid": 1, "pid_starttime": 2, "boot_id": "b", "pidns": 3}
    assert cache_module._valid_lease(good)
    for bad in ({**good, "format": "x"}, {**good, "mode": "other"}, {**good, "pid": "1"}, {**good, "boot_id": None}):
        assert not cache_module._valid_lease(bad)


def test_pid_lease_alive_dead_reused_and_foreign_namespace(tmp_path):
    cache = _root(tmp_path)
    cache.ensure(KEY, _build)
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        lease = cache.lease(KEY, "ext", pid=child.pid)
        assert json.load(open(lease.path))["mode"] == "pid"
        assert _lease_state(lease.path) is True
        assert cache.gc(0)[0].status == "leased"
        document = json.load(open(lease.path))
        # pid reuse: same pid, different start time -> dead
        document["pid_starttime"] += 1
        with open(lease.path, "w") as handle:
            json.dump(document, handle)
        assert _lease_state(lease.path) is False
        # different pid namespace or boot: cannot judge -> conservatively alive
        document["pidns"] = -1
        with open(lease.path, "w") as handle:
            json.dump(document, handle)
        assert _lease_state(lease.path) is True
        lease.release()
    finally:
        child.kill()
        child.wait()
    dead = cache.lease(KEY, "ext", pid=os.getpid())
    document = json.load(open(dead.path))
    document["pid"] = 2**22 + 12345
    with open(dead.path, "w") as handle:
        json.dump(document, handle)
    assert _lease_state(dead.path) is False
    dead.release()
    with pytest.raises(BackendFailed):
        cache.lease(KEY, "ext", pid=2**22 + 12345)


def test_gc_policy_idle_mount_busy_foreign_scratch_orphan(tmp_path):
    now = [1000.0]
    cache = _root(tmp_path, clock=lambda: now[0])
    cache.ensure(KEY, _build)
    cache.ensure(KEY2, _build)
    # recent use: kept
    assert {i.name: i.status for i in cache.gc(60)} == {KEY: "kept", KEY2: "kept"}
    now[0] += 120
    # mounted: KEY is held as an overlay lowerdir, KEY2 is free
    held = _mountinfo(cache.root, f"3 1 0:60 / /work/w0 rw - overlay overlay rw,lowerdir={cache.tree_path(KEY)},upperdir=/u\n")
    cache._mountinfo = lambda: held
    statuses = {i.name: i.status for i in cache.gc(60)}
    assert statuses == {KEY: "mounted", KEY2: "removed"}
    # busy key, foreign file and scratch entries
    descriptor = cache._lock(KEY, fcntl.LOCK_EX, 0)
    open(os.path.join(cache.root, "README"), "w").close()
    os.mkdir(os.path.join(cache.root, "zz" * 8 + ".tmp-0123456789ab"))
    os.mkdir(os.path.join(cache.root, "bad.key.tmp-0123456789ab"))
    os.makedirs(os.path.join(cache.root, ".state", KEY2, "leases"))
    os.makedirs(os.path.join(cache.root, ".state", "c" * 16))
    os.makedirs(os.path.join(cache.root, ".state", "dd.ignored"))
    os.mkdir(os.path.join(cache.root, KEY2))  # no COMPLETE, appears after orphan check
    cache._mountinfo = lambda: _mountinfo(cache.root)
    statuses = {i.name: i.status for i in cache.gc(60)}
    os.close(descriptor)
    assert statuses[KEY] == "busy" and statuses["README"] == "foreign" and statuses["bad.key.tmp-0123456789ab"] == "foreign"
    assert statuses["zz" * 8 + ".tmp-0123456789ab"] == "removed"
    assert statuses[KEY2] == "removed"          # incomplete key dir removed with its state
    assert statuses["c" * 16] == "removed"      # orphan state
    assert "dd.ignored" not in statuses


def test_gc_orphan_state_busy_and_appeared(tmp_path):
    cache = _root(tmp_path)
    os.makedirs(os.path.join(cache.root, ".state", KEY))
    descriptor = cache._lock(KEY, fcntl.LOCK_EX, 0)
    try:
        assert cache._gc_state_orphan(KEY).status == "busy"
    finally:
        os.close(descriptor)
    os.mkdir(cache.key_path(KEY))
    assert cache._gc_state_orphan(KEY).status == "kept"


def test_gc_removes_incomplete_key_without_state(tmp_path):
    cache = _root(tmp_path)
    os.mkdir(cache.key_path(KEY))
    assert [(i.name, i.status) for i in cache.gc(0)] == [(KEY, "removed")]


def test_gc_tolerates_lease_vanishing_between_list_and_check(tmp_path, monkeypatch):
    cache = _root(tmp_path)
    cache.ensure(KEY, _build)
    lease = cache.lease(KEY, "o")
    path = lease.path
    lease.release()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w").close()
    monkeypatch.setattr(os, "unlink", lambda p: (_ for _ in ()).throw(FileNotFoundError()))
    assert cache._prune_leases(KEY) == 0


def test_gc_is_exclusive(tmp_path):
    cache = _root(tmp_path)
    descriptor = cache._lock(".gc", fcntl.LOCK_EX, 0)
    try:
        with pytest.raises(CacheBusy):
            cache.gc(0)
    finally:
        os.close(descriptor)


def test_idle_clock_falls_back_to_complete_mtime_and_touch(tmp_path):
    now = [os.path.getmtime(str(tmp_path)) + 1000]
    cache = _root(tmp_path, clock=lambda: now[0])
    cache.ensure(KEY, _build)
    os.unlink(os.path.join(cache._state(KEY), "last-used"))
    assert cache._idle_seconds(KEY) > 900
    cache.touch_last_used(KEY)
    assert cache._idle_seconds(KEY) == 0


def test_remove_entry_refuses_bad_names_and_files(tmp_path):
    cache = _root(tmp_path)
    with pytest.raises(PathRefused):
        cache._remove_entry("../escape")
    open(os.path.join(cache.root, KEY), "w").close()
    with pytest.raises(PathRefused, match="non-directory"):
        cache._remove_entry(KEY)


def test_survey_rows(tmp_path):
    cache = _root(tmp_path)
    cache.ensure(KEY, _build)
    os.makedirs(os.path.join(cache.root, KEY2))
    os.mkdir(os.path.join(cache.root, KEY2 + ".tmp-0123456789ab"))
    open(os.path.join(cache.root, "README"), "w").close()
    rows = {row["name"]: row for row in cache.survey()}
    assert rows[KEY]["kind"] == "key" and rows[KEY]["files"] == 1 and rows[KEY]["idle_seconds"] >= 0
    assert rows[KEY2]["kind"] == "incomplete" and rows[KEY2]["idle_seconds"] is None
    assert rows[KEY2 + ".tmp-0123456789ab"]["kind"] == "scratch"
    assert rows["README"]["kind"] == "foreign"


def test_mount_holders_bind_subtree_option_and_malformed():
    key = "/srv/tree-cache/" + KEY
    info = "\n".join([
        "1 0 8:1 / / rw - ext4 /dev/sda rw",
        "2 1 0:50 / /srv/tree-cache rw - tmpfs tmpfs rw",
        f"3 1 0:50 /{KEY}/tree /assay-template ro - tmpfs tmpfs rw",
        f"4 1 0:50 /{KEY}x /other ro - tmpfs tmpfs rw",
        "",
        f"5 1 0:99 / /work/w0 rw - overlay overlay rw,lowerdir={key}/tree:/x,upperdir=/u,workdir=/w",
        "6 1 0:99 / /work/w1 rw - overlay overlay rw,lowerdir=/other,noatime",
        f"7 1 0:51 / {key}/tree/inner rw - tmpfs tmpfs rw",
        f"8 1 0:51 /{KEY}/tree /elsewhere ro - ext4 /dev/sdb rw",
        "9 1 0:52 / /bare rw - tmpfs tmpfs",
    ])
    assert mount_holders(key, info) == ["/assay-template", "/work/w0", key + "/tree/inner"]
    assert mount_holders(key, "1 0 8:1 / / rw - ext4 /dev/sda rw") == []
    with pytest.raises(RootInvalid, match="no mountinfo"):
        mount_holders(key, "2 1 0:50 / /elsewhere rw - tmpfs tmpfs rw")
    with pytest.raises(RootInvalid, match="malformed"):
        mount_holders(key, "garbage")


def test_default_mountinfo_reader_reads_proc():
    assert " - " in cache_module._read_mountinfo()


def test_cache_creates_locks_and_state_dirs(tmp_path):
    cache = _root(tmp_path)
    assert os.path.isdir(os.path.join(cache.root, ".locks")) and os.path.isdir(os.path.join(cache.root, ".state"))
    with pytest.raises(RootInvalid):
        Cache("relative")
