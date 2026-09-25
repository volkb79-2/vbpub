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
    helper_pid_ns = tmp_path / "helper-pid-ns"
    helper_cgroup_ns = tmp_path / "helper-cgroup-ns"
    host_pid_ns = tmp_path / "host-pid-ns"
    for namespace in (
        caller_pid_ns, unrelated_pid_ns, caller_cgroup_ns,
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
