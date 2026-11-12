from __future__ import annotations

import errno
import fcntl
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from cowtree import cache as cache_module
from cowtree.cache import Cache, mount_holders, tree_manifest, validate_key
from cowtree.errors import BackendFailed, CacheBusy, KeyCollision, KeyMissing, PathRefused, RootInvalid, TreeInvalid

KEY = "a" * 16
KEY2 = "b" * 16
LEASE = ".lease"
LAST_USED = ".last-used"


def _build(tree):
    os.makedirs(os.path.join(tree, "pkg"))
    with open(os.path.join(tree, "pkg", "mod.py"), "w", encoding="utf-8") as handle:
        handle.write("x = 1\n")
    os.symlink("pkg/mod.py", os.path.join(tree, "link"))


def _mountinfo(root, extra=""):
    return f"1 0 8:1 / / rw - ext4 /dev/sda1 rw\n2 1 0:50 / {root} rw - tmpfs tmpfs rw\n{extra}"


def _root(tmp_path, **kwargs):
    root = tmp_path / "cache"
    root.mkdir(mode=0o770, exist_ok=True)
    kwargs.setdefault("mountinfo", lambda: _mountinfo(str(root)))
    return Cache(str(root), require_tmpfs=True, **kwargs)


def _private(tmp_path, **kwargs):
    """A cache on the real (non-tmpfs) test filesystem: the explicit opt-out."""
    root = tmp_path / "private"
    root.mkdir(mode=0o770, exist_ok=True)
    return Cache(str(root), require_tmpfs=False, **kwargs)


def _key_file(cache, key, name):
    return os.path.join(cache.key_path(key), name)


def test_validate_key():
    assert validate_key(KEY) == KEY
    for bad in ("short", "a.b" + "c" * 10, "../" + "a" * 10, "", None, "a" * 129):
        with pytest.raises(PathRefused):
            validate_key(bad)


def test_cache_refuses_invalid_roots(tmp_path):
    with pytest.raises(RootInvalid):
        Cache("relative", require_tmpfs=False)
    target = tmp_path / "t"
    target.mkdir()
    link = tmp_path / "l"
    link.symlink_to(target)
    with pytest.raises(RootInvalid, match="symlinks"):
        Cache(str(link), require_tmpfs=False)
    with pytest.raises(RootInvalid):
        Cache(str(tmp_path / "missing"), require_tmpfs=False)
    open_dir = tmp_path / "w"
    open_dir.mkdir()
    open_dir.chmod(0o777)
    with pytest.raises(RootInvalid, match="world-writable"):
        Cache(str(open_dir), require_tmpfs=False)


# ---------------------------------------------------------------- tmpfs rule

def test_require_tmpfs_is_mandatory_and_has_no_default(tmp_path):
    with pytest.raises(TypeError):
        Cache(str(tmp_path))  # type: ignore[call-arg]


def test_cache_refuses_a_root_that_is_not_a_tmpfs(tmp_path):
    root = tmp_path / "disk"
    root.mkdir(mode=0o770)
    disk = f"1 0 8:1 / / rw - ext4 /dev/sda1 rw\n"
    with pytest.raises(RootInvalid, match="not a tmpfs"):
        Cache(str(root), require_tmpfs=True, mountinfo=lambda: disk)
    # the explicit opt-out
    assert Cache(str(root), require_tmpfs=False, mountinfo=lambda: disk).root == str(root)
    # ramfs and a tmpfs below the mount point are fine
    sub = tmp_path / "ram" / "cache"
    sub.mkdir(parents=True, mode=0o770)
    ram = f"1 0 8:1 / / rw - ext4 /dev/sda1 rw\n2 1 0:50 / {tmp_path / 'ram'} rw - ramfs ramfs rw\n"
    assert Cache(str(sub), require_tmpfs=True, mountinfo=lambda: ram).root == str(sub)


def test_gc_rechecks_tmpfs_and_deletes_nothing_on_a_real_disk(tmp_path):
    state = {"info": None}
    root = tmp_path / "cache"
    root.mkdir(mode=0o770)
    state["info"] = _mountinfo(str(root))
    cache = Cache(str(root), require_tmpfs=True, mountinfo=lambda: state["info"])
    cache.ensure(KEY, _build)
    state["info"] = "1 0 8:1 / / rw - ext4 /dev/sda1 rw\n"  # the tmpfs was unmounted underneath us
    with pytest.raises(RootInvalid, match="not a tmpfs"):
        cache.gc(0)
    assert os.path.isdir(cache.key_path(KEY))


# ---------------------------------------------------------------- publish

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


def test_published_key_has_the_host_contract_layout(tmp_path):
    cache = _root(tmp_path)
    cache.ensure(KEY, _build)
    assert sorted(os.listdir(cache.key_path(KEY))) == [LAST_USED, LEASE, "COMPLETE", "tree"]
    assert sorted(os.listdir(cache.root)) == [".locks", KEY]
    assert sorted(os.listdir(os.path.join(cache.root, ".locks"))) == [KEY]


def test_ensure_seal_makes_tree_read_only_and_gc_can_remove_it(tmp_path):
    cache = _root(tmp_path)
    published = cache.ensure(KEY, _build, seal=True)
    assert stat.S_IMODE(os.stat(os.path.join(published.tree, "pkg", "mod.py")).st_mode) & 0o222 == 0
    assert stat.S_IMODE(os.stat(published.tree).st_mode) & 0o222 == 0
    # the key directory itself is never sealed: .last-used and .lease stay usable under a sealed tree
    assert stat.S_IMODE(os.stat(published.path).st_mode) & 0o770 == 0o770
    cache.verify(KEY)
    cache.lease(KEY, "o").release()
    items = cache.gc(0)
    assert [(item.name, item.status) for item in items] == [(KEY, "removed")]
    assert not os.path.exists(published.path)


def test_force_rmtree_removes_mode_zero_directories(tmp_path):
    victim = tmp_path / "v"
    (victim / "a" / "b").mkdir(parents=True)
    (victim / "a" / "b" / "f").write_text("x")
    (victim / "a" / "b").chmod(0)
    (victim / "a").chmod(0)
    cache_module._force_rmtree(str(victim))
    assert not victim.exists()


def test_build_failure_removes_scratch_and_propagates(tmp_path):
    cache = _root(tmp_path)

    def boom(tree):
        open(os.path.join(tree, "partial"), "w").close()
        raise OSError(28, "No space left on device")

    with pytest.raises(OSError):
        cache.ensure(KEY, boom)
    assert sorted(os.listdir(cache.root)) == [".locks"]
    with pytest.raises(KeyMissing):
        cache.require_complete(KEY)


def test_ensure_replaces_incomplete_key_dir_and_refuses_file(tmp_path):
    cache = _root(tmp_path)
    os.makedirs(os.path.join(cache.key_path(KEY), "tree"))
    assert cache.ensure(KEY, _build).built
    open(cache.key_path(KEY2), "w").close()
    with pytest.raises(PathRefused):
        cache.ensure(KEY2, _build)


def test_unsupported_entry_type_is_refused(tmp_path):
    cache = _root(tmp_path)

    def build(tree):
        os.mkfifo(os.path.join(tree, "fifo"))

    with pytest.raises(PathRefused, match="unsupported"):
        cache.ensure(KEY, build)
    assert sorted(os.listdir(cache.root)) == [".locks"]


def test_scratch_name_collision_is_refused(tmp_path, monkeypatch):
    cache = _root(tmp_path)
    os.mkdir(os.path.join(cache.root, KEY + ".tmp-0123456789ab"))
    monkeypatch.setattr(cache, "_tmp_name", lambda key: key + ".tmp-0123456789ab")
    with pytest.raises(PathRefused, match="scratch directory already exists"):
        cache.ensure(KEY, _build)


def test_complete_marker_validation(tmp_path):
    cache = _root(tmp_path)
    cache.ensure(KEY, _build)
    marker = os.path.join(cache.key_path(KEY), "COMPLETE")
    good = json.load(open(marker))
    for bad in (
        "{", "[]", json.dumps({**good, "format": "x"}), json.dumps({**good, "key": KEY2}),
        json.dumps({**good, "files": "1"}), json.dumps({**good, "digest": 5}),
        json.dumps({**good, "identity": 5}),
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
    started = threading.Event()
    gate = threading.Event()

    def slow(tree):
        builds.append(1)
        started.set()
        assert gate.wait(10)
        _build(tree)

    results = []
    first = threading.Thread(target=lambda: results.append(cache.ensure(KEY, slow)))
    first.start()
    assert started.wait(10)
    second = threading.Thread(target=lambda: results.append(cache.ensure(KEY, slow, lock_timeout=30)))
    second.start()
    # the build lock is held: a non-waiting caller is refused while the build is in progress (no sleeping needed)
    with pytest.raises(CacheBusy):
        cache.ensure(KEY, slow, lock_timeout=0)
    assert not results
    gate.set()
    first.join(30)
    second.join(30)
    assert len(builds) == 1 and sorted(r.built for r in results) == [False, True]


# ---------------------------------------------------------------- key collision

def test_identity_is_recorded_and_a_different_identity_is_refused(tmp_path):
    cache = _root(tmp_path)
    first = cache.ensure(KEY, _build, identity="source-rev-1")
    assert json.load(open(_key_file(cache, KEY, "COMPLETE")))["identity"] == "source-rev-1"
    again = cache.ensure(KEY, lambda tree: pytest.fail("must not rebuild"), identity="source-rev-1")
    assert not again.built and again.digest == first.digest
    with pytest.raises(KeyCollision, match="source-rev-1"):
        cache.ensure(KEY, lambda tree: pytest.fail("must not rebuild"), identity="source-rev-2")
    # without an identity the key is trusted
    assert not cache.ensure(KEY, lambda tree: pytest.fail("must not rebuild")).built
    assert cache.verify(KEY)["digest"] == first.digest  # the published tree is untouched


def test_identity_cannot_be_checked_against_a_key_published_without_one(tmp_path):
    cache = _root(tmp_path)
    cache.ensure(KEY, _build)
    with pytest.raises(KeyCollision):
        cache.ensure(KEY, _build, identity="anything")


def test_identity_validation(tmp_path):
    cache = _root(tmp_path)
    for bad in ("", "x" * 513, 5):
        with pytest.raises(PathRefused, match="identity"):
            cache.ensure(KEY, _build, identity=bad)
    assert not os.path.exists(cache.key_path(KEY))


def test_key_collision_code():
    assert KeyCollision.code == "key-collision"


# ---------------------------------------------------------------- read-only commands

def test_read_only_operations_create_nothing(tmp_path):
    root = tmp_path / "cache"
    root.mkdir(mode=0o770)
    cache = Cache(str(root), require_tmpfs=False)
    assert os.listdir(root) == []
    assert cache.survey() == []
    with pytest.raises(KeyMissing):
        cache.verify(KEY)
    with pytest.raises(KeyMissing):
        cache.require_complete(KEY)
    assert os.listdir(root) == []


def test_survey_on_a_published_root_changes_nothing(tmp_path):
    cache = _root(tmp_path)
    cache.ensure(KEY, _build)
    before = sorted(os.listdir(cache.key_path(KEY))), os.stat(_key_file(cache, KEY, LAST_USED)).st_mtime_ns
    assert cache.survey()[0]["leased"] is False
    cache.verify(KEY)
    assert (sorted(os.listdir(cache.key_path(KEY))), os.stat(_key_file(cache, KEY, LAST_USED)).st_mtime_ns) == before


# ---------------------------------------------------------------- modes (umask independence, group sharing)

@pytest.fixture
def umask022():
    old = os.umask(0o022)
    try:
        yield
    finally:
        os.umask(old)


def _modes(cache, key):
    paths = {
        ".locks": os.path.join(cache.root, ".locks"),
        f".locks/{key}": os.path.join(cache.root, ".locks", key),
        "key": cache.key_path(key),
        "tree": cache.tree_path(key),
        "COMPLETE": _key_file(cache, key, "COMPLETE"),
        LEASE: _key_file(cache, key, LEASE),
        LAST_USED: _key_file(cache, key, LAST_USED),
    }
    return {name: stat.S_IMODE(os.stat(path).st_mode) for name, path in paths.items()}


def test_every_created_path_is_group_usable_under_umask_022(tmp_path, umask022):
    root = tmp_path / "cache"
    root.mkdir()
    os.chmod(root, 0o770)  # the host's root: root:<group>, group-writable (the umask did not matter for it either)
    cache = Cache(str(root), require_tmpfs=False)
    cache.ensure(KEY, _build, owner="lane").lease.release()
    cache.gc(10**9)
    modes = _modes(cache, KEY)
    for name in (".locks", f".locks/{KEY}", "key", "tree"):
        want = 0o770 if name in (".locks", "key", "tree") else 0o660
        assert modes[name] & want == want, (name, oct(modes[name]))
    for name in ("COMPLETE", LEASE, LAST_USED, f".locks/{KEY}"):
        # a second group member must be able to open the lock/lease (O_RDWR for locks) and touch .last-used
        assert modes[name] & 0o060 == 0o060, (name, oct(modes[name]))
    assert stat.S_IMODE(os.stat(os.path.join(cache.root, ".locks", ".gc")).st_mode) & 0o060 == 0o060


def test_modes_hold_for_build_scratch_and_orphan_paths_under_umask_022(tmp_path, umask022):
    cache = _root(tmp_path)
    seen = {}

    def build(tree):
        scratch = os.path.dirname(tree)
        seen["scratch"] = stat.S_IMODE(os.stat(scratch).st_mode)
        seen["tree"] = stat.S_IMODE(os.stat(tree).st_mode)
        _build(tree)

    cache.ensure(KEY, build)
    assert seen["scratch"] & 0o770 == 0o770 and seen["tree"] & 0o770 == 0o770


def test_root_setgid_bit_is_inherited_by_created_directories(tmp_path, umask022):
    root = tmp_path / "cache"
    root.mkdir()
    os.chmod(root, 0o2770)
    if not os.lstat(root).st_mode & stat.S_ISGID:
        pytest.skip("filesystem does not keep setgid on directories")
    cache = Cache(str(root), require_tmpfs=False)
    cache.ensure(KEY, _build)
    for path in (os.path.join(cache.root, ".locks"), cache.key_path(KEY), cache.tree_path(KEY)):
        assert os.lstat(path).st_mode & stat.S_ISGID, path


def test_second_member_can_use_the_cache_through_mode_bits(tmp_path, umask022):
    """No second uid is available here, so assert the access check the kernel would make for a group member:
    group bits on every path a lessee, a builder or a GC run opens."""
    cache = _private(tmp_path)
    cache.ensure(KEY, _build)
    modes = _modes(cache, KEY)

    def group_can(mode, want):
        return (mode >> 3) & want == want

    assert group_can(modes[".locks"], 0o7)                    # create/list lock files
    assert group_can(modes[f".locks/{KEY}"], 0o6)             # open O_RDWR to flock
    assert group_can(modes["key"], 0o7)                       # open .lease, touch .last-used, rename/remove the key
    assert group_can(modes[LEASE], 0o4) and group_can(modes[LAST_USED], 0o6)   # utime needs write on a foreign file
    assert group_can(modes["COMPLETE"], 0o4) and group_can(modes["tree"], 0o5)  # read the published tree


def test_lock_open_retries_while_a_concurrent_creator_applies_the_group_mode(tmp_path, monkeypatch):
    cache = _root(tmp_path)
    cache.ensure(KEY, _build)  # the lock file exists
    real_open = os.open
    denied = {"left": 3}

    def flaky(path, flags, *args, **kwargs):
        if str(path).endswith(f"/.locks/{KEY}") and not flags & os.O_CREAT and denied["left"]:
            denied["left"] -= 1
            raise PermissionError(errno.EACCES, "mode not applied yet")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", flaky)
    os.close(cache._lock(KEY, fcntl.LOCK_SH, 1))
    assert denied["left"] == 0
    monkeypatch.setattr(cache_module, "_OPEN_RETRIES", 2)
    denied["left"] = 99
    with pytest.raises(PermissionError):
        cache._lock(KEY, fcntl.LOCK_SH, 1)


# ---------------------------------------------------------------- leases

def test_ensure_with_owner_takes_lease_before_gc_can_run(tmp_path):
    cache = _root(tmp_path)
    published = cache.ensure(KEY, _build, owner="lane-1")
    assert published.lease is not None and published.lease.owner == "lane-1"
    assert published.lease.path == _key_file(cache, KEY, LEASE)
    assert [(i.status) for i in cache.gc(0)] == ["leased"]
    published.lease.release()
    assert [(i.status) for i in cache.gc(0)] == ["removed"]


def test_lease_flock_lifecycle_and_context_manager(tmp_path):
    cache = _root(tmp_path)
    cache.ensure(KEY, _build)
    with cache.lease(KEY, "o") as lease:
        lease.heartbeat()
        assert lease.path == _key_file(cache, KEY, LEASE) and lease.tree == cache.tree_path(KEY)
        assert cache.survey()[0]["leased"] is True
        with cache.lease(KEY, "second"):  # two lessees share the lock
            assert cache.gc(0)[0].status == "leased"
    assert cache.survey()[0]["leased"] is False
    lease.release()  # idempotent
    assert os.path.exists(lease.path)  # the lease file is part of the key, never unlinked by release


def test_release_tolerates_a_key_removed_underneath(tmp_path):
    cache = _root(tmp_path)
    cache.ensure(KEY, _build)
    lease = cache.lease(KEY, "o")
    shutil.rmtree(cache.key_path(KEY))
    lease.release()  # touching .last-used fails (key gone): ignored, the descriptor is still closed
    assert lease._descriptor is None


def test_lease_touches_last_used_first(tmp_path):
    now = [5000.0]
    cache = _root(tmp_path, clock=lambda: now[0])
    cache.ensure(KEY, _build)
    now[0] = 9000.0
    cache.lease(KEY, "o").release()
    assert os.stat(_key_file(cache, KEY, LAST_USED)).st_mtime == 9000.0


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


def test_lease_without_a_lease_file_is_key_missing_and_ensure_restores_markers(tmp_path):
    cache = _root(tmp_path)
    cache.ensure(KEY, _build)
    os.unlink(_key_file(cache, KEY, LEASE))
    os.unlink(_key_file(cache, KEY, LAST_USED))
    with pytest.raises(KeyMissing):
        cache.lease(KEY, "o")
    assert not cache.ensure(KEY, lambda tree: pytest.fail("must not rebuild")).built
    assert os.path.isfile(_key_file(cache, KEY, LEASE)) and os.path.isfile(_key_file(cache, KEY, LAST_USED))
    cache.lease(KEY, "o").release()


def test_lease_open_failure_is_typed(tmp_path, monkeypatch):
    cache = _root(tmp_path)
    cache.ensure(KEY, _build)
    real_open = os.open

    def deny(path, flags, *args, **kwargs):
        if str(path).endswith("/" + LEASE):
            raise PermissionError(errno.EACCES, "denied")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", deny)
    with pytest.raises(BackendFailed, match="lease-open"):
        cache.lease(KEY, "o")


def test_lease_revalidates_after_flock_when_the_key_was_removed_in_between(tmp_path, monkeypatch):
    """HOST-CONTRACT item 3: a GC removes the key between the lessee's open and flock; the lessee must notice."""
    cache = _root(tmp_path)
    cache.ensure(KEY, _build)
    real_flock = fcntl.flock
    fired = []

    def racing_flock(descriptor, operation):
        if operation & fcntl.LOCK_SH and not fired and descriptor > 2:
            try:
                if os.readlink(f"/proc/self/fd/{descriptor}").endswith(LEASE):
                    fired.append(1)
                    shutil.rmtree(cache.key_path(KEY))  # the host GC wins the race
            except OSError:
                pass
        return real_flock(descriptor, operation)

    monkeypatch.setattr(cache_module.fcntl, "flock", racing_flock)
    with pytest.raises(KeyMissing):
        cache.lease(KEY, "o")
    assert fired


def test_lease_valid_rejects_unlinked_replaced_and_incomplete(tmp_path):
    cache = _root(tmp_path)
    cache.ensure(KEY, _build)
    path = _key_file(cache, KEY, LEASE)
    descriptor = os.open(path, os.O_RDONLY)
    try:
        assert cache._lease_valid(descriptor, KEY) is True
        os.rename(path, path + ".old")
        assert cache._lease_valid(descriptor, KEY) is False          # path no longer resolves
        open(path, "w").close()
        assert cache._lease_valid(descriptor, KEY) is False          # same path, different inode
        os.unlink(path)
        os.rename(path + ".old", path)
        os.unlink(_key_file(cache, KEY, "COMPLETE"))
        assert cache._lease_valid(descriptor, KEY) is False          # COMPLETE gone
    finally:
        os.close(descriptor)


def test_lease_retries_while_a_gc_pass_holds_it_exclusively_then_times_out(tmp_path):
    cache = _root(tmp_path)
    cache.ensure(KEY, _build)
    holder = os.open(_key_file(cache, KEY, LEASE), os.O_RDONLY)
    fcntl.flock(holder, fcntl.LOCK_EX)
    try:
        with pytest.raises(CacheBusy, match="exclusively"):
            cache.lease(KEY, "o", lock_timeout=0.1)
    finally:
        os.close(holder)
    cache.lease(KEY, "o").release()


def _lessee_child(root, key):
    code = (
        "import sys; from cowtree.cache import Cache; "
        f"c = Cache({root!r}, require_tmpfs=False); l = c.lease({key!r}, 'child'); "
        "print('ready', flush=True); sys.stdin.read()"
    )
    return subprocess.Popen(
        [sys.executable, "-c", code], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
        env={**os.environ, "PYTHONPATH": str(Path(cache_module.__file__).parents[1])},
    )


def test_gc_honours_a_live_lessee_in_another_process_and_reclaims_after_it_dies(tmp_path):
    cache = _root(tmp_path)
    cache.ensure(KEY, _build)
    child = _lessee_child(cache.root, KEY)
    try:
        assert child.stdout.readline().strip() == "ready"
        assert [(i.name, i.status) for i in cache.gc(0)] == [(KEY, "leased")]
        child.kill()  # a crashed lessee: the kernel drops its flock, nothing is left to prune
        child.wait()
        assert [(i.name, i.status) for i in cache.gc(0)] == [(KEY, "removed")]
    finally:
        child.kill()
        child.wait()


# ---------------------------------------------------------------- interop with the host GC (HOST-CONTRACT item 4)

def _reference_gc(root: str, idle: float, now: float) -> list[str]:
    """The contract's host GC, written from HOST-CONTRACT.md item 4 alone (idle, LOCK_EX|LOCK_NB on <key>/.lease, ...)."""
    removed = []
    for name in sorted(os.listdir(root)):
        path = os.path.join(root, name)
        if name.startswith(".") or ".tmp-" in name or not os.path.exists(os.path.join(path, "COMPLETE")):
            continue
        try:
            used = os.stat(os.path.join(path, LAST_USED)).st_mtime
        except FileNotFoundError:
            used = os.stat(os.path.join(path, "COMPLETE")).st_mtime
        if now - used < idle:
            continue
        try:
            fd = os.open(os.path.join(path, LEASE), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            fd = None
        try:
            if fd is not None:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    continue
            shutil.rmtree(path)
            removed.append(name)
        finally:
            if fd is not None:
                os.close(fd)
    return removed


def test_contract_reference_gc_keeps_a_key_with_a_live_cowtree_lessee(tmp_path):
    cache = _root(tmp_path)
    cache.ensure(KEY, _build)
    cache.ensure(KEY2, _build)
    later = time.time() + 10_000
    with cache.lease(KEY, "lane"):
        assert _reference_gc(cache.root, 3600, later) == [KEY2]
        assert os.path.isdir(cache.key_path(KEY)) and cache.verify(KEY)
    assert _reference_gc(cache.root, 3600, later) == [KEY]


def _load_host_gc():
    candidates = [os.environ.get("COWTREE_TEST_MDT_TREE_CACHE", "")]
    candidates.append(str(Path(__file__).resolve().parents[3] / "mdt-cockpit" / "host-setup" / "mdt_tree_cache.py"))
    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            spec = importlib.util.spec_from_file_location("mdt_tree_cache_under_test", candidate)
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module  # dataclasses in the module resolve their annotations through it
            spec.loader.exec_module(module)
            return module
    return None


@pytest.fixture(scope="module")
def host_gc():
    module = _load_host_gc()
    if module is None:
        pytest.skip("mdt-cockpit/host-setup/mdt_tree_cache.py is not part of this checkout")
    return module


def test_shipped_host_gc_keeps_a_key_with_a_live_cowtree_lessee(tmp_path, host_gc):
    cache = _root(tmp_path)
    cache.ensure(KEY, _build)
    cache.ensure(KEY2, _build)
    later = time.time() + 10_000
    with cache.lease(KEY, "lane"):
        log = host_gc.gc_cache(Path(cache.root), 3600, "", later)
        assert any(line.startswith(f"keep {KEY}: leased") for line in log), log
        assert any(line.startswith(f"removed {KEY2}") for line in log), log
        assert os.path.isdir(cache.key_path(KEY)) and cache.verify(KEY)
        assert not os.path.exists(cache.key_path(KEY2))
    log = host_gc.gc_cache(Path(cache.root), 3600, "", later)
    assert any(line.startswith(f"removed {KEY}") for line in log), log


def test_shipped_host_gc_respects_a_lessee_in_another_process(tmp_path, host_gc):
    cache = _root(tmp_path)
    cache.ensure(KEY, _build)
    child = _lessee_child(cache.root, KEY)
    try:
        assert child.stdout.readline().strip() == "ready"
        log = host_gc.gc_cache(Path(cache.root), 0, "", time.time() + 10)
        assert any(line.startswith(f"keep {KEY}: leased") for line in log), log
    finally:
        child.kill()
        child.wait()
    assert os.path.isdir(cache.key_path(KEY))


def test_cowtree_gc_and_lessee_cope_with_the_shipped_host_gc_removing_a_key(tmp_path, host_gc):
    cache = _root(tmp_path)
    cache.ensure(KEY, _build)
    host_gc.gc_cache(Path(cache.root), 0, "", time.time() + 10)
    assert not os.path.exists(cache.key_path(KEY))
    with pytest.raises(KeyMissing):
        cache.lease(KEY, "o")
    assert cache.ensure(KEY, _build, owner="lane").built  # and a republish after the host removed it works


def test_shipped_host_gc_leaves_a_cowtree_root_layout_alone(tmp_path, host_gc):
    cache = _root(tmp_path)
    cache.ensure(KEY, _build, owner="lane")
    log = host_gc.gc_cache(Path(cache.root), 0, "", time.time() + 10)
    assert sorted(os.listdir(cache.root)) == [".locks", KEY]
    assert all("ERROR" not in line for line in log), log


# ---------------------------------------------------------------- gc policy

def test_gc_policy_idle_mount_busy_foreign_scratch(tmp_path):
    now = [1000.0]
    cache = _root(tmp_path, clock=lambda: now[0])
    cache.ensure(KEY, _build)
    cache.ensure(KEY2, _build)
    # recent use: kept
    assert {i.name: i.status for i in cache.gc(60)} == {KEY: "kept", KEY2: "kept"}
    now[0] += 120
    # mounted: KEY is held as an overlay lowerdir, KEY2 is free
    held = _mountinfo(cache.root, f"3 1 0:60 / /work/w0 rw - overlay overlay rw,lowerdir={cache.tree_path(KEY)},upperdir=/u\n")
    cache._mountinfo = cache._all_mountinfo = lambda: held
    statuses = {i.name: i.status for i in cache.gc(60)}
    assert statuses == {KEY: "mounted", KEY2: "removed"}
    # busy key, foreign file, scratch entries, an incomplete key directory
    descriptor = cache._lock(KEY, fcntl.LOCK_EX, 0)
    open(os.path.join(cache.root, "README"), "w").close()
    os.mkdir(os.path.join(cache.root, "zz" * 8 + ".tmp-0123456789ab"))
    os.mkdir(os.path.join(cache.root, "bad.key.tmp-0123456789ab"))
    os.mkdir(os.path.join(cache.root, KEY2))  # no COMPLETE
    cache._mountinfo = cache._all_mountinfo = lambda: _mountinfo(cache.root)
    statuses = {i.name: i.status for i in cache.gc(60)}
    os.close(descriptor)
    assert statuses[KEY] == "busy" and statuses["README"] == "foreign" and statuses["bad.key.tmp-0123456789ab"] == "foreign"
    assert statuses["zz" * 8 + ".tmp-0123456789ab"] == "removed"
    assert statuses[KEY2] == "removed"          # incomplete key dir removed
    assert ".locks" not in statuses


def test_gc_sees_a_bind_in_another_mount_namespace(tmp_path):
    cache = _root(tmp_path)
    cache.ensure(KEY, _build)
    own = _mountinfo(cache.root)
    other = f"77 1 0:50 /{KEY}/tree /container/tpl ro - tmpfs tmpfs rw\n"  # same tmpfs, bind seen only in another namespace
    cache._mountinfo = lambda: own
    cache._all_mountinfo = lambda: own + other
    assert [(i.status, i.detail) for i in cache.gc(0)] == [("mounted", "/container/tpl")]
    cache._all_mountinfo = lambda: own
    assert [i.status for i in cache.gc(0)] == ["removed"]


def test_gc_keeps_a_key_when_some_process_mountinfo_cannot_be_read(tmp_path):
    cache = _root(tmp_path)
    cache.ensure(KEY, _build)

    def unreadable():
        raise RootInvalid("cannot read /proc/42/mountinfo")

    cache._all_mountinfo = unreadable
    items = cache.gc(0)
    assert [(i.status) for i in items] == ["kept"] and "cannot judge mounts" in items[0].detail
    assert os.path.isdir(cache.key_path(KEY))


def test_gc_keeps_a_key_whose_lease_is_not_a_regular_file(tmp_path):
    cache = _root(tmp_path)
    cache.ensure(KEY, _build)
    lease = _key_file(cache, KEY, LEASE)
    os.unlink(lease)
    os.symlink("/dev/null", lease)
    items = cache.gc(0)
    assert [i.status for i in items] == ["kept"] and "regular file" in items[0].detail
    assert cache.survey()[0]["leased"] is None


def test_gc_removes_a_key_without_a_lease_file_like_the_host_gc(tmp_path):
    cache = _root(tmp_path)
    cache.ensure(KEY, _build)
    os.unlink(_key_file(cache, KEY, LEASE))
    assert [i.status for i in cache.gc(0)] == ["removed"]


def test_probe_lease_edges(tmp_path, monkeypatch):
    cache = _root(tmp_path)
    cache.ensure(KEY, _build)
    real_open = os.open
    monkeypatch.setattr(
        os, "open",
        lambda path, flags, *a, **k: (_ for _ in ()).throw(PermissionError(errno.EACCES, "no"))
        if str(path).endswith("/" + LEASE) else real_open(path, flags, *a, **k),
    )
    assert cache._probe_lease(KEY) == ("invalid", None)
    monkeypatch.setattr(os, "open", real_open)
    real_fstat = os.fstat
    lease = str(_key_file(cache, KEY, LEASE).resolve())

    def nonregular_lease(fd):
        info = real_fstat(fd)
        try:
            opened = os.path.realpath(os.readlink(f"/proc/self/fd/{fd}"))
        except OSError:
            return info
        if opened != lease:
            return info
        return os.stat_result((stat.S_IFIFO | 0o600,) + (0,) * 9)

    monkeypatch.setattr(cache_module.os, "fstat", nonregular_lease)
    assert cache._probe_lease(KEY) == ("invalid", None)
    monkeypatch.setattr(cache_module.os, "fstat", real_fstat)
    real_flock = fcntl.flock

    def broken(descriptor, operation):
        raise OSError(errno.EIO, "io")

    monkeypatch.setattr(cache_module.fcntl, "flock", broken)
    assert cache._probe_lease(KEY) == ("invalid", None)
    monkeypatch.setattr(cache_module.fcntl, "flock", real_flock)
    state, fd = cache._probe_lease(KEY)
    assert state == "free"
    os.close(fd)


def test_gc_removes_incomplete_key(tmp_path):
    cache = _root(tmp_path)
    os.mkdir(cache.key_path(KEY))
    assert [(i.name, i.status) for i in cache.gc(0)] == [(KEY, "removed")]


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
    os.unlink(_key_file(cache, KEY, LAST_USED))
    assert cache._idle_seconds(KEY) > 900
    cache.touch_last_used(KEY)  # recreates the marker
    assert cache._idle_seconds(KEY) == 0
    assert stat.S_IMODE(os.stat(_key_file(cache, KEY, LAST_USED)).st_mode) == 0o660


def test_touch_edges(tmp_path, monkeypatch):
    cache = _root(tmp_path)
    with pytest.raises(KeyMissing):
        cache.touch_last_used(KEY)  # the key directory does not exist
    cache.ensure(KEY, _build)
    os.unlink(_key_file(cache, KEY, LAST_USED))
    real_create = cache._create_file

    def lost_race(path):
        open(path, "w").close()  # another process created it first
        return real_create(path)

    monkeypatch.setattr(cache, "_create_file", lost_race)
    cache.touch_last_used(KEY)
    assert os.path.exists(_key_file(cache, KEY, LAST_USED))


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
    assert rows[KEY]["leased"] is False
    assert rows[KEY2]["kind"] == "incomplete" and rows[KEY2]["idle_seconds"] is None and rows[KEY2]["leased"] is None
    assert rows[KEY2 + ".tmp-0123456789ab"]["kind"] == "scratch"
    assert rows["README"]["kind"] == "foreign"
    assert ".locks" not in rows


# ---------------------------------------------------------------- mountinfo

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


def test_mount_holders_scans_the_holder_text_and_dedupes():
    key = "/srv/tc/" + KEY
    own = "1 0 8:1 / / rw - ext4 /dev/sda rw\n2 1 0:50 / /srv/tc rw - tmpfs tmpfs rw"
    other = f"9 1 0:50 /{KEY} /c/one ro - tmpfs tmpfs rw\n9 1 0:50 /{KEY} /c/one ro - tmpfs tmpfs rw"
    assert mount_holders(key, own, own + "\n" + other) == ["/c/one"]
    assert mount_holders(key, own) == []


def test_default_mountinfo_reader_reads_proc():
    assert " - " in cache_module._read_mountinfo()


def test_all_process_mountinfo_reader_scans_every_process():
    text = cache_module._read_all_mountinfo()
    assert " - " in text and len(text.splitlines()) >= len(cache_module._read_mountinfo().splitlines())


def _fake_proc(tmp_path):
    proc = tmp_path / "proc"
    proc.mkdir()
    return proc


def test_all_process_mountinfo_reader_skips_gone_and_zombie_but_fails_on_unreadable(tmp_path, monkeypatch):
    proc = _fake_proc(tmp_path)
    (proc / "1").mkdir()
    (proc / "1" / "mountinfo").write_text("one\n")
    (proc / "self").mkdir()                       # not a pid: ignored
    (proc / "2").symlink_to(proc / "missing")     # vanished between listdir and open
    (proc / "3").mkdir()
    (proc / "3" / "mountinfo").mkdir()            # unreadable, but the process is a zombie
    (proc / "3" / "stat").write_text("3 (x y) Z 1 1 1\n")
    assert cache_module._read_all_mountinfo(str(proc)) == "one\n"
    (proc / "4").mkdir()
    (proc / "4" / "mountinfo").mkdir()            # unreadable and alive: never guess
    (proc / "4" / "stat").write_text("4 (x) S 1 1 1\n")
    monkeypatch.setattr(cache_module.time, "sleep", lambda _s: None)
    with pytest.raises(RootInvalid, match="cannot read"):
        cache_module._read_all_mountinfo(str(proc))
    (proc / "4" / "stat").unlink()                # no stat either: cannot call it gone
    with pytest.raises(RootInvalid, match="cannot read"):
        cache_module._read_all_mountinfo(str(proc))
    with pytest.raises(RootInvalid, match="cannot list"):
        cache_module._read_all_mountinfo(str(tmp_path / "nowhere"))


def test_default_cache_scans_all_processes_but_a_custom_table_is_used_for_both(tmp_path):
    root = tmp_path / "cache"
    root.mkdir(mode=0o770)
    assert Cache(str(root), require_tmpfs=False)._all_mountinfo is cache_module._read_all_mountinfo
    table = lambda: ""  # noqa: E731
    assert Cache(str(root), require_tmpfs=False, mountinfo=table)._all_mountinfo is table


# ---------------------------------------------------------------- a held lease protects a key whatever its COMPLETE says

def _corrupt_complete(cache, how):
    marker = _key_file(cache, KEY, "COMPLETE")
    if how == "empty-object":
        with open(marker, "w") as handle:
            handle.write("{}")
    elif how == "missing":
        os.unlink(marker)
    else:  # corrupt tree under a valid COMPLETE is not an "unpublished" key; use garbage instead
        with open(marker, "w") as handle:
            handle.write("not json")


@pytest.mark.parametrize("how", ["empty-object", "missing", "garbage"])
def test_gc_and_ensure_never_remove_a_leased_key_with_invalid_complete(tmp_path, how):
    cache = _root(tmp_path)
    cache.ensure(KEY, _build)
    lease = cache.lease(KEY, "lane")
    _corrupt_complete(cache, how)
    assert [(i.name, i.status) for i in cache.gc(0)] == [(KEY, "leased")]
    with pytest.raises(CacheBusy, match="live lessee"):
        cache.ensure(KEY, _build)
    assert os.path.isdir(cache.tree_path(KEY))
    lease.release()
    assert [(i.name, i.status) for i in cache.gc(0)] == [(KEY, "removed")]


def test_ensure_rebuilds_an_unleased_key_with_invalid_complete_and_keeps_a_corrupt_leased_tree(tmp_path):
    cache = _root(tmp_path)
    cache.ensure(KEY, _build)
    _corrupt_complete(cache, "empty-object")
    assert cache.ensure(KEY, _build).built
    with cache.lease(KEY, "lane"):
        with open(os.path.join(cache.tree_path(KEY), "pkg", "mod.py"), "a") as handle:
            handle.write("corrupt\n")  # corrupt tree/ under a valid COMPLETE: a leased key is neither rebuilt nor removed
        assert cache.gc(0)[0].status == "leased"
        assert not cache.ensure(KEY, lambda tree: pytest.fail("must not rebuild")).built
    with pytest.raises(TreeInvalid):
        cache.verify(KEY)


def test_ensure_refuses_to_replace_a_key_with_an_unusable_lease(tmp_path):
    cache = _root(tmp_path)
    cache.ensure(KEY, _build)
    _corrupt_complete(cache, "missing")
    os.unlink(_key_file(cache, KEY, LEASE))
    os.symlink("/dev/null", _key_file(cache, KEY, LEASE))
    with pytest.raises(CacheBusy, match="unusable"):
        cache.ensure(KEY, _build)
    assert os.path.isdir(cache.tree_path(KEY))


def test_shipped_host_gc_and_cowtree_agree_on_a_leased_key_with_invalid_complete(tmp_path, host_gc):
    cache = _root(tmp_path)
    cache.ensure(KEY, _build)
    with cache.lease(KEY, "lane"):
        _corrupt_complete(cache, "empty-object")
        log = host_gc.gc_cache(Path(cache.root), 0, "", time.time() + 10)
        assert os.path.isdir(cache.key_path(KEY)), log
        assert [i.status for i in cache.gc(0)] == ["leased"]
