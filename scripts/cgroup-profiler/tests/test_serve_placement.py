"""Placement — RG55-INTERFACE-CONTRACT.md §8.3, design D-20/D-25, RW-35(a),
RG-55 P6 C8 (CP-9).

Every test here runs against a FAKE cgroupfs in a tmp directory (the seam
every other reader in this package takes), because the claims worth making
are all about what is written WHERE:

* **the whitelist is the safety property**, so it is tested from the
  refusing side first: a write planted at any other cgroup file — the gates
  slice's own `memory.max`, a production tier, another lane's leaf, the
  origin scope's `memory.high` — raises before the `open()`, and a `-`
  value to `cgroup.subtree_control` is refused even though the file itself
  is the one non-leaf write RW-35(a) allows;
* **`applied` is read back, never echoed** — proved with a leaf whose
  writes are rounded the way a real `memory.high` is, so an implementation
  that echoed the request would report a number the file does not hold;
* **the lane's pids really move**, at start and on every later discovery,
  and really come back at `stop`, after which the leaf is gone;
* **a refusal never costs the caller a session** — each `place-refused:*`
  code arrives on a session that started, is sampled, and stops normally.
"""

from __future__ import annotations

import json
import errno
import math
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from lib import access, liveness, placement, serve
from tests.conftest import cgroup_files, write_cgroup

CONTAINER_ID = "d" * 64
SESSION_ID = "s-20260912T101500Z-9f01"
TOKEN = "rg55-place-token-01"
LEAF_NAME = f"rg-{TOKEN}"
EPOCH_START = datetime(2026, 9, 12, 10, 15, 0, tzinfo=timezone.utc).timestamp()
META = {
    "lane": "rg55-place-lane", "project": "run-gate-project", "worktree": "/workspaces/vbpub",
    "commit": "0123456789abcdef0123456789abcdef01234567", "run_gate_revision": 41,
    "kind": "command", "expected": None,
}
SCOPE_CGROUP = f"dev.slice/dev-background.slice/docker-{CONTAINER_ID}.scope"
GATES_CGROUP = "/dev.slice/dev-gates.slice"
SLICE_MAX = 6 * 1024 * 1024 * 1024
MEMORY_HIGH = 805306368
MEMORY_MAX = 1073741824

RG55_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "rg55"
REGEN_ENV = "CGPROFILE_REGEN_RG55_GOLDENS"


@pytest.fixture(autouse=True)
def _fake_loaded_gates_unit(monkeypatch):
    """The fake cgroup tree represents an authored, loaded gates unit.

    The real `systemctl show` property contract is tested in `test_access.py`;
    placement tests must not depend on whichever host unit happens to exist.
    Individual refusal tests pass an explicit false verifier where needed.
    """
    monkeypatch.setattr(
        access, "verify_systemd_slice",
        lambda unit, path: unit == "dev-gates.slice" and path == GATES_CGROUP,
    )


# ── fakes ───────────────────────────────────────────────────────────────

def _fake_cgroup_root(
    tmp_path: Path, *, gates: bool = True, subtree_control: str = "",
    slice_memory_max: Optional[int] = SLICE_MAX, procs: str = "",
) -> Path:
    root = tmp_path / "cgroup"
    write_cgroup(root, "", cgroup_files())
    write_cgroup(root, "dev.slice", cgroup_files())
    write_cgroup(root, "dev.slice/dev-background.slice", cgroup_files())
    files = dict(cgroup_files(memory_current=500 * 1024 * 1024))
    files["cgroup.procs"] = procs
    files["io.stat"] = "8:0 rbytes=0 wbytes=0 rios=0 wios=0"
    write_cgroup(root, SCOPE_CGROUP, files)
    if gates:
        gates_files = dict(cgroup_files())
        gates_files["cpu.max"] = "500000 100000"
        gates_files["memory.max"] = "max" if slice_memory_max is None else str(slice_memory_max)
        gates_files["cgroup.subtree_control"] = subtree_control
        write_cgroup(root, "dev.slice/dev-gates.slice", gates_files)
    return root


def _fake_proc(tmp_path: Path) -> Path:
    proc = tmp_path / "proc"
    proc.mkdir(exist_ok=True)
    (proc / "loadavg").write_text("1.0 1.0 1.0 1/100 999\n")
    (proc / "meminfo").write_text("MemTotal: 1000 kB\nMemAvailable: 500 kB\n")
    pressure = proc / "pressure"
    pressure.mkdir(exist_ok=True)
    for name in ("cpu", "memory", "io"):
        (pressure / name).write_text(
            "some avg10=0.00 avg60=0.00 avg300=0.00 total=0\n"
            "full avg10=0.00 avg60=0.00 avg300=0.00 total=0\n"
        )
    return proc


def _fake_rmdir(path: str) -> None:
    """A cgroup `rmdir` on a REAL directory: the kernel's kernfs removes a
    cgroup whose interface files are still "in" it, so the fake tree has to
    do the same or nothing about `release` could be tested at all."""
    shutil.rmtree(path)


def _placement(
    root: Path, *, token: Optional[str] = TOKEN, request: Optional[placement.PlacementRequest] = None,
    writes: Optional[List[Any]] = None, rmdir=_fake_rmdir, **kw: Any,
) -> placement.LanePlacement:
    kw.setdefault("slice_unit_verifier", lambda _unit, _cgroup: True)
    return placement.LanePlacement(
        cgroup_root=str(root), gates_cgroup=GATES_CGROUP, token=token,
        origin_cgroup="/" + SCOPE_CGROUP,
        request=request if request is not None else placement.PlacementRequest(
            memory_high=MEMORY_HIGH, memory_max=MEMORY_MAX, cpu_weight=100,
        ),
        on_write=(lambda rel, value: writes.append((rel, value))) if writes is not None else None,
        rmdir=rmdir, **kw,
    )


def _leaf(root: Path) -> Path:
    return root / "dev.slice" / "dev-gates.slice" / LEAF_NAME


def _container_cgroup(root: Path, container_id: str = CONTAINER_ID) -> Path:
    if container_id == CONTAINER_ID:
        name = f"docker-{container_id}.scope"
    else:
        name = container_id
    relative = f"dev.slice/dev-gates.slice/{name}"
    files = dict(cgroup_files())
    files["cgroup.kill"] = "0"
    files["cgroup.events"] = "populated 1"
    write_cgroup(root, relative, files)
    return root / relative


def _host_proc_with_pids(monkeypatch, pids: List[int]) -> None:
    monkeypatch.setattr(access, "have_host_proc_view", lambda _root: True)
    monkeypatch.setattr(
        "lib.targets.pids_in_cgroup",
        lambda _cgroup, _root, _proc: list(pids),
    )


def _start_args(**overrides: Any) -> Dict[str, Any]:
    args = {
        "target": f"containerid:{CONTAINER_ID}", "scope": "container-shared",
        "token": TOKEN, "damon": "off", "interval": 1.0, "meta": META,
        "progress_stream": None, "idle_bound": None, "ceiling": None, "on_stall": None,
        "place": True, "memory_high": MEMORY_HIGH, "memory_max": MEMORY_MAX, "cpu_weight": 100,
    }
    args.update(overrides)
    return args


_SERVERS: List[serve.SessionServer] = []


@pytest.fixture(autouse=True)
def _no_session_outlives_its_test(tmp_path):
    """Every session here runs a real sampler thread against a real tmp
    directory. A thread still sampling while pytest removes that directory
    (this suite's `tmp_path_retention_policy`) raises FileNotFoundError from
    a thread nothing is waiting on, so each server this module builds is
    drained at teardown. It depends on `tmp_path` DELIBERATELY: that makes
    the tmp directory older than this fixture, so it is removed after this
    finalizer has stopped every session rather than out from under one."""
    yield
    for server in _SERVERS:
        server._stop_all_sessions(aborted_reason="test-teardown")
    _SERVERS.clear()


def _server(tmp_path: Path, root: Path, **kw: Any) -> serve.SessionServer:
    proc_root = kw.pop("proc_root", None)
    if proc_root is None:
        proc_root = _fake_proc(tmp_path)
    server = serve.SessionServer(
        sessions_dir=str(tmp_path / "sessions"), socket_path=str(tmp_path / "ctl.sock"),
        cgroup_root=str(root), proc_root=str(proc_root), clock=lambda: EPOCH_START,
        session_id_fn=lambda: SESSION_ID, accept_timeout=0.05,
        slice_unit_verifier=lambda _unit, _cgroup: True, **kw,
    )
    server.cgroup_rmdir = _fake_rmdir
    _SERVERS.append(server)
    return server


# ── §8.3: the four `start` options ──────────────────────────────────────

class TestParseRequest:
    def test_no_place_is_no_request_and_the_caps_are_not_even_read(self):
        """"All optional, all ignored without `--place`" is literal — a cap
        sent alongside `place: false` is not an error, because a consumer
        that sends a number it knows will be ignored has made none."""
        assert placement.parse_request({}) is None
        assert placement.parse_request({"place": None}) is None
        assert placement.parse_request({"place": False, "cpu_weight": "wrong"}) is None

    def test_a_request_carries_exactly_the_three_caps(self):
        req = placement.parse_request(_start_args())
        assert req == placement.PlacementRequest(
            memory_high=MEMORY_HIGH, memory_max=MEMORY_MAX, cpu_weight=100
        )
        assert req.cap_files() == ["memory.high", "memory.max", "cpu.weight"]
        assert placement.parse_request({"place": True}).cap_files() == []

    @pytest.mark.parametrize("value", [1.5, math.inf, -math.inf, math.nan])
    @pytest.mark.parametrize("field", ["memory_high", "memory_max"])
    def test_memory_caps_reject_fractional_and_nonfinite_values(self, field, value):
        with pytest.raises(placement.CapsError):
            placement.parse_request({"place": True, field: value})

    @pytest.mark.parametrize("value", [1.5, math.inf, -math.inf, math.nan])
    def test_cpu_weight_rejects_fractional_and_nonfinite_values(self, value):
        with pytest.raises(placement.CapsError):
            placement.parse_request({"place": True, "cpu_weight": value})

    def test_zero_memory_bytes_are_a_valid_cap(self):
        request = placement.parse_request({"place": True, "memory_high": 0})
        assert request.memory_high == 0

    @pytest.mark.parametrize("weight", [placement.CPU_WEIGHT_MIN, placement.CPU_WEIGHT_MAX])
    def test_cpu_weight_minimum_and_maximum_are_inclusive(self, weight):
        request = placement.parse_request({"place": True, "cpu_weight": weight})
        assert request.cpu_weight == weight

    @pytest.mark.parametrize("overrides, fragment", [
        ({"place": "yes"}, "--place"),
        ({"memory_high": "800M"}, "--memory-high"),
        ({"memory_high": True}, "--memory-high"),
        ({"memory_max": -1}, "--memory-max"),
        ({"cpu_weight": "heavy"}, "--cpu-weight"),
        ({"cpu_weight": True}, "--cpu-weight"),
        ({"cpu_weight": 0}, "--cpu-weight"),
        ({"cpu_weight": 10001}, "--cpu-weight"),
    ])
    def test_an_unparsable_cap_is_a_caps_error(self, overrides, fragment):
        with pytest.raises(placement.CapsError) as exc:
            placement.parse_request(_start_args(**overrides))
        assert fragment in str(exc.value)

    def test_the_server_turns_that_into_bad_argument_and_starts_nothing(self, tmp_path):
        """A cap the CLIENT typed wrong is a request error (exit 2, no
        session); `place-refused:*` is for what the HOST turned out to be
        and never fails `start`. The registry is the oracle for "nothing
        started", exactly as C7's `bad-policy` test asserts it."""
        server = _server(tmp_path, _fake_cgroup_root(tmp_path))
        resp = server._dispatch(
            {"verb": "start", "args": _start_args(cpu_weight=99999), "contract": 1}
        )
        assert resp["ok"] is False and resp["error"]["code"] == "bad-argument"
        assert "--cpu-weight" in resp["error"]["message"]
        assert server._sessions == {}


# ── D-25 / RW-35(a): the write whitelist ────────────────────────────────

class TestWriteGuard:
    def _guard(self, root: Path) -> placement.CgroupWriteGuard:
        return placement.CgroupWriteGuard(
            cgroup_root=str(root), gates_cgroup=GATES_CGROUP,
            origin_cgroup="/" + SCOPE_CGROUP, leaf_name=LEAF_NAME,
        )

    @pytest.mark.parametrize("name", list(placement.LEAF_FILES))
    def test_every_leaf_file_is_writable(self, tmp_path, name):
        root = _fake_cgroup_root(tmp_path)
        self._guard(root).check_write(str(_leaf(root) / name), "1")

    def test_the_one_non_leaf_write_is_subtree_control_with_plus_values(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)
        guard = self._guard(root)
        control = str(root / "dev.slice/dev-gates.slice/cgroup.subtree_control")
        guard.check_write(control, placement.SUBTREE_CONTROL_VALUE)

    @pytest.mark.parametrize("value, fragment", [
        ("-memory", "non-'+'"),
        ("+memory -cpu", "non-'+'"),
        ("+io", "delegate controller"),
        ("", "empty"),
    ])
    def test_subtree_control_refuses_anything_but_plus_the_three(self, tmp_path, value, fragment):
        """RW-35(a) is "`+memory +cpu +pids`, never `-`" — a `-memory` here
        would revoke the controller from every OTHER child of the gates
        slice, every other lane's leaf, live and mid-run."""
        root = _fake_cgroup_root(tmp_path)
        control = str(root / "dev.slice/dev-gates.slice/cgroup.subtree_control")
        with pytest.raises(placement.HostWriteError) as exc:
            self._guard(root).check_write(control, value)
        assert fragment in str(exc.value)

    def test_the_origin_scope_is_writable_for_the_move_back_and_nothing_else(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)
        guard = self._guard(root)
        guard.check_write(str(root / SCOPE_CGROUP / "cgroup.procs"), "4242")
        with pytest.raises(placement.HostWriteError):
            guard.check_write(str(root / SCOPE_CGROUP / "memory.high"), "1")

    @pytest.mark.parametrize("relative", [
        "dev.slice/dev-gates.slice/memory.max",          # the gates slice itself
        "dev.slice/dev-gates.slice/cgroup.procs",        # the gates slice's own procs
        "dev.slice/dev-background.slice/memory.max",     # a sibling tier
        "dev.slice/cgroup.procs",                        # an ancestor
        "memory.max",                                    # the cgroup root
        f"dev.slice/dev-gates.slice/{LEAF_NAME}/memory.min",   # a leaf file not on the list
        f"dev.slice/dev-gates.slice/{LEAF_NAME}/sub/memory.high",  # below the leaf
        "dev.slice/dev-gates.slice/not-a-lane/memory.high",    # not an rg-* leaf
    ])
    def test_a_write_planted_anywhere_else_is_refused(self, tmp_path, relative):
        """The planted-write test D-25 asks for: the guard is consulted
        BEFORE the `open()`, so a refusal is proved by the exception, not by
        inspecting the file afterwards."""
        root = _fake_cgroup_root(tmp_path)
        with pytest.raises(placement.HostWriteError) as exc:
            self._guard(root).check_write(str(root / relative), "1")
        assert "D-25 cgroup whitelist" in str(exc.value)

    def test_a_symlinked_leaf_cannot_smuggle_a_write_out_of_the_gates_slice(self, tmp_path):
        """Every comparison is on `realpath`, so a symlink planted under the
        gates slice resolves to where it really points and is refused."""
        root = _fake_cgroup_root(tmp_path)
        elsewhere = root / "dev.slice" / "dev-background.slice" / "victim"
        elsewhere.mkdir()
        (root / "dev.slice" / "dev-gates.slice" / LEAF_NAME).symlink_to(elsewhere)
        with pytest.raises(placement.HostWriteError):
            self._guard(root).check_write(str(_leaf(root) / "memory.high"), "1")

    def test_a_symlinked_leaf_cannot_smuggle_a_kill_to_another_rg_leaf(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)
        victim = root / "dev.slice" / "dev-gates.slice" / "rg-victim-token"
        victim.mkdir()
        _leaf(root).symlink_to(victim)
        guard = self._guard(root)
        with pytest.raises(placement.HostWriteError):
            guard.check_write(str(_leaf(root) / "cgroup.kill"), "1")
        assert not (victim / "cgroup.kill").exists()

    def test_another_session_leaf_is_outside_this_sessions_whitelist(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)
        other = root / "dev.slice" / "dev-gates.slice" / "rg-other-token"
        other.mkdir()
        guard = self._guard(root)
        with pytest.raises(placement.HostWriteError):
            guard.check_write(str(other / "memory.max"), "1")
        with pytest.raises(placement.HostWriteError):
            guard.check_rmdir(str(other))

    def test_only_an_rg_leaf_may_be_created_or_removed(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)
        guard = self._guard(root)
        guard.check_mkdir(str(_leaf(root)))
        guard.check_rmdir(str(_leaf(root)))
        with pytest.raises(placement.HostWriteError):
            guard.check_mkdir(str(root / "dev.slice/dev-gates.slice/other"))
        with pytest.raises(placement.HostWriteError):
            guard.check_rmdir(str(root / "dev.slice/dev-background.slice"))

    def test_a_placement_without_an_origin_scope_writes_no_scope_procs(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)
        guard = placement.CgroupWriteGuard(
            cgroup_root=str(root), gates_cgroup=GATES_CGROUP,
            origin_cgroup=None, leaf_name=LEAF_NAME,
        )
        with pytest.raises(placement.HostWriteError):
            guard.check_write(str(root / SCOPE_CGROUP / "cgroup.procs"), "4242")

    def test_exact_container_kill_is_the_only_extra_whitelisted_write(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)
        target = _container_cgroup(root)
        guard = placement.CgroupWriteGuard(
            cgroup_root=str(root), gates_cgroup=GATES_CGROUP,
            origin_cgroup=None, leaf_name=None,
            container_cgroup="/" + target.relative_to(root).as_posix(),
            container_id=CONTAINER_ID,
        )
        guard.check_write(str(target / "cgroup.kill"), "1")
        with pytest.raises(placement.HostWriteError):
            guard.check_write(str(target / "cgroup.kill"), "0")
        with pytest.raises(placement.HostWriteError):
            guard.check_write(str(target / "memory.max"), "1")

    @pytest.mark.parametrize("name", [
        CONTAINER_ID,
        *(f"{runtime}-{CONTAINER_ID}.scope" for runtime in (
            "docker", "crio", "containerd", "libpod",
        )),
    ])
    def test_runtime_leaf_spellings_must_encode_the_full_container_id(self, name):
        assert placement.container_cgroup_matches_id("/" + name, CONTAINER_ID)
        assert not placement.container_cgroup_matches_id("/" + name, "e" * 64)

    def test_container_leaf_rejects_traversal_and_malformed_ids(self):
        assert not placement.container_cgroup_matches_id(
            "/dev-gates.slice/../docker-" + CONTAINER_ID + ".scope", CONTAINER_ID,
        )
        assert not placement.container_cgroup_matches_id(
            "/docker-short.scope", CONTAINER_ID,
        )

    def test_container_leaf_rejects_malformed_claimed_ids(self):
        assert not placement.container_cgroup_matches_id(
            "/docker-" + CONTAINER_ID + ".scope", "short",
        )
        assert not placement.container_cgroup_matches_id(
            "/docker-" + CONTAINER_ID + ".scope", 64,
        )

    def test_container_leaf_requires_an_absolute_string_path(self):
        assert not placement.container_cgroup_matches_id(
            "docker-" + CONTAINER_ID + ".scope", CONTAINER_ID,
        )
        assert not placement.container_cgroup_matches_id(None, CONTAINER_ID)

    def test_uncomparable_realpaths_fail_closed(self, tmp_path, monkeypatch):
        root = _fake_cgroup_root(tmp_path)
        target = _container_cgroup(root)

        def uncomparable(_paths):
            raise ValueError("paths have incompatible roots")

        monkeypatch.setattr(placement.os.path, "commonpath", uncomparable)
        guard = placement.CgroupWriteGuard(
            cgroup_root=str(root), gates_cgroup=GATES_CGROUP,
            origin_cgroup=None, leaf_name=None,
            container_cgroup="/" + target.relative_to(root).as_posix(),
            container_id=CONTAINER_ID,
        )
        assert guard.container_kill_abs is None

    def test_a_leaf_cgroup_kill_only_accepts_the_literal_enforcement_value(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)
        leaf = _leaf(root)
        leaf.mkdir()
        guard = placement.CgroupWriteGuard(
            cgroup_root=str(root), gates_cgroup=GATES_CGROUP,
            origin_cgroup=None, leaf_name=LEAF_NAME,
        )
        guard.check_write(str(leaf / "cgroup.kill"), "1")
        with pytest.raises(placement.HostWriteError):
            guard.check_write(str(leaf / "cgroup.kill"), "0")

    @pytest.mark.parametrize("relative, claimed_id", [
        (SCOPE_CGROUP, CONTAINER_ID),
        ("dev.slice/dev-gates.slice/docker-" + "e" * 64 + ".scope", CONTAINER_ID),
        ("dev.slice/dev-gates.slice", CONTAINER_ID),
    ])
    def test_container_kill_guard_refuses_wrong_id_or_out_of_scope_paths(
        self, tmp_path, relative, claimed_id
    ):
        root = _fake_cgroup_root(tmp_path)
        target = "/" + relative
        guard = placement.CgroupWriteGuard(
            cgroup_root=str(root), gates_cgroup=GATES_CGROUP,
            origin_cgroup=None, leaf_name=None,
            container_cgroup=target, container_id=claimed_id,
        )
        with pytest.raises(placement.HostWriteError):
            guard.check_write(str(root / relative / "cgroup.kill"), "1")

    def test_symlinked_container_cgroup_cannot_redirect_the_kill(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)
        target = _container_cgroup(root)
        other_id = "e" * 64
        other = _container_cgroup(root, other_id)
        link = root / "dev.slice" / "dev-gates.slice" / f"docker-{CONTAINER_ID}.scope"
        shutil.rmtree(link)
        link.symlink_to(other)
        guard = placement.CgroupWriteGuard(
            cgroup_root=str(root), gates_cgroup=GATES_CGROUP,
            origin_cgroup=None, leaf_name=None,
            container_cgroup="/dev.slice/dev-gates.slice/" + link.name,
            container_id=CONTAINER_ID,
        )
        assert guard.container_kill_abs is None


class TestTargetContainerKill:
    def test_kills_only_the_exact_container_and_records_the_write(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)
        target = _container_cgroup(root)
        unrelated = _container_cgroup(root, "e" * 64)
        writes = []
        killer = placement.TargetContainerKill(
            cgroup_root=str(root), gates_cgroup=GATES_CGROUP,
            container_cgroup="/" + target.relative_to(root).as_posix(),
            container_id=CONTAINER_ID,
            slice_unit_verifier=lambda unit, path: (
                unit == "dev-gates.slice" and path == GATES_CGROUP
            ),
            on_write=lambda path, value: writes.append((path, value)),
        )
        assert killer.kill() is True
        assert (target / "cgroup.kill").read_text() == "1"
        assert (unrelated / "cgroup.kill").read_text().strip() == "0"
        assert writes == [
            ("dev.slice/dev-gates.slice/docker-" + CONTAINER_ID + ".scope/cgroup.kill", "1")
        ]

    def test_kill_can_succeed_without_an_optional_event_sink(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)
        target = _container_cgroup(root)
        killer = placement.TargetContainerKill(
            cgroup_root=str(root), gates_cgroup=GATES_CGROUP,
            container_cgroup="/" + target.relative_to(root).as_posix(),
            container_id=CONTAINER_ID,
            slice_unit_verifier=lambda _unit, _path: True,
        )
        assert killer.kill() is True
        assert (target / "cgroup.kill").read_text() == "1"

    def test_event_sink_failure_does_not_erase_a_successful_kernel_kill(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)
        target = _container_cgroup(root)
        logs: List[str] = []

        def broken_event_sink(_path, _value):
            raise OSError("session storage unavailable")

        killer = placement.TargetContainerKill(
            cgroup_root=str(root), gates_cgroup=GATES_CGROUP,
            container_cgroup="/" + target.relative_to(root).as_posix(),
            container_id=CONTAINER_ID,
            slice_unit_verifier=lambda _unit, _path: True,
            on_write=broken_event_sink, log=logs.append,
        )
        assert killer.kill() is True
        assert (target / "cgroup.kill").read_text() == "1"
        assert logs and "event row could not be recorded" in logs[-1]

    def test_event_sink_failure_without_a_log_sink_preserves_kill_result(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)
        target = _container_cgroup(root)

        def broken_event_sink(_path, _value):
            raise OSError("session storage unavailable")

        killer = placement.TargetContainerKill(
            cgroup_root=str(root), gates_cgroup=GATES_CGROUP,
            container_cgroup="/" + target.relative_to(root).as_posix(),
            container_id=CONTAINER_ID,
            slice_unit_verifier=lambda _unit, _path: True,
            on_write=broken_event_sink,
        )
        assert killer.kill() is True
        assert (target / "cgroup.kill").read_text() == "1"

    def test_unverified_or_unbounded_gates_slice_refuses_without_writing(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)
        target = _container_cgroup(root)
        killer = placement.TargetContainerKill(
            cgroup_root=str(root), gates_cgroup=GATES_CGROUP,
            container_cgroup="/" + target.relative_to(root).as_posix(),
            container_id=CONTAINER_ID,
            slice_unit_verifier=lambda _unit, _path: False,
        )
        assert killer.kill() is False
        assert killer.refusal_reason == "gates-slice-unverified-or-unbounded"
        assert (target / "cgroup.kill").read_text().strip() == "0"

    def test_an_unbounded_gates_slice_refuses_even_when_the_unit_is_verified(self, tmp_path):
        root = _fake_cgroup_root(tmp_path, slice_memory_max=None)
        target = _container_cgroup(root)
        killer = placement.TargetContainerKill(
            cgroup_root=str(root), gates_cgroup=GATES_CGROUP,
            container_cgroup="/" + target.relative_to(root).as_posix(),
            container_id=CONTAINER_ID,
            slice_unit_verifier=lambda _unit, _path: True,
        )
        assert killer.kill() is False
        assert killer.refusal_reason == "gates-slice-unverified-or-unbounded"
        assert (target / "cgroup.kill").read_text().strip() == "0"

    def test_a_unit_verifier_exception_fails_closed_and_is_logged(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)
        target = _container_cgroup(root)
        logs: List[str] = []

        def broken_verifier(_unit, _path):
            raise OSError("systemd unavailable")

        killer = placement.TargetContainerKill(
            cgroup_root=str(root), gates_cgroup=GATES_CGROUP,
            container_cgroup="/" + target.relative_to(root).as_posix(),
            container_id=CONTAINER_ID,
            slice_unit_verifier=broken_verifier, log=logs.append,
        )
        assert killer.kill() is False
        assert killer.refusal_reason == "gates-slice-unverified-or-unbounded"
        assert logs and "gates-slice proof failed" in logs[-1]
        assert (target / "cgroup.kill").read_text().strip() == "0"

    def test_a_unit_verifier_exception_fails_closed_without_a_log_sink(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)
        target = _container_cgroup(root)

        def broken_verifier(_unit, _path):
            raise OSError("systemd unavailable")

        killer = placement.TargetContainerKill(
            cgroup_root=str(root), gates_cgroup=GATES_CGROUP,
            container_cgroup="/" + target.relative_to(root).as_posix(),
            container_id=CONTAINER_ID, slice_unit_verifier=broken_verifier,
        )
        assert killer.kill() is False
        assert killer.refusal_reason == "gates-slice-unverified-or-unbounded"
        assert (target / "cgroup.kill").read_text().strip() == "0"

    def test_target_path_must_encode_the_exact_container_id(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)
        target = _container_cgroup(root, "e" * 64)
        killer = placement.TargetContainerKill(
            cgroup_root=str(root), gates_cgroup=GATES_CGROUP,
            container_cgroup="/" + target.relative_to(root).as_posix(),
            container_id=CONTAINER_ID,
            slice_unit_verifier=lambda _unit, _path: True,
        )
        assert killer.kill() is False
        assert killer.refusal_reason == "target-not-an-exact-gates-container"
        assert (target / "cgroup.kill").read_text().strip() == "0"

    def test_missing_exact_target_cgroup_refuses_before_open(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)
        target_cgroup = f"{GATES_CGROUP}/docker-{CONTAINER_ID}.scope"
        killer = placement.TargetContainerKill(
            cgroup_root=str(root), gates_cgroup=GATES_CGROUP,
            container_cgroup=target_cgroup, container_id=CONTAINER_ID,
            slice_unit_verifier=lambda _unit, _path: True,
        )
        assert killer.target_abs is not None
        assert killer.kill() is False
        assert killer.refusal_reason == "target-cgroup-missing"

    def test_a_cgroup_kill_write_error_is_reported_and_logged(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)
        target = _container_cgroup(root)
        (target / "cgroup.kill").unlink()
        (target / "cgroup.kill").mkdir()
        logs: List[str] = []
        killer = placement.TargetContainerKill(
            cgroup_root=str(root), gates_cgroup=GATES_CGROUP,
            container_cgroup="/" + target.relative_to(root).as_posix(),
            container_id=CONTAINER_ID,
            slice_unit_verifier=lambda _unit, _path: True, log=logs.append,
        )
        assert killer.kill() is False
        assert killer.refusal_reason == "target-cgroup-kill-write-failed"
        assert logs and "exact container cgroup.kill refused" in logs[-1]

    def test_a_cgroup_kill_write_error_without_a_log_sink_is_reported(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)
        target = _container_cgroup(root)
        (target / "cgroup.kill").unlink()
        (target / "cgroup.kill").mkdir()
        killer = placement.TargetContainerKill(
            cgroup_root=str(root), gates_cgroup=GATES_CGROUP,
            container_cgroup="/" + target.relative_to(root).as_posix(),
            container_id=CONTAINER_ID,
            slice_unit_verifier=lambda _unit, _path: True,
        )
        assert killer.kill() is False
        assert killer.refusal_reason == "target-cgroup-kill-write-failed"

    @pytest.mark.parametrize("events", ["populated 0", None, "malformed"])
    def test_empty_or_unreadable_target_is_not_reported_as_killed(
        self, tmp_path, events,
    ):
        root = _fake_cgroup_root(tmp_path)
        target = _container_cgroup(root)
        event_file = target / "cgroup.events"
        if events is None:
            event_file.unlink()
        else:
            event_file.write_text(events)
        killer = placement.TargetContainerKill(
            cgroup_root=str(root), gates_cgroup=GATES_CGROUP,
            container_cgroup="/" + target.relative_to(root).as_posix(),
            container_id=CONTAINER_ID,
            slice_unit_verifier=lambda _unit, _path: True,
        )
        assert killer.kill() is False
        assert killer.refusal_reason == "target-cgroup-empty-or-unreadable"
        assert (target / "cgroup.kill").read_text().strip() == "0"


# ── §8.3: apply, read-back, migration, release ──────────────────────────

class _RoundingPlacement(placement.LanePlacement):
    """A leaf whose writes land rounded, the way the kernel rounds
    `memory.high` to a page multiple. Nothing else changes — which is the
    point: `applied` must report what the FILE holds."""

    ROUNDING = 4096

    def _write(self, abs_target: str, value: str) -> None:
        if os.path.basename(abs_target).startswith("memory."):
            value = str(int(value) - int(value) % self.ROUNDING)
        super()._write(abs_target, value)


class TestApply:
    def test_the_leaf_is_created_capped_and_populated(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)
        writes: List[Any] = []
        plc = _placement(root, writes=writes)
        plc.apply([101, 102])

        assert plc.error is None
        assert plc.leaf_cgroup == f"{GATES_CGROUP}/{LEAF_NAME}"
        assert _leaf(root).is_dir()
        assert (_leaf(root) / "memory.high").read_text() == str(MEMORY_HIGH)
        assert (_leaf(root) / "cgroup.procs").read_text() == "102"  # one pid per write
        assert plc.block() == {
            "requested": True, "leaf": f"{GATES_CGROUP}/{LEAF_NAME}",
            "applied": {"memory.high": MEMORY_HIGH, "memory.max": MEMORY_MAX, "cpu.weight": 100},
            "pids_moved": 2, "error": None,
        }
        # D-25: every cgroup write is an events row — the sink sees the
        # subtree_control delegation, the mkdir, three caps and two pids.
        assert [rel for rel, _ in writes] == [
            "dev.slice/dev-gates.slice/cgroup.subtree_control",
            f"dev.slice/dev-gates.slice/{LEAF_NAME}",
            f"dev.slice/dev-gates.slice/{LEAF_NAME}/memory.high",
            f"dev.slice/dev-gates.slice/{LEAF_NAME}/memory.max",
            f"dev.slice/dev-gates.slice/{LEAF_NAME}/cpu.weight",
            f"dev.slice/dev-gates.slice/{LEAF_NAME}/cgroup.procs",
            f"dev.slice/dev-gates.slice/{LEAF_NAME}/cgroup.procs",
        ]

    def test_the_controllers_are_delegated_only_when_the_slice_lacks_them(self, tmp_path):
        """RW-35(a) is the ONE non-leaf write, so it happens only when it has
        to: a gates slice that already delegates all three is not written."""
        root = _fake_cgroup_root(tmp_path, subtree_control="memory cpu pids io")
        writes: List[Any] = []
        _placement(root, writes=writes).apply([])
        assert not any(rel.endswith("cgroup.subtree_control") for rel, _ in writes)

        partial = _fake_cgroup_root(tmp_path / "second", subtree_control="memory pids")
        writes = []
        _placement(partial, writes=writes).apply([])
        assert (partial / "dev.slice/dev-gates.slice/cgroup.subtree_control").read_text() == (
            placement.SUBTREE_CONTROL_VALUE
        )
        assert writes[0] == (
            "dev.slice/dev-gates.slice/cgroup.subtree_control", "+memory +cpu +pids",
        )

    def test_applied_is_read_back_from_the_leaf_not_echoed(self, tmp_path):
        """The S13.3.2 discipline, made falsifiable: these writes land
        rounded down, so an implementation that echoed the request would
        report a number the file does not hold."""
        root = _fake_cgroup_root(tmp_path)
        plc = _RoundingPlacement(
            cgroup_root=str(root), gates_cgroup=GATES_CGROUP, token=TOKEN,
            origin_cgroup="/" + SCOPE_CGROUP,
            request=placement.PlacementRequest(memory_high=MEMORY_HIGH + 1, cpu_weight=100),
        )
        plc.apply([])
        assert plc.applied["memory.high"] == MEMORY_HIGH
        assert plc.applied["memory.high"] != MEMORY_HIGH + 1
        assert int((_leaf(root) / "memory.high").read_text()) == plc.applied["memory.high"]
        assert "memory.max" not in plc.applied  # never requested, never claimed

    @pytest.mark.parametrize("kwargs, root_kwargs, expected", [
        ({"token": None}, {}, placement.REFUSED_NO_TOKEN),
        ({}, {"gates": False}, placement.REFUSED_NO_GATES_SLICE),
    ])
    def test_a_refusal_leaves_no_leaf_and_no_exception(self, tmp_path, kwargs, root_kwargs, expected):
        root = _fake_cgroup_root(tmp_path, **root_kwargs)
        plc = _placement(root, **kwargs)
        plc.apply([101])
        assert plc.error == expected
        assert plc.block()["leaf"] is None and plc.block()["pids_moved"] == 0
        assert not _leaf(root).exists()

    def test_a_memory_max_above_the_slice_ceiling_is_over_slice(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)
        plc = _placement(
            root, request=placement.PlacementRequest(memory_max=SLICE_MAX + 1)
        )
        plc.apply([])
        assert plc.error == placement.REFUSED_OVER_SLICE
        assert not _leaf(root).exists()

    def test_an_unlimited_slice_is_not_an_admission_capacity_object(self, tmp_path):
        """A directory with no finite memory ceiling is not trusted as the
        authored gates capacity object."""
        root = _fake_cgroup_root(tmp_path, slice_memory_max=None)
        plc = _placement(root, request=placement.PlacementRequest(memory_max=SLICE_MAX * 4))
        plc.apply([])
        assert plc.error == placement.REFUSED_NO_GATES_SLICE
        assert plc.leaf_cgroup is None

    def test_an_unbounded_cpu_slice_is_refused(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)
        gates = root / "dev.slice" / "dev-gates.slice"
        (gates / "cpu.max").write_text("max 100000")
        plc = _placement(root)
        plc.apply([])
        assert plc.error == placement.REFUSED_NO_GATES_SLICE
        assert plc.leaf_cgroup is None

    @pytest.mark.parametrize(("memory_max", "cpu_max"), [
        ("0", "500000 100000"),
        (str(SLICE_MAX), "0 100000"),
        (str(SLICE_MAX), "500000 0"),
        (str(SLICE_MAX), "invalid 100000"),
        (str(SLICE_MAX), "500000"),
        (str(SLICE_MAX), None),
    ])
    def test_capacity_requires_positive_parseable_memory_and_cpu(
        self, tmp_path, memory_max, cpu_max,
    ):
        root = _fake_cgroup_root(tmp_path)
        gates = root / "dev.slice" / "dev-gates.slice"
        (gates / "memory.max").write_text(memory_max)
        cpu_file = gates / "cpu.max"
        if cpu_max is None:
            cpu_file.unlink()
        else:
            cpu_file.write_text(cpu_max)
        plc = _placement(root)

        plc.apply([])

        assert plc.error == placement.REFUSED_NO_GATES_SLICE
        assert plc.leaf_cgroup is None

    def test_directory_without_a_verified_loaded_unit_is_refused(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)
        plc = _placement(root, slice_unit_verifier=lambda _unit, _cgroup: False)
        plc.apply([])
        assert plc.error == placement.REFUSED_NO_GATES_SLICE
        assert plc.leaf_cgroup is None

    def test_a_symlinked_leaf_is_parent_not_gates_slice(self, tmp_path):
        """D-25's own refusal: something already occupies `rg-<token>` and it
        does not RESOLVE into the gates slice, so every cap below would land
        on another cgroup."""
        root = _fake_cgroup_root(tmp_path)
        elsewhere = root / "dev.slice" / "dev-background.slice" / "victim"
        elsewhere.mkdir()
        (root / "dev.slice" / "dev-gates.slice" / LEAF_NAME).symlink_to(elsewhere)
        plc = _placement(root)
        plc.apply([101])
        assert plc.error == placement.REFUSED_PARENT_NOT_GATES_SLICE
        assert plc.leaf_cgroup is None
        assert not (elsewhere / "memory.high").exists()

    def test_an_existing_leaf_is_refused_without_changing_its_caps(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)
        _leaf(root).mkdir()
        (_leaf(root) / "memory.high").write_text("123")
        plc = _placement(root)
        plc.apply([101])
        assert plc.error == placement.write_failed(
            f"dev.slice/dev-gates.slice/{LEAF_NAME}"
        )
        assert plc.leaf_cgroup is None
        assert (_leaf(root) / "memory.high").read_text() == "123"

    def test_a_leaf_created_between_check_and_mkdir_is_refused(self, tmp_path, monkeypatch):
        root = _fake_cgroup_root(tmp_path)
        original_mkdir = placement.os.mkdir
        leaf = str(_leaf(root))

        def competing_mkdir(path):
            if path == leaf:
                original_mkdir(path)
                (_leaf(root) / "memory.high").write_text("456")
                raise FileExistsError(17, "leaf appeared", path)
            return original_mkdir(path)

        monkeypatch.setattr(placement.os, "mkdir", competing_mkdir)
        plc = _placement(root)
        plc.apply([])
        assert plc.error == placement.write_failed(
            f"dev.slice/dev-gates.slice/{LEAF_NAME}"
        )
        assert plc.leaf_cgroup is None
        assert (_leaf(root) / "memory.high").read_text() == "456"

    def test_a_failed_cap_write_abandons_the_leaf_and_names_the_file(self, tmp_path):
        """`place-refused:write-failed:<file>` names the path relative to the
        cgroup root: three files in a placement are called `cgroup.procs`,
        so a basename alone would not say which write the kernel refused."""
        root = _fake_cgroup_root(tmp_path)

        class _Unwritable(placement.LanePlacement):
            def _write(self, abs_target: str, value: str) -> None:
                if abs_target.endswith("memory.max"):
                    raise OSError(1, "Operation not permitted", abs_target)
                super()._write(abs_target, value)

        plc = _Unwritable(
            cgroup_root=str(root), gates_cgroup=GATES_CGROUP, token=TOKEN,
            origin_cgroup="/" + SCOPE_CGROUP, rmdir=_fake_rmdir,
            request=placement.PlacementRequest(memory_high=MEMORY_HIGH, memory_max=MEMORY_MAX),
        )
        plc.apply([101])
        assert plc.error == (
            f"place-refused:write-failed:dev.slice/dev-gates.slice/{LEAF_NAME}/memory.max"
        )
        assert plc.block()["leaf"] is None and plc.applied == {}
        assert not _leaf(root).exists()  # abandoned, not left empty and capless

    def test_a_leaf_that_cannot_even_be_abandoned_still_reports_the_cap_failure(self, tmp_path):
        """Both writes fail: the cap, and then the `rmdir` that would take
        the useless leaf back out. The caller still gets the CAP's code —
        the failure that actually refused the placement — and no exception."""
        root = _fake_cgroup_root(tmp_path)

        class _Unwritable(placement.LanePlacement):
            def _write(self, abs_target: str, value: str) -> None:
                if abs_target.endswith("cpu.weight"):
                    raise OSError(1, "Operation not permitted", abs_target)
                super()._write(abs_target, value)

        plc = _Unwritable(
            cgroup_root=str(root), gates_cgroup=GATES_CGROUP, token=TOKEN,
            origin_cgroup="/" + SCOPE_CGROUP, rmdir=_raise_busy,
            request=placement.PlacementRequest(cpu_weight=100),
        )
        plc.apply([])
        assert plc.error.endswith(f"{LEAF_NAME}/cpu.weight")
        assert plc.leaf_cgroup is None
        assert _leaf(root).is_dir()  # the host really does still carry it

    def test_a_failed_mkdir_is_reported_before_any_leaf_exists(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)
        # A FILE where the leaf must go: `makedirs` raises, and there is no
        # cap write to abandon because none has happened yet.
        (root / "dev.slice" / "dev-gates.slice" / LEAF_NAME).write_text("")
        plc = _placement(root)
        plc.apply([101])
        assert plc.error.startswith("place-refused:write-failed:")
        assert plc.leaf_cgroup is None


class TestMigration:
    def test_later_pids_are_moved_and_earlier_ones_are_not_rewritten(self, tmp_path):
        """§8.3's "also pids found later": the resolver keeps finding
        descendants while the lane forks, and `migrate` is a set difference,
        not a re-write of the whole subtree every discovery tick."""
        root = _fake_cgroup_root(tmp_path)
        writes: List[Any] = []
        plc = _placement(root, writes=writes)
        plc.apply([101])
        before = len(writes)

        assert plc.migrate([101, 102, 103]) == 2
        assert plc.block()["pids_moved"] == 3
        assert [value for _, value in writes[before:]] == ["102", "103"]
        assert plc.migrate([101, 102, 103]) == 0  # nothing new to do

    def test_a_pid_that_vanished_is_skipped_not_an_error(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)
        plc = _placement(root)
        plc.apply([101])
        _fake_rmdir(str(_leaf(root)))  # the leaf is gone from under us
        assert plc.migrate([777]) == 0
        assert plc.block()["pids_moved"] == 1

    def test_an_unplaced_placement_migrates_nothing(self, tmp_path):
        plc = _placement(_fake_cgroup_root(tmp_path, gates=False))
        plc.apply([101])
        assert plc.migrate([101]) == 0

    def test_private_pid_namespace_uses_systemd_attach_and_verifies_membership(
        self, tmp_path, monkeypatch,
    ):
        root = _fake_cgroup_root(tmp_path)
        attached: List[Any] = []
        writes: List[Any] = []
        monkeypatch.setattr(access, "have_host_proc_view", lambda _root: True)

        class _PrivateNamespace(placement.LanePlacement):
            def _write(self, abs_target: str, value: str) -> None:
                if abs_target.endswith("cgroup.procs"):
                    raise OSError(errno.ESRCH, "pid is outside this namespace", abs_target)
                super()._write(abs_target, value)

        plc = _PrivateNamespace(
            cgroup_root=str(root), gates_cgroup=GATES_CGROUP, token=TOKEN,
            origin_cgroup="/" + SCOPE_CGROUP, rmdir=_fake_rmdir,
            request=placement.PlacementRequest(),
            on_write=lambda rel, value: writes.append((rel, value)),
            systemd_attach=lambda unit, subcgroup, pid: attached.append(
                (unit, subcgroup, pid)
            ) is None,
            pid_exists=lambda _pid: True,
            pid_cgroup=lambda _pid: f"{GATES_CGROUP}/{LEAF_NAME}",
        )
        plc.apply([101])

        assert attached == [("dev-gates.slice", LEAF_NAME, 101)]
        assert (f"dev.slice/dev-gates.slice/{LEAF_NAME}/cgroup.procs", "101") in writes
        assert plc.error is None
        assert plc.block()["pids_moved"] == 1

    def test_non_esrch_write_failure_is_not_sent_to_systemd(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)
        attached: List[Any] = []
        logged: List[str] = []

        class _PrivateNamespace(placement.LanePlacement):
            def _write(self, abs_target: str, value: str) -> None:
                if abs_target.endswith("cgroup.procs"):
                    raise OSError(errno.EPERM, "write refused", abs_target)
                super()._write(abs_target, value)

        plc = _PrivateNamespace(
            cgroup_root=str(root), gates_cgroup=GATES_CGROUP, token=TOKEN,
            origin_cgroup="/" + SCOPE_CGROUP, rmdir=_fake_rmdir,
            request=placement.PlacementRequest(),
            systemd_attach=lambda unit, subcgroup, pid: attached.append(
                (unit, subcgroup, pid)
            ) or True,
            pid_exists=lambda _pid: True,
            pid_cgroup=lambda _pid: f"{GATES_CGROUP}/{LEAF_NAME}",
            log=logged.append,
        )
        plc.apply([101])

        assert attached == []
        assert plc.block()["pids_moved"] == 0
        assert plc.error == placement.write_failed(
            f"{GATES_CGROUP.lstrip('/')}/{LEAF_NAME}/cgroup.procs"
        )
        assert plc.block()["leaf"] is None
        assert not _leaf(root).exists()
        assert any(
            "direct migration of pid 101 failed" in message
            and "write refused" in message
            for message in logged
        )

    def test_failed_systemd_attach_abandons_empty_leaf(self, tmp_path, monkeypatch):
        root = _fake_cgroup_root(tmp_path)
        logged: List[str] = []
        monkeypatch.setattr(access, "have_host_proc_view", lambda _root: True)

        class _PrivateNamespace(placement.LanePlacement):
            def _write(self, abs_target: str, value: str) -> None:
                if abs_target.endswith("cgroup.procs"):
                    raise OSError(errno.ESRCH, "pid is outside this namespace", abs_target)
                super()._write(abs_target, value)

        plc = _PrivateNamespace(
            cgroup_root=str(root), gates_cgroup=GATES_CGROUP, token=TOKEN,
            origin_cgroup="/" + SCOPE_CGROUP, rmdir=_fake_rmdir,
            request=placement.PlacementRequest(),
            systemd_attach=lambda _unit, _subcgroup, _pid: False,
            pid_exists=lambda _pid: True,
            log=logged.append,
        )
        plc.apply([101])

        assert plc.leaf_cgroup is None
        assert plc.error == placement.write_failed(
            f"dev.slice/dev-gates.slice/{LEAF_NAME}/cgroup.procs"
        )
        assert "systemd could not attach pid 101" in logged[-1]
        assert not _leaf(root).exists()

    def test_systemd_attach_membership_mismatch_is_failure_and_logged(
        self, tmp_path, monkeypatch,
    ):
        root = _fake_cgroup_root(tmp_path)
        logged: List[str] = []
        monkeypatch.setattr(access, "have_host_proc_view", lambda _root: True)

        class _PrivateNamespace(placement.LanePlacement):
            def _write(self, abs_target: str, value: str) -> None:
                if abs_target.endswith("cgroup.procs"):
                    raise OSError(errno.ESRCH, "pid is outside this namespace", abs_target)
                super()._write(abs_target, value)

        plc = _PrivateNamespace(
            cgroup_root=str(root), gates_cgroup=GATES_CGROUP, token=TOKEN,
            origin_cgroup="/" + SCOPE_CGROUP, rmdir=_fake_rmdir,
            request=placement.PlacementRequest(),
            systemd_attach=lambda _unit, _subcgroup, _pid: True,
            pid_exists=lambda _pid: True,
            pid_cgroup=lambda _pid: "/dev.slice/dev-gates.slice/other-leaf",
            log=logged.append,
        )
        plc.apply([101])

        assert plc.leaf_cgroup is None
        assert plc.error == placement.write_failed(
            f"dev.slice/dev-gates.slice/{LEAF_NAME}/cgroup.procs"
        )
        assert "systemd attach of pid 101 was not visible" in logged[-1]
        assert not _leaf(root).exists()

    def test_systemd_attach_failures_without_log_sinks_still_refuse(
        self, tmp_path, monkeypatch,
    ):
        monkeypatch.setattr(access, "have_host_proc_view", lambda _root: True)
        root = _fake_cgroup_root(tmp_path / "attach")

        class _PrivateNamespace(placement.LanePlacement):
            def _write(self, abs_target: str, value: str) -> None:
                if abs_target.endswith("cgroup.procs"):
                    raise OSError(errno.ESRCH, "pid is outside this namespace", abs_target)
                super()._write(abs_target, value)

        attach_failed = _PrivateNamespace(
            cgroup_root=str(root), gates_cgroup=GATES_CGROUP, token=TOKEN,
            origin_cgroup="/" + SCOPE_CGROUP, rmdir=_fake_rmdir,
            request=placement.PlacementRequest(),
            systemd_attach=lambda _unit, _subcgroup, _pid: False,
            pid_exists=lambda _pid: True,
        )
        attach_failed.apply([101])
        assert attach_failed.error is not None

        mismatch_root = _fake_cgroup_root(tmp_path / "mismatch")
        mismatch = _PrivateNamespace(
            cgroup_root=str(mismatch_root), gates_cgroup=GATES_CGROUP, token=TOKEN,
            origin_cgroup="/" + SCOPE_CGROUP, rmdir=_fake_rmdir,
            request=placement.PlacementRequest(),
            systemd_attach=lambda _unit, _subcgroup, _pid: True,
            pid_exists=lambda _pid: True,
            pid_cgroup=lambda _pid: "/dev.slice/dev-gates.slice/other-leaf",
        )
        mismatch.apply([101])
        assert mismatch.error is not None

    @pytest.mark.parametrize("with_log", [True, False])
    def test_esrch_migration_without_host_proc_fails_closed(
        self, tmp_path, monkeypatch, with_log,
    ):
        root = _fake_cgroup_root(tmp_path)
        logged: List[str] = []
        attached: List[Any] = []
        monkeypatch.setattr(access, "have_host_proc_view", lambda _root: False)

        class _PrivateNamespace(placement.LanePlacement):
            def _write(self, abs_target: str, value: str) -> None:
                if abs_target.endswith("cgroup.procs"):
                    raise OSError(errno.ESRCH, "host pid is outside this namespace", abs_target)
                super()._write(abs_target, value)

        plc = _PrivateNamespace(
            cgroup_root=str(root), gates_cgroup=GATES_CGROUP, token=TOKEN,
            origin_cgroup="/" + SCOPE_CGROUP, rmdir=_fake_rmdir,
            request=placement.PlacementRequest(), systemd_attach=lambda *args: (
                attached.append(args) or True
            ), pid_exists=lambda _pid: True,
            log=logged.append if with_log else None,
        )
        plc.apply([101])

        assert attached == []
        assert plc.error == placement.write_failed(
            f"dev.slice/dev-gates.slice/{LEAF_NAME}/cgroup.procs"
        )
        assert plc.leaf_cgroup is None
        if with_log:
            assert "without a verified host-proc view" in logged[-1]

    def test_esrch_migration_for_a_vanished_pid_does_not_use_systemd(
        self, tmp_path, monkeypatch,
    ):
        root = _fake_cgroup_root(tmp_path)
        attached: List[Any] = []
        monkeypatch.setattr(access, "have_host_proc_view", lambda _root: True)

        class _PrivateNamespace(placement.LanePlacement):
            def _write(self, abs_target: str, value: str) -> None:
                if abs_target.endswith("cgroup.procs"):
                    raise OSError(errno.ESRCH, "pid exited", abs_target)
                super()._write(abs_target, value)

        plc = _PrivateNamespace(
            cgroup_root=str(root), gates_cgroup=GATES_CGROUP, token=TOKEN,
            origin_cgroup="/" + SCOPE_CGROUP, rmdir=_fake_rmdir,
            request=placement.PlacementRequest(),
            systemd_attach=lambda *args: attached.append(args) or True,
            pid_exists=lambda _pid: False,
        )
        plc.apply([101])

        assert attached == []
        assert plc.error is None
        assert plc.block()["pids_moved"] == 0

    def test_default_proc_helpers_use_the_placement_proc_root(self, tmp_path, monkeypatch):
        root = _fake_cgroup_root(tmp_path)
        proc = _fake_proc(tmp_path)
        (proc / "4242").mkdir()
        plc = _placement(root, proc_root=str(proc))
        calls: List[Any] = []

        def fake_cgroup_of_pid(pid, cgroup_root, proc_root):
            calls.append((pid, cgroup_root, proc_root))
            return "/dev.slice/dev-background.slice/work.scope"

        monkeypatch.setattr("lib.targets.cgroup_of_pid", fake_cgroup_of_pid)
        assert plc._default_pid_cgroup(4242) == "/dev.slice/dev-background.slice/work.scope"
        assert calls == [(4242, str(root), str(proc))]
        assert plc._default_pid_exists(4242) is True
        assert plc._default_pid_exists(9999) is False


def test_systemd_attach_uses_the_narrow_manager_method(monkeypatch):
    calls: List[Any] = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setenv("CGPROFILE_BUSCTL", "busctl-test")
    assert placement._systemd_attach_process(
        "dev-gates.slice", LEAF_NAME, 4242, run=fake_run
    ) is True
    assert calls == [(
        [
            "busctl-test", "--system", "call", "org.freedesktop.systemd1",
            "/org/freedesktop/systemd1", "org.freedesktop.systemd1.Manager",
            "AttachProcessesToUnit", "ssau", "dev-gates.slice", LEAF_NAME,
            "1", "4242",
        ],
        {"check": False, "capture_output": True, "text": True, "timeout": 5.0},
    )]


def test_systemd_attach_returns_false_when_busctl_cannot_start():
    def unavailable(*_args, **_kwargs):
        raise OSError(2, "busctl not found")

    assert placement._systemd_attach_process(
        "dev-gates.slice", LEAF_NAME, 4242, run=unavailable
    ) is False


@pytest.mark.parametrize(
    ("cgroup", "expected"),
    [
        ("dev.slice/dev-background.slice/job.scope", None),
        ("/dev.slice/dev-background.slice/job.scope", ("job.scope", "")),
        ("/dev.slice/dev-background.slice/job.scope/child", ("job.scope", "child")),
        (
            "/dev.slice/dev-background.slice/job.scope/worker.slice/task",
            ("worker.slice", "task"),
        ),
        ("/dev.slice/../job.scope", None),
    ],
)
def test_systemd_destination_is_narrow_and_derived_from_unit_path(cgroup, expected):
    assert placement._systemd_destination(cgroup) == expected


class TestRelease:
    def test_survivors_go_back_to_the_origin_scope_and_the_leaf_goes(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)
        plc = _placement(root)
        plc.apply([101, 102])
        # A pid the lane forked after the last discovery tick: `release`
        # reads the LEAF's own procs, not what it remembers moving.
        (_leaf(root) / "cgroup.procs").write_text("101\n102\n999\n")

        plc.release()
        assert plc.error is None
        assert not _leaf(root).exists()
        assert (root / SCOPE_CGROUP / "cgroup.procs").read_text() == "999"
        assert plc.placed is False
        plc.release()  # idempotent: a second stop does nothing at all
        assert plc.error is None

    def test_an_empty_leaf_moves_nobody_back(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)
        plc = _placement(root)
        plc.apply([])
        plc.release()
        assert (root / SCOPE_CGROUP / "cgroup.procs").read_text().strip() == ""

    def test_a_survivor_that_exits_mid_move_is_skipped(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)
        origin_procs = root / SCOPE_CGROUP / "cgroup.procs"

        class _ExitedSurvivor(placement.LanePlacement):
            def _write(self, abs_target: str, value: str) -> None:
                if abs_target == str(origin_procs):
                    raise OSError(errno.ESRCH, "pid exited", abs_target)
                super()._write(abs_target, value)

        plc = _ExitedSurvivor(
            cgroup_root=str(root), gates_cgroup=GATES_CGROUP, token=TOKEN,
            origin_cgroup="/" + SCOPE_CGROUP, rmdir=_fake_rmdir,
            request=placement.PlacementRequest(), pid_exists=lambda _pid: False,
        )
        plc.apply([])
        (_leaf(root) / "cgroup.procs").write_text("101\nnot-a-pid\n102\n")
        plc.release()
        assert not _leaf(root).exists()  # the leaf still goes

    def test_private_pid_namespace_restores_survivor_via_systemd(self, tmp_path, monkeypatch):
        root = _fake_cgroup_root(tmp_path)
        proc = _fake_proc(tmp_path)
        attached: List[Any] = []
        writes: List[Any] = []
        origin = "/" + SCOPE_CGROUP
        origin_procs = root / SCOPE_CGROUP / "cgroup.procs"
        current_cgroups = {4242: f"{GATES_CGROUP}/{LEAF_NAME}"}

        class _PrivateNamespace(placement.LanePlacement):
            def _write(self, abs_target: str, value: str) -> None:
                if abs_target == str(origin_procs):
                    raise OSError(errno.ESRCH, "host pid is outside this namespace", abs_target)
                super()._write(abs_target, value)

        def attach(unit: str, subcgroup: str, pid: int) -> bool:
            attached.append((unit, subcgroup, pid))
            current_cgroups[pid] = origin
            return True

        monkeypatch.setattr(access, "have_host_proc_view", lambda _root: True)
        monkeypatch.setattr(
            "lib.targets.pids_in_cgroup",
            lambda cgroup, _root, _proc: [4242]
            if cgroup == f"{GATES_CGROUP}/{LEAF_NAME}" else [],
        )
        plc = _PrivateNamespace(
            cgroup_root=str(root), gates_cgroup=GATES_CGROUP, token=TOKEN,
            origin_cgroup=origin, rmdir=_fake_rmdir, proc_root=str(proc),
            request=placement.PlacementRequest(),
            systemd_attach=attach, pid_exists=lambda pid: pid in current_cgroups,
            pid_cgroup=lambda pid: current_cgroups.get(pid),
            on_write=lambda rel, value: writes.append((rel, value)),
        )
        plc.apply([])
        (_leaf(root) / "cgroup.procs").write_text("0\n")

        plc.release()

        assert attached == [(f"docker-{CONTAINER_ID}.scope", "", 4242)]
        assert (f"{SCOPE_CGROUP}/cgroup.procs", "4242") in writes
        assert plc.error is None
        assert plc.released is True
        assert not _leaf(root).exists()

    def test_empty_leaf_after_unavailable_host_scan_can_be_released(self, tmp_path, monkeypatch):
        root = _fake_cgroup_root(tmp_path)
        plc = _placement(root, request=placement.PlacementRequest())
        plc.apply([])
        _host_proc_with_pids(monkeypatch, [])
        readings = iter(([0], []))
        monkeypatch.setattr(placement, "_read_pids", lambda _path: next(readings))

        plc.release()

        assert plc.error is None
        assert plc.released is True
        assert not _leaf(root).exists()

    def test_unavailable_host_scan_with_remaining_placeholder_keeps_leaf(
        self, tmp_path, monkeypatch,
    ):
        root = _fake_cgroup_root(tmp_path)
        logged: List[str] = []
        plc = _placement(root, request=placement.PlacementRequest(), log=logged.append)
        plc.apply([])
        _host_proc_with_pids(monkeypatch, [])
        monkeypatch.setattr(placement, "_read_pids", lambda _path: [0])

        plc.release()

        assert plc.error == placement.write_failed(f"{SCOPE_CGROUP}/cgroup.procs")
        assert plc.released is False
        assert _leaf(root).exists()
        assert "could not safely restore all survivors" in logged[-1]

    @pytest.mark.parametrize(
        ("exists", "membership", "expected_error", "expected_leaf"),
        [
            (False, None, None, False),
            (True, None, f"place-refused:write-failed:{SCOPE_CGROUP}/cgroup.procs", True),
            (True, "/elsewhere", None, False),
        ],
    )
    def test_host_proc_source_membership_is_checked_before_restore(
        self, tmp_path, monkeypatch, exists, membership, expected_error, expected_leaf,
    ):
        root = _fake_cgroup_root(tmp_path)
        proc = _fake_proc(tmp_path)
        _host_proc_with_pids(monkeypatch, [4242])
        plc = _placement(
            root, request=placement.PlacementRequest(), proc_root=str(proc),
            pid_exists=lambda _pid: exists, pid_cgroup=lambda _pid: membership,
            systemd_attach=lambda *_args: pytest.fail("systemd must not see stale membership"),
        )
        plc.apply([])
        (_leaf(root) / "cgroup.procs").write_text("0\n")

        plc.release()

        assert plc.error == expected_error
        assert _leaf(root).exists() is expected_leaf

    def test_host_proc_pid_disappearing_during_cgroup_read_is_skipped(self, tmp_path, monkeypatch):
        root = _fake_cgroup_root(tmp_path)
        proc = _fake_proc(tmp_path)
        _host_proc_with_pids(monkeypatch, [4242])
        existence = iter((True, False))
        plc = _placement(
            root, request=placement.PlacementRequest(), proc_root=str(proc),
            pid_exists=lambda _pid: next(existence), pid_cgroup=lambda _pid: None,
        )
        plc.apply([])
        (_leaf(root) / "cgroup.procs").write_text("0\n")

        plc.release()

        assert plc.error is None
        assert plc.released is True
        assert not _leaf(root).exists()

    def test_direct_restore_is_verified_and_records_event(self, tmp_path, monkeypatch):
        root = _fake_cgroup_root(tmp_path)
        proc = _fake_proc(tmp_path)
        origin = "/" + SCOPE_CGROUP
        origin_procs = root / SCOPE_CGROUP / "cgroup.procs"
        current = {4242: f"{GATES_CGROUP}/{LEAF_NAME}"}
        writes: List[Any] = []
        _host_proc_with_pids(monkeypatch, [4242])

        class _DirectRestore(placement.LanePlacement):
            def _write(self, abs_target: str, value: str) -> None:
                super()._write(abs_target, value)
                if abs_target == str(origin_procs):
                    current[4242] = origin

        plc = _DirectRestore(
            cgroup_root=str(root), gates_cgroup=GATES_CGROUP, token=TOKEN,
            origin_cgroup=origin, rmdir=_fake_rmdir, proc_root=str(proc),
            request=placement.PlacementRequest(), pid_exists=lambda _pid: True,
            pid_cgroup=lambda pid: current.get(pid),
            on_write=lambda rel, value: writes.append((rel, value)),
        )
        plc.apply([])
        (_leaf(root) / "cgroup.procs").write_text("0\n")

        plc.release()

        assert plc.error is None
        assert (f"{SCOPE_CGROUP}/cgroup.procs", "4242") in writes
        assert not _leaf(root).exists()

    @pytest.mark.parametrize(
        ("after_write", "exists_after", "expected_error", "expected_leaf"),
        [
            ("/elsewhere", True, True, True),
            (None, True, True, True),
            (None, False, False, False),
        ],
    )
    def test_direct_restore_requires_verified_destination_or_exit(
        self, tmp_path, monkeypatch, after_write, exists_after, expected_error, expected_leaf,
    ):
        root = _fake_cgroup_root(tmp_path)
        proc = _fake_proc(tmp_path)
        _host_proc_with_pids(monkeypatch, [4242])
        memberships = iter((f"{GATES_CGROUP}/{LEAF_NAME}", after_write))
        existence = iter((True, exists_after))
        plc = _placement(
            root, request=placement.PlacementRequest(), proc_root=str(proc),
            pid_exists=lambda _pid: next(existence),
            pid_cgroup=lambda _pid: next(memberships),
        )
        plc.apply([])
        (_leaf(root) / "cgroup.procs").write_text("0\n")

        plc.release()

        assert (plc.error is not None) is expected_error
        assert _leaf(root).exists() is expected_leaf

    def test_host_proc_restore_rejects_direct_write_failure(self, tmp_path, monkeypatch):
        root = _fake_cgroup_root(tmp_path)
        proc = _fake_proc(tmp_path)
        logged: List[str] = []
        attached: List[Any] = []
        origin_procs = root / SCOPE_CGROUP / "cgroup.procs"
        _host_proc_with_pids(monkeypatch, [4242])

        class _DirectFailure(placement.LanePlacement):
            def _write(self, abs_target: str, value: str) -> None:
                if abs_target == str(origin_procs):
                    raise OSError(errno.EPERM, "write denied", abs_target)
                super()._write(abs_target, value)

        plc = _DirectFailure(
            cgroup_root=str(root), gates_cgroup=GATES_CGROUP, token=TOKEN,
            origin_cgroup="/" + SCOPE_CGROUP, rmdir=_fake_rmdir, proc_root=str(proc),
            request=placement.PlacementRequest(), pid_exists=lambda _pid: True,
            pid_cgroup=lambda _pid: f"{GATES_CGROUP}/{LEAF_NAME}",
            systemd_attach=lambda *args: attached.append(args) or True,
            log=logged.append,
        )
        plc.apply([])
        (_leaf(root) / "cgroup.procs").write_text("0\n")

        plc.release()

        assert attached == []
        assert plc.error == placement.write_failed(f"{SCOPE_CGROUP}/cgroup.procs")
        assert _leaf(root).exists()
        assert "could not safely restore all survivors" in logged[-1]

    def test_host_proc_restore_refuses_origin_without_systemd_unit(self, tmp_path, monkeypatch):
        root = _fake_cgroup_root(tmp_path)
        proc = _fake_proc(tmp_path)
        target_cgroup = "unmanaged/child"
        write_cgroup(root, target_cgroup, cgroup_files())
        origin_procs = root / target_cgroup / "cgroup.procs"
        attached: List[Any] = []
        _host_proc_with_pids(monkeypatch, [4242])

        class _PrivateNamespace(placement.LanePlacement):
            def _write(self, abs_target: str, value: str) -> None:
                if abs_target == str(origin_procs):
                    raise OSError(errno.ESRCH, "host pid is outside this namespace", abs_target)
                super()._write(abs_target, value)

        plc = _PrivateNamespace(
            cgroup_root=str(root), gates_cgroup=GATES_CGROUP, token=TOKEN,
            origin_cgroup="/" + target_cgroup, rmdir=_fake_rmdir, proc_root=str(proc),
            request=placement.PlacementRequest(), pid_exists=lambda _pid: True,
            pid_cgroup=lambda _pid: f"{GATES_CGROUP}/{LEAF_NAME}",
            systemd_attach=lambda *args: attached.append(args) or True,
        )
        plc.apply([])
        (_leaf(root) / "cgroup.procs").write_text("0\n")

        plc.release()

        assert attached == []
        assert plc.error == placement.write_failed(f"{target_cgroup}/cgroup.procs")
        assert _leaf(root).exists()

    @pytest.mark.parametrize("with_log", [True, False])
    def test_systemd_restore_membership_mismatch_keeps_leaf_and_logs(
        self, tmp_path, monkeypatch, with_log,
    ):
        root = _fake_cgroup_root(tmp_path)
        proc = _fake_proc(tmp_path)
        logged: List[str] = []
        origin_procs = root / SCOPE_CGROUP / "cgroup.procs"
        attached: List[Any] = []
        _host_proc_with_pids(monkeypatch, [4242])

        class _PrivateNamespace(placement.LanePlacement):
            def _write(self, abs_target: str, value: str) -> None:
                if abs_target == str(origin_procs):
                    raise OSError(errno.ESRCH, "host pid is outside this namespace", abs_target)
                super()._write(abs_target, value)

        def attach(unit: str, subcgroup: str, pid: int) -> bool:
            attached.append((unit, subcgroup, pid))
            return True

        plc = _PrivateNamespace(
            cgroup_root=str(root), gates_cgroup=GATES_CGROUP, token=TOKEN,
            origin_cgroup="/" + SCOPE_CGROUP, rmdir=_fake_rmdir, proc_root=str(proc),
            request=placement.PlacementRequest(), systemd_attach=attach,
            pid_exists=lambda _pid: True,
            pid_cgroup=lambda _pid: f"{GATES_CGROUP}/{LEAF_NAME}",
            log=logged.append if with_log else None,
        )
        plc.apply([])
        (_leaf(root) / "cgroup.procs").write_text("0\n")

        plc.release()

        assert attached == [(f"docker-{CONTAINER_ID}.scope", "", 4242)]
        assert plc.error == placement.write_failed(f"{SCOPE_CGROUP}/cgroup.procs")
        assert _leaf(root).exists()
        if with_log:
            assert any("not visible" in row for row in logged)

    def test_systemd_restore_of_pid_that_exits_after_attach_is_complete(
        self, tmp_path, monkeypatch,
    ):
        root = _fake_cgroup_root(tmp_path)
        proc = _fake_proc(tmp_path)
        origin_procs = root / SCOPE_CGROUP / "cgroup.procs"
        current = {4242: f"{GATES_CGROUP}/{LEAF_NAME}"}
        _host_proc_with_pids(monkeypatch, [4242])

        class _PrivateNamespace(placement.LanePlacement):
            def _write(self, abs_target: str, value: str) -> None:
                if abs_target == str(origin_procs):
                    raise OSError(errno.ESRCH, "host pid is outside this namespace", abs_target)
                super()._write(abs_target, value)

        def attach(_unit: str, _subcgroup: str, pid: int) -> bool:
            current.pop(pid)
            return True

        plc = _PrivateNamespace(
            cgroup_root=str(root), gates_cgroup=GATES_CGROUP, token=TOKEN,
            origin_cgroup="/" + SCOPE_CGROUP, rmdir=_fake_rmdir, proc_root=str(proc),
            request=placement.PlacementRequest(), systemd_attach=attach,
            pid_exists=lambda pid: pid in current,
            pid_cgroup=lambda pid: current.get(pid),
        )
        plc.apply([])
        (_leaf(root) / "cgroup.procs").write_text("0\n")

        plc.release()

        assert plc.error is None
        assert plc.released is True
        assert not _leaf(root).exists()

    def test_unknown_private_namespace_survivor_keeps_leaf_and_never_calls_systemd(
        self, tmp_path, monkeypatch,
    ):
        root = _fake_cgroup_root(tmp_path)
        attached: List[Any] = []
        plc = _placement(
            root, request=placement.PlacementRequest(),
            systemd_attach=lambda *args: attached.append(args) or True,
        )
        plc.apply([])
        (_leaf(root) / "cgroup.procs").write_text("0\n")
        monkeypatch.setattr(access, "have_host_proc_view", lambda _root: False)

        plc.release()

        assert attached == []
        assert plc.error == placement.write_failed(f"{SCOPE_CGROUP}/cgroup.procs")
        assert plc.released is False
        assert plc.placed is True
        assert _leaf(root).exists()

    @pytest.mark.parametrize("with_log", [True, False])
    def test_failed_systemd_restore_keeps_leaf_and_reports_origin_path(
        self, tmp_path, monkeypatch, with_log,
    ):
        root = _fake_cgroup_root(tmp_path)
        proc = _fake_proc(tmp_path)
        attached: List[Any] = []
        logged: List[str] = []
        origin_procs = root / SCOPE_CGROUP / "cgroup.procs"

        class _PrivateNamespace(placement.LanePlacement):
            def _write(self, abs_target: str, value: str) -> None:
                if abs_target == str(origin_procs):
                    raise OSError(errno.ESRCH, "host pid is outside this namespace", abs_target)
                super()._write(abs_target, value)

        def fail_attach(unit: str, subcgroup: str, pid: int) -> bool:
            attached.append((unit, subcgroup, pid))
            return False

        monkeypatch.setattr(access, "have_host_proc_view", lambda _root: True)
        monkeypatch.setattr(
            "lib.targets.pids_in_cgroup",
            lambda cgroup, _root, _proc: [4242]
            if cgroup == f"{GATES_CGROUP}/{LEAF_NAME}" else [],
        )
        plc = _PrivateNamespace(
            cgroup_root=str(root), gates_cgroup=GATES_CGROUP, token=TOKEN,
            origin_cgroup="/" + SCOPE_CGROUP, rmdir=_fake_rmdir, proc_root=str(proc),
            request=placement.PlacementRequest(), systemd_attach=fail_attach,
            pid_exists=lambda pid: pid == 4242,
            pid_cgroup=lambda _pid: f"{GATES_CGROUP}/{LEAF_NAME}",
            log=logged.append if with_log else None,
        )
        plc.apply([])
        (_leaf(root) / "cgroup.procs").write_text("0\n")

        plc.release()

        assert attached == [(f"docker-{CONTAINER_ID}.scope", "", 4242)]
        assert plc.error == placement.write_failed(f"{SCOPE_CGROUP}/cgroup.procs")
        assert plc.released is False
        assert _leaf(root).exists()
        if with_log:
            assert any("systemd could not restore pid 4242" in row for row in logged)

    def test_a_leaf_that_will_not_go_is_retried_three_times_then_reported(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)
        slept: List[float] = []
        attempts: List[str] = []
        logged: List[str] = []

        def _refuse(path: str) -> None:
            attempts.append(path)
            raise OSError(16, "Device or resource busy", path)

        plc = _placement(root, rmdir=_refuse, sleep=slept.append, log=logged.append)
        plc.apply([])
        plc.release()
        assert len(attempts) == placement.RMDIR_ATTEMPTS
        assert slept == [placement.RMDIR_RETRY_SECONDS] * (placement.RMDIR_ATTEMPTS - 1)
        assert plc.error == (
            f"place-refused:write-failed:dev.slice/dev-gates.slice/{LEAF_NAME}"
        )
        # The leaf is still NAMED: it really is on the host, and an operator
        # reading the Summary has to be able to find it.
        assert plc.block()["leaf"] == f"{GATES_CGROUP}/{LEAF_NAME}"
        assert logged and "could not remove leaf" in logged[-1]

    def test_releasing_an_unplaced_placement_is_a_no_op(self, tmp_path):
        plc = _placement(_fake_cgroup_root(tmp_path, gates=False))
        plc.apply([])
        plc.release()
        assert plc.error == placement.REFUSED_NO_GATES_SLICE


class TestLeafReadingsAndKill:
    def test_leaf_readings_come_from_the_leaf(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)
        plc = _placement(root)
        plc.apply([])
        (_leaf(root) / "memory.pressure").write_text(
            "some avg10=40.00 avg60=0.00 avg300=0.00 total=0\n"
            "full avg10=33.00 avg60=0.00 avg300=0.00 total=0\n"
        )
        assert plc.leaf_readings() == {"psi_full_avg10": 33.0, "memory_high_applied": True}

    def test_without_memory_high_nothing_is_throttling(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)
        plc = _placement(root, request=placement.PlacementRequest(cpu_weight=100))
        plc.apply([])
        assert plc.leaf_readings()["memory_high_applied"] is False

    def test_an_unplaced_placement_has_no_leaf_readings(self, tmp_path):
        plc = _placement(_fake_cgroup_root(tmp_path, gates=False))
        plc.apply([])
        assert plc.leaf_readings() == {"psi_full_avg10": None, "memory_high_applied": False}

    def test_kill_writes_one_to_cgroup_kill(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)
        plc = _placement(root)
        plc.apply([101])
        assert plc.kill() is True
        assert (_leaf(root) / "cgroup.kill").read_text() == "1"

    def test_incomplete_placement_is_logged_and_never_killed(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)
        logged: List[str] = []
        plc = _placement(root, log=logged.append)
        plc.apply([101])
        # A real cgroup exposes this controller file even before the first
        # write; the fake cgroup helper creates it only when requested.
        (_leaf(root) / "cgroup.kill").write_text("0")
        plc.error = placement.write_failed("simulated-incomplete-placement")

        assert plc.kill() is False
        assert (_leaf(root) / "cgroup.kill").read_text() == "0"
        assert logged and "placement is incomplete" in logged[-1]

    def test_a_preexisting_leaf_is_refused_even_without_caps(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)
        _leaf(root).mkdir()
        plc = _placement(root, request=placement.PlacementRequest())
        plc.apply([])
        assert plc.placed is False
        assert plc.error == placement.write_failed(
            f"dev.slice/dev-gates.slice/{LEAF_NAME}"
        )

    def test_a_memory_max_equal_to_the_slice_ceiling_is_allowed(self, tmp_path):
        root = _fake_cgroup_root(tmp_path, slice_memory_max=SLICE_MAX)
        plc = _placement(root, request=placement.PlacementRequest(memory_max=SLICE_MAX))
        plc.apply([])
        assert plc.placed is True
        assert plc.error is None

    def test_a_kill_that_fails_without_a_log_sink_is_still_just_false(self, tmp_path):
        """The daemon always passes a log; a `LanePlacement` built without
        one (every unit test above) must not die trying to use it."""
        root = _fake_cgroup_root(tmp_path)
        plc = _placement(root)
        plc.apply([101])
        _fake_rmdir(str(_leaf(root)))
        assert plc.kill() is False

    def test_a_kill_the_leaf_will_not_take_is_false_and_logged(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)
        logged: List[str] = []
        plc = _placement(root, log=logged.append)
        plc.apply([101])
        _fake_rmdir(str(_leaf(root)))
        assert plc.kill() is False
        assert "cgroup.kill" in logged[0]

    def test_a_cgroup_kill_write_error_is_false_and_logged(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)
        logged: List[str] = []
        plc = _placement(root, log=logged.append)
        plc.apply([101])

        def fail_write(_path: str, _value: str) -> None:
            raise OSError(1, "operation not permitted")

        plc._write = fail_write
        assert plc.kill() is False
        assert "cgroup.kill" in logged[-1]

    def test_a_cgroup_kill_write_error_without_log_sink_is_false(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)
        plc = _placement(root)
        plc.apply([101])

        def fail_write(_path: str, _value: str) -> None:
            raise OSError(1, "operation not permitted")

        plc._write = fail_write
        assert plc.kill() is False


# ── §8.3 through the daemon: start / status / watch / stop ──────────────

class TestServerPlacement:
    def test_live_token_session_reuse_rejects_a_policy_change(self, tmp_path):
        root = _fake_cgroup_root(tmp_path, procs="101\n")
        server = _server(tmp_path, root)
        started = server._dispatch({
            "verb": "start", "args": _start_args(on_stall="report"), "contract": 1,
        })
        assert started["ok"] is True

        response = server._dispatch({
            "verb": "start", "args": _start_args(on_stall="kill"), "contract": 1,
        })

        assert response["ok"] is False
        assert response["error"]["code"] == "bad-policy"
        assert "different scope or liveness policy" in response["error"]["message"]

    @pytest.mark.parametrize("placement_state", ["missing", "released", "incomplete"])
    def test_shared_kill_reuse_requires_an_eligible_leaf(
        self, tmp_path, placement_state,
    ):
        root = _fake_cgroup_root(tmp_path, procs="101\n")
        server = _server(tmp_path, root)
        started = server._dispatch({
            "verb": "start",
            "args": _start_args(on_stall="report", place=placement_state != "missing"),
            "contract": 1,
        })
        assert started["ok"] is True
        sess = server._sessions[SESSION_ID]
        sess.watch.policy = liveness.parse_policy({"on_stall": "kill"})
        if placement_state == "released":
            sess.placement.release()
        elif placement_state == "incomplete":
            sess.placement.error = placement.write_failed("simulated-incomplete-leaf")

        response = server._dispatch({
            "verb": "start", "args": _start_args(on_stall="kill"), "contract": 1,
        })

        assert response["ok"] is False
        assert response["error"]["code"] == "bad-policy"
        assert "no verified kill leaf" in response["error"]["message"]

    def test_placement_refusal_is_logged_but_success_is_silent(self, tmp_path, monkeypatch):
        logs: List[str] = []
        monkeypatch.setattr(serve.SessionServer, "_log", staticmethod(logs.append))

        refused_root = _fake_cgroup_root(tmp_path / "refused", gates=False)
        refused_server = _server(tmp_path / "refused", refused_root)
        refused = refused_server._create_session_locked(
            container_id=CONTAINER_ID, cgroup="/" + SCOPE_CGROUP,
            scope="container-shared", token=TOKEN, interval=1.0, damon_req="off",
            meta=META, place_request=placement.PlacementRequest(),
        )
        assert refused.placement.error == placement.REFUSED_NO_GATES_SLICE
        assert logs and "placement refused" in logs[-1]

        logs.clear()
        success_root = _fake_cgroup_root(tmp_path / "success")
        success_server = _server(tmp_path / "success", success_root)
        success = success_server._create_session_locked(
            container_id=CONTAINER_ID, cgroup="/" + SCOPE_CGROUP,
            scope="container-shared", token=TOKEN, interval=1.0, damon_req="off",
            meta=META, place_request=placement.PlacementRequest(),
        )
        assert success.placement.placed is True
        assert logs == []

    def test_shared_kill_refusal_logs_manifest_failure_and_incomplete_cleanup(
        self, tmp_path, monkeypatch,
    ):
        root = _fake_cgroup_root(tmp_path, procs="101\n")
        logs: List[str] = []
        real_apply = placement.LanePlacement.apply

        def partial_apply(lane, pids):
            real_apply(lane, pids)
            lane.error = placement.write_failed("simulated-partial-placement")

        monkeypatch.setattr(placement.LanePlacement, "apply", partial_apply)
        monkeypatch.setattr(
            placement.LanePlacement, "_move_survivors_back",
            lambda _lane, _leaf: False,
        )
        monkeypatch.setattr(
            serve.store.RunDir, "write_manifest",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
        )
        monkeypatch.setattr(serve.SessionServer, "_log", staticmethod(logs.append))
        server = _server(tmp_path, root)

        response = server._dispatch({
            "verb": "start",
            "args": _start_args(on_stall="kill"),
            "contract": 1,
        })

        assert response["ok"] is False
        assert response["error"]["code"] == "bad-policy"
        assert "cleanup was incomplete" in response["error"]["message"]
        assert server._sessions == {}
        assert any("could not persist placement refusal" in row for row in logs)

    def test_start_places_the_lane_and_every_document_carries_the_block(self, tmp_path):
        root = _fake_cgroup_root(tmp_path, procs="101\n")
        server = _server(tmp_path, root)
        resp = server._dispatch({"verb": "start", "args": _start_args(), "contract": 1})

        assert resp["ok"] is True
        block = resp["placement"]
        assert block["leaf"] == f"{GATES_CGROUP}/{LEAF_NAME}"
        assert block["applied"] == {
            "memory.high": MEMORY_HIGH, "memory.max": MEMORY_MAX, "cpu.weight": 100,
        }
        assert block["error"] is None
        assert (root / "dev.slice/dev-gates.slice" / LEAF_NAME).is_dir()

        sess = server._sessions[SESSION_ID]
        assert sess.placement.proc_root == str(tmp_path / "proc")
        assert server._status_entry(sess)["placement"] == block
        reading, _watch = server._watch_lines(sess)
        assert reading["placement"] == block
        # D-25: the writes are in the session's own events.jsonl.
        rows = [
            json.loads(line)
            for line in (Path(sess.rundir.path) / "events.jsonl").read_text().splitlines()
        ]
        assert [r["kind"] for r in rows] == ["cgroup_write"] * len(rows)
        assert any(r["target"].endswith("cgroup.subtree_control") for r in rows)
        assert all(r["severity"] == "info" for r in rows)

        stop = server._dispatch({"verb": "stop", "args": {"session": SESSION_ID}, "contract": 1})
        assert stop["summary"]["placement"]["leaf"] == f"{GATES_CGROUP}/{LEAF_NAME}"
        assert not (root / "dev.slice/dev-gates.slice" / LEAF_NAME).exists()

    def test_the_gates_slice_snapshot_counts_the_live_leaf(self, tmp_path):
        """C5 wrote `leaves`/`sessions_live` off disk against a tree that
        could not yet have a leaf in it — this is the first test where one
        really exists."""
        root = _fake_cgroup_root(tmp_path)
        server = _server(tmp_path, root)
        server._dispatch({"verb": "start", "args": _start_args(), "contract": 1})
        host = server._host_snapshot()
        assert host["gates_slice"]["leaves"] == [LEAF_NAME]
        assert host["gates_slice"]["sessions_live"] == 1

    @pytest.mark.parametrize("overrides, root_kwargs, expected", [
        ({"token": None}, {}, placement.REFUSED_NO_TOKEN),
        ({}, {"gates": False}, placement.REFUSED_NO_GATES_SLICE),
        ({"memory_max": SLICE_MAX + 1}, {}, placement.REFUSED_OVER_SLICE),
    ])
    def test_a_refusal_never_fails_start(self, tmp_path, overrides, root_kwargs, expected):
        """§8.3: "placement never fails `start`" — the session starts, is
        sampled and stops normally; the refusal is a value in a block."""
        root = _fake_cgroup_root(tmp_path, **root_kwargs)
        server = _server(tmp_path, root)
        resp = server._dispatch(
            {"verb": "start", "args": _start_args(**overrides), "contract": 1}
        )
        assert resp["ok"] is True
        assert resp["placement"] == {
            "requested": True, "leaf": None, "applied": {}, "pids_moved": 0, "error": expected,
        }
        assert server._sessions[SESSION_ID].finished is False
        stop = server._dispatch({"verb": "stop", "args": {"session": SESSION_ID}, "contract": 1})
        assert stop["summary"]["placement"]["error"] == expected

    def test_a_session_that_never_asked_carries_null_everywhere(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)
        server = _server(tmp_path, root)
        resp = server._dispatch(
            {"verb": "start", "args": _start_args(place=False), "contract": 1}
        )
        assert resp["placement"] is None
        sess = server._sessions[SESSION_ID]
        assert server._status_entry(sess)["placement"] is None
        stop = server._dispatch({"verb": "stop", "args": {"session": SESSION_ID}, "contract": 1})
        assert "placement" not in stop["summary"]

    def test_a_pid_discovered_later_is_migrated_by_the_sampler(self, tmp_path):
        """The discovery-cadence half of §8.3, driven through the real
        `_on_session_sample` rather than by calling `migrate` directly."""
        root = _fake_cgroup_root(tmp_path, procs="101\n")
        server = _server(tmp_path, root)
        server._dispatch({"verb": "start", "args": _start_args(), "contract": 1})
        sess = server._sessions[SESSION_ID]
        sess.placement.moved.clear()  # pretend nothing has been moved yet

        class _Resolver:
            current_pids = {101, 202}

            def refresh(self):
                return self.current_pids

        sess.subtree_resolver = _Resolver()
        sess.last_discovery_mono = None
        abs_target = str(root / SCOPE_CGROUP)
        server._on_session_sample(
            sess, {"mono": 10.0, "t": EPOCH_START, "cg": {sess.cgroup: {}}, "host": {}},
            abs_target, None,
        )
        assert sess.placement.block()["pids_moved"] == 2

    def test_a_placed_leaf_makes_throttled_reachable(self, tmp_path):
        """§8.4's `throttled` is "the LEAF's memory.pressure full avg10 > 20
        while memory.high is applied" — C7 implemented and unit-tested the
        judgement with the readings stubbed; this is the wiring that finally
        feeds it real ones, and the state is unreachable without a leaf."""
        root = _fake_cgroup_root(tmp_path, procs="101\n")
        server = _server(tmp_path, root)
        server._dispatch({"verb": "start", "args": _start_args(), "contract": 1})
        sess = server._sessions[SESSION_ID]
        (_leaf(root) / "memory.pressure").write_text(
            "some avg10=90.00 avg60=0.00 avg300=0.00 total=0\n"
            "full avg10=44.00 avg60=0.00 avg300=0.00 total=0\n"
        )
        server._observe_liveness(
            sess, mono=1.0, record={}, abs_target=str(root / SCOPE_CGROUP),
            target_metrics={}, host_metrics={}, pids=[101],
        )
        assert sess.watch.state == liveness.STATE_THROTTLED
        assert "memory.high applied" in sess.watch.reason
        assert sess.watch.verdict == liveness.VERDICT_REPORTED  # never killed

    def test_an_unplaced_session_cannot_be_throttled(self, tmp_path):
        root = _fake_cgroup_root(tmp_path, procs="101\n")
        server = _server(tmp_path, root)
        server._dispatch(
            {"verb": "start", "args": _start_args(place=False), "contract": 1}
        )
        sess = server._sessions[SESSION_ID]
        server._observe_liveness(
            sess, mono=1.0, record={}, abs_target=str(root / SCOPE_CGROUP),
            target_metrics={}, host_metrics={}, pids=[101],
        )
        assert sess.watch.state == liveness.STATE_OK


class TestPlacedKill:
    def _stalled_session(self, tmp_path, **overrides):
        root = _fake_cgroup_root(tmp_path, procs="101\n")
        server = _server(tmp_path, root)
        server._dispatch(
            {"verb": "start", "args": _start_args(on_stall="kill", **overrides), "contract": 1}
        )
        sess = server._sessions[SESSION_ID]
        sess.watch.state = liveness.STATE_STALLED
        sess.watch.reason = "no activity"
        return root, server, sess

    def test_observed_stall_runs_the_requested_enforcement_path(self, tmp_path, monkeypatch):
        root, server, sess = self._stalled_session(tmp_path)
        (_leaf(root) / "cgroup.procs").write_text("101\n")
        monkeypatch.setattr(sess.watch, "observe", lambda _sample: None)

        server._observe_liveness(
            sess, mono=1.0, record={}, abs_target=str(root / SCOPE_CGROUP),
            target_metrics={}, host_metrics={}, pids=[101],
        )

        assert sess.watch.verdict == liveness.VERDICT_KILLED
        assert sess.finished is True

    def test_a_placed_session_dies_by_cgroup_kill_not_by_pid(self, tmp_path, monkeypatch):
        """§8.4: one write to the leaf's `cgroup.kill`, which the kernel
        applies atomically — it cannot miss a pid that forked between the
        resolver's walk and enforcement, as a userspace pid list could. Proved by
        the write itself (captured live: an ENFORCED kill now finalizes the
        session in the same call, and finalize's own `placement.release()`
        rmdir's the leaf before this test would otherwise get to re-read
        `cgroup.kill` — so the leaf's ABSENCE afterward is now part of what
        this test proves, not a reason to stop capturing the write), and by
        the fact that no signal was sent to the (real, unrelated) pid in the
        list."""
        root, server, sess = self._stalled_session(tmp_path)
        # The fake resolver has no /proc/101 process to migrate, so model a
        # genuinely occupied cgroup explicitly for this cgroup.kill oracle.
        (_leaf(root) / "cgroup.procs").write_text("101\n")
        written: List[tuple] = []
        real_write = sess.placement._write
        sess.placement._write = lambda path, value: (
            written.append((path, value)), real_write(path, value)
        )[-1]
        monkeypatch.setattr(
            serve.os, "kill",
            lambda *_args: pytest.fail("placed enforcement must not signal a numeric PID"),
        )
        server._enforce_stall_kill(sess, [os.getpid() + 1])
        assert [item for item in written if item[0].endswith("cgroup.kill")] == [
            (str(_leaf(root) / "cgroup.kill"), "1")
        ]
        assert sess.watch.verdict == liveness.VERDICT_KILLED
        assert "cgroup.kill applied to" in sess.watch.reason
        # The enforced kill finalized the session (this file's own new
        # coverage, mirroring `test_serve_watch.py`'s
        # `finished_before_stop`) — `release()` already ran, so the leaf is
        # gone rather than lingering for a `stop` nobody called.
        assert sess.finished is True
        assert not _leaf(root).exists()

    def test_an_unplaced_session_is_reported_without_signalling_a_pid(
        self, tmp_path, monkeypatch,
    ):
        """A private PID namespace makes a numeric host PID an unsafe
        enforcement target; an unplaced shared lane must remain reported."""
        root = _fake_cgroup_root(tmp_path, procs="101\n")
        server = _server(tmp_path, root)
        started = server._dispatch({
            "verb": "start",
            "args": _start_args(place=False, on_stall="report"),
            "contract": 1,
        })
        assert started["ok"] is True
        sess = server._sessions[SESSION_ID]
        # Exercise the enforcement refusal after an unplaced report-mode
        # session exists; the public start path correctly rejects a new
        # shared-scope kill request without placement.
        sess.watch.policy = liveness.parse_policy({"on_stall": "kill"})
        sess.watch.state = liveness.STATE_STALLED
        sess.watch.reason = "no activity"
        monkeypatch.setattr(
            serve.os, "kill",
            lambda *_args: pytest.fail("daemon must never signal a numeric host PID"),
        )
        server._enforce_stall_kill(sess, [4242])
        assert sess.watch.verdict == liveness.VERDICT_REPORTED
        assert "kill-refused:unplaced-token-subtree" in sess.watch.reason
        assert sess.finished is False

    def test_empty_placed_leaf_does_not_certify_a_cgroup_kill(
        self, tmp_path, monkeypatch,
    ):
        """A private-PID-namespace migration can leave a capped but empty
        leaf; neither cgroup.kill nor a numeric PID signal proves enforcement."""
        root, server, sess = self._stalled_session(tmp_path)
        (_leaf(root) / "cgroup.procs").write_text("")
        written: List[tuple] = []
        real_write = sess.placement._write
        sess.placement._write = lambda path, value: (
            written.append((path, value)), real_write(path, value)
        )[-1]
        monkeypatch.setattr(
            serve.os, "kill",
            lambda *_args: pytest.fail("daemon must never signal a numeric host PID"),
        )
        server._enforce_stall_kill(sess, [4242])
        assert not any(path.endswith("cgroup.kill") for path, _ in written)
        assert sess.watch.verdict == liveness.VERDICT_REPORTED
        assert "kill-refused:lane-leaf-cgroup-kill-refused" in sess.watch.reason
        assert sess.finished is False

    def test_shared_leaf_kill_write_failure_never_falls_back_to_pid(
        self, tmp_path, monkeypatch,
    ):
        root, server, sess = self._stalled_session(tmp_path)
        (_leaf(root) / "cgroup.procs").write_text("101\n")
        writes: List[tuple] = []

        def fail_kill_write(path: str, value: str) -> None:
            writes.append((path, value))
            raise PermissionError(1, "write denied", path)

        sess.placement._write = fail_kill_write
        monkeypatch.setattr(
            serve.os, "kill",
            lambda *_args: pytest.fail("failed cgroup enforcement must not signal a PID"),
        )
        server._enforce_stall_kill(sess, [4242])
        assert writes == [(str(_leaf(root) / "cgroup.kill"), "1")]
        assert sess.watch.verdict == liveness.VERDICT_REPORTED
        assert "kill-refused:lane-leaf-cgroup-kill-refused" in sess.watch.reason
        assert sess.finished is False

    def test_incomplete_shared_placement_is_refused_without_pid_fallback(
        self, tmp_path, monkeypatch,
    ):
        _root, server, sess = self._stalled_session(tmp_path)
        sess.placement.error = placement.write_failed("simulated-incomplete-leaf")
        monkeypatch.setattr(
            serve.os, "kill",
            lambda *_args: pytest.fail("incomplete placement must never signal a PID"),
        )

        server._enforce_stall_kill(sess, [4242])

        assert sess.watch.verdict == liveness.VERDICT_REPORTED
        assert "kill-refused:placement-incomplete" in sess.watch.reason
        assert sess.finished is False

    def test_container_scope_kills_only_its_exact_gates_cgroup(
        self, tmp_path, monkeypatch,
    ):
        root = _fake_cgroup_root(tmp_path)
        target = _container_cgroup(root)
        unrelated = _container_cgroup(root, "e" * 64)
        target_cgroup = "/" + target.relative_to(root).as_posix()
        monkeypatch.setattr(
            serve.targets_mod, "find_container_cgroup",
            lambda _container_id, root: target_cgroup,
        )
        server = _server(tmp_path, root)
        started = server._dispatch({
            "verb": "start",
            "args": _start_args(
                scope="container", token=None, place=False, on_stall="kill",
            ),
            "contract": 1,
        })
        assert started["ok"] is True, started
        sess = server._sessions[SESSION_ID]
        sess.watch.state = liveness.STATE_STALLED
        sess.watch.reason = "no activity"
        monkeypatch.setattr(
            serve.os, "kill",
            lambda *_args: pytest.fail("container enforcement must use its cgroup boundary"),
        )

        server._enforce_stall_kill(sess, [4242])

        assert (target / "cgroup.kill").read_text() == "1"
        assert (unrelated / "cgroup.kill").read_text().strip() == "0"
        assert sess.watch.verdict == liveness.VERDICT_KILLED
        assert f"cgroup.kill applied to exact target {target_cgroup}" in sess.watch.reason
        assert sess.finished is True

    def test_container_scope_runtime_kill_refusal_is_reported(self, tmp_path, monkeypatch):
        root = _fake_cgroup_root(tmp_path)
        target = _container_cgroup(root)
        target_cgroup = "/" + target.relative_to(root).as_posix()
        monkeypatch.setattr(
            serve.targets_mod, "find_container_cgroup",
            lambda _container_id, root: target_cgroup,
        )
        server = _server(tmp_path, root)
        started = server._dispatch({
            "verb": "start",
            "args": _start_args(
                scope="container", token=None, place=False, on_stall="kill",
            ),
            "contract": 1,
        })
        assert started["ok"] is True
        sess = server._sessions[SESSION_ID]
        sess.watch.state = liveness.STATE_STALLED
        sess.watch.reason = "no activity"
        (target / "cgroup.events").write_text("populated 0\n")
        monkeypatch.setattr(
            serve.os, "kill",
            lambda *_args: pytest.fail("container refusal must never signal a PID"),
        )

        server._enforce_stall_kill(sess, [4242])

        assert (target / "cgroup.kill").read_text().strip() == "0"
        assert sess.watch.verdict == liveness.VERDICT_REPORTED
        assert "kill-refused:target-cgroup-empty-or-unreadable" in sess.watch.reason
        assert sess.finished is False

    def test_container_scope_kill_refuses_a_target_outside_gates(
        self, tmp_path, monkeypatch,
    ):
        root = _fake_cgroup_root(tmp_path)
        server = _server(tmp_path, root)
        started = server._dispatch({
            "verb": "start",
            "args": _start_args(
                scope="container", token=None, place=False, on_stall="kill",
            ),
            "contract": 1,
        })
        assert started["ok"] is False
        assert started["error"]["code"] == "bad-policy"
        assert "exact target cgroup" in started["error"]["message"]
        assert server._sessions == {}


# ── goldens (§8.3's shapes, frozen for P5) ──────────────────────────────

def _check_golden(name: str, doc: Any) -> None:
    path = RG55_FIXTURES / name
    text = json.dumps(doc, indent=2) + "\n"
    if os.environ.get(REGEN_ENV) == "1":  # pragma: no cover - maintenance path
        path.write_text(text)
        return
    assert path.is_file(), f"missing golden {path} (regenerate with {REGEN_ENV}=1)"
    assert path.read_text() == text, f"{path} differs from the live document"


def test_the_placed_start_golden_is_the_live_document(tmp_path):
    root = _fake_cgroup_root(tmp_path, procs="101\n")
    proc_root = _fake_proc(tmp_path)
    proc_pid = proc_root / "101"
    proc_pid.mkdir()
    (proc_pid / "environ").write_bytes(
        f"RUN_GATE_PROFILE_SESSION={TOKEN}".encode("utf-8") + b"\0"
    )
    server = _server(tmp_path, root, proc_root=proc_root)
    resp = server._dispatch({"verb": "start", "args": _start_args(), "contract": 1})
    _check_golden("start-placed-v1.1.json", resp)


def test_every_refusal_shape_is_frozen(tmp_path):
    """One document per §8.8 `place-refused:*` code, so P5 codes its
    consumer branch against the BYTES rather than against this prose."""
    shapes: Dict[str, Any] = {}
    for name, overrides, root_kwargs in [
        ("no-token", {"token": None}, {}),
        ("no-gates-slice", {}, {"gates": False}),
        ("over-slice", {"memory_max": SLICE_MAX + 1}, {}),
    ]:
        case_dir = tmp_path / name
        case_dir.mkdir()
        root = _fake_cgroup_root(case_dir, **root_kwargs)
        server = _server(case_dir, root)
        resp = server._dispatch(
            {"verb": "start", "args": _start_args(**overrides), "contract": 1}
        )
        shapes[name] = resp["placement"]
        server._dispatch({"verb": "stop", "args": {"session": SESSION_ID}, "contract": 1})

    # `parent-not-gates-slice` and `write-failed` need a host condition no
    # `start` argument can produce, so their blocks come from the placement
    # object itself — the same block every document carries.
    root = _fake_cgroup_root(tmp_path / "symlink")
    (root / "dev.slice" / "dev-background.slice" / "victim").mkdir()
    (root / "dev.slice" / "dev-gates.slice" / LEAF_NAME).symlink_to(
        root / "dev.slice" / "dev-background.slice" / "victim"
    )
    plc = _placement(root)
    plc.apply([101])
    shapes["parent-not-gates-slice"] = plc.block()

    busy = _fake_cgroup_root(tmp_path / "busy")
    stuck = _placement(
        busy, rmdir=_raise_busy, sleep=lambda _seconds: None,
    )
    stuck.apply([101])
    stuck.release()
    shapes["write-failed"] = stuck.block()

    _check_golden("start-refused-v1.1.json", shapes)


def _raise_busy(path: str) -> None:
    raise OSError(16, "Device or resource busy", path)
