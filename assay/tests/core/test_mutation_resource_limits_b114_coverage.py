"""Behavioral boundary cases for native R2 cgroup resource evidence."""

from __future__ import annotations

import errno
import os
from dataclasses import replace
from pathlib import Path

import pytest

from assay import resource_limits
from assay.resource_limits import (
    CounterDelta,
    ResourceLimitCounters,
    ResourceLimitEvidence,
    ResourceLimitObservationError,
    ResourceObservationCapability,
    read_current_cgroup_counters,
)

_REAL_MOUNT_ID_FOR_FD = resource_limits._mount_id_for_fd
_REAL_INSPECT_CURRENT_CGROUP_OBSERVATION = (
    resource_limits.inspect_current_cgroup_observation
)


@pytest.fixture
def cgroup_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A visible, read-only-mounted hierarchy with two active controllers."""
    root = tmp_path / "cgroup"
    parent = root / "worker"
    candidate = parent / "lane"
    candidate.mkdir(parents=True)
    for directory in (root, parent, candidate):
        procs = directory / "cgroup.procs"
        procs.write_text("", encoding="ascii")
        procs.chmod(0o444)
    for directory in (parent, candidate):
        (directory / "pids.max").write_text("16\n", encoding="ascii")
        (directory / "memory.max").write_text("4096\n", encoding="ascii")
        (directory / "pids.events").write_text("max 2\n", encoding="ascii")
        (directory / "memory.events").write_text(
            "max 3\noom 4\noom_kill 5\noom_group_kill 6\n", encoding="ascii"
        )
    cgroup = tmp_path / "proc-cgroup"
    cgroup.write_text("0::/worker/lane\n", encoding="utf-8")
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text(
        f"31 23 0:28 / {root} ro - cgroup2 cgroup rw\n", encoding="utf-8"
    )
    monkeypatch.setattr(resource_limits, "_mount_id_for_fd", lambda _fd: 31)
    original_read_text = Path.read_text

    def read_thread_status(path: Path, *args, **kwargs):
        if path == Path("/proc/thread-self/status"):
            return "CapEff:\t0000000000000000\nCapPrm:\t0000000000000000\n"
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_thread_status)
    return root, parent, candidate, cgroup, mountinfo


def _read(tree) -> ResourceLimitCounters:
    return read_current_cgroup_counters(cgroup_file=tree[3], mountinfo_file=tree[4])


def _clean_evidence() -> ResourceLimitEvidence:
    before = ResourceLimitCounters(
        pids_max=4, memory_oom=8, memory_max=6,
        memory_oom_kill=10, memory_oom_group_kill=12,
    )
    after = ResourceLimitCounters(
        pids_max=4, memory_oom=8, memory_max=6,
        memory_oom_kill=10, memory_oom_group_kill=12,
    )
    return ResourceLimitEvidence.between(before, after)


def test_valid_combined_process_and_memory_observation_round_trips(cgroup_tree):
    before = _read(cgroup_tree)
    assert before == ResourceLimitCounters(
        pids_max=4,
        memory_oom=8,
        memory_max=6,
        memory_oom_kill=10,
        memory_oom_group_kill=12,
        limit_signature=(
            (str(cgroup_tree[2]), 31, True, 16, True, 4096, True, True),
            (str(cgroup_tree[1]), 31, True, 16, True, 4096, True, True),
        ),
    )
    (cgroup_tree[2] / "pids.events").write_text("max 3\n", encoding="ascii")
    (cgroup_tree[1] / "memory.events").write_text(
        "max 4\noom 4\noom_kill 5\noom_group_kill 7\n", encoding="ascii"
    )
    evidence = ResourceLimitEvidence.between(before, _read(cgroup_tree))
    assert evidence.limit_hit is True
    assert evidence.pids_events_max.delta == 1
    assert evidence.memory_events_max.delta == 1
    assert evidence.memory_events_oom_group_kill.delta == 1
    assert ResourceLimitEvidence.from_dict(evidence.to_dict()) == evidence


@pytest.mark.parametrize(
    ("cgroup_text", "mount_text", "message"),
    [
        ("1:name=/worker/lane\n", None, "absolute unified"),
        ("0::relative/lane\n", None, "absolute unified"),
        (None, "not a mount record\n", "malformed cgroup mount record"),
        (None, "0 23 0:28 / {root} ro - cgroup2 cgroup rw\n", "invalid cgroup mount ID"),
        (None, "31 23 0:28 / {root} ro - tmpfs tmpfs rw\n", "no visible cgroup v2"),
        (
            None,
            "31 23 0:28 / {root} ro - cgroup2 cgroup rw\n"
            "32 23 0:28 / {root} ro - cgroup2 cgroup rw\n",
            "multiple mounts cover",
        ),
    ],
)
def test_invalid_or_ambiguous_hierarchy_records_refuse(
    cgroup_tree, cgroup_text, mount_text, message
):
    root, _, _, cgroup, mountinfo = cgroup_tree
    if cgroup_text is not None:
        cgroup.write_text(cgroup_text, encoding="utf-8")
    if mount_text is not None:
        mountinfo.write_text(mount_text.format(root=root), encoding="utf-8")
    with pytest.raises(ResourceLimitObservationError, match=message):
        _read(cgroup_tree)


def test_cgroup_path_traversal_refuses_before_sampling(cgroup_tree):
    cgroup_tree[3].write_text("0::/worker/../other\n", encoding="utf-8")
    with pytest.raises(ResourceLimitObservationError, match="escapes its hierarchy"):
        _read(cgroup_tree)


def test_unrelated_nested_mount_does_not_refuse_valid_cgroup_observation(cgroup_tree):
    unrelated = cgroup_tree[0] / "other"
    unrelated.mkdir()
    with cgroup_tree[4].open("a", encoding="utf-8") as stream:
        stream.write(f"32 31 0:29 / {unrelated} ro - tmpfs tmpfs rw\n")
    counters = _read(cgroup_tree)
    assert (counters.pids_max, counters.memory_max) == (4, 6)


@pytest.mark.parametrize(
    ("controller", "content", "message"),
    [
        ("pids.max", "16 17\n", "malformed limit"),
        ("pids.max", "many\n", "malformed limit"),
        ("pids.max", "-1\n", "negative limit"),
        ("memory.max", "4096 8192\n", "malformed limit"),
        ("memory.max", "many\n", "malformed limit"),
        ("memory.max", "-1\n", "negative limit"),
        ("pids.events", "max 2\nmax 3\n", "malformed event line"),
        ("pids.events", "max many\n", "malformed counter"),
        ("pids.events", "max -1\n", "negative counter"),
        ("memory.events", "max 3\noom 4\noom_kill 5\n", "oom_group_kill"),
    ],
)
def test_malformed_process_or_memory_control_refuses_without_partial_evidence(
    cgroup_tree, controller, content, message
):
    (cgroup_tree[2] / controller).write_text(content, encoding="ascii")
    with pytest.raises(ResourceLimitObservationError, match=message):
        _read(cgroup_tree)


@pytest.mark.parametrize("controller", ["pids", "memory"])
def test_finite_limit_without_its_event_file_refuses(cgroup_tree, controller):
    (cgroup_tree[2] / f"{controller}.events").unlink()
    with pytest.raises(ResourceLimitObservationError, match=f"{controller}.max.*events"):
        _read(cgroup_tree)


@pytest.mark.parametrize("controller", ["pids", "memory"])
def test_no_visible_active_controller_counter_refuses(cgroup_tree, controller):
    for directory in cgroup_tree[1:3]:
        (directory / f"{controller}.max").unlink()
        (directory / f"{controller}.events").unlink()
    with pytest.raises(ResourceLimitObservationError, match=f"no visible {controller}.events"):
        _read(cgroup_tree)


def test_inactive_child_controller_keeps_valid_parent_evidence(cgroup_tree):
    child = cgroup_tree[2]
    for name in ("pids.max", "pids.events", "memory.max", "memory.events"):
        (child / name).unlink()
    counters = _read(cgroup_tree)
    assert (counters.pids_max, counters.memory_max, counters.memory_oom) == (2, 3, 4)
    assert counters.limit_signature[0] == (
        str(child), 31, False, None, False, None, False, False
    )


@pytest.mark.parametrize(
    ("status", "message"),
    [
        ("CapEff:\tZZ\nCapPrm:\t0\n", "malformed CapEff"),
        ("CapEff:\t0\n", "missing candidate capability sets"),
        ("CapEff:\t0\nCapPrm:\t2\n", "capabilities can bypass"),
    ],
)
def test_unsafe_or_malformed_candidate_capabilities_refuse(
    cgroup_tree, monkeypatch, status, message
):
    original_read_text = Path.read_text

    def read_status(path: Path, *args, **kwargs):
        if path == Path("/proc/thread-self/status"):
            return status
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_status)
    with pytest.raises(ResourceLimitObservationError, match=message):
        _read(cgroup_tree)


@pytest.mark.parametrize("permission", [0o020, 0o002])
def test_group_or_world_writable_ancestor_blocks_cgroup_migration(
    cgroup_tree, monkeypatch, permission
):
    parent_procs = cgroup_tree[1] / "cgroup.procs"
    parent_procs.chmod(0o444 | permission)
    if permission == 0o020:
        monkeypatch.setattr(os, "geteuid", lambda: parent_procs.stat().st_uid + 1)
        monkeypatch.setattr(os, "getegid", lambda: parent_procs.stat().st_gid)
    else:
        monkeypatch.setattr(os, "geteuid", lambda: parent_procs.stat().st_uid + 1)
        monkeypatch.setattr(os, "getegid", lambda: parent_procs.stat().st_gid + 1)
        monkeypatch.setattr(os, "getgroups", lambda: [])
    with pytest.raises(ResourceLimitObservationError, match="can write.*cgroup.procs"):
        _read(cgroup_tree)


def test_access_acl_that_could_grant_migration_refuses(cgroup_tree, monkeypatch):
    original_getxattr = os.getxattr

    def acl_for_ancestor(path, name):
        if Path(path) == cgroup_tree[1] / "cgroup.procs":
            return b"configured ACL"
        return original_getxattr(path, name)

    monkeypatch.setattr(os, "getxattr", acl_for_ancestor)
    with pytest.raises(ResourceLimitObservationError, match="ACL"):
        _read(cgroup_tree)


def test_acl_inspection_error_refuses_instead_of_treating_it_as_absent(
    cgroup_tree, monkeypatch
):
    original_getxattr = os.getxattr

    def unreadable_acl(path, name):
        if Path(path) == cgroup_tree[1] / "cgroup.procs":
            raise OSError(errno.EACCES, "ACL hidden")
        return original_getxattr(path, name)

    monkeypatch.setattr(os, "getxattr", unreadable_acl)
    with pytest.raises(ResourceLimitObservationError, match="cannot inspect access ACL"):
        _read(cgroup_tree)


def test_unreadable_counter_and_non_ascii_counter_refuse(cgroup_tree, monkeypatch):
    counter = cgroup_tree[2] / "pids.events"
    original_open = os.open

    def denied_counter(path, flags, *args, **kwargs):
        if Path(path) == counter:
            raise OSError(errno.EACCES, "denied")
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", denied_counter)
    with pytest.raises(ResourceLimitObservationError, match="cannot read pids.events"):
        _read(cgroup_tree)
    monkeypatch.setattr(os, "open", original_open)
    counter.write_bytes(b"max \xff\n")
    with pytest.raises(ResourceLimitObservationError, match="cannot read pids.events"):
        _read(cgroup_tree)


@pytest.mark.parametrize("source", ["cgroup", "mountinfo"])
def test_unreadable_kernel_identity_record_refuses(cgroup_tree, source):
    path = cgroup_tree[3] if source == "cgroup" else cgroup_tree[4]
    path.write_bytes(b"\xff")
    with pytest.raises(ResourceLimitObservationError, match="cannot inspect calling thread"):
        _read(cgroup_tree)


def test_unreadable_fdinfo_refuses_mount_identity_claim(cgroup_tree, monkeypatch):
    monkeypatch.setattr(resource_limits, "_mount_id_for_fd", _REAL_MOUNT_ID_FOR_FD)
    original_read_text = Path.read_text

    def deny_fdinfo(path: Path, *args, **kwargs):
        if str(path).startswith("/proc/self/fdinfo/"):
            raise OSError(errno.EACCES, "fdinfo hidden")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", deny_fdinfo)
    with pytest.raises(ResourceLimitObservationError, match="cannot inspect.*mount identity"):
        _read(cgroup_tree)


def test_platform_without_open_path_support_refuses(cgroup_tree, monkeypatch):
    monkeypatch.delattr(os, "O_PATH")
    with pytest.raises(ResourceLimitObservationError, match="cannot establish cgroup path mount identity"):
        _read(cgroup_tree)


def test_unopenable_hierarchy_root_refuses(cgroup_tree, monkeypatch):
    root = cgroup_tree[0]
    original_open = os.open

    def deny_root(path, flags, *args, **kwargs):
        if Path(path) == root:
            raise OSError(errno.EACCES, "hierarchy hidden")
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", deny_root)
    with pytest.raises(ResourceLimitObservationError, match="cannot open cgroup"):
        _read(cgroup_tree)


def test_control_parent_becoming_unopenable_refuses(cgroup_tree, monkeypatch):
    candidate = cgroup_tree[2]
    (candidate / "pids.max").unlink()
    original_open = os.open

    def deny_parent(path, flags, *args, **kwargs):
        if Path(path) == candidate:
            raise OSError(errno.EACCES, "parent hidden")
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", deny_parent)
    with pytest.raises(ResourceLimitObservationError, match="cannot open lane"):
        _read(cgroup_tree)


def test_uninspectable_optional_control_refuses_instead_of_absent(cgroup_tree, monkeypatch):
    root_limit = cgroup_tree[0] / "pids.max"
    original_stat = Path.stat

    def deny_stat(path: Path, *args, **kwargs):
        if path == root_limit:
            raise OSError(errno.EACCES, "control hidden")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", deny_stat)
    with pytest.raises(ResourceLimitObservationError, match="cannot inspect pids.max"):
        _read(cgroup_tree)


def test_missing_ancestor_migration_control_refuses(cgroup_tree):
    (cgroup_tree[1] / "cgroup.procs").unlink()
    with pytest.raises(ResourceLimitObservationError, match="cgroup.procs is unavailable"):
        _read(cgroup_tree)


def test_migration_control_becoming_unopenable_refuses(cgroup_tree, monkeypatch):
    control = cgroup_tree[2] / "cgroup.procs"
    original_open = os.open

    def deny_control(path, flags, *args, **kwargs):
        if Path(path) == control:
            raise OSError(errno.EACCES, "control hidden")
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", deny_control)
    with pytest.raises(ResourceLimitObservationError, match="cannot open cgroup.procs"):
        _read(cgroup_tree)


def test_migration_permission_becoming_unreadable_refuses(cgroup_tree, monkeypatch):
    control = cgroup_tree[2] / "cgroup.procs"
    original_stat = Path.stat
    visits = 0

    def deny_second_stat(path: Path, *args, **kwargs):
        nonlocal visits
        if path == control:
            visits += 1
            if visits == 2:
                raise OSError(errno.EACCES, "permissions hidden")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", deny_second_stat)
    with pytest.raises(ResourceLimitObservationError, match="cannot inspect permissions"):
        _read(cgroup_tree)
    assert visits == 2


def test_unreadable_candidate_thread_capabilities_refuse(cgroup_tree, monkeypatch):
    original_read_text = Path.read_text

    def deny_status(path: Path, *args, **kwargs):
        if path == Path("/proc/thread-self/status"):
            raise OSError(errno.EACCES, "status hidden")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", deny_status)
    with pytest.raises(ResourceLimitObservationError, match="cannot inspect candidate thread capabilities"):
        _read(cgroup_tree)


def test_counter_mount_identity_mismatch_refuses(cgroup_tree, monkeypatch):
    def mount_identity(fd):
        opened = Path(os.readlink(f"/proc/self/fd/{fd}"))
        return 32 if opened == cgroup_tree[2] / "memory.events" else 31

    monkeypatch.setattr(resource_limits, "_mount_id_for_fd", mount_identity)
    with pytest.raises(ResourceLimitObservationError, match="different mount ID"):
        _read(cgroup_tree)


@pytest.mark.parametrize(
    ("fdinfo", "message"),
    [
        ("pos:\t0\n", "no unique mount identity"),
        ("mnt_id:\t31\nmnt_id:\t32\n", "no unique mount identity"),
        ("mnt_id:\tbad\n", "malformed mount identity"),
        ("mnt_id:\t0\n", "invalid mount identity"),
    ],
)
def test_untrusted_opened_mount_identity_refuses(
    cgroup_tree, monkeypatch, fdinfo, message
):
    monkeypatch.setattr(resource_limits, "_mount_id_for_fd", _REAL_MOUNT_ID_FOR_FD)
    original_read_text = Path.read_text

    def bad_fdinfo(path: Path, *args, **kwargs):
        if str(path).startswith("/proc/self/fdinfo/"):
            return fdinfo
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", bad_fdinfo)
    with pytest.raises(ResourceLimitObservationError, match=message):
        _read(cgroup_tree)


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ({"cgroup_version": True}, "cgroup_version 2"),
        ({"cgroup_version": 2.0}, "cgroup_version 2"),
        ({"pids_events": {"max": {}, "other": {}}}, "pids_events must contain"),
        ({"memory_events": {"max": {}}}, "memory_events must contain"),
        ({"pids_events": {"max": {"before": 0, "after": 0}}}, "exactly before"),
        ({"memory_events": {"oom": {"before": 1, "after": 0, "delta": 0}}}, "must not decrease"),
    ],
)
def test_external_resource_evidence_malformed_shape_or_arithmetic_refuses(raw, message):
    document = _clean_evidence().to_dict()
    for key, value in raw.items():
        if key == "cgroup_version":
            document[key] = value
        elif key == "memory_events" and value == {"max": {}}:
            document[key] = value
        else:
            document[key].update(value)
    with pytest.raises(ValueError, match=message):
        ResourceLimitEvidence.from_dict(document)


def test_external_resource_evidence_unknown_field_refuses():
    document = _clean_evidence().to_dict()
    document["unverified"] = True
    with pytest.raises(ValueError, match="unknown or missing fields"):
        ResourceLimitEvidence.from_dict(document)


@pytest.mark.parametrize(
    ("available", "reason", "message"),
    [
        (1, None, "must be a bool"),
        (True, "hidden", "cannot carry"),
        (False, None, "requires a reason"),
    ],
)
def test_capability_report_cannot_certify_inconsistent_observation(
    available, reason, message
):
    with pytest.raises(ValueError, match=message):
        ResourceObservationCapability(available=available, reason=reason)


def test_preflight_thread_start_failure_reports_unavailable(monkeypatch):
    def cannot_start(_thread):
        raise RuntimeError("worker threads exhausted")

    monkeypatch.setattr(resource_limits.threading.Thread, "start", cannot_start)
    monkeypatch.setattr(
        resource_limits,
        "inspect_current_cgroup_observation",
        _REAL_INSPECT_CURRENT_CGROUP_OBSERVATION,
    )
    capability = resource_limits.inspect_current_cgroup_observation()
    assert capability.available is False
    assert capability.reason == (
        "cannot start cgroup observation probe thread: worker threads exhausted"
    )


def test_unexpected_probe_bug_is_not_misreported_as_environment_refusal(monkeypatch):
    def bug():
        raise ValueError("unexpected reader bug")

    monkeypatch.setattr(resource_limits, "read_current_cgroup_counters", bug)
    monkeypatch.setattr(
        resource_limits,
        "inspect_current_cgroup_observation",
        _REAL_INSPECT_CURRENT_CGROUP_OBSERVATION,
    )
    with pytest.raises(ValueError, match="unexpected reader bug"):
        resource_limits.inspect_current_cgroup_observation()


def test_counter_delta_model_refuses_decrease_and_forged_delta():
    with pytest.raises(ValueError, match="must not decrease"):
        CounterDelta(before=2, after=1, delta=0)
    with pytest.raises(ValueError, match="after minus before"):
        CounterDelta(before=2, after=3, delta=2)


def test_counter_delta_model_refuses_noninteger_counter():
    with pytest.raises(ValueError, match="non-negative integers"):
        CounterDelta(before=True, after=1, delta=0)


def test_counter_model_refuses_noninteger_count_and_signature_list():
    counters = ResourceLimitCounters(
        pids_max=0, memory_oom=0, memory_max=0,
        memory_oom_kill=0, memory_oom_group_kill=0,
    )
    with pytest.raises(ValueError, match="pids_max must be a non-negative integer"):
        replace(counters, pids_max=True)
    with pytest.raises(ValueError, match="limit_signature must be a tuple"):
        replace(counters, limit_signature=[])


def test_evidence_model_refuses_untyped_delta():
    with pytest.raises(ValueError, match="pids_events_max must be a CounterDelta"):
        replace(_clean_evidence(), pids_events_max={"before": 0, "after": 0, "delta": 0})


@pytest.mark.parametrize(
    "entry",
    [
        ("/lane", 0, True, 16, True, 4096, True, True),
        ("/lane", 31, False, 16, True, 4096, True, True),
        ("/lane", 31, True, 16, True, 4096, False, True),
        ("/lane", 31, True, 16, True, 4096, True, False),
    ],
)
def test_counter_signature_refuses_unbound_or_missing_limit_evidence(entry):
    with pytest.raises(ValueError, match="limit_signature entries are malformed"):
        ResourceLimitCounters(
            pids_max=0, memory_oom=0, memory_max=0,
            memory_oom_kill=0, memory_oom_group_kill=0,
            limit_signature=(entry,),
        )
