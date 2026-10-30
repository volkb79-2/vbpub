from __future__ import annotations

import json
import os
import sys
import threading
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from conftest import completed_process
from conftest import (
    GitRepo,
    make_deadline,
    make_lane,
    make_plan,
    native_outcome,
    prepared_snapshot,
)
import assay.resource_limits as resource_limits
import assay.mutation as mutation_module
from assay.adapters.python import PythonAdapter
from assay.errors import AssayError, Outcome, ReasonCode
from assay.process_tree import ProcessTreeError, ProcessTreeEvidence
from assay.mutation import (
    MutationTarget,
    judge_mutation,
    run_mutation,
)
from assay.resource_limits import (
    CounterDelta,
    ResourceLimitCounters,
    ResourceLimitEvidence,
    ResourceLimitObservationError,
    _mount_id_for_fd,
    read_current_cgroup_counters,
)
from assay.runner import default_process_runner, execute_command
from assay.verdict import MUTATION_BUCKETS, MutantOutcome, Mutation
from assay.verify import _check_b145_resource_limit_evidence

_REAL_MOUNT_ID_FOR_FD = resource_limits._mount_id_for_fd
_REAL_INSPECT_CURRENT_CGROUP_OBSERVATION = (
    resource_limits.inspect_current_cgroup_observation
)


def test_cgroup_capability_probe_uses_a_worker_thread(monkeypatch):
    main_thread = threading.get_ident()
    observed_calls: list[tuple[int, str | None]] = []
    counters = ResourceLimitCounters(
        pids_max=0,
        memory_max=0,
        memory_oom=0,
        memory_oom_kill=0,
        memory_oom_group_kill=0,
    )
    monkeypatch.setattr(
        resource_limits,
        "inspect_current_cgroup_observation",
        _REAL_INSPECT_CURRENT_CGROUP_OBSERVATION,
    )
    monkeypatch.setattr(
        resource_limits,
        "read_candidate_resource_counters",
        lambda **kwargs: observed_calls.append(
            (threading.get_ident(), kwargs.get("observer_socket_path"))
        )
        or counters,
    )

    observer_path = "/run/cgprofile-observer/resource-observer.sock"
    capability = resource_limits.inspect_current_cgroup_observation(
        observer_socket_path=observer_path
    )

    assert capability.available is True
    assert capability.reason is None
    assert len(observed_calls) == 1
    assert observed_calls[0][0] != main_thread
    assert observed_calls[0][1] == observer_path


def test_cgroup_capability_probe_preserves_full_reader_refusal(monkeypatch):
    monkeypatch.setattr(
        resource_limits,
        "inspect_current_cgroup_observation",
        _REAL_INSPECT_CURRENT_CGROUP_OBSERVATION,
    )
    monkeypatch.setattr(
        resource_limits,
        "read_candidate_resource_counters",
        lambda **_kwargs: (_ for _ in ()).throw(
            ResourceLimitObservationError("the ancestor is hidden")
        ),
    )

    capability = resource_limits.inspect_current_cgroup_observation()

    assert capability.available is False
    assert capability.reason == "the ancestor is hidden"


@pytest.fixture(autouse=True)
def _synthetic_cgroup_mount_id(monkeypatch: pytest.MonkeyPatch) -> None:
    # Reader fixtures pin their selected cgroup2 mount to ID 31.
    monkeypatch.setattr(resource_limits, "_mount_id_for_fd", lambda _fd: 31)


def _evidence(
    *,
    pids: tuple[int, int] = (0, 0),
    memory_max: tuple[int, int] = (0, 0),
    oom: tuple[int, int] = (0, 0),
    oom_kill: tuple[int, int] = (0, 0),
    oom_group_kill: tuple[int, int] = (0, 0),
) -> ResourceLimitEvidence:
    return ResourceLimitEvidence(
        pids_events_max=CounterDelta.between(*pids),
        memory_events_max=CounterDelta.between(*memory_max),
        memory_events_oom=CounterDelta.between(*oom),
        memory_events_oom_kill=CounterDelta.between(*oom_kill),
        memory_events_oom_group_kill=CounterDelta.between(*oom_group_kill),
    )


def _lock_cgroup_controls(*directories: Path) -> None:
    for directory in directories:
        for name in ("cgroup.procs", "cgroup.subtree_control"):
            path = directory / name
            path.write_text("", encoding="ascii")
            path.chmod(0o444)


def test_reader_resolves_current_cgroup_from_kernel_mount_records(tmp_path: Path):
    cgroup_dir = tmp_path / "cgroup" / "worker" / "lane"
    cgroup_dir.mkdir(parents=True)
    parent_dir = cgroup_dir.parent
    mount_root = tmp_path / "cgroup"
    for directory, pids_limit, memory_limit in (
        (parent_dir, "12", "4096"),
        (cgroup_dir, "max", "max"),
    ):
        (directory / "pids.max").write_text(f"{pids_limit}\n", encoding="ascii")
        (directory / "memory.max").write_text(f"{memory_limit}\n", encoding="ascii")
    (parent_dir / "pids.events").write_text("max 3\n", encoding="ascii")
    (cgroup_dir / "pids.events").write_text("max 2\n", encoding="ascii")
    (parent_dir / "memory.events").write_text(
        "max 2\noom 3\noom_kill 4\noom_group_kill 1\n", encoding="ascii"
    )
    (cgroup_dir / "memory.events").write_text(
        "max 1\noom 1\noom_kill 2\noom_group_kill 0\n", encoding="ascii"
    )
    (parent_dir / "pids.events.local").write_text("max 3\n", encoding="ascii")
    (cgroup_dir / "pids.events.local").write_text("max 2\n", encoding="ascii")
    (parent_dir / "memory.events.local").write_text(
        "max 2\noom 3\noom_kill 4\noom_group_kill 1\n", encoding="ascii"
    )
    (cgroup_dir / "memory.events.local").write_text(
        "max 1\noom 1\noom_kill 2\noom_group_kill 0\n", encoding="ascii"
    )
    _lock_cgroup_controls(mount_root, parent_dir, cgroup_dir)
    cgroup_file = tmp_path / "proc-cgroup"
    cgroup_file.write_text("0::/worker/lane\n", encoding="utf-8")
    mountinfo_file = tmp_path / "mountinfo"
    mountinfo_file.write_text(
        f"31 23 0:28 / {mount_root} ro,nosuid - cgroup2 cgroup rw\n"
        f"32 24 0:29 /worker/lane/child {tmp_path / 'child-cgroup'} ro - cgroup2 cgroup rw\n",
        encoding="utf-8",
    )

    assert read_current_cgroup_counters(
        cgroup_file=cgroup_file, mountinfo_file=mountinfo_file
    ) == ResourceLimitCounters(
        pids_max=5,
        memory_max=3,
        memory_oom=4,
        memory_oom_kill=6,
        memory_oom_group_kill=1,
        limit_signature=(
            (str(cgroup_dir), 31, True, None, True, None, True, True, True, True, False, False),
            (str(parent_dir), 31, True, 12, True, 4096, True, True, True, True, False, False),
        ),
        path_event_counters=(
            (str(cgroup_dir), 2, 1, 1, 2, 0, 2, 1, 1, 2, 0),
            (str(parent_dir), 3, 2, 3, 4, 1, 3, 2, 3, 4, 1),
        ),
    )


def test_reader_uses_the_calling_worker_thread_cgroup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    mount_point = tmp_path / "cgroup"
    worker = mount_point / "worker" / "lane"
    leader = mount_point / "leader" / "lane"
    for directory, pids_count in ((worker, 3), (leader, 91)):
        directory.mkdir(parents=True)
        (directory / "pids.max").write_text("max\n", encoding="ascii")
        (directory / "memory.max").write_text("max\n", encoding="ascii")
        (directory / "pids.events").write_text(
            f"max {pids_count}\n", encoding="ascii"
        )
        (directory / "memory.events").write_text(
            "max 0\noom 0\noom_kill 0\noom_group_kill 0\n",
            encoding="ascii",
        )
        (directory / "pids.events.local").write_text(
            f"max {pids_count}\n", encoding="ascii"
        )
        (directory / "memory.events.local").write_text(
            "max 0\noom 0\noom_kill 0\noom_group_kill 0\n",
            encoding="ascii",
        )
    _lock_cgroup_controls(mount_point, worker.parent, worker, leader.parent, leader)
    mountinfo_file = tmp_path / "mountinfo"
    mountinfo_file.write_text(
        f"31 23 0:28 / {mount_point} ro - cgroup2 cgroup rw\n",
        encoding="utf-8",
    )
    original_read_text = Path.read_text
    read_cgroup_paths: list[Path] = []

    def read_thread_identity(path: Path, *args: object, **kwargs: object) -> str:
        if path in {Path("/proc/thread-self/cgroup"), Path("/proc/self/cgroup")}:
            read_cgroup_paths.append(path)
            target = (
                "/worker/lane"
                if path == Path("/proc/thread-self/cgroup")
                else "/leader/lane"
            )
            return f"0::{target}\n"
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_thread_identity)

    result: list[ResourceLimitCounters] = []
    errors: list[BaseException] = []

    def read_on_worker_thread() -> None:
        try:
            result.append(read_current_cgroup_counters(mountinfo_file=mountinfo_file))
        except BaseException as exc:
            errors.append(exc)

    worker_thread = threading.Thread(
        target=read_on_worker_thread, name="resource-limit-sampler"
    )
    worker_thread.start()
    worker_thread.join(timeout=5)

    assert not worker_thread.is_alive()
    assert errors == []
    assert result[0].pids_max == 3
    assert read_cgroup_paths == [Path("/proc/thread-self/cgroup")]


def test_capability_guard_uses_the_calling_worker_thread_status(
    monkeypatch: pytest.MonkeyPatch,
):
    leader_status = "CapEff:\t0000000000000000\nCapPrm:\t0000000000000000\n"
    worker_status = "CapEff:\t0000000000000002\nCapPrm:\t0000000000000002\n"
    original_read_text = Path.read_text
    read_status_paths: list[Path] = []

    def read_thread_status(path: Path, *args: object, **kwargs: object) -> str:
        if path in {Path("/proc/thread-self/status"), Path("/proc/self/status")}:
            read_status_paths.append(path)
            return (
                worker_status
                if path == Path("/proc/thread-self/status")
                else leader_status
            )
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_thread_status)
    errors: list[BaseException] = []

    def check_from_worker_thread() -> None:
        try:
            resource_limits._candidate_capabilities_are_unprivileged()
        except BaseException as exc:
            errors.append(exc)

    worker_thread = threading.Thread(
        target=check_from_worker_thread, name="capability-check-worker"
    )
    worker_thread.start()
    worker_thread.join(timeout=5)

    assert not worker_thread.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], ResourceLimitObservationError)
    assert "capabilities" in str(errors[0])
    assert read_status_paths == [Path("/proc/thread-self/status")]


def test_mount_id_reader_uses_the_opened_file_identity(monkeypatch: pytest.MonkeyPatch):
    original_read_text = Path.read_text

    def read_fdinfo(path: Path, *args: object, **kwargs: object) -> str:
        if path == Path("/proc/self/fdinfo/77"):
            return "pos:\t0\nflags:\t0100000\nmnt_id:\t31\nino:\t123\n"
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_fdinfo)

    assert _mount_id_for_fd(77) == 31


@pytest.mark.parametrize(
    ("cgroup_path", "mount_root", "namespace_root_limit"),
    [
        ("/", "/", None),
        ("/worker/lane", "/worker", None),
        ("/child", "/", "pids.max"),
    ],
)
def test_reader_fails_closed_when_the_mount_hides_ancestor_cgroups(
    tmp_path: Path,
    cgroup_path: str,
    mount_root: str,
    namespace_root_limit: str | None,
):
    mount_point = tmp_path / "cgroup"
    mount_point.mkdir()
    if namespace_root_limit is not None:
        (mount_point / namespace_root_limit).write_text("32\n", encoding="ascii")
    cgroup_file = tmp_path / "proc-cgroup"
    cgroup_file.write_text(f"0::{cgroup_path}\n", encoding="utf-8")
    mountinfo_file = tmp_path / "mountinfo"
    mountinfo_file.write_text(
        f"31 23 0:28 {mount_root} {mount_point} ro - cgroup2 cgroup rw\n",
        encoding="utf-8",
    )

    with pytest.raises(ResourceLimitObservationError, match="ancestor|namespace"):
        read_current_cgroup_counters(
            cgroup_file=cgroup_file, mountinfo_file=mountinfo_file
        )


def test_reader_refuses_a_sibling_cgroup_overmount_at_the_candidate_path(
    tmp_path: Path,
):
    mount_root = tmp_path / "cgroup"
    cgroup_dir = mount_root / "worker" / "lane"
    cgroup_dir.mkdir(parents=True)
    cgroup_file = tmp_path / "proc-cgroup"
    cgroup_file.write_text("0::/worker/lane\n", encoding="utf-8")
    mountinfo_file = tmp_path / "mountinfo"
    mountinfo_file.write_text(
        f"31 23 0:28 / {mount_root} ro - cgroup2 cgroup rw\n"
        f"32 24 0:29 /worker/other {cgroup_dir} ro - cgroup2 cgroup rw\n",
        encoding="utf-8",
    )

    with pytest.raises(ResourceLimitObservationError, match="shadows"):
        read_current_cgroup_counters(
            cgroup_file=cgroup_file, mountinfo_file=mountinfo_file
        )


def test_reader_refuses_a_parent_overmount_hiding_the_cgroup_hierarchy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    overmounted_parent = tmp_path / "cgroup-parent"
    mount_point = overmounted_parent / "cgroup"
    (mount_point / "worker" / "lane").mkdir(parents=True)
    cgroup_file = tmp_path / "proc-cgroup"
    cgroup_file.write_text("0::/worker/lane\n", encoding="utf-8")
    mountinfo_file = tmp_path / "mountinfo"
    mountinfo_file.write_text(
        "23 23 0:23 / / rw - overlay overlay rw\n"
        f"24 23 0:24 / {overmounted_parent} ro - tmpfs tmpfs rw\n"
        f"31 24 0:28 / {mount_point} ro - cgroup2 cgroup rw\n"
        f"32 23 0:32 / {overmounted_parent} ro - tmpfs tmpfs rw\n",
        encoding="utf-8",
    )
    # The path now resolves through the later parent overmount, while mountinfo
    # still contains the original cgroup2 mount at ID 31.
    monkeypatch.setattr(resource_limits, "_mount_id_for_fd", lambda _fd: 32)

    with pytest.raises(ResourceLimitObservationError, match="overmount"):
        read_current_cgroup_counters(
            cgroup_file=cgroup_file, mountinfo_file=mountinfo_file
        )


def test_reader_refuses_a_sampled_counter_from_another_mount(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    mount_point = tmp_path / "cgroup"
    candidate = mount_point / "worker" / "lane"
    candidate.mkdir(parents=True)
    for directory in (candidate.parent, candidate):
        (directory / "pids.max").write_text("max\n", encoding="ascii")
        (directory / "memory.max").write_text("max\n", encoding="ascii")
        (directory / "pids.events").write_text("max 0\n", encoding="ascii")
        (directory / "memory.events").write_text(
            "max 0\noom 0\noom_kill 0\noom_group_kill 0\n",
            encoding="ascii",
        )
        (directory / "pids.events.local").write_text("max 0\n", encoding="ascii")
        (directory / "memory.events.local").write_text(
            "max 0\noom 0\noom_kill 0\noom_group_kill 0\n", encoding="ascii"
        )
    _lock_cgroup_controls(mount_point, candidate.parent, candidate)
    cgroup_file = tmp_path / "proc-cgroup"
    cgroup_file.write_text("0::/worker/lane\n", encoding="utf-8")
    mountinfo_file = tmp_path / "mountinfo"
    mountinfo_file.write_text(
        f"31 23 0:28 / {mount_point} ro - cgroup2 cgroup rw\n",
        encoding="utf-8",
    )
    original_mount_id_for_fd = resource_limits._mount_id_for_fd

    def report_event_file_overmount(fd: int) -> int:
        path = Path(os.readlink(f"/proc/self/fd/{fd}"))
        if path == candidate / "pids.events":
            return 32
        return original_mount_id_for_fd(fd)

    monkeypatch.setattr(
        resource_limits, "_mount_id_for_fd", report_event_file_overmount
    )

    with pytest.raises(ResourceLimitObservationError, match="different mount"):
        read_current_cgroup_counters(
            cgroup_file=cgroup_file, mountinfo_file=mountinfo_file
        )


def test_missing_optional_control_still_checks_its_parent_mount(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    mount_root = tmp_path / "cgroup"
    cgroup_dir = mount_root / "worker"
    cgroup_dir.mkdir(parents=True)
    _lock_cgroup_controls(mount_root, cgroup_dir)
    cgroup_file = tmp_path / "proc-cgroup"
    cgroup_file.write_text("0::/worker\n", encoding="utf-8")
    mountinfo_file = tmp_path / "mountinfo"
    mountinfo_file.write_text(
        f"31 23 0:28 / {mount_root} ro - cgroup2 cgroup rw\n",
        encoding="utf-8",
    )
    original_mount_id_for_fd = resource_limits._mount_id_for_fd

    def report_parent_overmount(fd: int) -> int:
        path = Path(os.readlink(f"/proc/self/fd/{fd}"))
        if path == cgroup_dir:
            return 32
        return original_mount_id_for_fd(fd)

    monkeypatch.setattr(resource_limits, "_mount_id_for_fd", report_parent_overmount)

    with pytest.raises(ResourceLimitObservationError, match="different mount"):
        read_current_cgroup_counters(
            cgroup_file=cgroup_file, mountinfo_file=mountinfo_file
        )


def test_reader_fails_closed_when_a_required_controller_counter_is_missing(
    tmp_path: Path,
):
    mount_point = tmp_path / "cgroup"
    cgroup_dir = mount_point / "lane"
    cgroup_dir.mkdir(parents=True)
    (cgroup_dir / "pids.max").write_text("16\n", encoding="ascii")
    (cgroup_dir / "memory.max").write_text("1024\n", encoding="ascii")
    (cgroup_dir / "pids.events").write_text("max 0\n", encoding="ascii")
    (cgroup_dir / "memory.events").write_text(
        "max 0\noom 0\noom_kill 0\n", encoding="ascii"
    )
    (cgroup_dir / "pids.events.local").write_text("max 0\n", encoding="ascii")
    (cgroup_dir / "memory.events.local").write_text(
        "max 0\noom 0\noom_kill 0\n", encoding="ascii"
    )
    _lock_cgroup_controls(mount_point, cgroup_dir)
    cgroup_file = tmp_path / "proc-cgroup"
    cgroup_file.write_text("0::/lane\n", encoding="utf-8")
    mountinfo_file = tmp_path / "mountinfo"
    mountinfo_file.write_text(
        f"31 23 0:28 / {mount_point} ro - cgroup2 cgroup rw\n",
        encoding="utf-8",
    )

    with pytest.raises(ResourceLimitObservationError, match="oom_group_kill"):
        read_current_cgroup_counters(
            cgroup_file=cgroup_file, mountinfo_file=mountinfo_file
        )


def test_reader_accepts_inactive_child_controllers_when_finite_parent_is_visible(
    tmp_path: Path,
):
    mount_point = tmp_path / "cgroup"
    parent_dir = mount_point / "worker"
    cgroup_dir = parent_dir / "lane"
    cgroup_dir.mkdir(parents=True)
    (parent_dir / "pids.max").write_text("12\n", encoding="ascii")
    (parent_dir / "memory.max").write_text("4096\n", encoding="ascii")
    (parent_dir / "pids.events").write_text("max 3\n", encoding="ascii")
    (parent_dir / "memory.events").write_text(
        "max 2\noom 3\noom_kill 4\noom_group_kill 1\n", encoding="ascii"
    )
    (parent_dir / "pids.events.local").write_text("max 3\n", encoding="ascii")
    (parent_dir / "memory.events.local").write_text(
        "max 2\noom 3\noom_kill 4\noom_group_kill 1\n", encoding="ascii"
    )
    _lock_cgroup_controls(mount_point, parent_dir, cgroup_dir)
    cgroup_file = tmp_path / "proc-cgroup"
    cgroup_file.write_text("0::/worker/lane\n", encoding="utf-8")
    mountinfo_file = tmp_path / "mountinfo"
    mountinfo_file.write_text(
        f"31 23 0:28 / {mount_point} ro - cgroup2 cgroup rw\n",
        encoding="utf-8",
    )

    counters = read_current_cgroup_counters(
        cgroup_file=cgroup_file, mountinfo_file=mountinfo_file
    )

    assert counters.pids_max == 3
    assert counters.memory_max == 2
    assert counters.memory_oom == 3
    assert counters.memory_oom_kill == 4
    assert counters.memory_oom_group_kill == 1


def test_reader_samples_unlimited_active_ancestors_for_local_events(
    tmp_path: Path,
):
    mount_point = tmp_path / "cgroup"
    limited = mount_point / "limited"
    active = limited / "active"
    candidate = active / "lane"
    candidate.mkdir(parents=True)
    (limited / "pids.max").write_text("32\n", encoding="ascii")
    (limited / "memory.max").write_text("8192\n", encoding="ascii")
    (limited / "pids.events").write_text("max 2\n", encoding="ascii")
    (limited / "memory.events").write_text(
        "max 1\noom 2\noom_kill 3\noom_group_kill 4\n", encoding="ascii"
    )
    (active / "pids.max").write_text("max\n", encoding="ascii")
    (active / "memory.max").write_text("max\n", encoding="ascii")
    (active / "pids.events").write_text("max 5\n", encoding="ascii")
    (active / "memory.events").write_text(
        "max 6\noom 7\noom_kill 8\noom_group_kill 9\n", encoding="ascii"
    )
    for directory, pids_events, memory_events in (
        (limited, "max 2\n", "max 1\noom 2\noom_kill 3\noom_group_kill 4\n"),
        (active, "max 5\n", "max 6\noom 7\noom_kill 8\noom_group_kill 9\n"),
    ):
        (directory / "pids.events.local").write_text(pids_events, encoding="ascii")
        (directory / "memory.events.local").write_text(memory_events, encoding="ascii")
    _lock_cgroup_controls(mount_point, limited, active, candidate)
    cgroup_file = tmp_path / "proc-cgroup"
    cgroup_file.write_text("0::/limited/active/lane\n", encoding="utf-8")
    mountinfo_file = tmp_path / "mountinfo"
    mountinfo_file.write_text(
        f"31 23 0:28 / {mount_point} ro - cgroup2 cgroup rw\n",
        encoding="utf-8",
    )

    counters = read_current_cgroup_counters(
        cgroup_file=cgroup_file, mountinfo_file=mountinfo_file
    )

    assert counters.pids_max == 7
    assert counters.memory_max == 7
    assert counters.memory_oom == 9
    assert counters.memory_oom_kill == 11
    assert counters.memory_oom_group_kill == 13


def test_reader_refuses_clone_migration_when_candidate_cgroup_procs_is_writable(
    tmp_path: Path,
):
    mount_point = tmp_path / "cgroup"
    parent_dir = mount_point / "worker"
    candidate = parent_dir / "lane"
    candidate.mkdir(parents=True)
    _lock_cgroup_controls(mount_point, parent_dir, candidate)
    (candidate / "cgroup.procs").chmod(0o644)
    cgroup_file = tmp_path / "proc-cgroup"
    cgroup_file.write_text("0::/worker/lane\n", encoding="utf-8")
    mountinfo_file = tmp_path / "mountinfo"
    mountinfo_file.write_text(
        f"31 23 0:28 / {mount_point} ro - cgroup2 cgroup rw\n",
        encoding="utf-8",
    )

    with pytest.raises(ResourceLimitObservationError, match="can write.*cgroup.procs"):
        read_current_cgroup_counters(
            cgroup_file=cgroup_file, mountinfo_file=mountinfo_file
        )


def test_reader_refuses_a_writable_cgroup_mount(tmp_path: Path):
    mount_point = tmp_path / "cgroup"
    mount_point.mkdir()
    cgroup_file = tmp_path / "proc-cgroup"
    cgroup_file.write_text("0::/worker/lane\n", encoding="utf-8")
    mountinfo_file = tmp_path / "mountinfo"
    mountinfo_file.write_text(
        f"31 23 0:28 / {mount_point} ro - cgroup2 cgroup rw\n"
        f"32 24 0:29 /worker/lane/child {tmp_path / 'second-cgroup'} rw - cgroup2 cgroup rw\n",
        encoding="utf-8",
    )

    with pytest.raises(
        ResourceLimitObservationError,
        match="cgroup2 mount exposes the candidate hierarchy writable",
    ):
        read_current_cgroup_counters(
            cgroup_file=cgroup_file, mountinfo_file=mountinfo_file
        )


def test_limit_configuration_change_invalidates_candidate_window():
    before = ResourceLimitCounters(
        pids_max=0,
        memory_max=0,
        memory_oom=0,
        memory_oom_kill=0,
        memory_oom_group_kill=0,
        limit_signature=(
            ("/worker/lane", 31, True, 12, True, 4096, True, True, True, True, False, False),
        ),
    )
    after = replace(
        before,
        limit_signature=(
            ("/worker/lane", 31, True, 12, True, 8192, True, True, True, True, False, False),
        ),
    )

    with pytest.raises(
        ResourceLimitObservationError,
        match=r"cgroup\[0\]\.memory\.max changed: 4096 -> 8192",
    ) as failure:
        ResourceLimitEvidence.between(before, after)
    assert str(failure.value) == (
        "cgroup resource-limit observation changed during a candidate command: "
        "cgroup[0].memory.max changed: 4096 -> 8192"
    )


def test_event_interface_change_invalidates_candidate_window():
    before = ResourceLimitCounters(
        pids_max=0,
        memory_max=0,
        memory_oom=0,
        memory_oom_kill=0,
        memory_oom_group_kill=0,
        limit_signature=(
            ("/worker/lane", 31, False, None, False, None, True, True, True, True, False, False),
        ),
    )
    after = replace(
        before,
        limit_signature=(
            ("/worker/lane", 31, False, None, False, None, False, True, False, True, False, False),
        ),
    )

    with pytest.raises(ResourceLimitObservationError, match="changed"):
        ResourceLimitEvidence.between(before, after)


def test_mount_identity_change_invalidates_candidate_window():
    before = ResourceLimitCounters(
        pids_max=0,
        memory_max=0,
        memory_oom=0,
        memory_oom_kill=0,
        memory_oom_group_kill=0,
        limit_signature=(
            ("/worker/lane", 31, True, 12, True, 4096, True, True, True, True, False, False),
        ),
    )
    after = replace(
        before,
        limit_signature=(
            ("/worker/lane", 32, True, 12, True, 4096, True, True, True, True, False, False),
        ),
    )

    with pytest.raises(ResourceLimitObservationError, match=r"\.mount_id changed: 31 -> 32"):
        ResourceLimitEvidence.between(before, after)


def test_resource_evidence_parser_rejects_forged_counter_arithmetic():
    raw = _evidence(pids=(3, 4)).to_dict()
    raw["pids_events"]["max"]["delta"] = 0

    with pytest.raises(ValueError, match="after minus before"):
        ResourceLimitEvidence.from_dict(raw)


def test_shared_ancestor_memory_max_is_telemetry_not_attribution_failure():
    leaf = ("/candidate", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    parent_before = ("/gates", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    parent_after = ("/gates", 0, 1, 0, 0, 0, 0, 1, 0, 0, 0)
    before = replace(_zero_counters(), path_event_counters=(leaf, parent_before))
    after = replace(_zero_counters(), path_event_counters=(leaf, parent_after))

    evidence = ResourceLimitEvidence.between(before, after)

    assert evidence.memory_events_max.delta == 0
    assert evidence.limit_hit is False


def test_successful_reclaim_memory_max_event_is_conservatively_not_a_kill():
    evidence = _evidence(memory_max=(4, 5))

    assert evidence.limit_hit is False
    assert evidence.memory_events_max.delta == 1
    assert evidence.memory_events_oom.delta == 0
    assert evidence.memory_events_oom_kill.delta == 0


def test_crashed_resource_limited_outcome_makes_r2_an_error():
    outcome = replace(
        native_outcome(
            path="src/mod.py",
            lineno=1,
            start_byte=0,
            end_byte=1,
            replacement_sha256="a" * 64,
            operator="python:compare-swap",
            description="x < y",
        ),
        resource_limit_evidence=_evidence(pids=(4, 5)),
    )
    mutation = Mutation(
        candidate_count=1,
        total=1,
        **{
            name: (outcome,) if name == "crashed" else ()
            for name in MUTATION_BUCKETS
        },
        candidate_ids=(outcome.candidate_id,),
    )
    status = judge_mutation(
        baseline=type("Baseline", (), {"outcome": Outcome.PASS, "reason_code": None})(),
        mutation=mutation,
    )

    assert status == (Outcome.ERROR, ReasonCode.EXEC_FAILED)


def test_model_rejects_a_resource_limited_candidate_as_killed():
    outcome = replace(
        native_outcome(
            path="src/mod.py",
            lineno=1,
            start_byte=0,
            end_byte=1,
            replacement_sha256="a" * 64,
            operator="python:compare-swap",
            description="x < y",
        ),
        resource_limit_evidence=_evidence(oom_kill=(3, 4)),
    )
    with pytest.raises(ValueError, match="cannot be classified as killed"):
        Mutation(
            candidate_count=1,
            total=1,
            **{
                name: (outcome,) if name == "killed" else ()
                for name in MUTATION_BUCKETS
            },
            candidate_ids=(outcome.candidate_id,),
        )


def test_native_outcome_can_omit_an_unobserved_resource_window():
    outcome = native_outcome(
        path="src/mod.py",
        lineno=1,
        start_byte=0,
        end_byte=1,
        replacement_sha256="a" * 64,
        operator="python:compare-swap",
        description="x < y",
    )

    unobserved = replace(outcome, resource_limit_evidence=None)

    assert unobserved.resource_limit_evidence is None
    assert "resource_limit_evidence" not in unobserved.to_dict()


def test_ingested_outcome_cannot_claim_local_resource_evidence():
    with pytest.raises(ValueError, match="ingested MutantOutcome cannot carry"):
        MutantOutcome(
            path="src/mod.py",
            lineno=1,
            start_byte=0,
            end_byte=1,
            replacement_sha256="a" * 64,
            operator="python:compare-swap",
            description="x < y",
            resource_limit_evidence=_evidence(),
        )


@pytest.mark.parametrize(
    "evidence",
    [
        _evidence(pids=(0, 1)),
        _evidence(oom=(0, 1)),
    ],
)
def test_raw_verifier_rejects_a_positive_limit_counter_delta_in_killed(evidence):
    entry = native_outcome(
        path="src/mod.py",
        lineno=1,
        start_byte=0,
        end_byte=1,
        replacement_sha256="a" * 64,
        operator="python:compare-swap",
        description="x < y",
    ).to_dict()
    entry["resource_limit_evidence"] = evidence.to_dict()
    failures: list[str] = []

    _check_b145_resource_limit_evidence("killed", entry, failures)

    assert any(
        "positive failed-fork or OOM counter delta" in item for item in failures
    )


@pytest.mark.parametrize("bucket", ["killed", "survived"])
def test_raw_verifier_accepts_positive_memory_max_telemetry(bucket):
    entry = native_outcome(
        path="src/mod.py",
        lineno=1,
        start_byte=0,
        end_byte=1,
        replacement_sha256="a" * 64,
        operator="python:compare-swap",
        description="x < y",
    ).to_dict()
    entry["resource_limit_evidence"] = _evidence(memory_max=(0, 1)).to_dict()
    failures: list[str] = []

    _check_b145_resource_limit_evidence(bucket, entry, failures)

    assert failures == []


def test_raw_verifier_accepts_a_positive_counter_delta_in_crashed():
    entry = native_outcome(
        path="src/mod.py",
        lineno=1,
        start_byte=0,
        end_byte=1,
        replacement_sha256="a" * 64,
        operator="python:compare-swap",
        description="x < y",
    ).to_dict()
    entry["resource_limit_evidence"] = _evidence(oom_group_kill=(0, 1)).to_dict()
    failures: list[str] = []

    _check_b145_resource_limit_evidence("crashed", entry, failures)

    assert failures == []


def test_raw_verifier_accepts_a_positive_counter_delta_in_survived():
    entry = native_outcome(
        path="src/mod.py",
        lineno=1,
        start_byte=0,
        end_byte=1,
        replacement_sha256="a" * 64,
        operator="python:compare-swap",
        description="x < y",
    ).to_dict()
    entry["resource_limit_evidence"] = _evidence(pids=(0, 1)).to_dict()
    failures: list[str] = []

    _check_b145_resource_limit_evidence("survived", entry, failures)

    assert failures == []


def test_raw_verifier_refuses_evidence_missing_memory_max_without_raising():
    entry = native_outcome(
        path="src/mod.py",
        lineno=1,
        start_byte=0,
        end_byte=1,
        replacement_sha256="a" * 64,
        operator="python:compare-swap",
        description="x < y",
    ).to_dict()
    evidence = _evidence().to_dict()
    del evidence["memory_events"]["max"]
    entry["resource_limit_evidence"] = evidence
    failures: list[str] = []

    _check_b145_resource_limit_evidence("crashed", entry, failures)

    assert any("invalid memory_events evidence" in failure for failure in failures)


def test_native_verdict_may_omit_an_unobserved_candidate_window():
    outcome = native_outcome(
        path="src/mod.py",
        lineno=1,
        start_byte=0,
        end_byte=1,
        replacement_sha256="a" * 64,
        operator="python:compare-swap",
        description="x < y",
    )
    unobserved = replace(outcome, resource_limit_evidence=None)

    assert "resource_limit_evidence" not in unobserved.to_dict()


def test_raw_verifier_accepts_omitted_resource_evidence_after_preflight():
    entry = native_outcome(
        path="src/mod.py",
        lineno=1,
        start_byte=0,
        end_byte=1,
        replacement_sha256="a" * 64,
        operator="python:compare-swap",
        description="x < y",
    ).to_dict()
    del entry["resource_limit_evidence"]
    failures: list[str] = []

    _check_b145_resource_limit_evidence("survived", entry, failures)

    assert failures == []


def test_raw_verifier_rejects_unobserved_kill_without_a_clean_report():
    entry = native_outcome(
        path="src/mod.py",
        lineno=1,
        start_byte=0,
        end_byte=1,
        replacement_sha256="a" * 64,
        operator="python:compare-swap",
        description="x < y",
    ).to_dict()
    del entry["resource_limit_evidence"]
    failures: list[str] = []

    _check_b145_resource_limit_evidence("killed", entry, failures)

    assert any("clean failed-call witness" in failure for failure in failures)


def test_raw_verifier_accepts_unobserved_kill_with_a_clean_report():
    entry = native_outcome(
        path="src/mod.py",
        lineno=1,
        start_byte=0,
        end_byte=1,
        replacement_sha256="a" * 64,
        operator="python:compare-swap",
        description="x < y",
    ).to_dict()
    del entry["resource_limit_evidence"]
    entry["execution"]["witness"] = {
        "node_id": "tests/test_mod.py::test_case",
        "when": "call",
        "outcome": "failed",
        "session_exit_status": 1,
        "process_exit_status": 1,
    }
    failures: list[str] = []

    _check_b145_resource_limit_evidence("killed", entry, failures)

    assert failures == []


def test_raw_verifier_report_precedence_accepts_a_positive_limit_event():
    entry = native_outcome(
        path="src/mod.py",
        lineno=1,
        start_byte=0,
        end_byte=1,
        replacement_sha256="a" * 64,
        operator="python:compare-swap",
        description="x < y",
    ).to_dict()
    entry["resource_limit_evidence"]["pids_events"]["max"].update(
        before=0, after=1, delta=1
    )
    entry["execution"]["witness"] = {
        "node_id": "tests/test_mod.py::test_case",
        "when": "call",
        "outcome": "failed",
        "session_exit_status": 1,
        "process_exit_status": 1,
    }
    failures: list[str] = []

    _check_b145_resource_limit_evidence("killed", entry, failures)

    assert failures == []


@pytest.mark.parametrize(
    ("field", "value"),
    [("delta", 0), ("before", True)],
)
def test_raw_verifier_independently_rejects_invalid_counter_arithmetic(field, value):
    entry = native_outcome(
        path="src/mod.py",
        lineno=1,
        start_byte=0,
        end_byte=1,
        replacement_sha256="a" * 64,
        operator="python:compare-swap",
        description="x < y",
    ).to_dict()
    counter = entry["resource_limit_evidence"]["pids_events"]["max"]
    counter.update(before=4, after=5, delta=1)
    counter[field] = value
    failures: list[str] = []

    _check_b145_resource_limit_evidence("crashed", entry, failures)

    assert failures
    assert any("pids_events.max" in item for item in failures)


def test_candidate_counter_delta_is_persisted_and_overrides_a_kill(
    tmp_path: Path, monkeypatch
):
    source = "def flags():\n    return True\n"
    repo = GitRepo(path=tmp_path / "repo")
    repo.path.mkdir()
    repo.git("init", "-q", "-b", "main")
    repo.git("config", "user.email", "assay-tests@example.com")
    repo.git("config", "user.name", "assay tests")
    repo.write("pkg/flags.py", source)
    repo.commit_all("add flags")

    def baseline_runner(argv, *, env, cwd, timeout):
        return completed_process(list(argv), returncode=0)

    baseline = execute_command(
        make_lane(argv=("pytest", "-q")),
        cwd=repo.path,
        process_runner=baseline_runner,
    )
    assert baseline.outcome is Outcome.PASS

    samples = iter(
        (
            ResourceLimitCounters(
                pids_max=0,
                memory_max=0,
                memory_oom=0,
                memory_oom_kill=0,
                memory_oom_group_kill=0,
            ),  # mutation preflight
            ResourceLimitCounters(
                pids_max=0,
                memory_max=0,
                memory_oom=0,
                memory_oom_kill=0,
                memory_oom_group_kill=0,
            ),  # before candidate
            ResourceLimitCounters(
                pids_max=1,
                memory_max=0,
                memory_oom=0,
                memory_oom_kill=0,
                memory_oom_group_kill=0,
            ),  # after candidate
        )
    )
    monkeypatch.setattr(
        "assay.mutation.read_candidate_resource_counters",
        lambda **_kwargs: next(samples),
    )

    def failing_candidate_runner(argv, *, env, cwd, timeout):
        result = completed_process(list(argv), returncode=1)
        from assay.process_tree import ProcessTreeEvidence

        result.process_tree_evidence = ProcessTreeEvidence(
            leader_status="exited",
            group_signalled_before_reap=False,
            outliving_descendants=0,
            descendants_signalled=0,
            cleanup_seconds=0.001,
            clean=True,
        ).to_dict()
        return result

    state_root = tmp_path / "state"
    progress = tmp_path / "progress.jsonl"
    scratch_root = tmp_path / "scratch"
    scratch_root.mkdir()
    with prepared_snapshot(repo, scratch_root=scratch_root) as prepared:
        result = run_mutation(
            baseline=baseline,
            prepared=prepared,
            plan=make_plan(make_lane(argv=("pytest", "-q"))),
            deadline=make_deadline(),
            targets=(
                MutationTarget(path="pkg/flags.py", text=source, lines=frozenset({2})),
            ),
            adapter=PythonAdapter(),
            jobs=1,
            max_mutants=10,
            operators=("python:bool-const-flip",),
            process_runner=failing_candidate_runner,
            clock=lambda: datetime(2026, 10, 5, tzinfo=timezone.utc),
            state_root=state_root,
            progress_artifact=progress,
        )

    assert not isinstance(result, str)
    assert result.killed == ()
    assert len(result.crashed) == result.total == 1
    expected = _evidence(pids=(0, 1)).to_dict()
    state_record, = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in state_root.glob("*.json")
    ]
    progress_records = [
        json.loads(line)
        for line in progress.read_text(encoding="utf-8").splitlines()
    ]
    progress_record, = [
        record for record in progress_records if record.get("event") == "candidate"
    ]
    assert (
        state_record["outcome_bucket"]
        == progress_record["outcome_bucket"]
        == "crashed"
    )
    assert state_record["resource_limit_evidence"] == expected
    assert progress_record["resource_limit_evidence"] == expected


def test_worker_start_failure_is_a_payload_free_execution_error(
    tmp_path: Path, monkeypatch
):
    source = "def flags():\n    return True\n"
    repo = GitRepo(path=tmp_path / "repo")
    repo.path.mkdir()
    repo.git("init", "-q", "-b", "main")
    repo.git("config", "user.email", "assay-tests@example.com")
    repo.git("config", "user.name", "assay tests")
    repo.write("pkg/flags.py", source)
    repo.commit_all("add flags")

    def baseline_runner(argv, *, env, cwd, timeout):
        return completed_process(list(argv), returncode=0)

    baseline = execute_command(
        make_lane(argv=("pytest", "-q")),
        cwd=repo.path,
        process_runner=baseline_runner,
    )
    monkeypatch.setattr(
        "assay.mutation.read_candidate_resource_counters",
        lambda **_kwargs: ResourceLimitCounters(
            pids_max=0,
            memory_max=0,
            memory_oom=0,
            memory_oom_kill=0,
            memory_oom_group_kill=0,
        ),
    )

    class ThreadLimitedExecutor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def submit(self, function, position):
            raise RuntimeError("can't start new thread")

    scratch_root = tmp_path / "scratch"
    scratch_root.mkdir()
    with prepared_snapshot(repo, scratch_root=scratch_root) as prepared:
        with pytest.raises(
            AssayError, match="could not start a native R2 candidate worker"
        ) as caught:
            run_mutation(
                baseline=baseline,
                prepared=prepared,
                plan=make_plan(make_lane(argv=("pytest", "-q"))),
                deadline=make_deadline(),
                targets=(
                    MutationTarget(
                        path="pkg/flags.py", text=source, lines=frozenset({2})
                    ),
                ),
                adapter=PythonAdapter(),
                jobs=1,
                max_mutants=10,
                operators=("python:bool-const-flip",),
                process_runner=baseline_runner,
                clock=lambda: datetime(2026, 10, 5, tzinfo=timezone.utc),
                executor_factory=lambda _jobs: ThreadLimitedExecutor(),
            )

    assert caught.value.outcome is Outcome.ERROR
    assert caught.value.reason_code is ReasonCode.EXEC_FAILED


@pytest.mark.skipif(
    os.environ.get("ASSAY_B145_LOW_PIDS_PROBE") != "1",
    reason="requires the dedicated tester-unified --pids-limit acceptance container",
)
def test_low_pids_limit_event_cannot_become_a_kill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source = "def flags():\n    return True\n"
    repo = GitRepo(path=tmp_path / "repo")
    repo.path.mkdir()
    repo.git("init", "-q", "-b", "main")
    repo.git("config", "user.email", "assay-tests@example.com")
    repo.git("config", "user.name", "assay tests")
    repo.write("README.md", "seed\n")
    repo.commit_all("seed")
    repo.write("pkg/flags.py", source)
    repo.commit_all("add flag target")
    code = "\n".join(
        (
            "import errno, os",
            "from pkg.flags import flags",
            "if flags():",
            "    raise SystemExit(0)",
            "children = []",
            "release_r, release_w = os.pipe()",
            "status = 0",
            "try:",
            "    for _ in range(64):",
            "        try:",
            "            child = os.fork()",
            "        except OSError as exc:",
            "            if exc.errno != errno.EAGAIN:",
            "                raise",
            "            status = 1",
            "            break",
            "        if child == 0:",
            "            os.close(release_w)",
            "            os.read(release_r, 1)",
            "            os.close(release_r)",
            "            os._exit(0)",
            "        children.append(child)",
            "finally:",
            "    os.close(release_w)",
            "    os.close(release_r)",
            "    for child in children:",
            "        os.waitpid(child, 0)",
            "raise SystemExit(status)",
        )
    )
    lane = make_lane(
        argv=(sys.executable, "-c", code),
        env={"PYTHONDONTWRITEBYTECODE": "1"},
    )
    baseline = execute_command(
        lane, cwd=repo.path, process_runner=default_process_runner
    )
    assert baseline.outcome is Outcome.PASS
    # This is the one real container acceptance probe in this module. Its
    # cgroup paths and mount IDs must come from the kernel, not the synthetic
    # mount identity installed by the fixture for filesystem-backed unit cases.
    monkeypatch.setattr(
        resource_limits, "_mount_id_for_fd", _REAL_MOUNT_ID_FOR_FD
    )
    scratch_root = tmp_path / "scratch"
    scratch_root.mkdir()
    with prepared_snapshot(repo, scratch_root=scratch_root) as prepared:
        mutation = run_mutation(
            baseline=baseline,
            prepared=prepared,
            plan=make_plan(lane),
            deadline=make_deadline(budget_seconds=30.0),
            targets=(
                MutationTarget(path="pkg/flags.py", text=source, lines=frozenset({2})),
            ),
            adapter=PythonAdapter(),
            jobs=1,
            max_mutants=1,
            operators=("python:bool-const-flip",),
            process_runner=default_process_runner,
            clock=lambda: datetime.now(timezone.utc),
        )

    assert not isinstance(mutation, str)
    assert mutation.killed == ()
    assert len(mutation.crashed) == mutation.total == 1
    assert mutation.crashed[0].resource_limit_evidence is not None
    assert mutation.crashed[0].resource_limit_evidence.pids_events_max.delta > 0
    assert judge_mutation(baseline=baseline, mutation=mutation) == (
        Outcome.ERROR,
        ReasonCode.EXEC_FAILED,
    )


def test_mutation_counter_reader_delegates_and_wraps_observer_failures(
    monkeypatch: pytest.MonkeyPatch,
):
    counters = ResourceLimitCounters(
        pids_max=0,
        memory_oom=0,
        memory_max=0,
        memory_oom_kill=0,
        memory_oom_group_kill=0,
    )

    observer_path = "/run/cgprofile-observer/resource-observer.sock"
    monkeypatch.setenv("ASSAY_RESOURCE_OBSERVER_SOCKET", observer_path)
    calls = []

    def observe(**kwargs):
        calls.append(kwargs)
        return counters

    monkeypatch.setattr(mutation_module, "read_candidate_resource_counters", observe)
    assert mutation_module._read_candidate_resource_counters() is counters
    assert calls == [{"observer_socket_path": observer_path}]

    def refused(**_kwargs):
        raise ResourceLimitObservationError("observer unavailable")

    monkeypatch.setattr(mutation_module, "read_candidate_resource_counters", refused)
    with pytest.raises(AssayError, match="required cgroup v2.*observer unavailable") as failure:
        mutation_module._read_candidate_resource_counters()
    assert failure.value.outcome is Outcome.NO_MEASUREMENT
    assert failure.value.reason_code is ReasonCode.CGROUP_OBSERVATION_UNAVAILABLE

    counters, reason = mutation_module._read_candidate_resource_counters_during_campaign()
    assert counters is None
    assert reason == (
        "cannot observe the required cgroup v2 process and memory limit events "
        "for native R2 candidate execution: observer unavailable"
    )


def test_candidate_counter_comparison_failure_is_unobserved_telemetry(
    monkeypatch: pytest.MonkeyPatch,
):
    def refused(*_args, **_kwargs):
        raise ResourceLimitObservationError("shared parent event is ambiguous")

    monkeypatch.setattr(ResourceLimitEvidence, "between", classmethod(refused))
    counters = ResourceLimitCounters(
        pids_max=0,
        memory_oom=0,
        memory_max=0,
        memory_oom_kill=0,
        memory_oom_group_kill=0,
    )
    evidence, reason = mutation_module._candidate_resource_limit_evidence(
        counters, counters
    )
    assert evidence is None
    assert reason == "shared parent event is ambiguous"


def test_limit_signature_detail_names_removed_and_added_cgroups():
    entry = (
        "/candidate",
        31,
        True,
        None,
        True,
        4096,
        True,
        True,
        True,
        True,
        False,
        False,
    )

    def counters(signature):
        return ResourceLimitCounters(
            pids_max=0,
            memory_oom=0,
            memory_max=0,
            memory_oom_kill=0,
            memory_oom_group_kill=0,
            limit_signature=signature,
        )

    assert resource_limits._limit_signature_change_detail(
        counters((entry,)), counters(())
    ) == "cgroup[0] removed: path=/candidate, mount_id=31"
    assert resource_limits._limit_signature_change_detail(
        counters(()), counters((entry,))
    ) == "cgroup[0] added: path=/candidate, mount_id=31"


@pytest.mark.parametrize(
    ("failure_read", "failure_endpoint"),
    [(4, "before"), (5, "after")],
)
def test_candidate_observer_loss_is_recorded_without_aborting_the_campaign(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure_read: int,
    failure_endpoint: str,
):
    source = "def flags():\n    return True and False\n"
    repo, lane, baseline, scratch_root = _run_mutation_observation_fixture(
        tmp_path, source
    )
    zero = _zero_counters()
    before_window = replace(
        zero,
        limit_signature=(
            ("/candidate", 31, True, None, True, 4096, True, True, True, True, False, False),
        ),
    )
    changed_limit = replace(
        zero,
        limit_signature=(
            ("/candidate", 31, True, None, True, 8192, True, True, True, True, False, False),
        ),
    )
    reads = iter((before_window, before_window, changed_limit, changed_limit))
    read_count = 0

    def read_counters(**_kwargs):
        nonlocal read_count
        read_count += 1
        if read_count == failure_read:
            raise ResourceLimitObservationError(
                f"observer disappeared {failure_endpoint} candidate 2"
            )
        return next(reads)

    monkeypatch.setattr(mutation_module, "read_candidate_resource_counters", read_counters)
    candidate_calls: list[tuple[str, ...]] = []

    def run_candidate(argv, *, env, cwd, timeout):
        candidate_calls.append(tuple(argv))
        result = completed_process(list(argv), returncode=0 if len(candidate_calls) == 1 else 1)
        result.process_tree_evidence = ProcessTreeEvidence(
            leader_status="exited",
            group_signalled_before_reap=False,
            outliving_descendants=0,
            descendants_signalled=0,
            cleanup_seconds=0.001,
            clean=True,
        ).to_dict()
        return result

    state_root = tmp_path / "state"
    progress_path = tmp_path / "progress.jsonl"
    with prepared_snapshot(repo, scratch_root=scratch_root) as prepared:
        mutation = run_mutation(
            baseline=baseline,
            prepared=prepared,
            plan=make_plan(lane),
            deadline=make_deadline(),
            targets=(MutationTarget(
                path="pkg/flags.py", text=source, lines=frozenset({2})
            ),),
            adapter=PythonAdapter(),
            jobs=1,
            max_mutants=10,
            operators=("python:bool-const-flip",),
            process_runner=run_candidate,
            clock=lambda: datetime(2026, 10, 5, tzinfo=timezone.utc),
            state_root=state_root,
            progress_artifact=progress_path,
        )

    assert len(candidate_calls) == 2
    assert len(mutation.survived) == 1
    assert mutation.survived[0].resource_limit_evidence is None
    assert mutation.killed == ()
    assert len(mutation.crashed) == 1
    assert mutation.crashed[0].resource_limit_evidence is None
    state_records = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in state_root.glob("*.json")
    ]
    unobserved_state = [record for record in state_records if "resource_observation" in record]
    assert len(unobserved_state) == 2
    assert all("resource_limit_evidence" not in record for record in unobserved_state)
    assert any("memory.max changed: 4096 -> 8192" in record["resource_observation"]["reason"]
               for record in unobserved_state)
    assert any(f"observer disappeared {failure_endpoint} candidate 2" in record["resource_observation"]["reason"]
               for record in unobserved_state)
    progress_records = [
        json.loads(line)
        for line in progress_path.read_text(encoding="utf-8").splitlines()
    ]
    candidate_records = [
        record for record in progress_records if record.get("event") == "candidate"
    ]
    assert len(candidate_records) == 2
    assert sorted(record["outcome_bucket"] for record in candidate_records) == [
        "crashed",
        "survived",
    ]
    assert all("resource_limit_evidence" not in record for record in candidate_records)
    assert all(record["resource_observation"]["status"] == "unobserved"
               for record in candidate_records)
    advisory_lines = [
        line for line in capsys.readouterr().err.splitlines()
        if "resource-limit window unobserved" in line
    ]
    assert len(advisory_lines) == 2
    assert any("memory.max changed: 4096 -> 8192" in line for line in advisory_lines)
    assert any(f"observer disappeared {failure_endpoint} candidate 2" in line for line in advisory_lines)


def test_pass_with_a_limit_event_remains_survived(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    source = "def flags():\n    return True\n"
    repo, lane, baseline, scratch_root = _run_mutation_observation_fixture(
        tmp_path, source
    )
    before = _zero_counters()
    after = replace(before, pids_max=1)
    samples = iter((before, before, after))
    monkeypatch.setattr(
        mutation_module,
        "read_candidate_resource_counters",
        lambda **_kwargs: next(samples),
    )
    candidate_calls: list[tuple[str, ...]] = []
    state_root = tmp_path / "state"
    with prepared_snapshot(repo, scratch_root=scratch_root) as prepared:
        mutation = run_mutation(
            baseline=baseline,
            prepared=prepared,
            plan=make_plan(lane),
            deadline=make_deadline(),
            targets=(MutationTarget(
                path="pkg/flags.py", text=source, lines=frozenset({2})
            ),),
            adapter=PythonAdapter(),
            jobs=1,
            max_mutants=1,
            operators=("python:bool-const-flip",),
            process_runner=_clean_candidate_runner(candidate_calls),
            clock=lambda: datetime(2026, 10, 5, tzinfo=timezone.utc),
            state_root=state_root,
            progress_artifact=tmp_path / "progress.jsonl",
        )

    assert mutation.survived[0].resource_limit_evidence is not None
    assert mutation.survived[0].resource_limit_evidence.limit_hit is True
    assert mutation.crashed == ()


def _run_mutation_observation_fixture(tmp_path: Path, source: str):
    repo = GitRepo(path=tmp_path / "repo")
    repo.path.mkdir()
    repo.git("init", "-q", "-b", "main")
    repo.git("config", "user.email", "assay-tests@example.com")
    repo.git("config", "user.name", "assay tests")
    repo.write("pkg/flags.py", source)
    repo.commit_all("add mutation target")
    lane = make_lane(argv=("pytest", "-q"))
    baseline = execute_command(
        lane,
        cwd=repo.path,
        process_runner=lambda argv, **_kwargs: completed_process(list(argv), returncode=0),
    )
    scratch_root = tmp_path / "scratch"
    scratch_root.mkdir()
    return repo, lane, baseline, scratch_root


def _clean_candidate_runner(calls: list[tuple[str, ...]]):
    def run(argv, *, env, cwd, timeout):
        calls.append(tuple(argv))
        result = completed_process(list(argv), returncode=0)
        result.process_tree_evidence = ProcessTreeEvidence(
            leader_status="exited",
            group_signalled_before_reap=False,
            outliving_descendants=0,
            descendants_signalled=0,
            cleanup_seconds=0.001,
            clean=True,
        ).to_dict()
        return result

    return run


def _zero_counters() -> ResourceLimitCounters:
    return ResourceLimitCounters(
        pids_max=0,
        memory_oom=0,
        memory_max=0,
        memory_oom_kill=0,
        memory_oom_group_kill=0,
    )


def test_run_mutation_observer_loss_after_second_candidate_is_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    source = "def flags():\n    return True and False\n"
    repo, lane, baseline, scratch_root = _run_mutation_observation_fixture(
        tmp_path, source
    )
    reads = 0
    zero = _zero_counters()

    def read_counters(**_kwargs):
        nonlocal reads
        reads += 1
        if reads == 5:
            raise ResourceLimitObservationError("observer disappeared after candidate 2")
        return zero

    monkeypatch.setattr(mutation_module, "read_candidate_resource_counters", read_counters)
    candidate_calls: list[tuple[str, ...]] = []
    state_root = tmp_path / "state"
    progress_path = tmp_path / "progress.jsonl"
    with prepared_snapshot(repo, scratch_root=scratch_root) as prepared:
        mutation = run_mutation(
            baseline=baseline,
            prepared=prepared,
            plan=make_plan(lane),
            deadline=make_deadline(),
            targets=(MutationTarget(
                path="pkg/flags.py", text=source, lines=frozenset({2})
            ),),
            adapter=PythonAdapter(),
            jobs=1,
            max_mutants=10,
            operators=("python:bool-const-flip",),
            process_runner=_clean_candidate_runner(candidate_calls),
            clock=lambda: datetime(2026, 10, 5, tzinfo=timezone.utc),
            state_root=state_root,
            progress_artifact=progress_path,
        )

    assert len(candidate_calls) == 2
    assert len(mutation.survived) == 2
    assert mutation.survived[0].resource_limit_evidence is not None
    assert mutation.survived[1].resource_limit_evidence is None
    saved_records = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in state_root.glob("*.json")
    ]
    assert len(saved_records) == 2
    unobserved = [record for record in saved_records if "resource_observation" in record]
    assert len(unobserved) == 1
    assert unobserved[0]["resource_observation"] == {
        "status": "unobserved",
        "reason": "after: cannot observe the required cgroup v2 process and memory limit events for native R2 candidate execution: observer disappeared after candidate 2",
    }
    progress_records = [
        json.loads(line)
        for line in progress_path.read_text(encoding="utf-8").splitlines()
    ]
    candidate_records = [
        record for record in progress_records if record.get("event") == "candidate"
    ]
    assert len(candidate_records) == 2
    assert sum("resource_observation" in record for record in candidate_records) == 1


def test_run_mutation_unattributable_ancestor_event_keeps_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    source = "def flags():\n    return True\n"
    repo, lane, baseline, scratch_root = _run_mutation_observation_fixture(
        tmp_path, source
    )
    leaf_before = ("/candidate", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    parent_before = ("/gates", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    parent_after = ("/gates", 1, 0, 0, 0, 0, 1, 0, 0, 0, 0)
    before = ResourceLimitCounters(
        pids_max=0, memory_oom=0, memory_max=0, memory_oom_kill=0,
        memory_oom_group_kill=0,
        path_event_counters=(leaf_before, parent_before),
    )
    after = ResourceLimitCounters(
        pids_max=1, memory_oom=0, memory_max=0, memory_oom_kill=0,
        memory_oom_group_kill=0,
        path_event_counters=(leaf_before, parent_after),
    )
    samples = iter((before, before, after))
    monkeypatch.setattr(
        mutation_module,
        "read_candidate_resource_counters",
        lambda **_kwargs: next(samples),
    )
    candidate_calls: list[tuple[str, ...]] = []
    state_root = tmp_path / "state"
    progress_path = tmp_path / "progress.jsonl"
    with prepared_snapshot(repo, scratch_root=scratch_root) as prepared:
        mutation = run_mutation(
            baseline=baseline,
            prepared=prepared,
            plan=make_plan(lane),
            deadline=make_deadline(),
            targets=(MutationTarget(
                path="pkg/flags.py", text=source, lines=frozenset({2})
            ),),
            adapter=PythonAdapter(),
            jobs=1,
            max_mutants=10,
            operators=("python:bool-const-flip",),
            process_runner=_clean_candidate_runner(candidate_calls),
            clock=lambda: datetime(2026, 10, 5, tzinfo=timezone.utc),
            state_root=state_root,
            progress_artifact=progress_path,
        )

    assert len(candidate_calls) == 1
    assert len(mutation.survived) == 1
    assert mutation.survived[0].resource_limit_evidence is None
    state_records = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in state_root.glob("*.json")
    ]
    assert len(state_records) == 1
    assert state_records[0]["resource_observation"]["status"] == "unobserved"
    assert (
        "shared ancestor resource event is not attributable"
        in state_records[0]["resource_observation"]["reason"]
    )
    progress_records = [
        json.loads(line)
        for line in progress_path.read_text(encoding="utf-8").splitlines()
    ]
    candidate_records = [
        record for record in progress_records if record.get("event") == "candidate"
    ]
    assert len(candidate_records) == 1
    assert candidate_records[0]["resource_observation"]["status"] == "unobserved"


def test_run_mutation_cleanup_error_takes_precedence_over_after_read_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    source = "def flags():\n    return True\n"
    repo, lane, baseline, scratch_root = _run_mutation_observation_fixture(
        tmp_path, source
    )
    reads = 0
    zero = _zero_counters()

    def read_counters(**_kwargs):
        nonlocal reads
        reads += 1
        if reads == 3:
            raise ResourceLimitObservationError("observer disappeared after command")
        return zero

    monkeypatch.setattr(mutation_module, "read_candidate_resource_counters", read_counters)

    def cleanup_fails(*_args, **_kwargs):
        raise ProcessTreeError("cannot prove the command window is empty")

    state_root = tmp_path / "state"
    progress_path = tmp_path / "progress.jsonl"
    with prepared_snapshot(repo, scratch_root=scratch_root) as prepared:
        with pytest.raises(AssayError, match="process-tree cleanup could not prove") as failure:
            run_mutation(
                baseline=baseline,
                prepared=prepared,
                plan=make_plan(lane),
                deadline=make_deadline(),
                targets=(MutationTarget(
                    path="pkg/flags.py", text=source, lines=frozenset({2})
                ),),
                adapter=PythonAdapter(),
                jobs=1,
                max_mutants=10,
                operators=("python:bool-const-flip",),
                process_runner=cleanup_fails,
                clock=lambda: datetime(2026, 10, 5, tzinfo=timezone.utc),
                state_root=state_root,
                progress_artifact=progress_path,
            )

    assert failure.value.outcome is Outcome.ERROR
    assert failure.value.reason_code is ReasonCode.EXEC_FAILED
    assert list(state_root.glob("*.json")) == []
    progress_records = [
        json.loads(line)
        for line in progress_path.read_text(encoding="utf-8").splitlines()
    ]
    assert not any(record.get("event") == "candidate" for record in progress_records)


def test_run_mutation_comparison_error_does_not_replace_execution_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    source = "def flags():\n    return True\n"
    repo, lane, baseline, scratch_root = _run_mutation_observation_fixture(
        tmp_path, source
    )
    leaf = ("/candidate", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    parent_before = ("/gates", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    parent_after = ("/gates", 1, 0, 0, 0, 0, 1, 0, 0, 0, 0)
    before = ResourceLimitCounters(
        pids_max=0, memory_oom=0, memory_max=0, memory_oom_kill=0,
        memory_oom_group_kill=0,
        path_event_counters=(leaf, parent_before),
    )
    after = ResourceLimitCounters(
        pids_max=1, memory_oom=0, memory_max=0, memory_oom_kill=0,
        memory_oom_group_kill=0,
        path_event_counters=(leaf, parent_after),
    )
    samples = iter((before, before, after))
    monkeypatch.setattr(
        mutation_module,
        "read_candidate_resource_counters",
        lambda **_kwargs: next(samples),
    )

    def execution_fails(*_args, **_kwargs):
        raise RuntimeError("candidate runner failed")

    state_root = tmp_path / "state"
    progress_path = tmp_path / "progress.jsonl"
    with prepared_snapshot(repo, scratch_root=scratch_root) as prepared:
        with pytest.raises(RuntimeError, match="candidate runner failed"):
            run_mutation(
                baseline=baseline,
                prepared=prepared,
                plan=make_plan(lane),
                deadline=make_deadline(),
                targets=(MutationTarget(
                    path="pkg/flags.py", text=source, lines=frozenset({2})
                ),),
                adapter=PythonAdapter(),
                jobs=1,
                max_mutants=10,
                operators=("python:bool-const-flip",),
                process_runner=execution_fails,
                clock=lambda: datetime(2026, 10, 5, tzinfo=timezone.utc),
                state_root=state_root,
                progress_artifact=progress_path,
            )

    assert list(state_root.glob("*.json")) == []
