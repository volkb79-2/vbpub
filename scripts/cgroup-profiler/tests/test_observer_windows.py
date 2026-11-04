"""CP-39: observer contract 3 measurement windows, on fake procfs and cgroupfs.

The registry is driven through its public ``open`` / ``close`` verbs and
``sample_due`` (the sampling thread's own body, called directly so a test never
races the thread). Only the kernel is faked: a directory of procfs and cgroupfs
files that the real code reads.
"""

from __future__ import annotations

import errno
import json
import os
import socket
import threading
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pytest

from cgprofile import observer_windows, serve

PAGE = os.sysconf("SC_PAGE_SIZE")
TICK = os.sysconf("SC_CLK_TCK")
GATES = "/dev.slice/dev-gates.slice"
LEAF = f"{GATES}/run-gate-test.scope"
OTHER_LEAF = f"{GATES}/other.scope"
EVENTS = "low 0\nhigh 0\nmax 0\noom 0\noom_kill 0\noom_group_kill 0\n"
PSI = "some avg10=0.00 avg60=0.00 avg300=0.00 total={some}\nfull avg10=0.00 avg60=0.00 avg300=0.00 total={full}\n"

PEER = 4242
PEER_TID = 50           # the peer's native TID (host TID 4250)


class World:
    """A fake host: procfs, a cgroup2 mount and the peer process of a lane."""

    def __init__(self, tmp_path: Path, *, peer_pid: int = PEER, uid: int = 4242,
                 tid: int = PEER_TID, host_tid: int = 4250) -> None:
        self.proc = tmp_path / "proc"
        self.cg = tmp_path / "cgroup"
        self.uid = uid
        (self.proc / "self").mkdir(parents=True)
        (self.proc / "self" / "mountinfo").write_text(
            f"29 22 0:25 / {self.cg} rw,nosuid,nodev - cgroup2 cgroup rw\n"
        )
        pressure = self.proc / "pressure"
        pressure.mkdir()
        for name in ("memory", "cpu", "io"):
            (pressure / name).write_text(PSI.format(some=0, full=0))
        for path in ("/", "/dev.slice", GATES, LEAF, OTHER_LEAF):
            self.cgroup(path)
        self.add_peer(peer_pid, tid=tid, host_tid=host_tid,
                      native_main=42 if peer_pid == PEER else None)

    # ── cgroupfs ────────────────────────────────────────────────────────

    def cgroup(self, path: str) -> Path:
        directory = self.cg.joinpath(*Path(path).parts[1:])
        directory.mkdir(parents=True, exist_ok=True)
        files = {
            "cgroup.procs": "",
            "memory.events.local": EVENTS,
            "memory.current": "700\n",
            "memory.peak": "700\n",
            "memory.swap.peak": "0\n",
            "cpu.stat": "usage_usec 1000000\nuser_usec 600000\nsystem_usec 400000\n",
            "io.stat": "8:0 rbytes=100 wbytes=200 rios=3 wios=4\n",
            "memory.pressure": PSI.format(some=1000, full=500),
            "cpu.pressure": PSI.format(some=2000, full=0),
            "io.pressure": PSI.format(some=3000, full=1500),
        }
        for name, content in files.items():
            (directory / name).write_text(content)
        return directory

    def write(self, cgroup: str, name: str, content: str) -> None:
        (self.cg.joinpath(*Path(cgroup).parts[1:]) / name).write_text(content)

    def members(self, *pids: int, cgroup: str = LEAF) -> None:
        self.write(cgroup, "cgroup.procs", "".join(f"{pid}\n" for pid in pids))

    # ── procfs ──────────────────────────────────────────────────────────

    def add_peer(self, pid: int, *, tid: int, host_tid: int, cgroup: str = LEAF,
                 ppid: int = 1, native_main: Optional[int] = None) -> None:
        # The main thread's PID inside the peer's namespace; distinct from the
        # TID a test asks about, whatever PID the test process happens to have.
        native_main = pid + 1_000_000 if native_main is None else native_main
        for task_pid, native in ((pid, native_main), (host_tid, tid)):
            task = self.proc / str(pid) / "task" / str(task_pid)
            task.mkdir(parents=True, exist_ok=True)
            (task / "status").write_text(self._status(task_pid, native))
            (task / "cgroup").write_text(f"0::{cgroup}\n")
        self.add_proc(pid, ppid=ppid, nspid=(pid, native_main), cgroup=cgroup, rss=100)

    def _status(self, host_pid: int, native: int) -> str:
        return (
            f"Name:\tpython\nUid:\t{self.uid} {self.uid} {self.uid} {self.uid}\n"
            f"Gid:\t4242 4242 4242 4242\nGroups:\t3000\nNSpid:\t{host_pid} {native}\n"
            f"CapEff:\t{0:016x}\nCapPrm:\t{0:016x}\n"
        )

    def add_proc(self, pid: int, *, ppid: int, nspid: Tuple[int, ...], cgroup: str = LEAF,
                 state: str = "S", cpu: Tuple[int, int, int, int] = (0, 0, 0, 0),
                 rss: int = 0, io: Optional[Tuple[int, int]] = (0, 0), start: int = 1000) -> None:
        directory = self.proc / str(pid)
        directory.mkdir(parents=True, exist_ok=True)
        self.set_stat(pid, ppid=ppid, state=state, cpu=cpu, start=start)
        (directory / "statm").write_text(f"9999 {rss} 0 0 0 0 0\n")
        if io is not None:
            (directory / "io").write_text(
                f"rchar: 1\nwchar: 2\nread_bytes: {io[0]}\nwrite_bytes: {io[1]}\n"
            )
        status = self._status(pid, nspid[-1]).replace(
            f"NSpid:\t{pid} {nspid[-1]}", "NSpid:\t" + " ".join(str(n) for n in nspid)
        )
        (directory / "status").write_text(status)
        (directory / "cgroup").write_text(f"0::{cgroup}\n")

    def set_stat(self, pid: int, *, ppid: int, state: str = "S",
                 cpu: Tuple[int, int, int, int] = (0, 0, 0, 0), start: int = 1000) -> None:
        fields = [state, str(ppid)] + ["0"] * 18
        fields[11], fields[12], fields[13], fields[14] = (str(v) for v in cpu)
        fields[19] = str(start)
        (self.proc / str(pid) / "stat").write_text(f"{pid} (a b) " + " ".join(fields) + "\n")

    def set_rss(self, pid: int, pages: int) -> None:
        (self.proc / str(pid) / "statm").write_text(f"9999 {pages} 0 0 0 0 0\n")

    def set_io(self, pid: int, read: int, write: int) -> None:
        (self.proc / str(pid) / "io").write_text(f"read_bytes: {read}\nwrite_bytes: {write}\n")

    def remove(self, pid: int) -> None:
        import shutil
        shutil.rmtree(self.proc / str(pid))

    def registry(self, **kwargs) -> observer_windows.WindowRegistry:
        kwargs.setdefault("autostart", False)
        return observer_windows.WindowRegistry(
            proc_root=str(self.proc), host_proc_root=str(self.proc), cgroup_root=str(self.cg),
            mountinfo_path=str(self.proc / "self" / "mountinfo"),
            gates_slice_name="dev-gates.slice", **kwargs,
        )


@pytest.fixture
def world(tmp_path) -> World:
    w = World(tmp_path)
    # A lane: wrapper shell 4200 (idle, ancestor of the peer) -> peer 4242 -> worker 5000 -> child 5001.
    w.add_proc(4200, ppid=1, nspid=(4200, 2))
    w.set_stat(PEER, ppid=4200)
    w.add_proc(5000, ppid=PEER, nspid=(5000, 100), cpu=(300, 100, 50, 10), rss=1000, io=(1000, 5000))
    w.add_proc(5001, ppid=5000, nspid=(5001, 101), rss=500, io=(10, 200))
    w.members(4200, PEER, 5000, 5001)
    return w


def _open(reg, *, root_pid=100, mode="process-tree", interval_ms=250, pid=PEER, uid=4242, tid=PEER_TID):
    return reg.open(peer_pid=pid, peer_uid=uid, thread_id=tid, root_pid=root_pid,
                    mode=mode, interval_ms=interval_ms)


def _close(reg, window, *, pid=PEER, uid=4242, tid=PEER_TID):
    return reg.close(peer_pid=pid, peer_uid=uid, thread_id=tid, window_id=window)


def _code(excinfo) -> str:
    return excinfo.value.code


# ── process-tree mode ───────────────────────────────────────────────────────

def test_process_tree_window_sums_rss_counts_exited_io_and_reads_zombie_cpu_exactly(world):
    reg = world.registry()
    opened = _open(reg)
    assert opened["mode"] == "process-tree"
    assert opened["window"].startswith("w-") and len(opened["window"]) == 34
    assert opened["kernel"] == {"peak_reset": False}

    world.set_rss(5001, 2000)
    world.set_io(5001, 50, 9000)
    reg.sample_due(force=True)
    world.remove(5001)                      # the child exits (its last I/O was sampled)
    reg.sample_due(force=True)
    world.set_stat(5000, ppid=PEER, state="Z", cpu=(300, 100, 50, 10))
    result = _close(reg, opened["window"])

    metrics = result["metrics"]
    assert list(metrics) == list(observer_windows.METRIC_KEYS)
    assert metrics["rss_peak_bytes"] == 3000 * PAGE
    assert metrics["io_read_bytes"] == 1000 + 50
    assert metrics["io_write_bytes"] == 5000 + 9000
    assert metrics["cpu_seconds"] == pytest.approx(460 / TICK)
    assert metrics["cpu_user_seconds"] == pytest.approx(350 / TICK)
    assert metrics["cpu_system_seconds"] == pytest.approx(110 / TICK)
    assert result["sources"] == {
        "rss_peak_bytes": "sampled-sum-rss",
        "io_read_bytes": "proc-io-lower-bound", "io_write_bytes": "proc-io-lower-bound",
        "cpu_seconds": "proc-stat-exact", "cpu_user_seconds": "proc-stat-exact",
        "cpu_system_seconds": "proc-stat-exact",
    }
    cgroup_only = {
        key: "REQUIRES_CGROUP_MODE" for key in (
            "memory_peak_bytes", "swap_peak_bytes", "io_read_ops", "io_write_ops",
            "psi_memory_some_seconds", "psi_memory_full_seconds", "psi_cpu_some_seconds",
            "psi_io_some_seconds", "psi_io_full_seconds",
        )
    }
    assert result["missing"] == cgroup_only
    assert all(metrics[key] is None for key in cgroup_only)
    assert result["attribution"] == {
        "mode": "process-tree", "foreign_pids_max": 0, "tree_pids_max": 2,
        "samples": 3, "baseline_bytes": None,
    }
    assert result["window"] == opened["window"]


def test_running_root_cpu_is_live_and_a_reaped_root_falls_back_to_the_last_sample(world):
    reg = world.registry()
    live = _open(reg)
    world.set_stat(5000, ppid=PEER, cpu=(400, 100, 50, 10))
    live_result = _close(reg, live["window"])
    assert live_result["sources"]["cpu_seconds"] == "proc-stat-live"
    assert live_result["metrics"]["cpu_seconds"] == pytest.approx(560 / TICK)

    reaped = _open(reg)
    world.set_stat(5000, ppid=PEER, cpu=(700, 100, 0, 0))
    reg.sample_due(force=True)              # last observation of the running root
    world.remove(5001)
    world.remove(5000)                      # waited for before the close
    result = _close(reg, reaped["window"])
    assert result["sources"]["cpu_seconds"] == "proc-stat-sampled"
    assert result["metrics"]["cpu_seconds"] == pytest.approx(800 / TICK)


def test_a_self_rooted_window_measures_the_peer_itself(world):
    reg = world.registry()
    opened = _open(reg, root_pid=42)
    result = _close(reg, opened["window"])
    # The peer plus its whole subtree, as sampled: 100 + 1000 + 500 pages.
    assert result["metrics"]["rss_peak_bytes"] == 1600 * PAGE
    assert result["attribution"]["tree_pids_max"] == 3


def test_a_reused_root_pid_is_never_counted_as_the_subject(world):
    reg = world.registry()
    opened = _open(reg)
    world.set_stat(5000, ppid=PEER, cpu=(300, 100, 50, 10))
    reg.sample_due(force=True)
    # PID 5000 is recycled by an unrelated process (new start time, big CPU and RSS).
    world.set_stat(5000, ppid=PEER, cpu=(90000, 0, 0, 0), start=2222)
    world.set_rss(5000, 999999)
    world.add_proc(5002, ppid=5000, nspid=(5002, 102), rss=999999)
    reg.sample_due(force=True)
    result = _close(reg, opened["window"])
    assert result["metrics"]["rss_peak_bytes"] == 1500 * PAGE
    assert result["metrics"]["cpu_seconds"] == pytest.approx(460 / TICK)
    assert result["sources"]["cpu_seconds"] == "proc-stat-sampled"
    assert result["attribution"]["tree_pids_max"] == 2


def test_missing_io_accounting_is_a_reason_not_a_zero(world):
    for pid in (5000, 5001):
        (world.proc / str(pid) / "io").unlink()
    reg = world.registry()
    opened = _open(reg)
    result = _close(reg, opened["window"])
    assert result["metrics"]["io_read_bytes"] is None
    assert result["missing"]["io_write_bytes"] == "OBSERVER_ERROR"


# ── cgroup mode ─────────────────────────────────────────────────────────────

def _advance_cgroup(world: World) -> None:
    world.write(LEAF, "cpu.stat", "usage_usec 3500000\nuser_usec 2000000\nsystem_usec 1500000\n")
    world.write(LEAF, "io.stat", "8:0 rbytes=1100 wbytes=4200 rios=13 wios=24\n"
                                 "8:16 rbytes=5 wbytes=5 rios=1 wios=1\n")
    world.write(LEAF, "memory.pressure", PSI.format(some=251000, full=100500))
    world.write(LEAF, "cpu.pressure", PSI.format(some=502000, full=0))
    world.write(LEAF, "io.pressure", PSI.format(some=1003000, full=751500))


def test_cgroup_window_returns_exact_deltas_and_resets_the_peak_per_file(world):
    reg = world.registry()
    opened = _open(reg, mode="cgroup")
    assert opened["kernel"] == {"peak_reset": True}
    # The kernel's per-fd peak: whatever the cgroup charged since the reset.
    world.write(LEAF, "memory.peak", "9000\n")
    world.write(LEAF, "memory.swap.peak", "77\n")
    _advance_cgroup(world)
    reg.sample_due(force=True)
    result = _close(reg, opened["window"])

    metrics = result["metrics"]
    assert metrics["memory_peak_bytes"] == 9000
    assert metrics["swap_peak_bytes"] == 77
    assert metrics["cpu_seconds"] == pytest.approx(2.5)
    assert metrics["cpu_user_seconds"] == pytest.approx(1.4)
    assert metrics["cpu_system_seconds"] == pytest.approx(1.1)
    assert metrics["io_read_bytes"] == 1000 + 5
    assert metrics["io_write_bytes"] == 4000 + 5
    assert metrics["io_read_ops"] == 10 + 1
    assert metrics["io_write_ops"] == 20 + 1
    assert metrics["psi_memory_some_seconds"] == pytest.approx(0.25)
    assert metrics["psi_memory_full_seconds"] == pytest.approx(0.1)
    assert metrics["psi_cpu_some_seconds"] == pytest.approx(0.5)
    assert metrics["psi_io_some_seconds"] == pytest.approx(1.0)
    assert metrics["psi_io_full_seconds"] == pytest.approx(0.75)
    assert metrics["rss_peak_bytes"] == 1500 * PAGE
    assert result["missing"] == {}
    assert result["sources"]["memory_peak_bytes"] == "memory.peak"
    assert result["sources"]["cpu_seconds"] == "cpu.stat"
    assert result["sources"]["io_write_ops"] == "io.stat"
    assert result["sources"]["psi_io_full_seconds"] == "psi"
    # Wrapper shell and peer are idle members covered by the baseline, not foreign.
    assert result["attribution"] == {
        "mode": "cgroup", "foreign_pids_max": 0, "tree_pids_max": 2,
        "samples": 2, "baseline_bytes": 700,
    }


def test_a_kernel_without_per_file_peak_reset_reports_the_reason(world):
    def refuse(_fd, _data):
        raise OSError(errno.EINVAL, "no per-file reset")

    reg = world.registry(peak_write=refuse)
    opened = _open(reg, mode="cgroup")
    assert opened["kernel"] == {"peak_reset": False}
    _advance_cgroup(world)
    result = _close(reg, opened["window"])
    assert result["metrics"]["memory_peak_bytes"] is None
    assert result["metrics"]["swap_peak_bytes"] is None
    assert result["missing"] == {
        "memory_peak_bytes": "PEAK_RESET_UNSUPPORTED",
        "swap_peak_bytes": "PEAK_RESET_UNSUPPORTED",
    }
    assert result["metrics"]["cpu_seconds"] == pytest.approx(2.5)


def test_disabled_controllers_are_named_per_metric(world):
    for name in ("io.stat", "memory.peak", "memory.swap.peak", "io.pressure"):
        (world.cg / "dev.slice" / "dev-gates.slice" / "run-gate-test.scope" / name).unlink()
    reg = world.registry()
    opened = _open(reg, mode="cgroup")
    result = _close(reg, opened["window"])
    disabled = {key for key, reason in result["missing"].items() if reason == "CONTROLLER_DISABLED"}
    assert disabled == {
        "memory_peak_bytes", "swap_peak_bytes", "io_read_bytes", "io_write_bytes",
        "io_read_ops", "io_write_ops", "psi_io_some_seconds", "psi_io_full_seconds",
    }
    assert result["metrics"]["psi_memory_some_seconds"] == 0.0


def test_a_foreign_pid_makes_every_cgroup_metric_shared_even_after_it_leaves(world):
    reg = world.registry()
    opened = _open(reg, mode="cgroup")
    world.members(4200, PEER, 5000, 5001, 6000)     # another lane's process appears
    reg.sample_due(force=True)
    world.members(4200, PEER, 5000, 5001)           # ... and is gone by the close
    reg.sample_due(force=True)
    _advance_cgroup(world)
    result = _close(reg, opened["window"])

    cgroup_metrics = [k for k in observer_windows.METRIC_KEYS if k != "rss_peak_bytes"]
    assert all(result["metrics"][key] is None for key in cgroup_metrics)
    assert {result["missing"][key] for key in cgroup_metrics} == {"SHARED_CGROUP"}
    assert not any(key in result["sources"] for key in cgroup_metrics)
    assert result["metrics"]["rss_peak_bytes"] == 1500 * PAGE
    assert result["attribution"]["foreign_pids_max"] == 1


def test_a_child_forked_after_the_table_scan_is_not_foreign(world):
    reg = world.registry()
    opened = _open(reg, mode="cgroup")
    stale = observer_windows.ProcTable(str(world.proc))   # the round's shared scan
    # 5002 is a child of the tree's 5001, forked after that scan: it is in
    # cgroup.procs but not in the scanned tree. A grandchild (5003) is
    # reached through it.
    world.add_proc(5002, ppid=5001, nspid=(5002, 12), rss=1)
    world.add_proc(5003, ppid=5002, nspid=(5003, 13), rss=1)
    world.members(4200, PEER, 5000, 5001, 5002, 5003)
    (window,) = reg._windows.values()
    reg._tick(window, stale)
    _advance_cgroup(world)
    result = _close(reg, opened["window"])
    assert result["attribution"]["foreign_pids_max"] == 0
    assert result["metrics"]["cpu_seconds"] == pytest.approx(2.5)
    assert "cpu_seconds" not in result["missing"]


def test_a_member_outside_the_tree_with_a_cyclic_parent_chain_is_foreign(world):
    reg = world.registry()
    opened = _open(reg, mode="cgroup")
    world.add_proc(7000, ppid=7001, nspid=(7000, 20), rss=1)
    world.add_proc(7001, ppid=7000, nspid=(7001, 21), rss=1)
    world.members(4200, PEER, 5000, 5001, 7000)
    (window,) = reg._windows.values()
    reg._tick(window, observer_windows.ProcTable(str(world.proc)))
    result = _close(reg, opened["window"])
    assert result["attribution"]["foreign_pids_max"] == 1


def test_a_foreign_pid_present_at_open_is_seen_by_the_first_sample(world):
    world.members(4200, PEER, 5000, 5001, 6000)
    reg = world.registry()
    opened = _open(reg, mode="cgroup")
    result = _close(reg, opened["window"])
    assert result["attribution"]["foreign_pids_max"] == 1
    assert result["missing"]["cpu_seconds"] == "SHARED_CGROUP"


def test_context_reports_container_host_and_ancestor_deltas(world):
    reg = world.registry()
    opened = _open(reg, mode="cgroup")
    world.write(GATES, "memory.events.local", EVENTS.replace("high 0", "high 2"))
    world.write(LEAF, "memory.events.local", EVENTS.replace("max 0", "max 5"))
    world.write(LEAF, "memory.pressure", PSI.format(some=1000, full=2000500))
    (world.proc / "pressure" / "memory").write_text(PSI.format(some=0, full=400000))
    context = _close(reg, opened["window"])["context"]

    assert context["container_psi_memory_full_seconds"] == pytest.approx(2.0)
    assert context["host_psi_memory_full_seconds"] == pytest.approx(0.4)
    assert context["container_memory_events_local"] == {"high": 0, "max": 5, "oom": 0, "oom_kill": 0}
    assert context["ancestors_memory_events_local"] == [
        {"path": GATES, "high": 2, "max": 0, "oom": 0, "oom_kill": 0},
        {"path": "/dev.slice", "high": 0, "max": 0, "oom": 0, "oom_kill": 0},
    ]


def test_context_nulls_what_cannot_be_read(world):
    (world.cg / "dev.slice" / "memory.events.local").unlink()
    (world.proc / "pressure" / "io").unlink()
    reg = world.registry()
    context = _close(reg, _open(reg)["window"])["context"]
    by_path = {e["path"]: e for e in context["ancestors_memory_events_local"]}
    assert by_path["/dev.slice"]["high"] is None
    assert by_path[GATES]["high"] == 0
    assert context["host_psi_io_some_seconds"] is None
    assert context["host_psi_memory_some_seconds"] == 0.0


# ── binding and refusals ────────────────────────────────────────────────────

def test_root_pid_must_be_the_peer_or_a_live_descendant(world):
    world.add_proc(7000, ppid=1, nspid=(7000, 300))          # not below the peer
    reg = world.registry()
    for root in (300, 424242):
        with pytest.raises(observer_windows.WindowError) as refused:
            _open(reg, root_pid=root)
        assert _code(refused) == "observer-refused"
        assert "descendant" in str(refused.value)
    assert reg.open_count() == 0


def test_a_descendant_in_another_cgroup_outside_the_peers_is_refused(world):
    world.add_proc(5003, ppid=PEER, nspid=(5003, 103), cgroup=OTHER_LEAF)
    reg = world.registry()
    with pytest.raises(observer_windows.WindowError) as refused:
        _open(reg, root_pid=103)
    assert _code(refused) == "observer-refused"
    assert "outside the peer's cgroup" in str(refused.value)


def test_a_descendant_in_a_cgroup_below_the_peers_is_accepted(world):
    child_leaf = f"{LEAF}/sub"
    world.cgroup(child_leaf)
    world.add_proc(5004, ppid=PEER, nspid=(5004, 104), cgroup=child_leaf)
    reg = world.registry()
    opened = _open(reg, root_pid=104, mode="cgroup")
    assert reg.open_count() == 1
    world.write(child_leaf, "cpu.stat", "usage_usec 1500000\nuser_usec 1500000\nsystem_usec 0\n")
    # The window reads the sub-cgroup that holds the root, not the peer's.
    result = _close(reg, opened["window"])
    assert result["metrics"]["cpu_seconds"] == pytest.approx(0.5)


def test_a_peer_outside_the_gates_slice_is_refused(tmp_path):
    outside = World(tmp_path)
    outside.add_peer(PEER, tid=PEER_TID, host_tid=4250, cgroup="/dev.slice")
    reg = outside.registry()
    with pytest.raises(observer_windows.WindowError) as refused:
        _open(reg, root_pid=42)
    assert _code(refused) == "observer-refused"


def test_missing_peer_credentials_and_bad_thread_are_refused(world):
    reg = world.registry()
    with pytest.raises(observer_windows.WindowError) as refused:
        reg.open(peer_pid=None, peer_uid=None, thread_id=PEER_TID, root_pid=100,
                 mode="process-tree", interval_ms=250)
    assert _code(refused) == "peer-refused"
    with pytest.raises(observer_windows.WindowError) as refused:
        _open(reg, tid=999)
    assert _code(refused) == "observer-refused"


@pytest.mark.parametrize(("mode", "interval", "needle"), [
    ("threads", 250, "mode"), ("cgroup", 99, "interval_ms"), ("cgroup", 5001, "interval_ms"),
])
def test_mode_and_interval_are_validated(world, mode, interval, needle):
    reg = world.registry()
    with pytest.raises(observer_windows.WindowError) as refused:
        _open(reg, mode=mode, interval_ms=interval)
    assert _code(refused) == "bad-argument" and needle in str(refused.value)


def test_a_window_can_only_be_closed_by_the_peer_that_opened_it(world):
    other_pid, other_tid, other_host_tid = 4300, 61, 4301
    world.add_peer(other_pid, tid=other_tid, host_tid=other_host_tid)
    reg = world.registry()
    opened = _open(reg)
    with pytest.raises(observer_windows.WindowError) as refused:
        _close(reg, opened["window"], pid=other_pid, tid=other_tid)
    assert _code(refused) == "peer-refused"
    assert reg.open_count() == 1                  # still the opener's
    assert _close(reg, opened["window"])["window"] == opened["window"]
    assert reg.open_count() == 0


def test_a_recycled_peer_pid_does_not_inherit_the_window(world):
    reg = world.registry()
    opened = _open(reg)
    world.set_stat(PEER, ppid=4200, start=7777)            # a new process with the same PID
    with pytest.raises(observer_windows.WindowError) as refused:
        _close(reg, opened["window"])
    assert _code(refused) == "peer-refused"


def test_unknown_and_malformed_windows_are_bad_arguments_and_a_closed_one_is_unknown(world):
    reg = world.registry()
    opened = _open(reg)
    _close(reg, opened["window"])
    forged = opened["window"][:10] + "0" * 24
    for window in ("nope", "w-xyz", forged, opened["window"]):
        with pytest.raises(observer_windows.WindowError) as refused:
            _close(reg, window)
        assert _code(refused) == "bad-argument", window


def test_the_per_peer_window_limit_is_64_and_closing_frees_a_slot(world):
    reg = world.registry()
    windows = [_open(reg)["window"] for _ in range(observer_windows.MAX_WINDOWS_PER_PEER)]
    with pytest.raises(observer_windows.WindowError) as refused:
        _open(reg)
    assert _code(refused) == "window-refused" and refused.value.reason == "limit"
    _close(reg, windows[0])
    _open(reg)


def test_the_daemon_wide_window_limit_applies_across_peers(world):
    world.add_peer(4300, tid=61, host_tid=4301)
    reg = world.registry(max_total=2)
    _open(reg)
    _open(reg)
    with pytest.raises(observer_windows.WindowError) as refused:
        _open(reg, root_pid=4300 + 1_000_000, pid=4300, tid=61)
    assert _code(refused) == "window-refused" and refused.value.reason == "limit"


def test_an_expired_window_is_lost_and_its_files_are_released(world):
    now = {"t": 1000.0}
    reg = world.registry(mono=lambda: now["t"], ttl_seconds=100)
    opened = _open(reg, mode="cgroup")
    now["t"] += 101
    reg.sample_due(force=True)
    assert reg.open_count() == 0
    with pytest.raises(observer_windows.WindowError) as late:
        _close(reg, opened["window"])
    assert _code(late) == "window-lost"


def test_a_window_survives_until_its_ttl(world):
    now = {"t": 1000.0}
    reg = world.registry(mono=lambda: now["t"], ttl_seconds=100)
    opened = _open(reg)
    now["t"] += 99
    reg.sample_due(force=True)
    assert _close(reg, opened["window"])["window"] == opened["window"]


def test_windows_do_not_survive_a_daemon_restart(world):
    first = world.registry()
    opened = _open(first)
    restarted = world.registry()
    with pytest.raises(observer_windows.WindowError) as lost:
        _close(restarted, opened["window"])
    assert _code(lost) == "window-lost"


def test_windows_of_a_dead_peer_are_dropped_so_they_cannot_exhaust_the_limit(world):
    reg = world.registry()
    _open(reg)
    world.remove(PEER)
    reg.sample_due(force=True)
    assert reg.open_count() == 0


def test_only_due_windows_are_sampled(world):
    now = {"t": 0.0}
    reg = world.registry(mono=lambda: now["t"])
    opened = _open(reg, interval_ms=1000)
    world.set_rss(5001, 4000)
    reg.sample_due()                                 # 0 s: not yet due
    now["t"] = 1.0
    reg.sample_due()                                 # due now
    assert _close(reg, opened["window"])["attribution"]["samples"] == 2


def test_the_sampling_thread_runs_windows_without_any_request(world):
    reg = world.registry(autostart=True)
    try:
        opened = _open(reg, interval_ms=100)
        world.set_rss(5001, 6000)
        deadline = threading.Event()
        for _ in range(100):
            if reg._windows[opened["window"]].samples >= 3:
                break
            deadline.wait(0.05)
        result = _close(reg, opened["window"])
    finally:
        reg.shutdown()
    assert result["attribution"]["samples"] >= 3
    assert result["metrics"]["rss_peak_bytes"] == 7000 * PAGE
    assert reg.open_count() == 0


def test_the_sampling_thread_exits_when_no_window_remains(world):
    reg = world.registry(autostart=True)
    try:
        _close(reg, _open(reg, interval_ms=100)["window"])
        thread = reg._thread
        if thread is not None:
            thread.join(timeout=3)
            assert not thread.is_alive()
    finally:
        reg.shutdown()


def test_shutdown_releases_every_open_window(world):
    reg = world.registry()
    _open(reg, mode="cgroup")
    held = [w.mem_peak.fd for w in reg._windows.values()]
    reg.shutdown()
    assert reg.open_count() == 0
    for fd in held:
        with pytest.raises(OSError):
            os.fstat(fd)


# ── wire level (SessionServer) ──────────────────────────────────────────────

def _server(world: World, tmp_path: Path, **kwargs) -> serve.SessionServer:
    return serve.SessionServer(
        sessions_dir=str(tmp_path / "sessions"), cgroup_root=str(world.cg),
        proc_root=str(world.proc), gates_slice_name="dev-gates.slice", **kwargs,
    )


def _wire(verb: str, contract: int = 3, **args) -> bytes:
    return json.dumps({"verb": verb, "args": args, "contract": contract}).encode()


def test_wire_open_and_close_round_trip_with_the_documented_shape(world, tmp_path):
    server = _server(world, tmp_path)
    try:
        peer = (PEER, 4242, 4242)
        opened = server._observer_dispatch(
            _wire("window-open", thread_id=PEER_TID, root_pid=100, mode="process-tree", interval_ms=250),
            peer,
        )
        assert opened["ok"] is True and opened["contract"] == 3
        assert set(opened) == {"ok", "contract", "window", "mode", "started_at", "kernel"}
        closed = server._observer_dispatch(
            _wire("window-close", thread_id=PEER_TID, window=opened["window"]), peer,
        )
        assert closed["ok"] is True and closed["contract"] == 3
        assert set(closed) == {
            "ok", "contract", "window", "metrics", "missing", "sources", "context", "attribution",
        }
        # The reply is one JSON line well inside the 4096-byte request budget's sibling: it must serialise.
        assert json.loads(json.dumps(closed)) == closed
    finally:
        server.windows.shutdown()


def test_wire_interval_defaults_and_argument_validation(world, tmp_path):
    server = _server(world, tmp_path)
    peer = (PEER, 4242, 4242)
    try:
        ok = server._observer_dispatch(
            _wire("window-open", thread_id=PEER_TID, root_pid=100, mode="cgroup"), peer)
        assert ok["ok"] is True
        bad = [
            _wire("window-open", thread_id=PEER_TID, root_pid=100),
            _wire("window-open", thread_id=PEER_TID, root_pid=100, mode="cgroup", extra=1),
            _wire("window-open", thread_id=True, root_pid=100, mode="cgroup"),
            _wire("window-open", thread_id=PEER_TID, root_pid=0, mode="cgroup"),
            _wire("window-open", thread_id=PEER_TID, root_pid=100, mode=3),
            _wire("window-open", thread_id=PEER_TID, root_pid=100, mode="cgroup", interval_ms="250"),
            _wire("window-close", thread_id=PEER_TID),
            _wire("window-close", thread_id=PEER_TID, window=5),
            _wire("window-close", thread_id=0, window="w-x"),
            _wire("resource-limits", thread_id=PEER_TID),
            _wire("start"),
        ]
        for payload in bad:
            response = server._observer_dispatch(payload, peer)
            assert response["ok"] is False, payload
            assert response["contract"] == 3
            assert response["error"]["code"] == "bad-argument", payload
    finally:
        server.windows.shutdown()


def test_wire_refusals_carry_their_documented_codes(world, tmp_path):
    server = _server(world, tmp_path)
    try:
        no_peer = server._observer_dispatch(
            _wire("window-open", thread_id=PEER_TID, root_pid=100, mode="cgroup"), None)
        assert no_peer["error"]["code"] == "peer-refused"
        foreign = server._observer_dispatch(
            _wire("window-open", thread_id=PEER_TID, root_pid=999, mode="cgroup"), (PEER, 4242, 4242))
        assert foreign["error"]["code"] == "observer-refused"
        lost = server._observer_dispatch(
            _wire("window-close", thread_id=PEER_TID, window="w-" + "0" * 32), (PEER, 4242, 4242))
        assert lost["error"]["code"] == "window-lost"
        server.windows.max_per_peer = 0
        limited = server._observer_dispatch(
            _wire("window-open", thread_id=PEER_TID, root_pid=100, mode="cgroup"), (PEER, 4242, 4242))
        assert limited["error"] == {
            "code": "window-refused", "reason": "limit", "message": "per-peer window limit reached",
        }
    finally:
        server.windows.shutdown()


def test_contract_2_cannot_reach_the_window_verbs(world, tmp_path):
    server = _server(world, tmp_path)
    response = server._observer_dispatch(
        _wire("window-open", contract=2, thread_id=PEER_TID, root_pid=100, mode="cgroup"),
        (PEER, 4242, 4242),
    )
    assert response["ok"] is False and response["contract"] == 2
    assert "accepts only 'resource-limits'" in response["error"]["message"]


def test_version_advertises_both_contracts_and_keeps_contract_2(world, tmp_path):
    version = _server(world, tmp_path).handle_version({})
    observer = version["resource_observer"]
    assert observer["contract"] == 2
    assert observer["contracts"] == [2, 3]


def test_a_stuck_window_does_not_delay_other_observer_requests(world, tmp_path):
    world.add_peer(4300, tid=61, host_tid=4301)
    world.add_proc(5300, ppid=4300, nspid=(5300, 200))
    server = _server(world, tmp_path)
    try:
        opened = server._observer_dispatch(
            _wire("window-open", thread_id=PEER_TID, root_pid=100, mode="cgroup"), (PEER, 4242, 4242))
        window = server.windows._windows[opened["window"]]
        results: List[Dict] = []

        def other_peer() -> None:
            results.append(server._observer_dispatch(
                _wire("window-open", thread_id=61, root_pid=200, mode="process-tree"), (4300, 4242, 4242)))
            results.append(server._observer_dispatch(
                _wire("resource-limits", contract=2, thread_id=61), (4300, 4242, 4242)))

        with window.lock:                 # a tick of this window is in progress and slow
            worker = threading.Thread(target=other_peer)
            worker.start()
            worker.join(timeout=3)
            assert not worker.is_alive()
        assert results[0]["ok"] is True
        assert results[1]["contract"] == 2          # answered (resource-limits has its own checks)
    finally:
        server.windows.shutdown()


def test_real_socket_peer_credentials_drive_a_window(tmp_path):
    pid, uid, tid = os.getpid(), os.getuid(), threading.get_native_id()
    world = World(tmp_path, peer_pid=pid, uid=uid, tid=tid, host_tid=pid + 100000)
    world.add_proc(pid + 1, ppid=pid, nspid=(pid + 1, 777), cpu=(10, 0, 0, 0), rss=50)
    world.members(pid, pid + 1)
    server = _server(world, tmp_path)
    client, daemon_side = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        def ask(payload: bytes) -> Dict:
            left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
            handler = threading.Thread(target=server._handle_observer_connection, args=(right,))
            handler.start()
            left.sendall(payload + b"\n")
            data = b""
            while not data.endswith(b"\n"):
                data += left.recv(65536)
            handler.join(timeout=5)
            left.close()
            return json.loads(data)

        opened = ask(_wire("window-open", thread_id=tid, root_pid=777, mode="process-tree"))
        assert opened["ok"] is True, opened
        closed = ask(_wire("window-close", thread_id=tid, window=opened["window"]))
        assert closed["ok"] is True, closed
        assert closed["metrics"]["rss_peak_bytes"] == 50 * PAGE
        assert closed["sources"]["cpu_seconds"] == "proc-stat-live"
    finally:
        client.close()
        daemon_side.close()
        server.windows.shutdown()


# ── failure modes: unreadable, malformed, racing and exhausted ──────────────

@pytest.mark.parametrize("stat", [
    None,                                   # the process is gone
    "5 no-closing-parenthesis S 1\n",
    "5 (a) S 1 2 3\n",                      # truncated record
    "5 (a) " + " ".join(["S", "x"] + ["0"] * 18) + "\n",   # ppid is not a number
])
def test_unusable_stat_records_are_no_process(tmp_path, stat):
    if stat is not None:
        (tmp_path / "5").mkdir()
        (tmp_path / "5" / "stat").write_text(stat)
    assert observer_windows.read_proc_row(str(tmp_path), 5) is None


def test_process_table_skips_unreadable_entries_and_survives_a_missing_root(tmp_path):
    assert observer_windows.ProcTable(str(tmp_path / "absent")).rows == {}
    (tmp_path / "7").mkdir()
    (tmp_path / "7" / "stat").write_text("garbage")
    (tmp_path / "meminfo").write_text("x")
    assert observer_windows.ProcTable(str(tmp_path)).rows == {}


def test_a_parent_loop_in_the_process_table_cannot_hang_the_walk(world):
    world.set_stat(5000, ppid=5001)         # 5000 and 5001 name each other as parent
    world.set_stat(5001, ppid=5000)
    table = observer_windows.ProcTable(str(world.proc))
    assert sorted(table.descendants(5000)) == [5000, 5001]
    assert 5000 in table.ancestors(5001)


def test_malformed_per_process_counters_read_as_unavailable(tmp_path):
    (tmp_path / "9").mkdir()
    assert observer_windows._read_rss_bytes(str(tmp_path), 9) is None          # no statm
    (tmp_path / "9" / "statm").write_text("1\n")
    assert observer_windows._read_rss_bytes(str(tmp_path), 9) is None          # too short
    (tmp_path / "9" / "statm").write_text("1 x\n")
    assert observer_windows._read_rss_bytes(str(tmp_path), 9) is None          # not a number
    assert observer_windows._read_proc_io(str(tmp_path), 9) is None            # no io
    (tmp_path / "9" / "io").write_text("rchar: 1\nread_bytes: x\nwrite_bytes: 4\nnoise\n")
    assert observer_windows._read_proc_io(str(tmp_path), 9) is None            # one counter unreadable
    (tmp_path / "9" / "io").write_text("read_bytes: 3\nwrite_bytes: 4\n")
    assert observer_windows._read_proc_io(str(tmp_path), 9) == (3, 4)


def test_peak_file_states(tmp_path):
    absent = observer_windows.PeakFile(str(tmp_path / "nothing"))
    assert (absent.present, absent.reset_ok, absent.read()) == (False, False, None)
    absent.close()

    (tmp_path / "dir").mkdir()              # cannot be opened for writing: present, no reset
    unwritable = observer_windows.PeakFile(str(tmp_path / "dir"))
    assert (unwritable.present, unwritable.reset_ok, unwritable.read()) == (True, False, None)

    garbage = tmp_path / "garbage"
    garbage.write_text("0\n")
    peak = observer_windows.PeakFile(str(garbage))
    assert peak.reset_ok
    garbage.write_bytes(b"\xff\xfe")
    assert peak.read() is None              # not ASCII
    garbage.write_text("12abc")
    assert peak.read() is None              # not a number
    garbage.write_text("42\n")
    assert peak.read() == 42
    os.close(peak.fd)                       # closed behind its back
    peak.close()
    assert peak.fd is None
    peak.close()                            # idempotent


def test_running_out_of_descriptors_refuses_the_window_and_leaks_nothing(world):
    import resource

    reg = world.registry()
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    held = []
    try:
        top = max(int(name) for name in os.listdir("/proc/self/fd")) + 1
        resource.setrlimit(resource.RLIMIT_NOFILE, (top + 1, hard))
        while True:                         # fill every hole below the limit
            try:
                held.append(os.dup(0))
            except OSError:
                break
        os.close(held.pop())                # leave exactly one descriptor: the window needs two
        with pytest.raises(observer_windows.WindowError) as refused:
            _open(reg, mode="cgroup")
    finally:
        for fd in held:
            os.close(fd)
        resource.setrlimit(resource.RLIMIT_NOFILE, (soft, hard))
    assert _code(refused) == "window-refused" and refused.value.reason == "limit"
    assert reg.open_count() == 0
    # Nothing leaked: with the limit restored the same open succeeds.
    assert _open(reg, mode="cgroup")["mode"] == "cgroup"


def test_an_unreadable_root_cgroup_refuses_the_window(world):
    (world.proc / "5000" / "cgroup").unlink()
    with pytest.raises(observer_windows.WindowError) as refused:
        _open(world.registry())
    assert _code(refused) == "observer-refused"


def test_a_peer_recycled_between_the_proof_and_the_scan_is_refused(world, monkeypatch):
    real = observer_windows.ProcTable

    class Recycled(real):
        def __init__(self, proc_root):
            super().__init__(proc_root)
            self.rows.pop(PEER)             # the peer exited after its identity was proven

    monkeypatch.setattr(observer_windows, "ProcTable", Recycled)
    with pytest.raises(observer_windows.WindowError) as refused:
        _open(world.registry())
    assert _code(refused) == "observer-refused" and "peer process identity" in str(refused.value)


def test_a_root_recycled_while_it_was_being_bound_is_refused(world, monkeypatch):
    real = observer_windows.read_proc_row
    seen = {"root": 0}

    def racing(proc_root, pid):
        row = real(proc_root, pid)
        if pid == 5000 and row is not None:
            seen["root"] += 1
            if seen["root"] > 1:            # the recheck after the cgroup read
                return row.__class__(**{**row.__dict__, "start_ticks": row.start_ticks + 1})
        return row

    monkeypatch.setattr(observer_windows, "read_proc_row", racing)
    with pytest.raises(observer_windows.WindowError) as refused:
        _open(world.registry())
    assert _code(refused) == "observer-refused" and "identity changed" in str(refused.value)


def test_a_window_with_no_readable_memory_reports_the_reason(world):
    reg = world.registry()
    opened = _open(reg)
    for pid in (5000, 5001):
        (world.proc / str(pid) / "statm").unlink()
    fresh = _open(reg)                       # a window whose every sample finds no statm
    result = _close(reg, fresh["window"])
    assert result["metrics"]["rss_peak_bytes"] is None
    assert result["missing"]["rss_peak_bytes"] == "OBSERVER_ERROR"
    assert _close(reg, opened["window"])["metrics"]["rss_peak_bytes"] == 1500 * PAGE


def test_an_unreadable_cgroup_procs_never_invents_a_foreign_pid(world):
    (world.cg / "dev.slice" / "dev-gates.slice" / "run-gate-test.scope" / "cgroup.procs").unlink()
    reg = world.registry()
    result = _close(reg, _open(reg, mode="cgroup")["window"])
    assert result["attribution"]["foreign_pids_max"] == 0


def test_cgroup_files_missing_at_open_are_named_controller_disabled(world):
    leaf = world.cg / "dev.slice" / "dev-gates.slice" / "run-gate-test.scope"
    (leaf / "cpu.stat").write_text("usage_usec 1\n")      # present but incomplete
    reg = world.registry()
    result = _close(reg, _open(reg, mode="cgroup")["window"])
    assert {result["missing"][k] for k in ("cpu_seconds", "cpu_user_seconds", "cpu_system_seconds")} == {
        "CONTROLLER_DISABLED"}
    (leaf / "cpu.stat").unlink()
    result = _close(reg, _open(reg, mode="cgroup")["window"])
    assert result["missing"]["cpu_seconds"] == "CONTROLLER_DISABLED"


def test_counters_that_vanish_or_go_backwards_during_the_window_are_errors(world):
    leaf = world.cg / "dev.slice" / "dev-gates.slice" / "run-gate-test.scope"
    reg = world.registry()
    vanished = _open(reg, mode="cgroup")
    for name in ("cpu.stat", "io.stat", "memory.pressure"):
        (leaf / name).unlink()
    result = _close(reg, vanished["window"])
    gone = {key: result["missing"][key] for key in (
        "cpu_seconds", "cpu_user_seconds", "cpu_system_seconds", "io_read_bytes", "io_write_ops",
        "psi_memory_some_seconds", "psi_memory_full_seconds")}
    assert set(gone.values()) == {"OBSERVER_ERROR"}

    (leaf / "cpu.stat").write_text("usage_usec 1000000\nuser_usec 600000\nsystem_usec 400000\n")
    (leaf / "io.stat").write_text("8:0 rbytes=100 wbytes=200 rios=3 wios=4\n")
    (leaf / "memory.pressure").write_text(PSI.format(some=1000, full=500))
    backwards = _open(reg, mode="cgroup")
    (leaf / "cpu.stat").write_text("usage_usec 10\nuser_usec 10\nsystem_usec 10\n")
    (leaf / "io.stat").write_text("8:0 rbytes=1 wbytes=1 rios=1 wios=1\n")
    (leaf / "memory.peak").write_text("not a number\n")
    result = _close(reg, backwards["window"])
    assert result["missing"]["cpu_seconds"] == "OBSERVER_ERROR"
    assert result["missing"]["io_write_bytes"] == "OBSERVER_ERROR"
    assert result["missing"]["memory_peak_bytes"] == "OBSERVER_ERROR"
    # The fake kernel never rewrote swap's peak after the reset write left "reset" in it.
    assert result["missing"]["swap_peak_bytes"] == "OBSERVER_ERROR"


def test_dropping_a_window_twice_is_harmless_and_old_tombstones_are_evicted(world, monkeypatch):
    monkeypatch.setattr(observer_windows, "_TOMBSTONES", 2)
    reg = world.registry()
    opened = [_open(reg)["window"] for _ in range(3)]
    windows = [reg._windows[w] for w in opened]
    for window in windows:
        reg._drop(window)
    reg._drop(windows[0])                    # already gone: nothing happens
    assert reg.open_count() == 0
    for window_id in opened[1:]:
        with pytest.raises(observer_windows.WindowError) as lost:
            _close(reg, window_id)
        assert _code(lost) == "window-lost"
    with pytest.raises(observer_windows.WindowError) as forgotten:
        _close(reg, opened[0])               # evicted from the bounded tombstone list
    assert _code(forgotten) == "bad-argument"


def test_peer_validation_of_the_window_binding_itself(world):
    from cgprofile import resource_observer

    base = dict(proc_root=str(world.proc), cgroup_root=str(world.cg),
                mountinfo_path=str(world.proc / "self" / "mountinfo"),
                gates_slice_name="dev-gates.slice", peer_pid=PEER, peer_uid=4242, thread_id=PEER_TID)
    assert resource_observer.resolve_window_peer(**base)["nspid"] == (PEER, 42)
    for override, needle in (
        ({"peer_pid": None}, "peer PID"), ({"peer_pid": 0}, "peer PID"),
        ({"thread_id": True}, "native TID"), ({"thread_id": 0}, "native TID"),
        ({"peer_uid": None}, "peer UID"), ({"peer_uid": -1}, "peer UID"),
        ({"cgroup_root": "relative/root"}, "not absolute"),
    ):
        with pytest.raises(resource_observer.ResourceObserverError, match=needle):
            resource_observer.resolve_window_peer(**{**base, **override})


def test_process_cgroup_and_namespace_readers_refuse_what_they_cannot_prove(world):
    from cgprofile import resource_observer

    proc = str(world.proc)
    assert resource_observer.process_cgroup_path(proc, 5000, 0) == resource_observer.PurePosixPath(LEAF)
    assert resource_observer.process_nspid(proc, 5000) == (5000, 100)
    assert resource_observer.process_nspid(proc, 99999) is None
    with pytest.raises(resource_observer.ResourceObserverError, match="cannot read"):
        resource_observer.process_cgroup_path(proc, 99999, 0)
    for text in ("1:name=systemd:/legacy\n", "0::relative\n"):
        (world.proc / "5000" / "cgroup").write_text(text)
        with pytest.raises(resource_observer.ResourceObserverError, match="no absolute unified"):
            resource_observer.process_cgroup_path(proc, 5000, 0)


GOLDEN_WINDOW = "w-0123456789abcdef0123456789abcdef"
GOLDEN_STARTED = "2026-11-03T10:00:00Z"
FIXTURES = Path(__file__).parent / "fixtures" / "rg55"


def golden_exchanges(tmp_path: Path) -> Dict[str, Dict]:
    """The four contract-3 golden exchanges, produced by the daemon's own dispatch.

    ``window`` and ``started_at`` are the only values that vary between runs,
    so they are pinned to the fixture's example values.
    """
    exchanges: Dict[str, Dict] = {}
    peer = (PEER, 4242, 4242)

    def run(name: str, mode: str, prepare, *, close_name: str, record_open: bool = False) -> None:
        sub = tmp_path / name
        sub.mkdir()
        world = World(sub)
        world.add_proc(4200, ppid=1, nspid=(4200, 2))
        world.set_stat(PEER, ppid=4200)
        world.add_proc(5000, ppid=PEER, nspid=(5000, 100), cpu=(300, 100, 50, 10), rss=1000, io=(1000, 5000))
        world.add_proc(5001, ppid=5000, nspid=(5001, 101), rss=500, io=(10, 200))
        world.members(4200, PEER, 5000, 5001)
        server = _server(world, sub)
        server.windows.shutdown()
        server.windows = world.registry(wall=lambda: 1793700000.0)
        open_request = {"verb": "window-open", "args": {
            "thread_id": PEER_TID, "root_pid": 100, "mode": mode, "interval_ms": 250}, "contract": 3}
        opened = server._observer_dispatch(json.dumps(open_request).encode(), peer)
        assert opened["ok"] is True
        window = opened["window"]
        prepare(world, server)
        close_request = {"verb": "window-close", "args": {"thread_id": PEER_TID, "window": window}, "contract": 3}
        closed = server._observer_dispatch(json.dumps(close_request).encode(), peer)
        assert closed["ok"] is True

        def pin(value):
            return json.loads(json.dumps(value).replace(window, GOLDEN_WINDOW))

        if record_open:
            exchanges["observer-window-v3-open"] = {"request": open_request, "response": pin(opened)}
        close_request["args"]["window"] = GOLDEN_WINDOW
        exchanges[close_name] = {"request": close_request, "response": pin(closed)}

    def process_tree(world, server):
        world.set_stat(5000, ppid=PEER, state="Z", cpu=(300, 100, 50, 10))

    def cgroup(world, server):
        world.write(LEAF, "memory.peak", "9000\n")
        world.write(LEAF, "memory.swap.peak", "77\n")
        _advance_cgroup(world)
        server.windows.sample_due(force=True)

    def shared(world, server):
        world.members(4200, PEER, 5000, 5001, 6000)
        server.windows.sample_due(force=True)
        _advance_cgroup(world)

    run("a", "cgroup", cgroup, close_name="observer-window-v3-close-cgroup", record_open=True)
    run("b", "process-tree", process_tree, close_name="observer-window-v3-close-process-tree")
    run("c", "cgroup", shared, close_name="observer-window-v3-close-shared")
    return exchanges


@pytest.mark.parametrize("name", [
    "observer-window-v3-open", "observer-window-v3-close-process-tree",
    "observer-window-v3-close-cgroup", "observer-window-v3-close-shared",
])
def test_golden_fixtures_are_exactly_what_the_daemon_produces(tmp_path, name):
    produced = golden_exchanges(tmp_path)[name]
    golden = json.loads((FIXTURES / f"{name}.json").read_text())
    assert golden == produced
    assert (FIXTURES / f"{name}.json").read_text() == json.dumps(produced, indent=2) + "\n"


def test_golden_close_exchanges_tell_the_three_stories(tmp_path):
    produced = golden_exchanges(tmp_path)
    exact = produced["observer-window-v3-close-process-tree"]["response"]
    assert exact["sources"]["cpu_seconds"] == "proc-stat-exact"
    shared = produced["observer-window-v3-close-shared"]["response"]
    assert shared["attribution"]["foreign_pids_max"] == 1
    assert shared["missing"]["memory_peak_bytes"] == "SHARED_CGROUP"
    assert produced["observer-window-v3-close-cgroup"]["response"]["missing"] == {}
