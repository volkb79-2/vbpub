"""Tests for the daemon's read-only, SO_PEERCRED-bound resource observer."""

from __future__ import annotations

import errno
from pathlib import Path

import pytest

from lib import resource_observer


GATES = "/dev.slice/dev-gates.slice"
LEAF = f"{GATES}/run-gate-test.scope"
PIDS_EVENTS = "max 0\n"
MEMORY_EVENTS = "max 0\noom 0\noom_kill 0\noom_group_kill 0\n"


def _stat(pid: int, start: int = 123) -> str:
    fields = ["S", *(["0"] * 19)]
    fields[19] = str(start)
    return f"{pid} (assay worker) " + " ".join(fields) + "\n"


def _status(
    native_tid: int, *, host_tid: int, uid: int = 4242, caps: int = 0
) -> str:
    return (
        f"Name:\tpython\nUid:\t{uid} {uid} {uid} {uid}\n"
        "Gid:\t4242 4242 4242 4242\nGroups:\t3000\n"
        f"NSpid:\t{host_tid} {native_tid}\n"
        f"CapEff:\t{caps:016x}\nCapPrm:\t{caps:016x}\n"
    )


def _fixture(
    tmp_path: Path,
    *,
    cgroup: str = LEAF,
    proc_cgroup: str | None = None,
    mount_root: str = "/",
    peer_uid: int = 4242,
):
    proc_root = tmp_path / "proc"
    peer = proc_root / "4242"
    (peer / "task" / "4242").mkdir(parents=True)
    (peer / "task" / "4250").mkdir()
    (peer / "stat").write_text(_stat(4242), encoding="ascii")
    for host_tid, native_tid in ((4242, 42), (4250, 50)):
        task = peer / "task" / str(host_tid)
        (task / "status").write_text(
            _status(native_tid, host_tid=host_tid, uid=peer_uid), encoding="ascii"
        )
        (task / "cgroup").write_text(
            f"0::{proc_cgroup or cgroup}\n", encoding="ascii"
        )

    cgroup_root = tmp_path / "sys-fs-cgroup"
    paths = ("/", "/dev.slice", GATES, LEAF)
    for cgroup_path in paths:
        directory = cgroup_root.joinpath(*Path(cgroup_path).parts[1:])
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "cgroup.procs").write_text("", encoding="ascii")
        (directory / "pids.max").write_text("max\n", encoding="ascii")
        (directory / "memory.max").write_text("max\n", encoding="ascii")
        (directory / "pids.events").write_text(PIDS_EVENTS, encoding="ascii")
        (directory / "memory.events").write_text(MEMORY_EVENTS, encoding="ascii")
        # The synthetic requester is neither the fixture owner nor in its
        # group, so 0644 proves it cannot write cgroup.procs.
        (directory / "cgroup.procs").chmod(0o644)

    mountinfo = proc_root / "self" / "mountinfo"
    mountinfo.parent.mkdir(parents=True)
    mountinfo.write_text(
        f"29 22 0:25 {mount_root} {cgroup_root} rw,nosuid,nodev - cgroup2 cgroup rw\n",
        encoding="ascii",
    )
    return {
        "proc_root": str(proc_root),
        "cgroup_root": str(cgroup_root),
        "mountinfo_path": str(mountinfo),
        "peer_pid": 4242,
        "peer_uid": peer_uid,
        "thread_id": 50,
        "gates_slice_name": "dev-gates.slice",
    }


def _observe(kwargs):
    return resource_observer.observe_peer_cgroup(**kwargs)


def test_observation_returns_only_peer_ancestry_and_no_process_ids(tmp_path):
    result = _observe(_fixture(tmp_path))

    assert result["cgroup_version"] == 2
    assert result["thread_id"] == 50
    assert result["gates_slice"] == GATES
    assert [entry["path"] for entry in result["cgroups"]] == [
        LEAF,
        GATES,
        "/dev.slice",
        "/",
    ]
    assert all(entry["mount_id"] == 29 for entry in result["cgroups"])
    assert all(entry["pids_events"] == {"max": 0} for entry in result["cgroups"])
    assert all(set(entry) == {
        "path", "mount_id", "pids_max_present", "pids_max",
        "memory_max_present", "memory_max", "pids_events", "memory_events",
    } for entry in result["cgroups"])
    assert "peer_pid" not in result and "host_tid" not in result


def test_private_daemon_namespace_maps_peer_path_to_full_host_hierarchy(tmp_path):
    kwargs = _fixture(
        tmp_path,
        cgroup=LEAF,
        proc_cgroup=f"/../../{LEAF.lstrip('/')}",
        mount_root="/../..",
    )

    result = _observe(kwargs)

    assert [entry["path"] for entry in result["cgroups"]] == [
        LEAF,
        GATES,
        "/dev.slice",
        "/",
    ]


@pytest.mark.parametrize(
    ("proc_cgroup", "mount_root"),
    [
        (f"/../../{LEAF.lstrip('/')}", "/"),
        (f"/{LEAF.lstrip('/')}", "/../.."),
        (f"/../../other.slice/{LEAF.rsplit('/', 1)[-1]}", "/../.."),
    ],
)
def test_private_daemon_namespace_refuses_unmatched_path_mapping(
    tmp_path, proc_cgroup, mount_root
):
    kwargs = _fixture(
        tmp_path, cgroup=LEAF, proc_cgroup=proc_cgroup, mount_root=mount_root
    )
    with pytest.raises(resource_observer.ResourceObserverError):
        _observe(kwargs)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("peer_pid", None, "host-visible peer PID"),
        ("peer_pid", 0, "host-visible peer PID"),
        ("peer_pid", 1.5, "host-visible peer PID"),
        ("peer_uid", None, "valid peer UID"),
        ("peer_uid", -1, "valid peer UID"),
        ("peer_uid", 1.5, "valid peer UID"),
        ("thread_id", True, "positive native TID"),
        ("thread_id", 0, "positive native TID"),
    ],
)
def test_observation_rejects_missing_or_invalid_identity(tmp_path, field, value, message):
    kwargs = _fixture(tmp_path)
    kwargs[field] = value
    with pytest.raises(resource_observer.ResourceObserverError, match=message):
        _observe(kwargs)


def test_observation_accepts_peer_uid_zero(tmp_path):
    result = _observe(_fixture(tmp_path, peer_uid=0))

    assert result["thread_id"] == 50


def test_observation_rejects_relative_configured_cgroup_root(tmp_path):
    kwargs = _fixture(tmp_path)
    kwargs["cgroup_root"] = "relative/cgroup"
    with pytest.raises(resource_observer.ResourceObserverError, match="root is not absolute"):
        _observe(kwargs)


def test_observation_rejects_native_tid_not_owned_by_peer(tmp_path):
    kwargs = _fixture(tmp_path)
    kwargs["thread_id"] = 51
    with pytest.raises(resource_observer.ResourceObserverError, match="not a unique thread"):
        _observe(kwargs)


def test_observation_rejects_peer_threads_in_distinct_cgroups(tmp_path):
    kwargs = _fixture(tmp_path)
    task = Path(kwargs["proc_root"]) / "4242/task/4242/cgroup"
    task.write_text("0::/dev.slice/dev-gates.slice/other.scope\n", encoding="ascii")
    with pytest.raises(resource_observer.ResourceObserverError, match="span multiple cgroups"):
        _observe(kwargs)


@pytest.mark.parametrize(
    "outside",
    [
        "/dev.slice/dev-background.slice/run-gate-test.scope",
        "/dev.slice/dev-gates.slice-typo/run-gate-test.scope",
        GATES,
    ],
)
def test_observation_rejects_cgroups_outside_the_gates_slice(tmp_path, outside):
    with pytest.raises(resource_observer.ResourceObserverError, match="descendant of the configured gates slice"):
        _observe(_fixture(tmp_path, cgroup=outside))


@pytest.mark.parametrize("caps", [1 << 1, 1 << 21])
def test_observation_rejects_peer_capabilities_that_bypass_dac(tmp_path, caps):
    kwargs = _fixture(tmp_path)
    status = Path(kwargs["proc_root"]) / "4242/task/4250/status"
    status.write_text(_status(50, host_tid=4250, caps=caps), encoding="ascii")
    with pytest.raises(resource_observer.ResourceObserverError, match="capabilities can bypass"):
        _observe(kwargs)


@pytest.mark.parametrize("field", ["CapEff", "CapPrm"])
@pytest.mark.parametrize("caps", [1 << 1, 1 << 21])
def test_observation_rejects_bypass_capability_in_either_mask(tmp_path, field, caps):
    kwargs = _fixture(tmp_path)
    status = Path(kwargs["proc_root"]) / "4242/task/4250/status"
    lines = _status(50, host_tid=4250, caps=0).splitlines()
    target = next(index for index, line in enumerate(lines) if line.startswith(f"{field}:"))
    lines[target] = f"{field}:\t{caps:016x}"
    status.write_text("\n".join(lines) + "\n", encoding="ascii")

    with pytest.raises(resource_observer.ResourceObserverError, match="capabilities can bypass"):
        _observe(kwargs)


def test_observation_rejects_writable_cgroup_procs(tmp_path):
    kwargs = _fixture(tmp_path)
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(resource_observer, "_mode_grants_write", lambda *_args: True)
    with pytest.raises(resource_observer.ResourceObserverError, match="can write cgroup.procs"):
        _observe(kwargs)
    monkeypatch.undo()


def test_observation_rejects_missing_cgroup_procs_and_acl(tmp_path, monkeypatch):
    kwargs = _fixture(tmp_path)
    leaf_procs = Path(kwargs["cgroup_root"]) / LEAF.lstrip("/") / "cgroup.procs"
    leaf_procs.unlink()
    with pytest.raises(resource_observer.ResourceObserverError, match="cgroup.procs is unavailable"):
        _observe(kwargs)

    leaf_procs.write_text("", encoding="ascii")
    monkeypatch.setattr(resource_observer, "_has_access_acl", lambda _path: True)
    with pytest.raises(resource_observer.ResourceObserverError, match="cannot prove cgroup.procs DAC"):
        _observe(kwargs)


def test_mount_identity_requires_one_full_root_mount_and_no_nested_overmount(tmp_path):
    kwargs = _fixture(tmp_path)
    mountinfo = Path(kwargs["mountinfo_path"])
    cgroup_root = Path(kwargs["cgroup_root"])
    mountinfo.write_text(
        f"29 22 0:25 /dev.slice {cgroup_root} rw - cgroup2 cgroup rw\n",
        encoding="ascii",
    )
    with pytest.raises(resource_observer.ResourceObserverError, match="does not expose the hierarchy root"):
        _observe(kwargs)

    mountinfo.write_text(
        f"29 22 0:25 / {cgroup_root} rw - cgroup2 cgroup rw\n"
        f"30 29 0:26 / {cgroup_root / 'dev.slice'} rw - tmpfs tmpfs rw\n",
        encoding="ascii",
    )
    with pytest.raises(resource_observer.ResourceObserverError, match="nested mount"):
        _observe(kwargs)


def test_mount_identity_rejects_bad_mountinfo_and_mount_id(tmp_path):
    kwargs = _fixture(tmp_path)
    mountinfo = Path(kwargs["mountinfo_path"])
    mountinfo.write_text("not mountinfo\n", encoding="ascii")
    with pytest.raises(resource_observer.ResourceObserverError, match="malformed record"):
        _observe(kwargs)

    mountinfo.write_text(
        f"bad 22 0:25 / {kwargs['cgroup_root']} rw - cgroup2 cgroup rw\n",
        encoding="ascii",
    )
    with pytest.raises(resource_observer.ResourceObserverError, match="mount ID is malformed"):
        _observe(kwargs)


def test_observation_rejects_bad_peer_proc_facts(tmp_path):
    kwargs = _fixture(tmp_path)
    status = Path(kwargs["proc_root"]) / "4242/task/4250/status"
    status.write_text(_status(50, host_tid=4250, uid=999), encoding="ascii")
    with pytest.raises(resource_observer.ResourceObserverError, match="UID changed"):
        _observe(kwargs)

    status.write_text("Name:\tpython\n", encoding="ascii")
    with pytest.raises(resource_observer.ResourceObserverError, match="omits required"):
        _observe(kwargs)


@pytest.mark.parametrize(
    ("filename", "contents", "message"),
    [
        ("pids.events", "max 0\nmax 1\n", "malformed event record"),
        ("pids.events", "max nope\n", "malformed event value"),
        ("pids.events", "max -1\n", "negative event value"),
        ("pids.events", "fork 1\n", "omits required event counters"),
    ],
)
def test_observation_rejects_malformed_event_files(tmp_path, filename, contents, message):
    kwargs = _fixture(tmp_path)
    path = Path(kwargs["cgroup_root"]) / LEAF.lstrip("/") / filename
    path.write_text(contents, encoding="ascii")
    with pytest.raises(resource_observer.ResourceObserverError, match=message):
        _observe(kwargs)


@pytest.mark.parametrize(
    ("filename", "contents", "message"),
    [
        ("pids.max", "max 1\n", "pids.max is malformed"),
        ("memory.max", "-1\n", "memory.max is malformed"),
    ],
)
def test_observation_rejects_malformed_limits(tmp_path, filename, contents, message):
    kwargs = _fixture(tmp_path)
    path = Path(kwargs["cgroup_root"]) / LEAF.lstrip("/") / filename
    path.write_text(contents, encoding="ascii")
    with pytest.raises(resource_observer.ResourceObserverError, match=message):
        _observe(kwargs)


def test_finite_limit_requires_its_event_file(tmp_path):
    kwargs = _fixture(tmp_path)
    directory = Path(kwargs["cgroup_root"]) / LEAF.lstrip("/")
    (directory / "memory.max").write_text("1024\n", encoding="ascii")
    (directory / "memory.events").unlink()
    with pytest.raises(resource_observer.ResourceObserverError, match="memory.max is present"):
        _observe(kwargs)


def test_absent_limit_and_events_are_reported_as_absent(tmp_path):
    kwargs = _fixture(tmp_path)
    directory = Path(kwargs["cgroup_root"]) / LEAF.lstrip("/")
    for filename in ("pids.max", "memory.max", "pids.events", "memory.events"):
        (directory / filename).unlink()
    entry = _observe(kwargs)["cgroups"][0]
    assert entry["pids_max_present"] is False and entry["pids_events"] is None
    assert entry["memory_max_present"] is False and entry["memory_events"] is None


def test_helper_parsers_and_mode_branches(tmp_path, monkeypatch):
    path = tmp_path / "counter"
    path.write_text("max\n", encoding="ascii")
    assert resource_observer._read_limit(path) == (True, None)
    path.unlink()
    assert resource_observer._read_limit(path) == (False, None)
    path.write_text("7\n", encoding="ascii")
    assert resource_observer._read_limit(path) == (True, 7)

    events = tmp_path / "events"
    events.write_text("max 2\nextra 99\n", encoding="ascii")
    assert resource_observer._read_events(events, frozenset({"max"})) == {"max": 2}
    events.unlink()
    assert resource_observer._read_events(events, frozenset({"max"})) is None

    assert resource_observer._mode_grants_write(0o200, 1, 2, 1, 3, ())
    assert resource_observer._mode_grants_write(0o020, 1, 2, 3, 2, ())
    assert resource_observer._mode_grants_write(0o002, 1, 2, 3, 3, ())
    assert resource_observer._mode_grants_write(0o020, 1, 2, 3, 3, (2,))
    assert not resource_observer._mode_grants_write(0o644, 1, 2, 3, 3, ())

    monkeypatch.setattr(resource_observer.os, "getxattr", lambda *_args: (_ for _ in ()).throw(OSError(errno.ENODATA, "none")))
    assert resource_observer._has_access_acl(path) is False
    monkeypatch.setattr(resource_observer.os, "getxattr", lambda *_args: b"acl")
    assert resource_observer._has_access_acl(path) is True
    monkeypatch.setattr(resource_observer.os, "getxattr", lambda *_args: (_ for _ in ()).throw(OSError(errno.EPERM, "denied")))
    with pytest.raises(resource_observer.ResourceObserverError, match="cannot inspect ACL"):
        resource_observer._has_access_acl(path)


def test_private_path_mapping_rejects_noncanonical_roots_and_components():
    with pytest.raises(resource_observer.ResourceObserverError, match="not canonical"):
        resource_observer._host_cgroup_path("//../../dev.slice/x", 2)
    with pytest.raises(resource_observer.ResourceObserverError, match="not canonical"):
        resource_observer._host_cgroup_path("/dev.slice/../x", 0)
    with pytest.raises(resource_observer.ResourceObserverError, match="not canonical"):
        resource_observer._host_cgroup_path("/../../dev.slice/../x", 2)


@pytest.mark.parametrize("new_fact", ["cgroup", "start_ticks"])
def test_observation_rechecks_peer_identity_after_sampling(tmp_path, monkeypatch, new_fact):
    kwargs = _fixture(tmp_path)
    resolve = resource_observer._resolve_peer
    calls = 0

    def changed(*args):
        nonlocal calls
        result = resolve(*args)
        calls += 1
        if calls == 2:
            if new_fact == "cgroup":
                result["cgroup"] = "/dev.slice/dev-gates.slice/changed.scope"
            else:
                result["start_ticks"] += 1
        return result

    monkeypatch.setattr(resource_observer, "_resolve_peer", changed)
    with pytest.raises(resource_observer.ResourceObserverError, match="identity changed during observation"):
        _observe(kwargs)


def test_peer_thread_enumeration_refuses_errors_and_empty_sets(tmp_path, monkeypatch):
    kwargs = _fixture(tmp_path)
    task_root = Path(kwargs["proc_root"]) / "4242/task"
    iter_dir = Path.iterdir

    def denied(path):
        if path == task_root:
            raise PermissionError("hidden")
        return iter_dir(path)

    monkeypatch.setattr(Path, "iterdir", denied)
    with pytest.raises(resource_observer.ResourceObserverError, match="cannot enumerate"):
        _observe(kwargs)

    monkeypatch.setattr(Path, "iterdir", iter_dir)
    for task in task_root.iterdir():
        if task.name.isdecimal():
            for child in task.iterdir():
                child.unlink()
            task.rmdir()
    with pytest.raises(resource_observer.ResourceObserverError, match="no visible live threads"):
        _observe(kwargs)


def test_peer_start_time_race_refuses(tmp_path, monkeypatch):
    kwargs = _fixture(tmp_path)
    start_ticks = resource_observer._proc_start_ticks
    calls = 0

    def changed_start(path):
        nonlocal calls
        result = start_ticks(path)
        calls += 1
        return result + 1 if calls == 2 else result

    monkeypatch.setattr(resource_observer, "_proc_start_ticks", changed_start)
    with pytest.raises(resource_observer.ResourceObserverError, match="PID changed during identity"):
        _observe(kwargs)


def test_peer_threads_must_share_uid_filesystem_credentials_and_tid_mapping(tmp_path):
    kwargs = _fixture(tmp_path)
    selected = Path(kwargs["proc_root"]) / "4242/task/4250/status"
    other = Path(kwargs["proc_root"]) / "4242/task/4242/status"
    other.write_text(_status(42, host_tid=4242, uid=999), encoding="ascii")
    with pytest.raises(resource_observer.ResourceObserverError, match="do not share the socket peer UID"):
        _observe(kwargs)

    other.write_text(
        _status(42, host_tid=4242).replace("Gid:\t4242 4242 4242 4242", "Gid:\t4242 4242 4242 4444"),
        encoding="ascii",
    )
    with pytest.raises(resource_observer.ResourceObserverError, match="different filesystem credentials"):
        _observe(kwargs)

    selected.write_text(_status(50, host_tid=4242), encoding="ascii")
    with pytest.raises(resource_observer.ResourceObserverError, match="does not match its host task ID"):
        _observe(kwargs)


def test_status_reader_rejects_duplicate_malformed_and_out_of_range_facts(tmp_path):
    path = tmp_path / "status"
    valid = _status(50, host_tid=4250)
    path.write_text(valid, encoding="ascii")
    parsed = resource_observer._read_status(path)
    assert isinstance(parsed["NSpid"], tuple)
    assert parsed["NSpid"] == (4250, 50)

    path.write_text(valid + "Uid:\t4242 4242 4242 4242\n", encoding="ascii")
    with pytest.raises(resource_observer.ResourceObserverError, match="repeats Uid"):
        resource_observer._read_status(path)

    path.write_text(valid.replace("CapEff:\t0000000000000000", "CapEff:\tnope"), encoding="ascii")
    with pytest.raises(resource_observer.ResourceObserverError, match="malformed numeric"):
        resource_observer._read_status(path)

    path.write_text(valid.replace("NSpid:\t4250 50", "NSpid:\t"), encoding="ascii")
    with pytest.raises(resource_observer.ResourceObserverError, match="out-of-range"):
        resource_observer._read_status(path)

    path.write_text(valid.replace("Uid:\t4242 4242 4242 4242", "Uid:\t4242 4242 4242"), encoding="ascii")
    with pytest.raises(resource_observer.ResourceObserverError, match="out-of-range"):
        resource_observer._read_status(path)

    path.write_text(valid.replace("NSpid:\t4250 50", "NSpid:\t0 50"), encoding="ascii")
    with pytest.raises(resource_observer.ResourceObserverError, match="out-of-range"):
        resource_observer._read_status(path)

    path.write_text(valid.replace("CapEff:\t0000000000000000", "CapEff:\t-1"), encoding="ascii")
    with pytest.raises(resource_observer.ResourceObserverError, match="out-of-range"):
        resource_observer._read_status(path)

    path.write_text(valid.replace("CapPrm:\t0000000000000000", "CapPrm:\t-1"), encoding="ascii")
    with pytest.raises(resource_observer.ResourceObserverError, match="out-of-range"):
        resource_observer._read_status(path)


def test_proc_fact_readers_refuse_unreadable_or_malformed_records(tmp_path, monkeypatch):
    stat_path = tmp_path / "stat"
    stat_path.write_text("no close parenthesis", encoding="ascii")
    with pytest.raises(resource_observer.ResourceObserverError, match="stat record is malformed"):
        resource_observer._proc_start_ticks(stat_path)
    stat_path.write_text(") " + " ".join(["S", *("0" for _ in range(19))]) + "\n", encoding="ascii")
    with pytest.raises(resource_observer.ResourceObserverError, match="stat record is malformed"):
        resource_observer._proc_start_ticks(stat_path)
    stat_path.write_text("(worker) S", encoding="ascii")
    with pytest.raises(resource_observer.ResourceObserverError, match="start-time fact is malformed"):
        resource_observer._proc_start_ticks(stat_path)
    stat_path.write_text("42 (worker) " + " ".join(["S", *("0" for _ in range(18))]) + "\n", encoding="ascii")
    with pytest.raises(resource_observer.ResourceObserverError, match="start-time fact is malformed"):
        resource_observer._proc_start_ticks(stat_path)
    stat_path.write_text("(worker) S " + "0 " * 18 + "bad\n", encoding="ascii")
    with pytest.raises(resource_observer.ResourceObserverError, match="start-time fact is malformed"):
        resource_observer._proc_start_ticks(stat_path)

    read_text = Path.read_text

    def denied(path, *args, **kwargs):
        if path == stat_path:
            raise PermissionError("denied")
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", denied)
    with pytest.raises(resource_observer.ResourceObserverError, match="cannot read authenticated peer"):
        resource_observer._proc_start_ticks(stat_path)
    monkeypatch.setattr(Path, "read_text", read_text)

    task_dir = tmp_path / "4242"
    task_dir.mkdir()
    (task_dir / "cgroup").write_text("0::/workload\n", encoding="ascii")
    malformed_status = {
        "NSpid": [4242, 42],
        "Uid": (1, 1, 1, 1),
        "Gid": (1, 1, 1, 1),
        "Groups": (),
        "CapEff": 0,
        "CapPrm": 0,
    }
    monkeypatch.setattr(
        resource_observer, "_read_status", lambda _path: malformed_status
    )
    with pytest.raises(resource_observer.ResourceObserverError, match="no valid NSpid"):
        resource_observer._task_facts(task_dir)

    monkeypatch.setattr(resource_observer, "_read_status", lambda _path: {})
    with pytest.raises(resource_observer.ResourceObserverError, match="no valid NSpid"):
        resource_observer._task_facts(task_dir)

    monkeypatch.setattr(
        resource_observer, "_read_status",
        lambda _path: {"NSpid": (4242, 42), "Uid": (1, 1, 1, 1),
                       "Gid": (1, 1, 1, 1), "Groups": (), "CapEff": 0, "CapPrm": 0},
    )
    cgroup_path = task_dir / "cgroup"
    cgroup_path.write_text("1:cpu:/not-unified\n", encoding="ascii")
    with pytest.raises(resource_observer.ResourceObserverError, match="no absolute unified cgroup"):
        resource_observer._task_facts(task_dir)
    cgroup_path.write_text("0::/foo/../bar\n", encoding="ascii")
    with pytest.raises(resource_observer.ResourceObserverError, match="not canonical"):
        resource_observer._task_facts(task_dir)
    cgroup_path.write_text("0::/foo/./bar\n", encoding="ascii")
    with pytest.raises(resource_observer.ResourceObserverError, match="not canonical"):
        resource_observer._task_facts(task_dir)


@pytest.mark.parametrize(
    ("method_name", "relative_path"),
    [
        ("read_text", "4242/stat"),
        ("iterdir", "4242/task"),
        ("read_text", "4242/task/4242/status"),
        ("read_text", "4242/task/4242/cgroup"),
    ],
)
def test_observation_retries_a_peer_procfs_entry_that_disappears_once(
    tmp_path, monkeypatch, method_name, relative_path
):
    kwargs = _fixture(tmp_path)
    vanished_path = Path(kwargs["proc_root"]) / relative_path
    original = getattr(Path, method_name)
    vanished = False

    def disappear_once(path, *args, **call_kwargs):
        nonlocal vanished
        if path == vanished_path and not vanished:
            vanished = True
            raise FileNotFoundError(errno.ENOENT, "transient procfs race", str(path))
        return original(path, *args, **call_kwargs)

    monkeypatch.setattr(Path, method_name, disappear_once)

    result = _observe(kwargs)

    assert result["thread_id"] == kwargs["thread_id"]
    assert vanished


def test_observation_fails_closed_after_repeated_peer_procfs_disappearance(
    tmp_path, monkeypatch
):
    kwargs = _fixture(tmp_path)
    vanished_path = Path(kwargs["proc_root"]) / "4242/task/4242/status"
    original = Path.read_text
    reads = 0

    def always_missing(path, *args, **call_kwargs):
        nonlocal reads
        if path == vanished_path:
            reads += 1
            raise FileNotFoundError(errno.ENOENT, "persistent procfs race", str(path))
        return original(path, *args, **call_kwargs)

    monkeypatch.setattr(Path, "read_text", always_missing)

    with pytest.raises(
        resource_observer.ResourceObserverError,
        match="peer process or thread disappeared during identity verification",
    ):
        _observe(kwargs)

    assert 1 < reads <= resource_observer._PEER_SNAPSHOT_ATTEMPTS


def test_observation_reports_status_permission_failure_without_retry(tmp_path, monkeypatch):
    kwargs = _fixture(tmp_path)
    denied_path = Path(kwargs["proc_root"]) / "4242/task/4242/status"
    original = Path.read_text
    reads = 0

    def denied(path, *args, **call_kwargs):
        nonlocal reads
        if path == denied_path:
            reads += 1
            raise PermissionError(errno.EACCES, "denied", str(path))
        return original(path, *args, **call_kwargs)

    monkeypatch.setattr(Path, "read_text", denied)

    with pytest.raises(
        resource_observer.ResourceObserverError,
        match=r"cannot read peer thread status \(PermissionError, errno=13\)",
    ):
        _observe(kwargs)

    assert reads == 1


def test_mount_reader_refuses_unreadable_truncated_invalid_and_ambiguous_mounts(tmp_path):
    kwargs = _fixture(tmp_path)
    mountinfo = Path(kwargs["mountinfo_path"])
    root = kwargs["cgroup_root"]
    for contents, message in (
        ("1 2 0:1 /", "malformed record"),
        ("1 2 0:1 / x rw - \n", "truncated record"),
        (f"1 2 0:1 / {root} - cgroup2 cgroup rw\n", "truncated record"),
        (f"0 2 0:1 / {root} rw - cgroup2 cgroup rw\n", "mount ID is invalid"),
        (f"1 2 0:1 / {root} rw - ext4 /dev/x rw\n", "no unique full-root"),
        (f"1 2 0:1 / {root} rw - cgroup2 cgroup rw\n"
         f"2 1 0:1 / {root} rw - cgroup2 cgroup rw\n", "no unique full-root"),
        (f"1 2 0:1 //../.. {root} rw - cgroup2 cgroup rw\n", "does not expose the hierarchy root"),
    ):
        mountinfo.write_text(contents, encoding="ascii")
        with pytest.raises(resource_observer.ResourceObserverError, match=message):
            _observe(kwargs)


def test_unified_mountinfo_escapes_are_decoded():
    assert resource_observer._unescape_mountinfo(r"/a\040b\011c\012d\134e") == "/a b\tc\nd\\e"


def test_cgroup_entry_refuses_missing_directory_and_missing_pids_events(tmp_path):
    kwargs = _fixture(tmp_path)
    directory = Path(kwargs["cgroup_root"]) / LEAF.lstrip("/")
    with pytest.raises(resource_observer.ResourceObserverError, match="cannot inspect cgroup ancestor"):
        resource_observer._read_cgroup_entry(
            resource_observer.PurePosixPath(LEAF), directory / "missing", 29, []
        )

    not_directory = tmp_path / "not-a-cgroup-directory"
    not_directory.write_text("ordinary file", encoding="ascii")
    with pytest.raises(resource_observer.ResourceObserverError, match="is not a directory"):
        resource_observer._read_cgroup_entry(
            resource_observer.PurePosixPath(LEAF), not_directory, 29, []
        )

    (directory / "pids.max").write_text("12\n", encoding="ascii")
    (directory / "pids.events").unlink()
    with pytest.raises(resource_observer.ResourceObserverError, match="pids.max is present"):
        _observe(kwargs)


@pytest.mark.parametrize("which", ["limit", "events", "status", "cgroup", "mountinfo"])
def test_proc_and_counter_read_errors_are_not_silently_absent(tmp_path, monkeypatch, which):
    kwargs = _fixture(tmp_path)
    target = {
        "limit": Path(kwargs["cgroup_root"]) / LEAF.lstrip("/") / "pids.max",
        "events": Path(kwargs["cgroup_root"]) / LEAF.lstrip("/") / "pids.events",
        "status": Path(kwargs["proc_root"]) / "4242/task/4242/status",
        "cgroup": Path(kwargs["proc_root"]) / "4242/task/4242/cgroup",
        "mountinfo": Path(kwargs["mountinfo_path"]),
    }[which]
    read_text = Path.read_text

    def denied(path, *args, **kw):
        if path == target:
            raise PermissionError("denied")
        return read_text(path, *args, **kw)

    monkeypatch.setattr(Path, "read_text", denied)
    with pytest.raises(resource_observer.ResourceObserverError):
        _observe(kwargs)
