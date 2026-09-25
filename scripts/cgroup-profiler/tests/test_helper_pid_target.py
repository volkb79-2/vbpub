"""Behavioral helper-mode PID mapping tests against isolated proc/cgroup fixtures."""

from __future__ import annotations

import os
from pathlib import Path
from typing import List

import pytest

import cgprofile as cg
from lib import access, damon as damon_lib, limits as limits_lib
from lib import metrics as metrics_lib, sampler as sampler_lib, store as store_lib
from lib import targets as targets_mod
from tests.conftest import cgroup_files, write_cgroup


def _fake_proc_stat(pid: int, start_ticks: int) -> str:
    fields = ["S", *(["0"] * 18), str(start_ticks)]
    return f"{pid} (worker (fixture)) {' '.join(fields)}\n"


def _write_process(
    proc_root: Path, pid: int, *, pid_namespace: Path, cgroup_namespace: Path,
    nspid: str, start_ticks: int, cgroup_path: str,
) -> None:
    process = proc_root / str(pid)
    process.mkdir(parents=True)
    ns = process / "ns"
    ns.mkdir()
    (ns / "pid").symlink_to(pid_namespace)
    (ns / "cgroup").symlink_to(cgroup_namespace)
    (process / "status").write_text(
        f"Name:\tworker\nNSpid:\t{nspid}\n"
        "RssAnon:\t321 kB\nRssFile:\t654 kB\nVmSwap:\t0 kB\n"
    )
    (process / "stat").write_text(_fake_proc_stat(pid, start_ticks))
    (process / "io").write_text("read_bytes: 1234\nwrite_bytes: 5678\n")
    (process / "cgroup").write_text(f"0::{cgroup_path}\n")


def _prepare_private_namespaced_target(
    monkeypatch, tmp_path: Path, cgroup_root: Path, *, process_in_target: bool = True,
) -> tuple[str, Path, str]:
    """Translate caller PID 4242 to its helper host-proc PID without PID guessing."""
    container_id = "a" * 64
    caller_pid = 4242
    helper_pid = 51001
    helper_local_pid = 10
    start_ticks = 98765
    caller_proc = tmp_path / "caller-proc"
    helper_proc = tmp_path / "hostproc"
    caller_pid_ns = tmp_path / "caller-pid-ns"
    unrelated_pid_ns = tmp_path / "unrelated-pid-ns"
    caller_cgroup_ns = tmp_path / "caller-cgroup-ns"
    unrelated_cgroup_ns = tmp_path / "unrelated-cgroup-ns"
    helper_pid_ns = tmp_path / "helper-pid-ns"
    helper_cgroup_ns = tmp_path / "helper-cgroup-ns"
    host_pid_ns = tmp_path / "host-pid-ns"
    for namespace in (
        caller_pid_ns, unrelated_pid_ns, caller_cgroup_ns, unrelated_cgroup_ns,
        helper_pid_ns, helper_cgroup_ns, host_pid_ns,
    ):
        namespace.touch()

    caller_process = caller_proc / str(caller_pid)
    caller_process.mkdir(parents=True)
    caller_ns = caller_process / "ns"
    caller_ns.mkdir()
    (caller_ns / "pid").symlink_to(caller_pid_ns)
    (caller_ns / "cgroup").symlink_to(caller_cgroup_ns)
    (caller_process / "status").write_text(f"Name:\tworker\nNSpid:\t{caller_pid}\n")
    (caller_process / "stat").write_text(_fake_proc_stat(caller_pid, start_ticks))
    (caller_process / "cgroup").write_text("0::/worker.scope\n")

    helper_scope = "dev.slice/dev-interactive.slice/cgprofile-helper.scope"
    container_root = f"dev.slice/dev-background.slice/docker-{container_id}.scope"
    selected = f"{container_root}/worker.scope"
    sibling = f"{container_root}/sibling.scope"
    write_cgroup(cgroup_root, helper_scope, cgroup_files(memory_current=0) | {
        "cgroup.procs": f"{helper_local_pid}\n",
    })
    write_cgroup(cgroup_root, container_root, cgroup_files() | {"cgroup.procs": ""})
    write_cgroup(cgroup_root, selected, cgroup_files() | {"cgroup.procs": "0\n"})
    write_cgroup(cgroup_root, sibling, cgroup_files() | {"cgroup.procs": "0\n"})

    helper_cgroup = selected if process_in_target else sibling
    helper_proc_path = f"/../../../{helper_cgroup}"
    host_pid_one = helper_proc / "1"
    (host_pid_one / "ns").mkdir(parents=True)
    (host_pid_one / "ns" / "pid").symlink_to(host_pid_ns)
    _write_process(
        helper_proc, helper_pid, pid_namespace=caller_pid_ns,
        cgroup_namespace=caller_cgroup_ns,
        nspid=f"{helper_pid} {caller_pid}", start_ticks=start_ticks,
        cgroup_path=helper_proc_path,
    )
    # Same cgroup and inner number, but a distinct PID namespace and process
    # start time. Numeric PID fields alone must not select this process.
    _write_process(
        helper_proc, helper_pid + 1, pid_namespace=unrelated_pid_ns,
        cgroup_namespace=caller_cgroup_ns,
        nspid=f"{helper_pid + 1} {caller_pid}", start_ticks=start_ticks + 1,
        cgroup_path=helper_proc_path,
    )

    monkeypatch.setattr(access, "PROC_ROOT", str(caller_proc))
    monkeypatch.setattr(access, "have_host_cgroup_view", lambda: False)
    monkeypatch.setattr(access, "self_container_id", lambda: container_id)
    cgroup_ns_inode = caller_cgroup_ns.stat().st_ino
    pid_ns_inode = caller_pid_ns.stat().st_ino
    monkeypatch.setattr(
        access, "local_namespace_inode",
        lambda name: cgroup_ns_inode if name == "cgroup" else pid_ns_inode if name == "pid" else None,
    )
    internal_spec = cg._predigest_specs([f"pid:{caller_pid}"], resolve_self=True)[0]

    # The helper has a private cgroup root and private PID view, but reads the
    # explicit broader proc bind. Its local membership anchors the namespace
    # root in the fake host cgroup tree, matching the deployed resolution path.
    monkeypatch.setattr(access, "PROC_ROOT", str(helper_proc))
    monkeypatch.setattr(access, "in_container", lambda: True)
    real_stat = access.os.stat

    def namespace_view_stat(path, *args, **kwargs):
        if os.fspath(path) == "/proc/1/ns/pid":
            return real_stat(helper_pid_ns, *args, **kwargs)
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(access.os, "stat", namespace_view_stat)
    monkeypatch.setattr(
        access, "local_namespace_inode",
        lambda name: helper_cgroup_ns.stat().st_ino if name == "cgroup"
        else helper_pid_ns.stat().st_ino if name == "pid" else None,
    )
    monkeypatch.setattr(targets_mod.os, "getpid", lambda: helper_local_pid)
    real_read_text = targets_mod.util.read_text

    def helper_read_text(path: str):
        if path == "/proc/self/cgroup":
            return "0::/\n"
        return real_read_text(path)

    monkeypatch.setattr(targets_mod.util, "read_text", helper_read_text)
    return internal_spec, helper_proc, "/" + selected


def _replace_helper_identity(spec: str, identity: str) -> str:
    target, separator, encoded_options = spec.partition("@")
    assert separator
    options = encoded_options.split(",")
    for index, option in enumerate(options):
        if option.startswith(f"{targets_mod._HELPER_PID_OPTION}="):
            options[index] = f"{targets_mod._HELPER_PID_OPTION}={identity}"
            return f"{target}@{','.join(options)}"
    raise AssertionError("predigested PID target omitted its helper identity")


def _identity_option(spec: str) -> str:
    _target, separator, encoded_options = spec.partition("@")
    assert separator
    for option in encoded_options.split(","):
        key, separator, value = option.partition("=")
        if separator and key == targets_mod._HELPER_PID_OPTION:
            return value
    raise AssertionError("predigested PID target omitted its helper identity")


def _without_option(spec: str, option_name: str) -> str:
    target, separator, encoded_options = spec.partition("@")
    assert separator
    options = [
        option for option in encoded_options.split(",")
        if option.partition("=")[0] != option_name
    ]
    return f"{target}@{','.join(options)}"


def _use_caller_proc_view(monkeypatch, tmp_path: Path) -> Path:
    caller_proc = tmp_path / "caller-proc"
    caller_process = caller_proc / "4242"
    monkeypatch.setattr(access, "PROC_ROOT", str(caller_proc))
    pid_namespace_inode = (caller_process / "ns" / "pid").stat().st_ino
    cgroup_namespace_inode = (caller_process / "ns" / "cgroup").stat().st_ino
    monkeypatch.setattr(
        access, "local_namespace_inode",
        lambda name: cgroup_namespace_inode if name == "cgroup"
        else pid_namespace_inode if name == "pid" else None,
    )
    return caller_proc


def test_private_namespace_pid_preserves_cgroup_proc_sampling_and_damon(
    monkeypatch, tmp_path: Path, cgroup_root: Path,
):
    internal_spec, helper_proc, selected_cgroup = _prepare_private_namespaced_target(
        monkeypatch, tmp_path, cgroup_root,
    )
    helper_pid = 51001

    class OneSampleSampler:
        def __init__(self, membership, config, sample_fn, **kwargs):
            self.membership = membership
            self.sample_fn = sample_fn

        def run(self, should_stop, on_sample, on_topology):
            if should_stop():
                return
            raw = self.sample_fn(self.membership)
            on_sample({
                "seq": 0, "t": 1.0, "mono": 0.0,
                "cg": raw.get("cg", {}), "proc": raw.get("proc", {}),
                "host": raw.get("host", {}),
            })

    class FakeDamonSession:
        instances: List["FakeDamonSession"] = []

        def __init__(self, targets, **kwargs):
            self.targets = targets
            self.instances.append(self)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def collect(self):
            return []

    monkeypatch.setattr(access, "CGROUP_ROOT", str(cgroup_root))
    monkeypatch.setattr(access, "PROC_ROOT", str(helper_proc))
    monkeypatch.setattr(metrics_lib, "sample_host", lambda *args, **kwargs: {})
    monkeypatch.setattr(limits_lib, "mount_flags", lambda *args, **kwargs: set())
    monkeypatch.setattr(sampler_lib, "Sampler", OneSampleSampler)
    monkeypatch.setattr(damon_lib, "available", lambda: True)
    monkeypatch.setattr(damon_lib, "DamonSession", FakeDamonSession)
    monkeypatch.setattr(cg.signal, "signal", lambda *args: None)

    run_dir = tmp_path / "run"
    args = cg.argparse.Namespace(
        run_dir=str(run_dir), target=[internal_spec], observe=[],
        follow_children=False, max_depth=4, hot_interval=0.25, idle_interval=2.0,
        discovery_interval=2.0, duration=None, damon=True, caps=None,
    )
    assert cg.cmd_collect(args) == 0

    run = store_lib.RunDir(str(tmp_path), run_id="run", create=False)
    target = run.read_manifest()["targets"][0]
    assert (target["kind"], target["cgroup"], target["pid"]) == (
        "pid", selected_cgroup, helper_pid,
    )
    sample = next(iter(run.read("samples")))
    assert sample["proc"][str(helper_pid)]["rss_anon"] == 321 * 1024
    assert [(item.kind, item.pid) for item in FakeDamonSession.instances[0].targets] == [
        ("vaddr", helper_pid),
    ]


def test_private_namespace_pid_refuses_mapping_outside_selected_subpath(
    monkeypatch, tmp_path: Path, cgroup_root: Path,
):
    internal_spec, helper_proc, _selected_cgroup = _prepare_private_namespaced_target(
        monkeypatch, tmp_path, cgroup_root, process_in_target=False,
    )

    with pytest.raises(targets_mod.TargetError, match="mapping not found"):
        targets_mod.parse_target(
            internal_spec, str(cgroup_root), proc_root=str(helper_proc),
        )


def test_predigest_pid_carries_caller_identity_and_namespace_relative_subpath(
    monkeypatch, tmp_path: Path, cgroup_root: Path,
):
    internal_spec, helper_proc, _selected_cgroup = _prepare_private_namespaced_target(
        monkeypatch, tmp_path, cgroup_root,
    )
    scheme, rest = targets_mod._split_spec(internal_spec)
    container_id, options = targets_mod._parse_options(rest)
    identity = tuple(int(part) for part in _identity_option(internal_spec).split(":"))
    helper_pid = helper_proc / "51001"

    assert scheme == "containerid"
    assert container_id == "a" * 64
    assert targets_mod.unquote(options["subpath"]) == "/worker.scope"
    assert options["as"] == "pid-4242"
    assert identity == (
        (helper_pid / "ns" / "pid").stat().st_ino,
        4242,
        98765,
        (helper_pid / "ns" / "cgroup").stat().st_ino,
    )


def test_predigest_pid_preserves_explicit_label(
    monkeypatch, tmp_path: Path, cgroup_root: Path,
):
    _prepare_private_namespaced_target(monkeypatch, tmp_path, cgroup_root)
    _use_caller_proc_view(monkeypatch, tmp_path)

    converted = cg._predigest_specs(["pid:4242@as=foreground"], resolve_self=True)[0]

    _scheme, rest = targets_mod._split_spec(converted)
    _container_id, options = targets_mod._parse_options(rest)
    assert options["as"] == "foreground"
    assert options[targets_mod._HELPER_PID_OPTION]


@pytest.mark.parametrize(
    "spec,error",
    [
        pytest.param("pid:worker", "pid: needs a number", id="non-numeric-pid"),
        pytest.param("pid:4242@subpath=/worker", "subpath is reserved", id="reserved-caller-subpath"),
    ],
)
def test_predigest_pid_reports_invalid_public_specs(
    spec: str, error: str, capsys,
):
    with pytest.raises(SystemExit) as raised:
        cg._predigest_specs([spec], resolve_self=True)

    assert raised.value.code == 2
    assert error in capsys.readouterr().err


@pytest.mark.parametrize(
    "failure,error",
    [
        pytest.param("non-positive", "positive process id", id="non-positive-pid"),
        pytest.param("host-cgroup-view", "private cgroup view", id="host-cgroup-view"),
        pytest.param("not-visible", "not visible", id="pid-not-visible"),
        pytest.param("namespace-mismatch", "different cgroup namespace", id="namespace-mismatch"),
        pytest.param("relative-path", "no absolute unified cgroup path", id="relative-cgroup-path"),
        pytest.param("unsafe-path", "unsafe cgroup path", id="unsafe-cgroup-path"),
    ],
)
def test_caller_helper_pid_cgroup_path_refuses_invalid_facts(
    monkeypatch, tmp_path: Path, cgroup_root: Path, failure: str, error: str,
):
    _prepare_private_namespaced_target(monkeypatch, tmp_path, cgroup_root)
    caller_proc = _use_caller_proc_view(monkeypatch, tmp_path)
    pid = 4242
    if failure == "non-positive":
        pid = 0
    elif failure == "host-cgroup-view":
        monkeypatch.setattr(access, "have_host_cgroup_view", lambda: True)
    elif failure == "not-visible":
        pid = 9999
    elif failure == "namespace-mismatch":
        monkeypatch.setattr(access, "local_namespace_inode", lambda name: 1)
    elif failure == "relative-path":
        (caller_proc / "4242" / "cgroup").write_text("0::worker.scope\n")
    elif failure == "unsafe-path":
        (caller_proc / "4242" / "cgroup").write_text("0::/worker/../escape.scope\n")

    with pytest.raises(targets_mod.TargetError, match=error):
        cg._helper_pid_cgroup_path(pid)


def test_caller_pid_identity_refuses_incomplete_nspid_facts(
    monkeypatch, tmp_path: Path, cgroup_root: Path,
):
    _prepare_private_namespaced_target(monkeypatch, tmp_path, cgroup_root)
    caller_proc = _use_caller_proc_view(monkeypatch, tmp_path)
    (caller_proc / "4242" / "status").write_text("Name:\tworker\nNSpid:\t4243\n")

    with pytest.raises(targets_mod.TargetError, match="identity changed or is incomplete"):
        cg._helper_pid_identity(4242)


def test_caller_pid_identity_refuses_start_time_change_during_reads(
    monkeypatch, tmp_path: Path, cgroup_root: Path,
):
    _prepare_private_namespaced_target(monkeypatch, tmp_path, cgroup_root)
    caller_proc = _use_caller_proc_view(monkeypatch, tmp_path)
    stat = caller_proc / "4242" / "stat"
    read_text = targets_mod.util.read_text
    first_read = True

    def replace_caller_start_time(path):
        nonlocal first_read
        contents = read_text(path)
        if path == str(stat) and first_read:
            first_read = False
            stat.write_text(_fake_proc_stat(4242, 98766))
        return contents

    monkeypatch.setattr(targets_mod.util, "read_text", replace_caller_start_time)

    with pytest.raises(targets_mod.TargetError, match="identity changed or is incomplete"):
        cg._helper_pid_identity(4242)
    assert not first_read


@pytest.mark.parametrize(
    "status_contents",
    [
        pytest.param(None, id="missing-status"),
        pytest.param("", id="empty-status"),
        pytest.param("Name:\tworker\n", id="missing-nspid"),
        pytest.param("Name:\tworker\nNSpid:\t51001 x\n", id="malformed-nspid"),
        pytest.param("Name:\tworker\nNSpid:\t\n", id="empty-nspid"),
        pytest.param("Name:\tworker\nNSpid:\t51001 0\n", id="zero-nspid"),
    ],
)
def test_helper_pid_refuses_missing_or_invalid_nspid_facts(
    monkeypatch, tmp_path: Path, cgroup_root: Path, status_contents: str | None,
):
    internal_spec, helper_proc, _selected_cgroup = _prepare_private_namespaced_target(
        monkeypatch, tmp_path, cgroup_root,
    )
    status = helper_proc / "51001" / "status"
    if status_contents is None:
        status.unlink()
    else:
        status.write_text(status_contents)

    with pytest.raises(targets_mod.TargetError, match="mapping not found"):
        targets_mod.parse_target(
            internal_spec, str(cgroup_root), proc_root=str(helper_proc),
        )


@pytest.mark.parametrize(
    "stat_contents",
    [
        pytest.param(None, id="missing-stat"),
        pytest.param("", id="empty-stat"),
        pytest.param("51001 worker S 0 0\n", id="malformed-stat"),
        pytest.param(
            "51001 (worker) " + " ".join(["S", *("0" for _ in range(18))]) + "\n",
            id="truncated-stat",
        ),
        pytest.param(
            "51001 (worker) " + " ".join(["S", *("0" for _ in range(18)), "bad"]) + "\n",
            id="invalid-start-time",
        ),
        pytest.param(
            "51001 (worker) " + " ".join(["S", *("0" for _ in range(18)), "-1"]) + "\n",
            id="negative-start-time",
        ),
    ],
)
def test_helper_pid_refuses_missing_or_invalid_stat_start_time(
    monkeypatch, tmp_path: Path, cgroup_root: Path, stat_contents: str | None,
):
    internal_spec, helper_proc, _selected_cgroup = _prepare_private_namespaced_target(
        monkeypatch, tmp_path, cgroup_root,
    )
    stat = helper_proc / "51001" / "stat"
    if stat_contents is None:
        stat.unlink()
    else:
        stat.write_text(stat_contents)

    with pytest.raises(targets_mod.TargetError, match="mapping not found"):
        targets_mod.parse_target(
            internal_spec, str(cgroup_root), proc_root=str(helper_proc),
        )


@pytest.mark.parametrize(
    "identity",
    [
        pytest.param("bad:4242:98765:1", id="non-integer-fact"),
        pytest.param("1:4242:98765", id="missing-field"),
        pytest.param("1:4242:98765:2:3", id="extra-field"),
    ],
)
def test_helper_pid_rejects_malformed_encoded_identity(
    monkeypatch, tmp_path: Path, cgroup_root: Path, identity: str,
):
    internal_spec, helper_proc, _selected_cgroup = _prepare_private_namespaced_target(
        monkeypatch, tmp_path, cgroup_root,
    )

    with pytest.raises(targets_mod.TargetError, match="identity is malformed"):
        targets_mod.parse_target(
            _replace_helper_identity(internal_spec, identity),
            str(cgroup_root), proc_root=str(helper_proc),
        )


@pytest.mark.parametrize(
    "index,value",
    [
        pytest.param(0, "0", id="zero-pid-namespace-inode"),
        pytest.param(1, "0", id="zero-namespace-pid"),
        pytest.param(2, "-1", id="negative-start-time"),
        pytest.param(3, "0", id="zero-cgroup-namespace-inode"),
    ],
)
def test_helper_pid_rejects_invalid_encoded_identity_facts(
    monkeypatch, tmp_path: Path, cgroup_root: Path, index: int, value: str,
):
    internal_spec, helper_proc, _selected_cgroup = _prepare_private_namespaced_target(
        monkeypatch, tmp_path, cgroup_root,
    )
    identity = _identity_option(internal_spec).split(":")
    identity[index] = value

    with pytest.raises(targets_mod.TargetError, match="identity contains an invalid process fact"):
        targets_mod.parse_target(
            _replace_helper_identity(internal_spec, ":".join(identity)),
            str(cgroup_root), proc_root=str(helper_proc),
        )


@pytest.mark.parametrize(
    "mismatch",
    [
        pytest.param("start-time", id="start-time-mismatch"),
        pytest.param("pid-namespace", id="pid-namespace-inode-mismatch"),
        pytest.param("cgroup-namespace", id="cgroup-namespace-inode-mismatch"),
        pytest.param("namespace-pid", id="innermost-nspid-mismatch"),
    ],
)
def test_helper_pid_identity_filters_reject_each_mismatched_fact(
    monkeypatch, tmp_path: Path, cgroup_root: Path, mismatch: str,
):
    internal_spec, helper_proc, _selected_cgroup = _prepare_private_namespaced_target(
        monkeypatch, tmp_path, cgroup_root,
    )
    process = helper_proc / "51001"
    if mismatch == "start-time":
        (process / "stat").write_text(_fake_proc_stat(51001, 98766))
    elif mismatch == "pid-namespace":
        (process / "ns" / "pid").unlink()
        (process / "ns" / "pid").symlink_to(tmp_path / "unrelated-pid-ns")
    elif mismatch == "cgroup-namespace":
        (process / "ns" / "cgroup").unlink()
        (process / "ns" / "cgroup").symlink_to(tmp_path / "unrelated-cgroup-ns")
    else:
        (process / "status").write_text("Name:\tworker\nNSpid:\t51001 4243\n")

    with pytest.raises(targets_mod.TargetError, match="mapping not found"):
        targets_mod.parse_target(
            internal_spec, str(cgroup_root), proc_root=str(helper_proc),
        )


def test_helper_pid_refuses_cgroup_change_after_candidate_enumeration(
    monkeypatch, tmp_path: Path, cgroup_root: Path,
):
    internal_spec, helper_proc, _selected_cgroup = _prepare_private_namespaced_target(
        monkeypatch, tmp_path, cgroup_root,
    )
    process_cgroup = helper_proc / "51001" / "cgroup"
    selected_relative = _selected_cgroup.lstrip("/")
    sibling_path = f"/../../../{selected_relative.rsplit('/', 1)[0]}/sibling.scope"
    read_candidate_cgroup = targets_mod._cgroup_path_for_pid
    moved = False

    def change_membership_after_first_read(pid, root, proc_root, namespace_root):
        nonlocal moved
        resolved = read_candidate_cgroup(pid, root, proc_root, namespace_root)
        if pid == 51001 and not moved:
            moved = True
            process_cgroup.write_text(f"0::{sibling_path}\n")
        return resolved

    monkeypatch.setattr(targets_mod, "_cgroup_path_for_pid", change_membership_after_first_read)

    with pytest.raises(targets_mod.TargetError, match="mapping not found"):
        targets_mod.parse_target(
            internal_spec, str(cgroup_root), proc_root=str(helper_proc),
        )
    assert moved


def test_helper_pid_refuses_pid_reuse_during_proc_fact_reads(
    monkeypatch, tmp_path: Path, cgroup_root: Path,
):
    internal_spec, helper_proc, _selected_cgroup = _prepare_private_namespaced_target(
        monkeypatch, tmp_path, cgroup_root,
    )
    stat = helper_proc / "51001" / "stat"
    read_text = targets_mod.util.read_text
    first_read = True

    def replace_start_time_after_first_read(path):
        nonlocal first_read
        contents = read_text(path)
        if path == str(stat) and first_read:
            first_read = False
            stat.write_text(_fake_proc_stat(51001, 98766))
        return contents

    monkeypatch.setattr(targets_mod.util, "read_text", replace_start_time_after_first_read)

    with pytest.raises(targets_mod.TargetError, match="mapping not found"):
        targets_mod.parse_target(
            internal_spec, str(cgroup_root), proc_root=str(helper_proc),
        )
    assert not first_read


def test_helper_pid_rejects_ambiguous_proc_identity(
    monkeypatch, tmp_path: Path, cgroup_root: Path,
):
    internal_spec, helper_proc, selected_cgroup = _prepare_private_namespaced_target(
        monkeypatch, tmp_path, cgroup_root,
    )
    # A changing or inconsistent proc bind can expose duplicate observations
    # for one namespace-local PID. The resolver must fail closed instead of
    # choosing whichever host PID happens to sort first.
    _write_process(
        helper_proc, 51003,
        pid_namespace=tmp_path / "caller-pid-ns",
        cgroup_namespace=tmp_path / "caller-cgroup-ns",
        nspid="51003 4242", start_ticks=98765,
        cgroup_path=f"/../../../{selected_cgroup.lstrip('/')}",
    )

    with pytest.raises(targets_mod.TargetError, match="mapping ambiguous"):
        targets_mod.parse_target(
            internal_spec, str(cgroup_root), proc_root=str(helper_proc),
        )


def test_helper_pid_identity_without_label_uses_namespace_pid_label(
    monkeypatch, tmp_path: Path, cgroup_root: Path,
):
    internal_spec, helper_proc, _selected_cgroup = _prepare_private_namespaced_target(
        monkeypatch, tmp_path, cgroup_root,
    )
    spec = _without_option(internal_spec, "as")

    [target] = targets_mod.parse_target(
        spec, str(cgroup_root), proc_root=str(helper_proc),
    )

    assert (target.kind, target.pid, target.label) == ("pid", 51001, "pid-4242")


def test_helper_pid_option_is_reserved_for_containerid_targets(
    tmp_path: Path, cgroup_root: Path,
):
    with pytest.raises(targets_mod.TargetError, match="reserved for helper PID targets"):
        targets_mod.parse_target(
            "cgroup:/dev.slice/worker.scope@_cgprofile_pid=1:2:3:4",
            str(cgroup_root),
        )
