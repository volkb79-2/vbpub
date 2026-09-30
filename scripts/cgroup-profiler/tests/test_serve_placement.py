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
import math
import os
import posixpath
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
SCOPE_UNIT = f"rg-profile-{TOKEN}.scope"
SCOPE_UNIT_CGROUP = f"dev.slice/dev-gates.slice/{SCOPE_UNIT}"
PLACEMENT_CGROUP = f"/{SCOPE_UNIT_CGROUP}/{LEAF_NAME}"
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
    monkeypatch.setattr(placement, "_systemd_unit_cgroup_matches", lambda _unit, _path: True)


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


def _seed_token_process(proc_root: Path, pid: int, token: str = TOKEN) -> None:
    """Create the proc facts the real token resolver requires."""
    proc_pid = proc_root / str(pid)
    proc_pid.mkdir(parents=True, exist_ok=True)
    environ = proc_pid / "environ"
    if not environ.exists():
        environ.write_bytes(f"RUN_GATE_PROFILE_SESSION={token}".encode() + b"\0")
    fields = ["S", "1"] + ["0"] * 18
    fields[19] = str(pid * 97)
    (proc_pid / "stat").write_text(
        f"{pid} (fake lane process) " + " ".join(fields) + "\n"
    )
    children = proc_pid / "task" / str(pid) / "children"
    children.parent.mkdir(parents=True, exist_ok=True)
    children.write_text("")


def _fake_rmdir(path: str) -> None:
    """A cgroup `rmdir` on a REAL directory: the kernel's kernfs removes a
    cgroup whose interface files are still "in" it, so the fake tree has to
    do the same or nothing about `release` could be tested at all."""
    shutil.rmtree(path)


def _placement(
    root: Path, *, token: Optional[str] = TOKEN, request: Optional[placement.PlacementRequest] = None,
    writes: Optional[List[Any]] = None, rmdir=_fake_rmdir, **kw: Any,
) -> placement.LanePlacement:
    placement_cls = kw.pop("_placement_cls", placement.LanePlacement)
    origin_cgroup = kw.pop("origin_cgroup", "/" + SCOPE_CGROUP)
    on_write = kw.pop("on_write", None)
    scope_created = kw.pop("scope_created", None)
    attach_filter = kw.pop("attach_filter", None)
    attach_calls = kw.pop("attach_calls", None)
    kw.setdefault("slice_unit_verifier", lambda _unit, _cgroup: True)
    locations: Dict[int, str] = {}
    unit_cgroups: Dict[str, str] = {f"docker-{CONTAINER_ID}.scope": "/" + SCOPE_CGROUP}
    scope_paths: Dict[str, str] = {}

    def process_cgroup(pid: int) -> Optional[str]:
        return locations.setdefault(pid, "/" + SCOPE_CGROUP)

    def sync_membership() -> None:
        for procs_path in root.rglob("cgroup.procs"):
            relative = "/" + procs_path.parent.relative_to(root).as_posix()
            members = sorted(pid for pid, path in locations.items() if path == relative)
            procs_path.write_text("".join(f"{pid}\n" for pid in members))

    def move_fake(pid: int, destination: str) -> None:
        locations[pid] = destination
        sync_membership()

    def fake_scope_create(unit: str, _slice: str, _pids, _controllers):
        relative = f"dev.slice/dev-gates.slice/{unit}"
        scope_path = root / relative
        if scope_path.exists():
            return None
        files = dict(cgroup_files())
        gates_control = root / "dev.slice/dev-gates.slice/cgroup.subtree_control"
        if gates_control.exists():
            files["cgroup.subtree_control"] = gates_control.read_text()
        write_cgroup(root, relative, files)
        scope_paths[unit] = "/" + relative
        unit_cgroups[unit] = "/" + relative
        for pid in _pids:
            move_fake(pid, "/" + relative)
        if scope_created is not None:
            scope_created(unit)
        return "/" + relative

    def fake_scope_stop(unit: str) -> bool:
        scope_path = root / scope_paths.get(
            unit, f"dev.slice/dev-gates.slice/{unit}"
        ).lstrip("/")
        if scope_path.exists():
            shutil.rmtree(scope_path)
        return True

    def fake_mkdir(path: str) -> None:
        os.mkdir(path)
        relative = Path(path).relative_to(root).as_posix()
        write_cgroup(root, relative, cgroup_files())

    def fake_attach(unit: str, subcgroup: str, pid: int) -> bool:
        if attach_filter is not None and not attach_filter(unit, subcgroup, pid):
            return False
        if attach_calls is not None:
            attach_calls.append((unit, subcgroup, pid))
        unit_root = unit_cgroups.get(unit)
        if unit_root is None:
            return False
        destination = posixpath.normpath(
            unit_root if not subcgroup else f"{unit_root}/{subcgroup}"
        )
        move_fake(pid, destination)
        return True

    def fake_unit_verifier(unit: str, cgroup: str) -> bool:
        unit_cgroups[unit] = posixpath.normpath(cgroup)
        return True

    kw.setdefault("scope_create", fake_scope_create)
    kw.setdefault("scope_stop", fake_scope_stop)
    kw.setdefault("systemd_attach", fake_attach)
    kw.setdefault("pid_cgroup", process_cgroup)
    kw.setdefault("pid_exists", lambda pid: pid > 0)
    kw.setdefault("pid_start_time", lambda pid: str(pid * 97))
    kw.setdefault("host_pid", lambda: 999999)
    kw.setdefault("host_proc_view", lambda _proc: True)
    kw.setdefault("unit_cgroup_verifier", fake_unit_verifier)
    def fake_pids_in_cgroup(cgroup: str, _cgroup_root: str, _proc_root: str) -> List[int]:
        expected = posixpath.normpath(cgroup)
        path = root / expected.lstrip("/") / "cgroup.procs"
        try:
            entries = [int(line) for line in path.read_text().splitlines() if line.strip().isdigit()]
        except OSError:
            return []
        for pid in entries:
            if pid > 0:
                locations[pid] = expected
        return sorted(set(entries) | {
            pid for pid, current in locations.items() if current == expected
        })

    kw.setdefault("mkdir", fake_mkdir)
    kw.setdefault("pid_parent", lambda pid: {102: 101, 103: 101, 999: 101}.get(pid))
    kw.setdefault("pids_in_cgroup", fake_pids_in_cgroup)
    kw.setdefault("state_write", lambda _state: None)
    return placement_cls(
        cgroup_root=str(root), gates_cgroup=GATES_CGROUP, token=token,
        origin_cgroup=origin_cgroup,
        request=request if request is not None else placement.PlacementRequest(
            memory_high=MEMORY_HIGH, memory_max=MEMORY_MAX, cpu_weight=100,
        ),
        on_write=(
            lambda rel, value: (
                writes.append((rel, value)) if writes is not None else None,
                on_write(rel, value) if on_write is not None else None,
            )
        ),
        rmdir=rmdir, **kw,
    )


def _leaf(root: Path, token: str = TOKEN) -> Path:
    return (
        root / "dev.slice" / "dev-gates.slice" / f"rg-profile-{token}.scope"
        / f"rg-{token}"
    )


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
    origin_procs = root / SCOPE_CGROUP / "cgroup.procs"
    if origin_procs.is_file():
        for item in origin_procs.read_text().splitlines():
            if item.strip().isdigit() and int(item) > 0:
                _seed_token_process(Path(proc_root), int(item))

    def test_placement_factory(**options: Any) -> placement.LanePlacement:
        return _placement(
            root,
            token=options["token"],
            request=options["request"],
            origin_cgroup=options["origin_cgroup"],
            on_write=options.get("on_write"),
            log=options.get("log"),
            proc_root=options.get("proc_root"),
            state_write=options.get("state_write"),
        )

    server = serve.SessionServer(
        sessions_dir=str(tmp_path / "sessions"), socket_path=str(tmp_path / "ctl.sock"),
        cgroup_root=str(root), proc_root=str(proc_root), clock=lambda: EPOCH_START,
        session_id_fn=lambda: SESSION_ID, accept_timeout=0.05,
        slice_unit_verifier=lambda _unit, _cgroup: True,
        placement_factory=kw.pop("placement_factory", test_placement_factory),
        **kw,
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
        write_cgroup(root, SCOPE_UNIT_CGROUP, cgroup_files())
        return placement.CgroupWriteGuard(
            cgroup_root=str(root), gates_cgroup=GATES_CGROUP,
            origin_cgroup="/" + SCOPE_CGROUP, leaf_name=LEAF_NAME,
            scope_cgroup="/" + SCOPE_UNIT_CGROUP,
        )

    @pytest.mark.parametrize("name", list(placement.LEAF_FILES))
    def test_every_leaf_file_is_writable(self, tmp_path, name):
        root = _fake_cgroup_root(tmp_path)
        self._guard(root).check_write(str(_leaf(root) / name), "1")

    def test_scope_root_control_is_writable_with_plus_values(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)
        guard = self._guard(root)
        control = str(root / SCOPE_UNIT_CGROUP / "cgroup.subtree_control")
        guard.check_write(control, placement.SUBTREE_CONTROL_VALUE)

    @pytest.mark.parametrize("value, fragment", [
        ("-memory", "non-'+'"),
        ("+memory -cpu", "non-'+'"),
        ("+io", "delegate controller"),
        ("", "empty"),
    ])
    def test_subtree_control_refuses_anything_but_plus_the_three(self, tmp_path, value, fragment):
        """The delegated scope accepts only the exact required controllers."""
        root = _fake_cgroup_root(tmp_path)
        control = str(root / SCOPE_UNIT_CGROUP / "cgroup.subtree_control")
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
        f"{SCOPE_UNIT_CGROUP}/{LEAF_NAME}/memory.min",   # a leaf file not on the list
        f"{SCOPE_UNIT_CGROUP}/{LEAF_NAME}/sub/memory.high",  # below the leaf
        f"{SCOPE_UNIT_CGROUP}/not-a-lane/memory.high",    # not an rg-* leaf
    ])
    def test_a_write_planted_anywhere_else_is_refused(self, tmp_path, relative):
        """The planted-write test D-25 asks for: the guard is consulted
        BEFORE the `open()`, so a refusal is proved by the exception, not by
        inspecting the file afterwards."""
        root = _fake_cgroup_root(tmp_path)
        with pytest.raises(placement.HostWriteError) as exc:
            self._guard(root).check_write(str(root / relative), "1")
        assert "D-25 cgroup whitelist" in str(exc.value)

    def test_a_symlinked_leaf_cannot_smuggle_a_write_out_of_the_delegated_scope(self, tmp_path):
        """Every comparison is on `realpath`, so a symlink planted under the
        gates slice resolves to where it really points and is refused."""
        root = _fake_cgroup_root(tmp_path)
        write_cgroup(root, SCOPE_UNIT_CGROUP, cgroup_files())
        elsewhere = root / "dev.slice" / "dev-background.slice" / "victim"
        elsewhere.mkdir()
        (_leaf(root)).symlink_to(elsewhere)
        with pytest.raises(placement.HostWriteError):
            self._guard(root).check_write(str(_leaf(root) / "memory.high"), "1")

    def test_a_symlinked_leaf_cannot_smuggle_a_kill_to_another_rg_leaf(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)
        write_cgroup(root, SCOPE_UNIT_CGROUP, cgroup_files())
        victim_scope = root / "dev.slice" / "dev-gates.slice" / "rg-profile-victim.scope"
        victim_scope.mkdir()
        victim = victim_scope / "rg-victim-token"
        victim.mkdir()
        _leaf(root).symlink_to(victim)
        guard = self._guard(root)
        with pytest.raises(placement.HostWriteError):
            guard.check_write(str(_leaf(root) / "cgroup.kill"), "1")
        assert not (victim / "cgroup.kill").exists()

    def test_another_session_leaf_is_outside_this_sessions_whitelist(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)
        other_scope = root / "dev.slice" / "dev-gates.slice" / "rg-profile-other.scope"
        other_scope.mkdir()
        other = other_scope / "rg-other-token"
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
            scope_cgroup="/" + SCOPE_UNIT_CGROUP,
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
        leaf.parent.mkdir(parents=True)
        leaf.mkdir()
        guard = placement.CgroupWriteGuard(
            cgroup_root=str(root), gates_cgroup=GATES_CGROUP,
            origin_cgroup=None, leaf_name=LEAF_NAME,
            scope_cgroup="/" + SCOPE_UNIT_CGROUP,
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


class _UnreadableReadbackPlacement(placement.LanePlacement):
    """Simulate a cap that accepts a write but cannot be read back."""

    def _write(self, abs_target: str, value: str) -> None:
        super()._write(abs_target, value)
        if os.path.basename(abs_target) == "memory.high":
            os.unlink(abs_target)


class TestApply:
    def test_the_leaf_is_created_capped_and_populated(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)
        writes: List[Any] = []
        plc = _placement(root, writes=writes)
        plc.apply([101, 102])

        assert plc.error is None
        assert plc.leaf_cgroup == PLACEMENT_CGROUP
        assert _leaf(root).is_dir()
        assert (_leaf(root) / "memory.high").read_text() == str(MEMORY_HIGH)
        assert (_leaf(root) / "cgroup.procs").read_text() == "101\n102\n"
        assert plc.block() == {
            "requested": True, "leaf": PLACEMENT_CGROUP,
            "applied": {"memory.high": MEMORY_HIGH, "memory.max": MEMORY_MAX, "cpu.weight": 100},
            "pids_moved": 2, "error": None,
        }
        # D-25: every cgroup write is an events row — the sink sees the
        # leaf creation, process moves, controller delegation, and three caps.
        assert [rel for rel, _ in writes] == [
            f"{SCOPE_UNIT_CGROUP}/{LEAF_NAME}",
            f"{SCOPE_UNIT_CGROUP}/{LEAF_NAME}/cgroup.procs",
            f"{SCOPE_UNIT_CGROUP}/{LEAF_NAME}/cgroup.procs",
            f"{SCOPE_UNIT_CGROUP}/cgroup.subtree_control",
            f"{SCOPE_UNIT_CGROUP}/{LEAF_NAME}/memory.high",
            f"{SCOPE_UNIT_CGROUP}/{LEAF_NAME}/memory.max",
            f"{SCOPE_UNIT_CGROUP}/{LEAF_NAME}/cpu.weight",
        ]

    def test_controllers_are_enabled_only_when_the_scope_lacks_them(self, tmp_path):
        """The gates-slice state is not written; enable within our scope only."""
        root = _fake_cgroup_root(tmp_path, subtree_control="memory cpu pids io")
        writes: List[Any] = []
        _placement(root, writes=writes).apply([101])
        assert not any(rel.endswith("cgroup.subtree_control") for rel, _ in writes)

        partial = _fake_cgroup_root(tmp_path / "second", subtree_control="memory pids")
        writes = []
        _placement(partial, writes=writes).apply([101])
        assert (partial / SCOPE_UNIT_CGROUP / "cgroup.subtree_control").read_text() == (
            placement.SUBTREE_CONTROL_VALUE
        )
        assert (
            f"{SCOPE_UNIT_CGROUP}/cgroup.subtree_control", "+memory +cpu +pids",
        ) in writes

    def test_applied_is_read_back_from_the_leaf_not_echoed(self, tmp_path):
        """The S13.3.2 discipline, made falsifiable: these writes land
        rounded down, so an implementation that echoed the request would
        report a number the file does not hold."""
        root = _fake_cgroup_root(tmp_path)
        plc = _placement(
            root, _placement_cls=_RoundingPlacement,
            request=placement.PlacementRequest(memory_high=MEMORY_HIGH + 1, cpu_weight=100),
        )
        plc.apply([101])
        assert plc.applied["memory.high"] == MEMORY_HIGH
        assert plc.applied["memory.high"] != MEMORY_HIGH + 1
        assert int((_leaf(root) / "memory.high").read_text()) == plc.applied["memory.high"]
        assert "memory.max" not in plc.applied  # never requested, never claimed

    def test_missing_cap_readback_refuses_placement_and_cleans_up(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)
        plc = _placement(root, _placement_cls=_UnreadableReadbackPlacement)
        plc.apply([101])
        assert plc.error == placement.write_failed(
            f"{SCOPE_UNIT_CGROUP}/{LEAF_NAME}/memory.high"
        )
        assert not plc.successfully_placed
        assert plc.released
        assert not _leaf(root).exists()

    def test_apply_releases_when_final_leaf_membership_proof_fails(
        self, tmp_path, monkeypatch,
    ):
        root = _fake_cgroup_root(tmp_path)
        plc = _placement(root)

        def refuse_membership():
            plc.error = placement.REFUSED_IDENTITY_UNAVAILABLE
            return False

        monkeypatch.setattr(plc, "_verify_leaf_membership", refuse_membership)
        plc.apply([101])

        assert plc.error == placement.REFUSED_IDENTITY_UNAVAILABLE
        assert not plc.successfully_placed
        assert plc.released
        assert not _leaf(root).exists()

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
        plc.apply([101])
        assert plc.error == placement.REFUSED_OVER_SLICE
        assert not _leaf(root).exists()

    def test_an_unlimited_slice_is_not_an_admission_capacity_object(self, tmp_path):
        """A directory with no finite memory ceiling is not trusted as the
        authored gates capacity object."""
        root = _fake_cgroup_root(tmp_path, slice_memory_max=None)
        plc = _placement(root, request=placement.PlacementRequest(memory_max=SLICE_MAX * 4))
        plc.apply([101])
        assert plc.error == placement.REFUSED_NO_GATES_SLICE
        assert plc.leaf_cgroup is None

    def test_an_unbounded_cpu_slice_is_refused(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)
        gates = root / "dev.slice" / "dev-gates.slice"
        (gates / "cpu.max").write_text("max 100000")
        plc = _placement(root)
        plc.apply([101])
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

        plc.apply([101])

        assert plc.error == placement.REFUSED_NO_GATES_SLICE
        assert plc.leaf_cgroup is None

    def test_directory_without_a_verified_loaded_unit_is_refused(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)
        plc = _placement(root, slice_unit_verifier=lambda _unit, _cgroup: False)
        plc.apply([101])
        assert plc.error == placement.REFUSED_NO_GATES_SLICE
        assert plc.leaf_cgroup is None

    def test_a_symlinked_leaf_is_not_adopted_or_written(self, tmp_path):
        """The path is checked after systemd creates the exact scope."""
        root = _fake_cgroup_root(tmp_path)
        elsewhere = root / "dev.slice" / "dev-background.slice" / "victim"
        elsewhere.mkdir()

        def plant_symlink(_unit: str) -> None:
            _leaf(root).symlink_to(elsewhere)

        plc = _placement(root, scope_created=plant_symlink)
        plc.apply([101])
        assert plc.error == placement.REFUSED_PARENT_NOT_GATES_SLICE
        assert plc.leaf_cgroup is None
        assert not (elsewhere / "memory.high").exists()
        assert (root / SCOPE_CGROUP / "cgroup.procs").read_text() == "101\n"

    def test_an_existing_leaf_is_refused_without_changing_its_caps(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)
        write_cgroup(root, SCOPE_UNIT_CGROUP, cgroup_files())
        write_cgroup(root, f"{SCOPE_UNIT_CGROUP}/{LEAF_NAME}", cgroup_files())
        (_leaf(root) / "memory.high").write_text("123")
        plc = _placement(root)
        plc.apply([101])
        assert plc.error == "place-refused:scope-unavailable"
        assert plc.leaf_cgroup is None
        assert (_leaf(root) / "memory.high").read_text() == "123"

    def test_a_leaf_created_between_check_and_mkdir_is_refused(self, tmp_path, monkeypatch):
        root = _fake_cgroup_root(tmp_path)
        original_mkdir = placement.os.mkdir
        leaf = str(_leaf(root))

        def competing_mkdir(path, *args, **kwargs):
            if path == leaf:
                original_mkdir(path)
                (_leaf(root) / "memory.high").write_text("456")
                raise FileExistsError(17, "leaf appeared", path)
            return original_mkdir(path, *args, **kwargs)

        monkeypatch.setattr(placement.os, "mkdir", competing_mkdir)
        plc = _placement(root)
        plc.apply([101])
        assert plc.error == placement.write_failed(f"{SCOPE_UNIT_CGROUP}/{LEAF_NAME}")
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

        plc = _placement(
            root, _placement_cls=_Unwritable,
            request=placement.PlacementRequest(memory_high=MEMORY_HIGH, memory_max=MEMORY_MAX),
        )
        plc.apply([101])
        assert plc.error == (
            f"place-refused:write-failed:{SCOPE_UNIT_CGROUP}/{LEAF_NAME}/memory.max"
        )
        assert plc.block()["leaf"] is None
        assert plc.applied == {"memory.high": MEMORY_HIGH}
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

        plc = _placement(
            root, _placement_cls=_Unwritable, rmdir=_raise_busy,
            request=placement.PlacementRequest(cpu_weight=100),
        )
        plc.apply([101])
        assert plc.error == placement.write_failed(f"{SCOPE_UNIT_CGROUP}/{LEAF_NAME}")
        assert plc.leaf_cgroup == PLACEMENT_CGROUP
        assert _leaf(root).is_dir()  # the host really does still carry it

    def test_direct_slice_leaf_name_is_not_a_placement_collision(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)
        # The supported geometry is scope/leaf. A stale direct rg-* entry is
        # unrelated to the uniquely named transient scope.
        (root / "dev.slice" / "dev-gates.slice" / LEAF_NAME).write_text("")
        plc = _placement(root)
        plc.apply([101])
        assert plc.error is None
        assert plc.placed


class TestLeafMembershipVerification:
    def _ready_lane(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)
        lane = _placement(root)
        lane.apply([101])
        assert lane.placed and lane.error is None
        return root, lane

    def test_missing_leaf_identity_is_refused(self, tmp_path):
        lane = _placement(_fake_cgroup_root(tmp_path))
        assert lane._verify_leaf_membership() is False
        assert lane.error == placement.REFUSED_IDENTITY_UNAVAILABLE

    def test_unknown_leaf_membership_is_refused(self, tmp_path, monkeypatch):
        _root, lane = self._ready_lane(tmp_path)
        monkeypatch.setattr(lane, "_owned_cgroup_pids", lambda _cgroup: None)
        assert lane._verify_leaf_membership() is False
        assert lane.error == placement.write_failed(
            f"{SCOPE_UNIT_CGROUP}/{LEAF_NAME}/cgroup.procs"
        )

    def test_unrecorded_leaf_member_requires_a_capturable_identity(
        self, tmp_path, monkeypatch,
    ):
        _root, lane = self._ready_lane(tmp_path)
        monkeypatch.setattr(lane, "_owned_cgroup_pids", lambda _cgroup: [101, 102])
        monkeypatch.setattr(lane, "_capture_pid", lambda _pid: None)
        assert lane._verify_leaf_membership() is False
        assert lane.error == placement.REFUSED_IDENTITY_UNAVAILABLE

    @pytest.mark.parametrize(
        ("failure", "same_process", "pid_exists", "pid_cgroup", "expected_state"),
        [
            ("member-exited", False, False, None, "exited"),
            ("member-reused", False, True, None, "leaf"),
            ("member-moved", True, True, "/dev.slice/unrelated.scope", "leaf"),
        ],
    )
    def test_actual_leaf_members_are_identity_and_location_checked(
        self, tmp_path, monkeypatch, failure, same_process, pid_exists,
        pid_cgroup, expected_state,
    ):
        _root, lane = self._ready_lane(tmp_path)
        monkeypatch.setattr(lane, "_owned_cgroup_pids", lambda _cgroup: [101])
        monkeypatch.setattr(lane, "_same_process", lambda *_args: same_process)
        monkeypatch.setattr(lane, "_pid_exists", lambda _pid: pid_exists)
        if pid_cgroup is not None:
            monkeypatch.setattr(lane, "_pid_cgroup", lambda _pid: pid_cgroup)

        assert lane._verify_leaf_membership() is False, failure
        assert lane.error == placement.REFUSED_IDENTITY_UNAVAILABLE
        assert lane.pid_records[101]["state"] == expected_state

    @pytest.mark.parametrize(
        ("failure", "same_process", "pid_exists", "pid_cgroup", "expected"),
        [
            ("record-exited", False, False, None, True),
            ("record-reused", False, True, None, False),
            ("record-location-unknown", True, True, None, False),
            ("record-outside-leaf", True, True, "/dev.slice/origin.scope", False),
        ],
    )
    def test_recorded_members_are_rechecked_after_leaf_enumeration(
        self, tmp_path, monkeypatch, failure, same_process, pid_exists,
        pid_cgroup, expected,
    ):
        _root, lane = self._ready_lane(tmp_path)
        monkeypatch.setattr(lane, "_owned_cgroup_pids", lambda _cgroup: [])
        monkeypatch.setattr(lane, "_same_process", lambda *_args: same_process)
        monkeypatch.setattr(lane, "_pid_exists", lambda _pid: pid_exists)
        if pid_cgroup is not None or failure == "record-location-unknown":
            monkeypatch.setattr(lane, "_pid_cgroup", lambda _pid: pid_cgroup)

        assert lane._verify_leaf_membership() is expected, failure
        if expected:
            assert lane.error is None
            assert lane.pid_records[101]["state"] == "exited"
        else:
            assert lane.error == placement.REFUSED_IDENTITY_UNAVAILABLE

    def test_membership_snapshot_detects_an_exit_between_the_two_reads(
        self, tmp_path, monkeypatch,
    ):
        _root, lane = self._ready_lane(tmp_path)
        monkeypatch.setattr(lane, "_owned_cgroup_pids", lambda _cgroup: [101])
        checks = iter([True, False])
        monkeypatch.setattr(lane, "_same_process", lambda *_args: next(checks))
        monkeypatch.setattr(lane, "_pid_exists", lambda _pid: False)
        monkeypatch.setattr(lane, "_pid_cgroup", lambda _pid: lane.leaf_cgroup)

        assert lane._verify_leaf_membership() is False
        assert lane.error == placement.REFUSED_IDENTITY_UNAVAILABLE
        assert lane.pid_records[101]["state"] == "exited"

    def test_membership_journal_failure_is_not_reported_as_verified(
        self, tmp_path, monkeypatch,
    ):
        _root, lane = self._ready_lane(tmp_path)
        lane.pid_records.clear()
        monkeypatch.setattr(lane, "_owned_cgroup_pids", lambda _cgroup: [])
        monkeypatch.setattr(
            lane, "_state_write",
            lambda _journal: (_ for _ in ()).throw(OSError("disk full")),
        )

        assert lane._verify_leaf_membership() is False
        assert lane.error == placement.REFUSED_STATE_UNAVAILABLE


@pytest.mark.parametrize(("subcgroup", "expected"), [
    (LEAF_NAME, "/" + LEAF_NAME),
    ("", "/"),
    ("nested/worker", "/nested/worker"),
])
def test_systemd_attach_uses_the_narrow_manager_method(monkeypatch, subcgroup, expected):
    calls: List[Any] = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setenv("CGPROFILE_BUSCTL", "busctl-test")
    assert placement._systemd_attach_process(
        "dev-gates.slice", subcgroup, 4242, run=fake_run
    ) is True
    assert calls == [(
        [
            "busctl-test", "--system", "call", "org.freedesktop.systemd1",
            "/org/freedesktop/systemd1", "org.freedesktop.systemd1.Manager",
            "AttachProcessesToUnit", "ssau", "dev-gates.slice", expected,
            "1", "4242",
        ],
        {"check": False, "capture_output": True, "text": True, "timeout": 5.0},
    )]


@pytest.mark.parametrize("subcgroup", [
    "/absolute", "../escape", "nested//worker", "nested/./worker",
    "nested/../worker", "worker\nother",
])
def test_systemd_attach_refuses_unsafe_subcgroup_before_call(subcgroup):
    assert placement._systemd_attach_process(
        "dev-gates.slice", subcgroup, 4242,
        run=lambda *_args, **_kwargs: pytest.fail("unsafe path reached host bus"),
    ) is False


def test_systemd_attach_returns_false_when_busctl_cannot_start():
    def unavailable(*_args, **_kwargs):
        raise OSError(2, "busctl not found")

    assert placement._systemd_attach_process(
        "dev-gates.slice", LEAF_NAME, 4242, run=unavailable
    ) is False


def test_systemd_scope_creation_supplies_initial_pids_and_delegation(monkeypatch):
    calls: List[Any] = []
    monkeypatch.setenv("CGPROFILE_BUSCTL", "busctl-test")
    monkeypatch.setattr(placement, "_systemd_unit_path", lambda _unit: "/unit/path")
    monkeypatch.setattr(
        placement, "_systemd_property",
        lambda _path, interface, name: {
            ("org.freedesktop.systemd1.Unit", "LoadState"): "loaded",
            ("org.freedesktop.systemd1.Scope", "ControlGroup"): (
                "/dev.slice/dev-gates.slice/" + SCOPE_UNIT
            ),
            ("org.freedesktop.systemd1.Scope", "Slice"): "dev-gates.slice",
        }.get((interface, name)),
    )
    monkeypatch.setattr(placement, "_systemd_bool_property", lambda *_args: True)
    monkeypatch.setattr(
        placement, "_systemd_string_array_property",
        lambda *_args: list(placement.REQUIRED_CONTROLLERS),
    )
    monkeypatch.setattr(placement.time, "sleep", lambda _seconds: None)

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, "o /unit/path\n", "")

    actual = placement._systemd_create_scope(
        SCOPE_UNIT, "dev-gates.slice", [101, 202],
        placement.REQUIRED_CONTROLLERS, run=fake_run,
    )

    assert actual == "/dev.slice/dev-gates.slice/" + SCOPE_UNIT
    assert calls == [(
        [
            "busctl-test", "--system", "call", "org.freedesktop.systemd1",
            "/org/freedesktop/systemd1", "org.freedesktop.systemd1.Manager",
            "StartTransientUnit", "ssa(sv)a(sa(sv))", SCOPE_UNIT, "fail", "5",
            "Description", "s", "cgprofile lane placement",
            "Slice", "s", "dev-gates.slice",
            "PIDs", "au", "2", "101", "202",
            "Delegate", "b", "true",
            "DelegateControllers", "as", "3", "memory", "cpu", "pids", "0",
        ],
        {"check": False, "capture_output": True, "text": True, "timeout": 10.0},
    )]


@pytest.mark.parametrize("failure", ["load", "path", "slice", "delegate", "controllers"])
def test_systemd_scope_creation_refuses_unverified_properties(monkeypatch, failure):
    monkeypatch.setenv("CGPROFILE_BUSCTL", "busctl-test")
    monkeypatch.setattr(placement, "_systemd_unit_path", lambda _unit: "/unit/path")
    values = {
        ("org.freedesktop.systemd1.Unit", "LoadState"): "loaded",
        ("org.freedesktop.systemd1.Scope", "ControlGroup"): (
            "/dev.slice/dev-gates.slice/" + SCOPE_UNIT
        ),
        ("org.freedesktop.systemd1.Scope", "Slice"): "dev-gates.slice",
    }
    if failure == "load":
        values[("org.freedesktop.systemd1.Unit", "LoadState")] = "not-found"
    elif failure == "path":
        values[("org.freedesktop.systemd1.Scope", "ControlGroup")] = None
    elif failure == "slice":
        values[("org.freedesktop.systemd1.Scope", "Slice")] = "dev-background.slice"
    monkeypatch.setattr(
        placement, "_systemd_property",
        lambda _path, interface, name: values.get((interface, name)),
    )
    monkeypatch.setattr(
        placement, "_systemd_bool_property",
        lambda *_args: failure != "delegate",
    )
    monkeypatch.setattr(
        placement, "_systemd_string_array_property",
        lambda *_args: [] if failure == "controllers" else list(placement.REQUIRED_CONTROLLERS),
    )
    monkeypatch.setattr(placement.time, "sleep", lambda _seconds: None)
    result = placement._systemd_create_scope(
        SCOPE_UNIT, "dev-gates.slice", [101], placement.REQUIRED_CONTROLLERS,
        run=lambda argv, **_kwargs: subprocess.CompletedProcess(argv, 0, "", ""),
    )
    assert result is None


@pytest.mark.parametrize("pids", [[], [0], [-1], [True]])
def test_systemd_scope_creation_refuses_invalid_initial_pid_sets(pids):
    assert placement._systemd_create_scope(
        SCOPE_UNIT, "dev-gates.slice", pids, placement.REQUIRED_CONTROLLERS,
        run=lambda *_args, **_kwargs: pytest.fail("invalid pid set reached systemd"),
    ) is None


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


class TestDelegatedScopeMigration:
    def test_initial_pids_are_journaled_then_placed_below_the_verified_scope(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)
        writes: List[Any] = []
        journal_updates: List[Dict[str, Any]] = []
        plc = _placement(
            root, writes=writes,
            state_write=lambda value: journal_updates.append(json.loads(json.dumps(value))),
        )

        plc.apply([101, 102])

        scope = root / SCOPE_UNIT_CGROUP
        assert plc.error is None and plc.placed
        assert scope.is_dir() and _leaf(root).is_dir()
        assert (scope / "cgroup.procs").read_text() == ""
        assert (_leaf(root) / "cgroup.procs").read_text() == "101\n102\n"
        assert (root / SCOPE_CGROUP / "cgroup.procs").read_text() == ""
        prepared = next(item for item in journal_updates if item["state"] == "creating-scope")
        assert set(prepared["pids"]) == {"101", "102"}
        assert all(
            item["origin_cgroup"] == "/" + SCOPE_CGROUP
            and item["origin_unit"] == f"docker-{CONTAINER_ID}.scope"
            and item["state"] == "prepared"
            for item in prepared["pids"].values()
        )
        assert any(item["state"] == "moving-to-leaf" for item in journal_updates)
        assert journal_updates[-1]["state"] == "placed"
        assert [path for path, _ in writes].count(
            f"{SCOPE_UNIT_CGROUP}/cgroup.subtree_control"
        ) == 1

    def test_later_descendants_are_moved_once_and_inherit_verified_ancestry(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)
        writes: List[Any] = []
        plc = _placement(root, writes=writes)
        plc.apply([101])
        before = len(writes)

        assert plc.migrate([101, 102, 103]) == 2
        assert plc.migrate([101, 102, 103]) == 0
        assert (_leaf(root) / "cgroup.procs").read_text() == "101\n102\n103\n"
        assert set(plc.pid_records) == {101, 102, 103}
        assert all(
            plc.pid_records[pid]["origin_cgroup"] == "/" + SCOPE_CGROUP
            for pid in (102, 103)
        )
        assert [value for _, value in writes[before:]] == ["102", "103"]

    def test_vanished_descendant_is_skipped_without_a_systemd_move(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)
        calls: List[Any] = []
        plc = _placement(root, attach_calls=calls)
        plc.apply([101])
        plc._pid_exists = lambda pid: pid != 777

        assert plc.migrate([777]) == 0
        assert plc.error is None
        assert calls == [(SCOPE_UNIT, LEAF_NAME, 101)]

    def test_unplaced_session_does_not_migrate_discovered_pids(self, tmp_path):
        plc = _placement(_fake_cgroup_root(tmp_path, gates=False))
        plc.apply([101])

        assert plc.migrate([101, 102]) == 0
        assert plc.error == placement.REFUSED_NO_GATES_SLICE
        assert plc.block()["pids_moved"] == 0

    def test_release_restores_each_survivor_through_its_recorded_systemd_unit(
        self, tmp_path,
    ):
        root = _fake_cgroup_root(tmp_path)
        calls: List[Any] = []
        plc = _placement(root, attach_calls=calls)
        plc.apply([101])

        plc.release()

        assert plc.error is None and plc.released
        assert calls == [
            (SCOPE_UNIT, LEAF_NAME, 101),
            (f"docker-{CONTAINER_ID}.scope", "", 101),
        ]
        assert (root / SCOPE_CGROUP / "cgroup.procs").read_text() == "101\n"

    def test_empty_target_and_missing_recovery_writer_refuse_before_scope_creation(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)
        empty = _placement(root)
        empty.apply([])
        assert empty.error == "place-refused:no-target-pids"
        assert not (root / SCOPE_UNIT_CGROUP).exists()

        second_root = _fake_cgroup_root(tmp_path / "second")
        no_writer = _placement(second_root, state_write=None)
        no_writer.apply([101])
        assert no_writer.error == placement.REFUSED_STATE_UNAVAILABLE
        assert not (second_root / SCOPE_UNIT_CGROUP).exists()

    def test_missing_host_proc_view_refuses_before_scope_creation(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)
        plc = _placement(root, host_proc_view=lambda _proc: False)
        plc.apply([101])
        assert plc.error == placement.REFUSED_NO_HOST_PROC
        assert not (root / SCOPE_UNIT_CGROUP).exists()

    def test_failed_manager_attach_restores_scope_members_and_removes_the_leaf(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)
        plc = _placement(
            root,
            attach_filter=lambda unit, _subcgroup, _pid: unit != SCOPE_UNIT,
        )

        plc.apply([101])

        assert plc.error == placement.write_failed(
            f"{SCOPE_UNIT_CGROUP}/{LEAF_NAME}/cgroup.procs"
        )
        assert not _leaf(root).exists()
        assert not (root / SCOPE_UNIT_CGROUP).exists()
        assert (root / SCOPE_CGROUP / "cgroup.procs").read_text() == "101\n"
        assert plc.block()["leaf"] is None

    def test_failed_restore_keeps_owned_leaf_and_journal_for_recovery(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)
        journal_updates: List[Dict[str, Any]] = []
        plc = _placement(
            root,
            attach_filter=lambda unit, _subcgroup, _pid: unit == SCOPE_UNIT,
            state_write=lambda value: journal_updates.append(json.loads(json.dumps(value))),
        )
        plc.apply([101])
        assert plc.error is None

        plc.release()

        assert plc.error == placement.write_failed(
            f"{SCOPE_UNIT_CGROUP}/{LEAF_NAME}/cgroup.procs"
        )
        assert plc.placed and _leaf(root).exists()
        assert journal_updates[-1]["state"] == "recovery-required"
        assert journal_updates[-1]["leaf_created"] is True

    def test_pid_reuse_blocks_restore_and_preserves_the_leaf(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)
        plc = _placement(root)
        plc.apply([101])
        plc.pid_records[101]["start_time_ticks"] = "reused"

        plc.release()

        assert plc.error == placement.REFUSED_IDENTITY_UNAVAILABLE
        assert _leaf(root).exists() and plc.placed
        assert plc._journal["state"] == "recovery-required"

    def test_unjournaled_process_in_leaf_is_not_guessed_at_or_moved(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)
        plc = _placement(root)
        plc.apply([101])
        (_leaf(root) / "cgroup.procs").write_text("101\n777\n")

        plc.release()

        assert plc.error == placement.REFUSED_IDENTITY_UNAVAILABLE
        assert _leaf(root).exists() and plc.placed
        assert plc._journal["pids"].keys() == {"101"}

    def test_child_created_after_last_discovery_is_restored_via_journaled_parent(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)
        plc = _placement(root)
        plc.apply([101])
        (_leaf(root) / "cgroup.procs").write_text("101\n999\n")

        plc.release()

        assert plc.error is None and plc.released
        assert not _leaf(root).exists()
        assert not (root / SCOPE_UNIT_CGROUP).exists()
        assert (root / SCOPE_CGROUP / "cgroup.procs").read_text() == "101\n999\n"


class TestPlacementTransactionRefusals:
    def test_invalid_token_never_queries_or_mutates_host_state(self, tmp_path):
        plc = _placement(
            _fake_cgroup_root(tmp_path), token="../unsafe",
            scope_create=lambda *_args: pytest.fail("invalid token reached systemd"),
        )
        plc.apply([101])
        assert plc.error == placement.REFUSED_NO_TOKEN

    def test_capture_that_finds_no_live_target_is_a_nonfatal_refusal(self, tmp_path):
        plc = _placement(
            _fake_cgroup_root(tmp_path), pid_exists=lambda _pid: False,
        )
        plc.apply([101])
        assert plc.error == "place-refused:no-target-pids"
        assert plc.scope_cgroup is None

    def test_prejournaled_pid_is_not_captured_twice(self, tmp_path):
        plc = _placement(_fake_cgroup_root(tmp_path))
        captured = plc._capture_pid(101)
        assert captured is not None
        plc.pid_records[101] = captured
        plc.apply([101])
        assert plc.error is None and plc.placed
        assert plc.pid_records[101]["state"] == "leaf"

    @pytest.mark.parametrize(
        "phase",
        ["creating-scope", "scope-created", "scope-verified", "creating-leaf", "leaf-created"],
    )
    def test_journal_write_failure_stops_at_each_ownership_boundary(self, tmp_path, phase):
        root = _fake_cgroup_root(tmp_path)
        calls: List[str] = []

        def write_state(value):
            calls.append(value["state"])
            if value["state"] == phase:
                raise OSError("durable state unavailable")

        plc = _placement(root, state_write=write_state)
        plc.apply([101])

        assert plc.error == placement.REFUSED_STATE_UNAVAILABLE
        assert phase in calls
        if phase in {"scope-created", "scope-verified", "creating-leaf"}:
            # systemd already owns the initial process. Keep its exact scope
            # and incomplete journal for restart recovery; do not guess cleanup.
            assert (root / SCOPE_UNIT_CGROUP).is_dir()
        else:
            assert not (root / SCOPE_UNIT_CGROUP).exists()
        if phase in {"creating-leaf", "leaf-created"}:
            assert not _leaf(root).exists()
        if phase == "leaf-created":
            assert not (root / SCOPE_UNIT_CGROUP).exists()

    @pytest.mark.parametrize("failure", ["exception", "timeout-result"])
    def test_uncertain_scope_creation_is_journaled_and_reported(self, tmp_path, failure):
        root = _fake_cgroup_root(tmp_path)
        logged: List[str] = []

        def create(*_args):
            if failure == "exception":
                raise RuntimeError("manager connection lost")
            return None

        plc = _placement(root, scope_create=create, log=logged.append)
        plc.apply([101])
        assert plc.error == "place-refused:scope-unavailable"
        assert plc._journal["state"] == "scope-create-uncertain"
        if failure == "exception":
            assert any("scope creation failed" in item for item in logged)

    def test_scope_creation_exception_is_safe_without_an_optional_logger(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)

        def create(*_args):
            raise RuntimeError("manager connection lost")

        plc = _placement(root, scope_create=create)
        plc.apply([101])
        assert plc.error == "place-refused:scope-unavailable"

    def test_scope_returned_outside_gates_slice_is_preserved_for_recovery(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)
        plc = _placement(
            root,
            scope_create=lambda unit, _slice, _pids, _controllers: (
                f"/dev.slice/dev-background.slice/{unit}"
            ),
        )
        plc.apply([101])
        assert plc.error == placement.REFUSED_PARENT_NOT_GATES_SLICE
        assert plc._journal["state"] == "recovery-required"

    def test_scope_unit_verification_failure_does_not_make_a_leaf(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)
        plc = _placement(
            root,
            unit_cgroup_verifier=lambda unit, _path: unit.startswith("docker-"),
        )
        plc.apply([101])
        assert plc.error == placement.REFUSED_PARENT_NOT_GATES_SLICE
        assert not _leaf(root).exists()
        assert (root / SCOPE_UNIT_CGROUP).is_dir()

    @pytest.mark.parametrize("changed_identity", ["pid-reuse", "membership"])
    def test_process_change_after_scope_transfer_is_not_migrated_to_leaf(
        self, tmp_path, changed_identity,
    ):
        root = _fake_cgroup_root(tmp_path)
        origin = "/" + SCOPE_CGROUP
        if changed_identity == "pid-reuse":
            starts = iter(["10197", "10197", "changed"])
            plc = _placement(root, pid_start_time=lambda _pid: next(starts))
        else:
            locations = iter([origin, origin, "/dev.slice/escaped.scope"])
            plc = _placement(root, pid_cgroup=lambda _pid: next(locations))

        plc.apply([101])

        assert plc.error == placement.REFUSED_IDENTITY_UNAVAILABLE
        assert not _leaf(root).exists()
        assert plc._journal["state"] == "recovery-required"

    def test_controller_delegation_requires_proven_empty_scope(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)

        def unreadable_membership(_cgroup, _root, _proc_root):
            raise PermissionError("host process view unavailable")

        plc = _placement(root, pids_in_cgroup=unreadable_membership)
        plc.apply([101])
        assert plc.error == placement.write_failed(f"{SCOPE_UNIT_CGROUP}/cgroup.procs")
        assert plc.placed is True
        assert _leaf(root).exists()  # recovery retains ownership evidence

    def test_controller_write_failure_restores_processes_and_removes_the_leaf(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)
        plc = _placement(root)
        real_write = plc._write

        def fail_delegate(path, value):
            if path.endswith("/cgroup.subtree_control"):
                raise OSError(1, "controller delegation denied", path)
            real_write(path, value)

        plc._write = fail_delegate
        plc.apply([101])
        assert plc.error == placement.write_failed(
            f"{SCOPE_UNIT_CGROUP}/cgroup.subtree_control"
        )
        assert not _leaf(root).exists()
        assert not (root / SCOPE_UNIT_CGROUP).exists()

    @pytest.mark.parametrize("phase", ["controllers-enabled", "placed"])
    def test_journal_failure_after_caps_are_applied_still_restores_lane(self, tmp_path, phase):
        root = _fake_cgroup_root(tmp_path)

        def write_state(value):
            if value["state"] == phase:
                raise OSError("journal unavailable")

        plc = _placement(root, state_write=write_state)
        plc.apply([101])
        assert plc.error == placement.REFUSED_STATE_UNAVAILABLE
        assert plc.released  # release completed despite the reported journal failure
        assert not _leaf(root).exists()
        assert not (root / SCOPE_UNIT_CGROUP).exists()

    def test_apply_continues_past_a_target_that_exited_before_capture(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)
        plc = _placement(root, pid_exists=lambda pid: pid == 101)
        plc.apply([202, 101])
        assert plc.error is None and plc.placed
        assert set(plc.pid_records) == {101}

    def test_capture_refusal_during_discovery_stops_later_pid_moves(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)
        locations = {101: "/dev.slice/outside.scope", 202: "/" + SCOPE_CGROUP}
        plc = _placement(root, pid_cgroup=lambda pid: locations[pid])
        plc.apply([101, 202])
        assert plc.error == placement.REFUSED_IDENTITY_UNAVAILABLE
        assert plc.scope_cgroup is None
        assert not _leaf(root).exists()

    def test_pid_exiting_during_scope_transfer_is_journaled_and_not_moved(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)
        reads = {101: 0}

        def exists(pid):
            reads[pid] = reads.get(pid, 0) + 1
            return reads[pid] <= 2

        def scope_created(_unit):
            (root / SCOPE_UNIT_CGROUP / "cgroup.procs").write_text("")

        plc = _placement(
            root, pid_exists=exists, scope_created=scope_created,
            pids_in_cgroup=lambda *_args: [],
        )
        plc.apply([101])
        assert plc.pid_records[101]["state"] == "exited"
        assert plc.placed and plc.error is None

    def test_cgroup_membership_reader_distinguishes_empty_unknown_and_nonempty(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)
        write_cgroup(root, SCOPE_UNIT_CGROUP, cgroup_files())
        plc = _placement(root)
        scope_procs = root / SCOPE_UNIT_CGROUP / "cgroup.procs"
        assert plc._cgroup_has_processes("/missing/scope") is None
        assert plc._cgroup_has_processes("/" + SCOPE_CGROUP) is False
        scope_procs.write_text("101\n")
        plc._pids_in_cgroup = lambda *_args: []
        assert plc._cgroup_has_processes("/" + SCOPE_UNIT_CGROUP) is None
        scope_procs.write_text("")
        assert plc._cgroup_has_processes("/" + SCOPE_UNIT_CGROUP) is False

    def test_abandon_removes_a_useless_leaf_and_clears_applied_values(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)
        lane = _placement(root)
        lane.apply([101])
        assert lane.placed
        assert lane.applied
        leaf = lane.leaf_abs
        lane._abandon(leaf)
        assert lane.leaf_cgroup is None
        assert lane.applied == {}
        assert not Path(leaf).exists()

    def test_abandon_swallows_removal_failure_after_clearing_claimed_caps(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)

        def busy(_path):
            raise OSError("busy")

        lane = _placement(root, rmdir=busy)
        lane.apply([101])
        leaf = lane.leaf_abs
        lane._abandon(leaf)
        assert lane.leaf_cgroup is None
        assert lane.applied == {}
        assert Path(leaf).exists()

    def test_scope_creation_persistence_error_keeps_journal_for_uncertain_unit(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)
        logged: List[str] = []

        def write_state(value):
            if value["state"] == "scope-create-uncertain":
                raise OSError("disk full")

        plc = _placement(root, scope_create=lambda *_args: None,
                         state_write=write_state, log=logged.append)
        plc.apply([101])
        assert plc.error == "place-refused:scope-unavailable"
        assert any("could not persist recovery journal" in item for item in logged)


class TestPlacementMigrationEdges:
    def _ready_lane(self, tmp_path, **options):
        root = _fake_cgroup_root(tmp_path)
        lane = _placement(root, **options)
        lane.apply([101])
        assert lane.placed and lane.error is None
        return root, lane

    def _record_new_pid(self, lane, pid=202):
        record = dict(lane.pid_records[101])
        record.update({"pid": pid, "start_time_ticks": str(pid * 97), "state": "prepared"})
        lane.pid_records[pid] = record
        locations = {pid: record["origin_cgroup"]}
        lane._pid_cgroup = lambda member: locations.get(member)
        lane._pid_exists = lambda _member: True
        lane._pid_start_time = lambda member: str(member * 97)
        lane._systemd_attach = lambda _unit, _subcgroup, member: (
            locations.__setitem__(member, lane.leaf_cgroup) or True
        )
        return record, locations

    def test_migration_of_a_pid_already_in_its_leaf_is_idempotent(self, tmp_path):
        _root, lane = self._ready_lane(tmp_path)
        record, locations = self._record_new_pid(lane)
        locations[202] = lane.leaf_cgroup
        assert lane.migrate([202]) == 0
        assert record["state"] == "leaf"
        assert 202 in lane.moved

    def test_exited_record_is_not_reattached(self, tmp_path):
        _root, lane = self._ready_lane(tmp_path)
        record, _locations = self._record_new_pid(lane)
        record["state"] = "exited"
        assert lane.migrate([202]) == 0
        assert record["state"] == "exited"

    def test_a_live_pid_that_changes_identity_before_migration_is_refused(self, tmp_path):
        _root, lane = self._ready_lane(tmp_path)
        record, _locations = self._record_new_pid(lane)
        lane._pid_start_time = lambda _pid: "reused"
        assert lane.migrate([202]) == 0
        assert lane.error == placement.REFUSED_IDENTITY_UNAVAILABLE

    def test_pid_exit_before_migration_is_recorded_as_exited(self, tmp_path):
        _root, lane = self._ready_lane(tmp_path)
        record, _locations = self._record_new_pid(lane)
        lane._pid_exists = lambda _pid: False
        assert lane.migrate([202]) == 0
        assert record["state"] == "exited"
        assert lane.error is None

    def test_migration_refuses_process_outside_origin_and_scope(self, tmp_path):
        _root, lane = self._ready_lane(tmp_path)
        _record, locations = self._record_new_pid(lane)
        locations[202] = "/dev.slice/dev-background.slice/other.scope"
        assert lane.migrate([202]) == 0
        assert lane.error == placement.REFUSED_IDENTITY_UNAVAILABLE

    @pytest.mark.parametrize("missing", ["unit", "scope"])
    def test_migration_requires_the_verified_scope_identity(self, tmp_path, missing):
        _root, lane = self._ready_lane(tmp_path)
        self._record_new_pid(lane)
        if missing == "unit":
            lane.scope_unit = None
        else:
            lane.scope_cgroup = None
        assert lane.migrate([202]) == 0
        assert lane.error == placement.REFUSED_IDENTITY_UNAVAILABLE

    def test_migration_write_guard_refusal_is_not_sent_to_systemd(self, tmp_path, monkeypatch):
        _root, lane = self._ready_lane(tmp_path)
        self._record_new_pid(lane)
        monkeypatch.setattr(
            lane.guard, "check_write",
            lambda *_args: (_ for _ in ()).throw(placement.HostWriteError("outside")),
        )
        lane._systemd_attach = lambda *_args: pytest.fail("guard refusal reached systemd")
        assert lane.migrate([202]) == 0
        assert lane.error == placement.REFUSED_PARENT_NOT_GATES_SLICE

    def test_pid_preparation_journal_failure_prevents_attach(self, tmp_path):
        _root, lane = self._ready_lane(tmp_path)
        lane.pid_records.pop(202, None)
        lane._pid_cgroup = lambda _pid: "/" + SCOPE_CGROUP
        lane._state_write = lambda _state: (_ for _ in ()).throw(OSError("disk full"))
        lane._systemd_attach = lambda *_args: pytest.fail("unpersisted PID reached systemd")
        assert lane.migrate([202]) == 0
        assert lane.error == placement.REFUSED_STATE_UNAVAILABLE

    def test_capture_error_during_migration_stops_discovery(self, tmp_path):
        _root, lane = self._ready_lane(tmp_path)
        lane._capture_pid = lambda _pid: (
            setattr(lane, "error", placement.REFUSED_IDENTITY_UNAVAILABLE) or None
        )
        assert lane.migrate([202]) == 0
        assert lane.error == placement.REFUSED_IDENTITY_UNAVAILABLE

    def test_exit_journal_failure_during_migration_is_reported(self, tmp_path):
        _root, lane = self._ready_lane(tmp_path)
        record, _locations = self._record_new_pid(lane)
        lane._pid_exists = lambda _pid: False
        lane._state_write = lambda state: (
            (_ for _ in ()).throw(OSError("disk full"))
            if state["state"] == "placing" else None
        )
        assert lane.migrate([202]) == 0
        assert record["state"] == "exited"
        assert lane.error == placement.REFUSED_STATE_UNAVAILABLE

    def test_move_state_journal_failure_prevents_attach(self, tmp_path):
        _root, lane = self._ready_lane(tmp_path)
        record, _locations = self._record_new_pid(lane)
        lane._state_write = lambda state: (
            (_ for _ in ()).throw(OSError("disk full"))
            if state["state"] == "moving-to-leaf" else None
        )
        lane._systemd_attach = lambda *_args: pytest.fail("uncommitted move reached systemd")
        assert lane.migrate([202]) == 0
        assert lane.error == placement.REFUSED_STATE_UNAVAILABLE
        assert record["state"] == "moving-to-leaf"

    @pytest.mark.parametrize(
        "outcome",
        ["refused", "exception", "exception-no-logger", "exited", "exited-write-failure"],
    )
    def test_attach_failure_is_distinguished_from_a_racing_process_exit(
        self, tmp_path, outcome,
    ):
        _root, lane = self._ready_lane(tmp_path)
        record, _locations = self._record_new_pid(lane)
        logged: List[str] = []
        lane._log = None if outcome == "exception-no-logger" else logged.append
        alive = {202: True}
        lane._pid_exists = lambda pid: alive.get(pid, True)

        def attach(_unit, _subcgroup, _pid):
            if outcome in {"exception", "exception-no-logger"}:
                raise RuntimeError("systemd unavailable")
            if outcome in {"exited", "exited-write-failure"}:
                alive[202] = False
            return False

        lane._systemd_attach = attach
        if outcome == "exited-write-failure":
            lane._state_write = lambda state: (
                (_ for _ in ()).throw(OSError("disk full"))
                if state["state"] == "placing" else None
            )
        assert lane.migrate([202]) == 0
        if outcome == "exited":
            assert record["state"] == "exited"
            assert lane.error is None
        elif outcome == "exited-write-failure":
            assert record["state"] == "exited"
            assert lane.error == placement.REFUSED_STATE_UNAVAILABLE
        else:
            assert lane.error == placement.write_failed(
                f"{SCOPE_UNIT_CGROUP}/{LEAF_NAME}/cgroup.procs"
            )
            if outcome != "exception-no-logger":
                assert any("could not attach" in item for item in logged)
            if outcome == "exception":
                assert any("attach of pid" in item for item in logged)
            if outcome == "exception-no-logger":
                assert logged == []

    @pytest.mark.parametrize(
        "after_attach", ["exit", "exit-write-failure", "reuse", "wrong-cgroup"],
    )
    def test_success_reply_is_verified_against_identity_and_actual_membership(
        self, tmp_path, after_attach,
    ):
        _root, lane = self._ready_lane(tmp_path)
        record, locations = self._record_new_pid(lane)
        if after_attach in {"exit", "exit-write-failure"}:
            alive = {202: True}
            lane._pid_exists = lambda pid: alive.get(pid, True)
            lane._systemd_attach = lambda *_args: (alive.__setitem__(202, False) or True)
            if after_attach == "exit-write-failure":
                lane._state_write = lambda state: (
                    (_ for _ in ()).throw(OSError("disk full"))
                    if state["state"] == "placing" else None
                )
        elif after_attach == "reuse":
            starts = iter([str(202 * 97), "reused"])
            lane._pid_start_time = lambda _pid: next(starts)
            lane._systemd_attach = lambda *_args: True
        else:
            lane._systemd_attach = lambda *_args: True

        assert lane.migrate([202]) == 0
        if after_attach == "exit":
            assert record["state"] == "exited"
            assert lane.error is None
        elif after_attach == "exit-write-failure":
            assert record["state"] == "exited"
            assert lane.error == placement.REFUSED_STATE_UNAVAILABLE
        else:
            assert lane.error == placement.REFUSED_IDENTITY_UNAVAILABLE
            if after_attach == "reuse":
                assert record["state"] == "identity-mismatch"
            else:
                assert record["state"] == "membership-mismatch"

    def test_migration_persistence_failure_after_verified_move_is_reported(self, tmp_path):
        _root, lane = self._ready_lane(tmp_path)
        record, locations = self._record_new_pid(lane)
        writes: List[Any] = []
        lane._on_write = lambda path, value: writes.append((path, value))
        lane._state_write = lambda state: (
            (_ for _ in ()).throw(OSError("disk full"))
            if state["state"] == "placing" else None
        )

        assert lane.migrate([202]) == 0
        assert locations[202] == lane.leaf_cgroup
        assert record["state"] == "leaf"
        assert lane.error == placement.REFUSED_STATE_UNAVAILABLE
        assert writes[-1] == (f"{SCOPE_UNIT_CGROUP}/{LEAF_NAME}/cgroup.procs", "202")


class TestPlacementCleanupEdges:
    def _ready_lane(self, tmp_path, **options):
        root = _fake_cgroup_root(tmp_path)
        lane = _placement(root, **options)
        lane.apply([101])
        assert lane.placed and lane.error is None
        return root, lane

    def test_owned_pid_resolution_rejects_unknown_mismatched_and_nonpositive_membership(self, tmp_path):
        _root, lane = self._ready_lane(tmp_path)
        assert lane._owned_cgroup_pids(None) == []
        assert lane._owned_cgroup_pids("/missing/scope") is None

        lane._pids_in_cgroup = lambda *_args: (_ for _ in ()).throw(PermissionError("hidden"))
        assert lane._owned_cgroup_pids(lane.leaf_cgroup) is None

        lane._pids_in_cgroup = lambda *_args: []
        assert lane._owned_cgroup_pids(lane.leaf_cgroup) is None

        lane._pids_in_cgroup = lambda *_args: [0]
        assert lane._owned_cgroup_pids(lane.leaf_cgroup) is None

    def test_owned_pid_resolution_deduplicates_host_resolver_results(self, tmp_path):
        _root, lane = self._ready_lane(tmp_path)
        lane._pids_in_cgroup = lambda *_args: [101, 101]
        assert lane._owned_cgroup_pids(lane.leaf_cgroup) == [101]

    def test_restore_refuses_without_scope_identity_or_durable_journal(self, tmp_path):
        _root, lane = self._ready_lane(tmp_path)
        lane.scope_unit = None
        assert lane._restore_owned_processes() is False

        _root2, no_journal = self._ready_lane(tmp_path / "no-journal")
        no_journal._state_write = lambda _state: (_ for _ in ()).throw(OSError("disk full"))
        assert no_journal._restore_owned_processes() is False
        assert no_journal.error == placement.REFUSED_STATE_UNAVAILABLE

    def test_restore_marks_a_gone_process_exited_then_finishes_when_membership_clears(self, tmp_path):
        _root, lane = self._ready_lane(tmp_path)
        record = lane.pid_records[101]
        lane._pid_exists = lambda _pid: False
        reads = iter([[101], [], [], []])
        lane._owned_cgroup_pids = lambda _cgroup: next(reads)
        assert lane._restore_owned_processes() is True
        assert record["state"] == "exited"

    def test_restore_accepts_a_pid_already_back_at_its_origin(self, tmp_path):
        _root, lane = self._ready_lane(tmp_path)
        record = lane.pid_records[101]
        lane._pid_cgroup = lambda _pid: record["origin_cgroup"]
        reads = iter([[101], [], [], []])
        lane._owned_cgroup_pids = lambda _cgroup: next(reads)
        assert lane._restore_owned_processes() is True
        assert record["state"] == "restored"

    def test_restore_refuses_a_live_reused_pid_and_keeps_the_leaf(self, tmp_path):
        root, lane = self._ready_lane(tmp_path)
        lane._pid_start_time = lambda _pid: "reused"
        assert lane._restore_owned_processes() is False
        assert lane.error == placement.REFUSED_IDENTITY_UNAVAILABLE
        assert _leaf(root).exists()

    def test_restore_refuses_a_pid_that_escaped_its_owned_source(self, tmp_path):
        _root, lane = self._ready_lane(tmp_path)
        lane._pid_cgroup = lambda _pid: "/dev.slice/unrelated.scope"
        assert lane._restore_owned_processes() is False
        assert lane.error == placement.REFUSED_IDENTITY_UNAVAILABLE

    def test_restore_refuses_an_unverified_origin_unit(self, tmp_path):
        _root, lane = self._ready_lane(tmp_path)
        lane._unit_cgroup_verifier = lambda _unit, _path: False
        assert lane._restore_owned_processes() is False
        assert lane.error == placement.REFUSED_IDENTITY_UNAVAILABLE

    def test_restore_journal_failure_does_not_send_pid_to_systemd(self, tmp_path):
        _root, lane = self._ready_lane(tmp_path)
        lane._state_write = lambda _state: (_ for _ in ()).throw(OSError("disk full"))
        lane._systemd_attach = lambda *_args: pytest.fail("unpersisted restoration reached systemd")
        assert lane._restore_owned_processes() is False
        assert lane.error == placement.REFUSED_STATE_UNAVAILABLE

    @pytest.mark.parametrize(
        "outcome", ["refused-live", "refused-exited", "exception", "exception-no-logger"],
    )
    def test_restore_attach_failure_never_discards_scope_evidence(self, tmp_path, outcome):
        root, lane = self._ready_lane(tmp_path)
        logged: List[str] = []
        lane._log = None if outcome == "exception-no-logger" else logged.append
        alive = {101: True}

        def attach(*_args):
            if outcome in {"exception", "exception-no-logger"}:
                raise RuntimeError("systemd unavailable")
            if outcome == "refused-exited":
                alive[101] = False
            return False

        lane._pid_exists = lambda pid: alive.get(pid, True)
        lane._systemd_attach = attach
        if outcome == "refused-exited":
            reads = iter([[101], [], [], []])
            lane._owned_cgroup_pids = lambda _cgroup: next(reads)
        assert lane._restore_owned_processes() is (outcome == "refused-exited")
        if outcome == "refused-exited":
            assert lane.pid_records[101]["state"] == "exited"
        else:
            assert not lane.released
            assert _leaf(root).exists()
            assert any("restore of pid" in item for item in logged) is (outcome == "exception")
            if outcome == "exception-no-logger":
                assert logged == []

    def test_restore_records_process_exit_after_systemd_accepts_restoration(self, tmp_path):
        _root, lane = self._ready_lane(tmp_path)
        alive = {101: True}
        lane._pid_exists = lambda pid: alive.get(pid, True)
        lane._systemd_attach = lambda *_args: (alive.__setitem__(101, False) or True)
        reads = iter([[101], [], [], []])
        lane._owned_cgroup_pids = lambda _cgroup: next(reads)
        assert lane._restore_owned_processes() is True
        assert lane.pid_records[101]["state"] == "exited"

    @pytest.mark.parametrize(
        ("phase", "setup"),
        [
            ("exited", "gone-before-restore"),
            ("already-restored", "at-origin"),
            ("before-attach", "normal"),
            ("attach-exited", "attach-refused-exit"),
            ("post-attach-exited", "attach-success-exit"),
            ("after-restore", "attach-success"),
        ],
    )
    def test_each_restore_journal_boundary_fails_closed(self, tmp_path, phase, setup):
        _root, lane = self._ready_lane(tmp_path)
        if setup == "gone-before-restore":
            lane._pid_exists = lambda _pid: False
            failing_call = 2
        elif setup == "at-origin":
            lane._pid_cgroup = lambda _pid: lane.pid_records[101]["origin_cgroup"]
            failing_call = 2
        elif setup == "normal":
            failing_call = 2
        elif setup == "attach-refused-exit":
            alive = {101: True}
            lane._pid_exists = lambda pid: alive.get(pid, True)
            lane._systemd_attach = lambda *_args: (alive.__setitem__(101, False) or False)
            failing_call = 3
        elif setup == "attach-success-exit":
            alive = {101: True}
            lane._pid_exists = lambda pid: alive.get(pid, True)
            lane._systemd_attach = lambda *_args: (alive.__setitem__(101, False) or True)
            reads = iter([[101], [], [], []])
            lane._owned_cgroup_pids = lambda _cgroup: next(reads)
            failing_call = 3
        else:
            attach = lane._systemd_attach
            lane._systemd_attach = lambda unit, subcgroup, pid: attach(unit, subcgroup, pid)
            failing_call = 3

        writes = 0

        def fail_at_boundary(_state):
            nonlocal writes
            writes += 1
            if writes == failing_call:
                raise OSError("journal device unavailable")

        lane._state_write = fail_at_boundary
        assert lane._restore_owned_processes() is False
        assert lane.error == placement.REFUSED_STATE_UNAVAILABLE
        assert writes == failing_call

    def test_restore_post_attach_pid_reuse_is_not_mistaken_for_restoration(self, tmp_path):
        root, lane = self._ready_lane(tmp_path)
        starts = iter(["9797", "reused"])
        lane._pid_start_time = lambda _pid: next(starts)
        lane._systemd_attach = lambda *_args: True
        assert lane._restore_owned_processes() is False
        assert lane.error == placement.REFUSED_IDENTITY_UNAVAILABLE
        assert _leaf(root).exists()

    def test_restore_fails_if_the_post_restore_membership_read_is_unknown(self, tmp_path):
        _root, lane = self._ready_lane(tmp_path)
        reads = iter([[101], [], None, []])
        lane._owned_cgroup_pids = lambda _cgroup: next(reads)
        assert lane._restore_owned_processes() is False
        assert lane.error is not None

    def test_restore_exhaustion_preserves_persistent_unresolved_membership(self, tmp_path):
        _root, lane = self._ready_lane(tmp_path)
        lane._pid_cgroup = lambda _pid: lane.pid_records[101]["origin_cgroup"]
        calls = 0

        def stale_membership(_cgroup):
            nonlocal calls
            calls += 1
            return [101] if calls % 4 == 3 else []

        lane._owned_cgroup_pids = stale_membership
        lane._sleep = lambda _seconds: None
        assert lane._restore_owned_processes() is False
        assert lane.error is not None

    def test_restore_rechecks_membership_after_systemd_accepts_the_move(self, tmp_path):
        root, lane = self._ready_lane(tmp_path)
        lane._systemd_attach = lambda *_args: True
        lane._pid_cgroup = lambda _pid: lane.leaf_cgroup
        assert lane._restore_owned_processes() is False
        assert lane.error == placement.write_failed(
            f"{SCOPE_UNIT_CGROUP}/{LEAF_NAME}/cgroup.procs"
        )
        assert _leaf(root).exists()

    def test_restore_retries_a_stale_membership_snapshot_before_declaring_failure(self, tmp_path):
        _root, lane = self._ready_lane(tmp_path)
        lane._pid_cgroup = lambda _pid: lane.pid_records[101]["origin_cgroup"]
        # A stale first read shows one PID; its next read confirms the source
        # emptied. No move is necessary because identity says it was restored.
        reads = iter([[101], [], [101], [], [], [], [], []])
        lane._owned_cgroup_pids = lambda _cgroup: next(reads)
        sleeps: List[float] = []
        lane._sleep = sleeps.append
        assert lane._restore_owned_processes() is True
        assert sleeps == [placement.RMDIR_RETRY_SECONDS]

    def test_release_is_idempotent_without_an_owned_scope(self, tmp_path):
        _root, lane = self._ready_lane(tmp_path)
        lane.released = True
        lane.release()
        lane.scope_cgroup = None
        lane.release()

    def test_release_preserves_leaf_when_its_identity_is_not_owned(self, tmp_path):
        root, lane = self._ready_lane(tmp_path)
        lane.guard.is_leaf = lambda _path: False
        lane.release()
        assert not lane.released
        assert _leaf(root).exists()
        assert lane.error is not None

    def test_release_records_leaf_removal_failure_and_keeps_recovery_state(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)

        def cannot_remove(path):
            raise OSError(16, "device or resource busy", path)

        logged: List[str] = []
        lane = _placement(root, rmdir=cannot_remove, log=logged.append, sleep=lambda _s: None)
        lane.apply([101])
        assert lane.placed
        lane.release()
        assert not lane.released
        assert lane.error == placement.write_failed(f"{SCOPE_UNIT_CGROUP}/{LEAF_NAME}")
        assert _leaf(root).exists()
        assert lane._journal["state"] == "recovery-required"
        assert logged and "could not remove leaf" in logged[-1]

    def test_release_can_retire_a_scope_after_its_leaf_is_already_absent(self, tmp_path):
        root, lane = self._ready_lane(tmp_path)
        shutil.rmtree(_leaf(root))
        lane._restore_owned_processes = lambda: True
        lane.release()
        assert lane.released
        assert not (root / SCOPE_UNIT_CGROUP).exists()

    def test_release_accepts_scope_auto_retirement_after_verified_restoration(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)

        def auto_retire_scope_after_leaf(path):
            _fake_rmdir(path)
            if path == str(_leaf(root)):
                _fake_rmdir(str(root / SCOPE_UNIT_CGROUP))

        lane = _placement(
            root,
            rmdir=auto_retire_scope_after_leaf,
            unit_absence_verifier=lambda unit: (
                unit == SCOPE_UNIT and not (root / SCOPE_UNIT_CGROUP).exists()
            ),
        )
        lane.apply([101])
        assert lane.placed

        lane.release()

        assert lane.released
        assert lane._journal["state"] == "complete"
        assert lane.pid_records[101]["state"] == "restored"
        assert not (root / SCOPE_UNIT_CGROUP).exists()
        assert (root / SCOPE_CGROUP / "cgroup.procs").read_text() == "101\n"

    @pytest.mark.parametrize(
        ("condition", "expected", "expected_state"),
        [
            ("process-exited", True, "exited"),
            ("start-time-unknown", False, "leaf"),
            ("pid-reused", True, "exited"),
            ("not-restored", False, "leaf"),
        ],
    )
    def test_auto_retirement_requires_each_original_pid_to_be_proved_restored(
        self, tmp_path, condition, expected, expected_state,
    ):
        root, lane = self._ready_lane(tmp_path)
        shutil.rmtree(_leaf(root))
        shutil.rmtree(root / SCOPE_UNIT_CGROUP)
        lane._unit_absence_verifier = lambda unit: unit == SCOPE_UNIT
        if condition == "process-exited":
            lane._pid_exists = lambda _pid: False
        else:
            lane._pid_exists = lambda _pid: True
        if condition == "start-time-unknown":
            lane._pid_start_time = lambda _pid: None
        elif condition == "pid-reused":
            lane._pid_start_time = lambda _pid: "reused"
        else:
            lane._pid_start_time = lambda pid: str(pid * 97)
        if condition == "not-restored":
            lane._pid_cgroup = lambda _pid: "/dev.slice/unrelated.scope"

        assert lane._scope_retired_after_restore(
            str(root / SCOPE_UNIT_CGROUP)
        ) is expected
        assert lane.pid_records[101]["state"] == expected_state

    def test_release_refuses_auto_retired_scope_when_unit_absence_is_unknown(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)

        def auto_retire_scope_after_leaf(path):
            _fake_rmdir(path)
            if path == str(_leaf(root)):
                _fake_rmdir(str(root / SCOPE_UNIT_CGROUP))

        lane = _placement(
            root, rmdir=auto_retire_scope_after_leaf,
            unit_absence_verifier=lambda _unit: None,
        )
        lane.apply([101])
        lane.release()

        assert not lane.released
        assert lane._journal["state"] == "recovery-required"
        assert lane.pid_records[101]["state"] == "restored"

    def test_release_reports_journal_failure_after_verified_auto_retirement(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)

        def auto_retire_scope_after_leaf(path):
            _fake_rmdir(path)
            if path == str(_leaf(root)):
                _fake_rmdir(str(root / SCOPE_UNIT_CGROUP))

        lane = _placement(
            root,
            rmdir=auto_retire_scope_after_leaf,
            unit_absence_verifier=lambda unit: (
                unit == SCOPE_UNIT and not (root / SCOPE_UNIT_CGROUP).exists()
            ),
        )
        lane.apply([101])
        assert lane.placed

        def fail_only_completion(journal):
            if journal["state"] == "complete":
                raise OSError("disk full")

        lane._state_write = fail_only_completion
        lane.release()

        assert lane.error == placement.REFUSED_STATE_UNAVAILABLE
        assert not lane.released
        assert not (root / SCOPE_UNIT_CGROUP).exists()

    def test_release_is_safe_without_an_optional_journal_object(self, tmp_path):
        root, lane = self._ready_lane(tmp_path)
        lane._journal = None
        lane.release()
        assert lane.released
        assert not (root / SCOPE_UNIT_CGROUP).exists()

    def test_leaf_removal_journal_failure_leaves_the_empty_scope_for_recovery(self, tmp_path):
        root, lane = self._ready_lane(tmp_path)
        lane._state_write = lambda state: (
            (_ for _ in ()).throw(OSError("disk full"))
            if state["state"] == "leaf-removed" else None
        )
        lane.release()
        assert lane.error == placement.REFUSED_STATE_UNAVAILABLE
        assert not _leaf(root).exists()
        assert (root / SCOPE_UNIT_CGROUP).exists()

    @pytest.mark.parametrize("obstruction", ["members", "children", "unreadable", "unit"])
    def test_release_requires_empty_childless_verified_scope(self, tmp_path, monkeypatch, obstruction):
        root, lane = self._ready_lane(tmp_path)
        if obstruction == "members":
            lane._cgroup_has_processes = lambda _cgroup: True
        elif obstruction == "children":
            (root / SCOPE_UNIT_CGROUP / "unexpected-child").mkdir()
        elif obstruction == "unreadable":
            real_listdir = os.listdir

            def listdir(path):
                if path == str(root / SCOPE_UNIT_CGROUP):
                    raise PermissionError("scope cannot be inspected")
                return real_listdir(path)

            monkeypatch.setattr(os, "listdir", listdir)
        else:
            lane._unit_cgroup_verifier = lambda unit, _cgroup: unit.startswith("docker-")
        lane.release()
        assert not lane.released
        assert (root / SCOPE_UNIT_CGROUP).exists()

    def test_release_requires_systemd_to_confirm_scope_retirement(self, tmp_path):
        root, lane = self._ready_lane(tmp_path)
        logged: List[str] = []
        lane._scope_stop = lambda _unit: False
        lane._log = logged.append
        lane.release()
        assert not lane.released
        assert (root / SCOPE_UNIT_CGROUP).exists()
        assert any("would not retire empty scope" in item for item in logged)

    def test_scope_retirement_refusal_is_safe_without_an_optional_logger(self, tmp_path):
        root, lane = self._ready_lane(tmp_path)
        lane._scope_stop = lambda _unit: False
        lane._log = None
        lane.release()
        assert not lane.released
        assert (root / SCOPE_UNIT_CGROUP).exists()

    def test_release_reports_final_journal_failure_after_scope_was_stopped(self, tmp_path):
        root, lane = self._ready_lane(tmp_path)
        lane._state_write = lambda state: (
            (_ for _ in ()).throw(OSError("disk full"))
            if state["state"] == "complete" else None
        )
        lane.release()
        assert lane.error == placement.REFUSED_STATE_UNAVAILABLE
        assert not lane.released
        assert not (root / SCOPE_UNIT_CGROUP).exists()


class TestPlacementJournalRecovery:
    def _make_journal(self, root):
        snapshots: List[Dict[str, Any]] = []
        lane = _placement(
            root,
            state_write=lambda value: snapshots.append(json.loads(json.dumps(value))),
        )
        lane.apply([101])
        assert lane.placed and snapshots[-1]["state"] == "placed"
        return json.loads(json.dumps(snapshots[-1]))

    def _patch_manager(self, monkeypatch, **overrides):
        values = {
            ("org.freedesktop.systemd1.Unit", "LoadState"): "loaded",
            ("org.freedesktop.systemd1.Scope", "ControlGroup"): "/" + SCOPE_UNIT_CGROUP,
            ("org.freedesktop.systemd1.Scope", "Slice"): "dev-gates.slice",
        }
        values.update(overrides)
        monkeypatch.setattr(access, "have_host_proc_view", lambda _proc: True)
        monkeypatch.setattr(placement, "_systemd_unit_path", lambda _unit: "/unit/scope")
        monkeypatch.setattr(
            placement, "_systemd_property",
            lambda _path, interface, name: values.get((interface, name)),
        )
        monkeypatch.setattr(placement, "_systemd_bool_property", lambda *_args: True)
        monkeypatch.setattr(
            placement, "_systemd_string_array_property",
            lambda *_args: list(placement.REQUIRED_CONTROLLERS),
        )
        monkeypatch.setattr(placement, "_systemd_unit_cgroup_matches", lambda *_args: True)

    def _patch_factory(self, monkeypatch, root):
        real_class = placement.LanePlacement

        def recovery_factory(**kwargs):
            return _placement(
                root,
                _placement_cls=real_class,
                token=kwargs["token"],
                origin_cgroup=kwargs["origin_cgroup"],
                state_write=kwargs["state_write"],
                proc_root=kwargs["proc_root"],
            )

        monkeypatch.setattr(placement, "LanePlacement", recovery_factory)

    def test_restart_recovery_restores_exact_scope_members_then_retires_scope(
        self, tmp_path, monkeypatch,
    ):
        root = _fake_cgroup_root(tmp_path)
        snapshots: List[Dict[str, Any]] = []
        initial = _placement(
            root,
            state_write=lambda value: snapshots.append(json.loads(json.dumps(value))),
        )
        initial.apply([101])
        journal = json.loads(json.dumps(snapshots[-1]))
        assert journal["state"] == "placed"

        monkeypatch.setattr(access, "have_host_proc_view", lambda _proc: True)
        monkeypatch.setattr(placement, "_systemd_unit_path", lambda _unit: "/unit/scope")

        def string_property(_path, interface, name):
            return {
                ("org.freedesktop.systemd1.Unit", "LoadState"): "loaded",
                ("org.freedesktop.systemd1.Scope", "ControlGroup"): "/" + SCOPE_UNIT_CGROUP,
                ("org.freedesktop.systemd1.Scope", "Slice"): "dev-gates.slice",
            }.get((interface, name))

        monkeypatch.setattr(placement, "_systemd_property", string_property)
        monkeypatch.setattr(placement, "_systemd_bool_property", lambda *_args: True)
        monkeypatch.setattr(
            placement, "_systemd_string_array_property",
            lambda *_args: list(placement.REQUIRED_CONTROLLERS),
        )
        monkeypatch.setattr(placement, "_systemd_unit_cgroup_matches", lambda *_args: True)
        real_class = placement.LanePlacement

        def recovery_factory(**kwargs):
            return _placement(
                root,
                _placement_cls=real_class,
                token=kwargs["token"],
                origin_cgroup=kwargs["origin_cgroup"],
                state_write=kwargs["state_write"],
                proc_root=kwargs["proc_root"],
            )

        monkeypatch.setattr(placement, "LanePlacement", recovery_factory)
        updates: List[Dict[str, Any]] = []
        result = placement.recover_journal(
            journal,
            cgroup_root=str(root),
            gates_cgroup=GATES_CGROUP,
            proc_root="/proc",
            state_write=lambda value: updates.append(json.loads(json.dumps(value))),
        )

        assert result is None
        assert (root / SCOPE_CGROUP / "cgroup.procs").read_text() == "101\n"
        assert not (root / SCOPE_UNIT_CGROUP).exists()
        assert updates[-1]["state"] == "complete"
        assert updates[-1]["leaf_created"] is False

    @pytest.mark.parametrize(
        "corruption",
        [
            "scope-unit", "gates-unit", "gates-path", "scope-relative",
            "scope-normalization", "scope-parent", "scope-name", "leaf-without-scope",
            "leaf-path", "empty-pids", "nonobject-pids", "nonnumeric-pid",
            "nonobject-record", "pid-mismatch", "relative-origin", "nonstring-origin",
            "nonstring-unit", "nonstring-unit-path", "nonstring-subgroup",
            "nondigit-start-time", "origin-destination-mismatch",
        ],
    )
    def test_recovery_rejects_each_untrusted_journal_identity_shape(
        self, tmp_path, monkeypatch, corruption,
    ):
        root = _fake_cgroup_root(tmp_path)
        journal = self._make_journal(root)
        record = journal["pids"]["101"]
        if corruption == "scope-unit":
            journal["scope_unit"] = "other.scope"
        elif corruption == "gates-unit":
            journal["gates_unit"] = "dev-background.slice"
        elif corruption == "gates-path":
            journal["gates_cgroup"] = "/dev.slice/dev-background.slice"
        elif corruption == "scope-relative":
            journal["scope_cgroup"] = "dev.slice/dev-gates.slice/" + SCOPE_UNIT
        elif corruption == "scope-normalization":
            journal["scope_cgroup"] = "/dev.slice/dev-gates.slice/../dev-gates.slice/" + SCOPE_UNIT
        elif corruption == "scope-parent":
            journal["scope_cgroup"] = "/dev.slice/dev-background.slice/" + SCOPE_UNIT
        elif corruption == "scope-name":
            journal["scope_cgroup"] = "/dev.slice/dev-gates.slice/other.scope"
        elif corruption == "leaf-without-scope":
            journal["scope_cgroup"] = None
        elif corruption == "leaf-path":
            journal["leaf_cgroup"] = "/dev.slice/dev-gates.slice/other.scope/rg-other"
        elif corruption == "empty-pids":
            journal["pids"] = {}
        elif corruption == "nonobject-pids":
            journal["pids"] = []
        elif corruption == "nonnumeric-pid":
            journal["pids"] = {"1x": record}
        elif corruption == "nonobject-record":
            journal["pids"] = {"101": []}
        elif corruption == "pid-mismatch":
            record["pid"] = 202
        elif corruption == "relative-origin":
            record["origin_cgroup"] = "relative.scope"
        elif corruption == "nonstring-origin":
            record["origin_cgroup"] = []
        elif corruption == "nonstring-unit":
            record["origin_unit"] = 42
        elif corruption == "nonstring-unit-path":
            record["origin_unit_cgroup"] = None
        elif corruption == "nonstring-subgroup":
            record["origin_subcgroup"] = 42
        elif corruption == "nondigit-start-time":
            record["start_time_ticks"] = "4x"
        else:
            record["origin_subcgroup"] = "different-path"

        monkeypatch.setattr(
            placement, "_systemd_unit_path",
            lambda _unit: pytest.fail("untrusted journal reached systemd"),
        )
        result = placement.recover_journal(
            journal,
            cgroup_root=str(root),
            gates_cgroup=GATES_CGROUP,
            proc_root="/proc",
            state_write=lambda _value: pytest.fail("untrusted journal was rewritten"),
        )
        assert result == placement.REFUSED_STATE_UNAVAILABLE

    def test_recovery_refuses_when_host_proc_view_is_not_authoritative(self, tmp_path, monkeypatch):
        root = _fake_cgroup_root(tmp_path)
        journal = self._make_journal(root)
        monkeypatch.setattr(access, "have_host_proc_view", lambda _proc: False)
        result = placement.recover_journal(
            journal, cgroup_root=str(root), gates_cgroup=GATES_CGROUP,
            proc_root="/proc", state_write=lambda _value: pytest.fail("refusal was not durable"),
        )
        assert result == placement.REFUSED_NO_HOST_PROC

    def test_missing_systemd_unit_is_clean_only_when_processes_are_proved_restored(self, tmp_path, monkeypatch):
        root = _fake_cgroup_root(tmp_path)
        journal = self._make_journal(root)
        journal["scope_cgroup"] = None
        journal["leaf_cgroup"] = None
        proc = tmp_path / "proc"
        (proc / "101").mkdir(parents=True)
        monkeypatch.setattr(access, "have_host_proc_view", lambda _root: True)
        monkeypatch.setattr(placement, "_systemd_unit_path", lambda _unit: None)
        monkeypatch.setattr(placement, "_systemd_unit_is_absent", lambda _unit: True)
        monkeypatch.setattr(placement, "_process_start_time_ticks", lambda _proc, _pid: "9797")
        monkeypatch.setattr(
            "lib.targets.cgroup_of_pid",
            lambda _pid, _root, _proc: "/" + SCOPE_CGROUP,
        )
        updates = []
        result = placement.recover_journal(
            journal, cgroup_root=str(root), gates_cgroup=GATES_CGROUP,
            proc_root=str(proc), state_write=updates.append,
        )
        assert result is None
        assert updates[-1]["state"] == "complete"

    def test_recovery_rechecks_the_leaf_if_a_scope_appears_between_path_checks(
        self, tmp_path, monkeypatch,
    ):
        root = _fake_cgroup_root(tmp_path)
        journal = self._make_journal(root)
        scope_path = root / SCOPE_UNIT_CGROUP
        leaf_path = _leaf(root)
        shutil.rmtree(scope_path)
        real_lexists = os.path.lexists
        scope_reappeared = False

        def race_scope_creation(path):
            nonlocal scope_reappeared
            if path == str(scope_path) and not scope_reappeared:
                assert not real_lexists(path)
                write_cgroup(root, SCOPE_UNIT_CGROUP, cgroup_files())
                write_cgroup(root, f"{SCOPE_UNIT_CGROUP}/{LEAF_NAME}", cgroup_files())
                scope_reappeared = True
                # This lookup saw the absent scope; the subsequent independent
                # leaf check must catch the unit/path reappearing concurrently.
                return False
            return real_lexists(path)

        monkeypatch.setattr(access, "have_host_proc_view", lambda _proc: True)
        monkeypatch.setattr(placement, "_systemd_unit_path", lambda _unit: None)
        monkeypatch.setattr(placement, "_systemd_unit_is_absent", lambda _unit: True)
        monkeypatch.setattr(placement.os.path, "lexists", race_scope_creation)

        result = placement.recover_journal(
            journal, cgroup_root=str(root), gates_cgroup=GATES_CGROUP,
            proc_root="/proc",
            state_write=lambda _value: pytest.fail("raced recovery was accepted"),
        )

        assert scope_reappeared and real_lexists(str(leaf_path))
        assert result == placement.REFUSED_STATE_UNAVAILABLE

    def test_missing_systemd_unit_refuses_when_process_identity_cannot_be_read(
        self, tmp_path, monkeypatch,
    ):
        root = _fake_cgroup_root(tmp_path)
        journal = self._make_journal(root)
        journal["scope_cgroup"] = None
        journal["leaf_cgroup"] = None
        proc = tmp_path / "proc"
        (proc / "101").mkdir(parents=True)
        monkeypatch.setattr(access, "have_host_proc_view", lambda _root: True)
        monkeypatch.setattr(placement, "_systemd_unit_path", lambda _unit: None)
        monkeypatch.setattr(placement, "_systemd_unit_is_absent", lambda _unit: True)
        monkeypatch.setattr(
            placement, "_process_start_time_ticks", lambda _proc, _pid: None,
        )

        result = placement.recover_journal(
            journal, cgroup_root=str(root), gates_cgroup=GATES_CGROUP,
            proc_root=str(proc),
            state_write=lambda _value: pytest.fail("unidentified process was accepted"),
        )

        assert result == placement.REFUSED_IDENTITY_UNAVAILABLE

    def test_missing_process_after_crash_needs_no_identity_read_and_completes_journal(
        self, tmp_path, monkeypatch,
    ):
        root = _fake_cgroup_root(tmp_path)
        journal = self._make_journal(root)
        journal["scope_cgroup"] = None
        journal["leaf_cgroup"] = None
        proc = tmp_path / "empty-proc"
        monkeypatch.setattr(access, "have_host_proc_view", lambda _root: True)
        monkeypatch.setattr(placement, "_systemd_unit_path", lambda _unit: None)
        monkeypatch.setattr(placement, "_systemd_unit_is_absent", lambda _unit: True)
        monkeypatch.setattr(
            placement, "_process_start_time_ticks",
            lambda *_args: pytest.fail("exited process must not be reidentified"),
        )
        updates = []
        result = placement.recover_journal(
            journal, cgroup_root=str(root), gates_cgroup=GATES_CGROUP,
            proc_root=str(proc), state_write=updates.append,
        )
        assert result is None
        assert updates[-1]["state"] == "complete"
        assert updates[-1]["pids"]["101"]["state"] == "exited"

    @pytest.mark.parametrize("problem", ["scope-remains", "pid-moved", "write-failed"])
    def test_missing_systemd_unit_refuses_unproved_cleanup(self, tmp_path, monkeypatch, problem):
        root = _fake_cgroup_root(tmp_path)
        journal = self._make_journal(root)
        if problem != "scope-remains":
            journal["scope_cgroup"] = None
            journal["leaf_cgroup"] = None
        proc = tmp_path / "proc"
        (proc / "101").mkdir(parents=True)
        monkeypatch.setattr(access, "have_host_proc_view", lambda _root: True)
        monkeypatch.setattr(placement, "_systemd_unit_path", lambda _unit: None)
        monkeypatch.setattr(placement, "_systemd_unit_is_absent", lambda _unit: True)
        monkeypatch.setattr(placement, "_process_start_time_ticks", lambda _proc, _pid: "9797")
        current = "/dev.slice/outside.scope" if problem == "pid-moved" else "/" + SCOPE_CGROUP
        monkeypatch.setattr("lib.targets.cgroup_of_pid", lambda *_args: current)
        def writer(_journal):
            if problem == "write-failed":
                raise OSError("disk full")
        result = placement.recover_journal(
            journal, cgroup_root=str(root), gates_cgroup=GATES_CGROUP,
            proc_root=str(proc), state_write=writer,
        )
        assert result == (
            placement.REFUSED_IDENTITY_UNAVAILABLE
            if problem == "pid-moved"
            else placement.REFUSED_STATE_UNAVAILABLE
        )

    def test_recovery_marks_a_reused_pid_exited_without_inspecting_or_moving_it(
        self, tmp_path, monkeypatch,
    ):
        root = _fake_cgroup_root(tmp_path)
        journal = self._make_journal(root)
        journal["scope_cgroup"] = None
        journal["leaf_cgroup"] = None
        proc = tmp_path / "proc"
        (proc / "101").mkdir(parents=True)
        monkeypatch.setattr(access, "have_host_proc_view", lambda _root: True)
        monkeypatch.setattr(placement, "_systemd_unit_path", lambda _unit: None)
        monkeypatch.setattr(placement, "_systemd_unit_is_absent", lambda _unit: True)
        monkeypatch.setattr(placement, "_process_start_time_ticks", lambda _proc, _pid: "reused")
        monkeypatch.setattr(
            "lib.targets.cgroup_of_pid",
            lambda *_args: pytest.fail("reused PID must not be inspected or moved"),
        )
        updates = []
        result = placement.recover_journal(
            journal, cgroup_root=str(root), gates_cgroup=GATES_CGROUP,
            proc_root=str(proc), state_write=updates.append,
        )
        assert result is None
        assert updates[-1]["state"] == "complete"
        assert updates[-1]["pids"]["101"]["state"] == "exited"

    def test_recovery_does_not_treat_manager_query_failure_as_unit_absence(
        self, tmp_path, monkeypatch,
    ):
        root = _fake_cgroup_root(tmp_path)
        journal = self._make_journal(root)
        journal["scope_cgroup"] = None
        journal["leaf_cgroup"] = None
        monkeypatch.setattr(access, "have_host_proc_view", lambda _root: True)
        monkeypatch.setattr(placement, "_systemd_unit_path", lambda _unit: None)
        monkeypatch.setattr(placement, "_systemd_unit_is_absent", lambda _unit: None)
        result = placement.recover_journal(
            journal, cgroup_root=str(root), gates_cgroup=GATES_CGROUP,
            proc_root="/proc", state_write=lambda _value: pytest.fail("unknown unit was accepted"),
        )
        assert result == placement.REFUSED_STATE_UNAVAILABLE

    @pytest.mark.parametrize(
        "problem",
        [
            "not-loaded", "missing-cgroup", "relative-cgroup", "wrong-slice",
            "not-delegated", "unknown-delegation", "missing-controller", "unknown-controllers",
            "wrong-parent", "wrong-unit-name",
            "unverified-unit", "missing-directory",
        ],
    )
    def test_existing_scope_must_match_all_systemd_ownership_properties(
        self, tmp_path, monkeypatch, problem,
    ):
        root = _fake_cgroup_root(tmp_path)
        journal = self._make_journal(root)
        properties = {
            ("org.freedesktop.systemd1.Unit", "LoadState"): "loaded",
            ("org.freedesktop.systemd1.Scope", "ControlGroup"): "/" + SCOPE_UNIT_CGROUP,
            ("org.freedesktop.systemd1.Scope", "Slice"): "dev-gates.slice",
        }
        if problem == "not-loaded":
            properties[("org.freedesktop.systemd1.Unit", "LoadState")] = "not-found"
        elif problem == "missing-cgroup":
            properties[("org.freedesktop.systemd1.Scope", "ControlGroup")] = None
        elif problem == "relative-cgroup":
            properties[("org.freedesktop.systemd1.Scope", "ControlGroup")] = "relative/path"
        elif problem == "wrong-slice":
            properties[("org.freedesktop.systemd1.Scope", "Slice")] = "dev-background.slice"
        monkeypatch.setattr(access, "have_host_proc_view", lambda _proc: True)
        monkeypatch.setattr(placement, "_systemd_unit_path", lambda _unit: "/unit/scope")
        monkeypatch.setattr(
            placement, "_systemd_property",
            lambda _path, interface, name: properties.get((interface, name)),
        )
        monkeypatch.setattr(
            placement, "_systemd_bool_property",
            lambda *_args: (
                None if problem == "unknown-delegation" else problem != "not-delegated"
            ),
        )
        monkeypatch.setattr(
            placement, "_systemd_string_array_property",
            lambda *_args: (
                None if problem == "unknown-controllers" else
                ["memory"] if problem == "missing-controller"
                else list(placement.REQUIRED_CONTROLLERS)
            ),
        )
        monkeypatch.setattr(
            placement, "_systemd_unit_cgroup_matches",
            lambda *_args: problem != "unverified-unit",
        )
        if problem == "wrong-parent":
            properties[("org.freedesktop.systemd1.Scope", "ControlGroup")] = "/dev.slice/other.slice/" + SCOPE_UNIT
        elif problem == "wrong-unit-name":
            properties[("org.freedesktop.systemd1.Scope", "ControlGroup")] = "/dev.slice/dev-gates.slice/other.scope"
        elif problem == "missing-directory":
            shutil.rmtree(root / SCOPE_UNIT_CGROUP)
        result = placement.recover_journal(
            journal, cgroup_root=str(root), gates_cgroup=GATES_CGROUP,
            proc_root="/proc", state_write=lambda _value: pytest.fail("invalid scope was mutated"),
        )
        assert result == placement.REFUSED_STATE_UNAVAILABLE

    def test_unjournaled_leaf_is_neither_adopted_nor_removed(self, tmp_path, monkeypatch):
        root = _fake_cgroup_root(tmp_path)
        journal = self._make_journal(root)
        journal["leaf_created"] = False
        self._patch_manager(monkeypatch)
        self._patch_factory(monkeypatch, root)
        result = placement.recover_journal(
            journal, cgroup_root=str(root), gates_cgroup=GATES_CGROUP,
            proc_root="/proc", state_write=lambda _value: None,
        )
        assert result is not None
        assert _leaf(root).is_dir()
        assert (root / SCOPE_UNIT_CGROUP).is_dir()

    def test_broken_leaf_symlink_is_not_treated_as_a_recovery_directory(self, tmp_path, monkeypatch):
        root = _fake_cgroup_root(tmp_path)
        journal = self._make_journal(root)
        shutil.rmtree(_leaf(root))
        _leaf(root).symlink_to(tmp_path / "missing-target", target_is_directory=True)
        self._patch_manager(monkeypatch)
        result = placement.recover_journal(
            journal, cgroup_root=str(root), gates_cgroup=GATES_CGROUP,
            proc_root="/proc", state_write=lambda _value: pytest.fail("unsafe leaf mutated"),
        )
        assert result == placement.REFUSED_STATE_UNAVAILABLE
        assert _leaf(root).is_symlink()

    def test_missing_leaf_is_not_invented_during_recovery(self, tmp_path, monkeypatch):
        root = _fake_cgroup_root(tmp_path)
        journal = self._make_journal(root)
        shutil.rmtree(_leaf(root))
        journal["leaf_created"] = False
        self._patch_manager(monkeypatch)
        self._patch_factory(monkeypatch, root)
        updates = []
        result = placement.recover_journal(
            journal, cgroup_root=str(root), gates_cgroup=GATES_CGROUP,
            proc_root="/proc", state_write=updates.append,
        )
        assert result is None
        assert not (root / SCOPE_UNIT_CGROUP).exists()
        assert updates[-1]["state"] == "complete"

    def test_recovery_returns_the_cleanup_failure_instead_of_claiming_success(
        self, tmp_path, monkeypatch,
    ):
        root = _fake_cgroup_root(tmp_path)
        journal = self._make_journal(root)
        self._patch_manager(monkeypatch)
        real_class = placement.LanePlacement

        def leave_recovery_required(self):
            self.error = placement.REFUSED_IDENTITY_UNAVAILABLE

        monkeypatch.setattr(real_class, "release", leave_recovery_required)
        self._patch_factory(monkeypatch, root)
        result = placement.recover_journal(
            journal, cgroup_root=str(root), gates_cgroup=GATES_CGROUP,
            proc_root="/proc", state_write=lambda _value: None,
        )
        assert result == placement.REFUSED_IDENTITY_UNAVAILABLE

    @pytest.mark.parametrize("journal", [
        None,
        {},
        {"schema": 2},
        {"schema": 1, "token": "../bad"},
        {
            "schema": 1, "token": TOKEN, "scope_unit": SCOPE_UNIT,
            "gates_unit": "dev-gates.slice", "gates_cgroup": [],
        },
        {
            "schema": 1, "token": TOKEN, "scope_unit": SCOPE_UNIT,
            "gates_unit": "dev-gates.slice", "gates_cgroup": GATES_CGROUP,
            "scope_cgroup": ["outside"],
        },
        {
            "schema": 1, "token": TOKEN, "scope_unit": SCOPE_UNIT,
            "gates_unit": "dev-gates.slice", "gates_cgroup": GATES_CGROUP,
            "scope_cgroup": "/dev.slice/dev-gates.slice/" + SCOPE_UNIT,
            "leaf_cgroup": ["outside"],
        },
    ])
    def test_malformed_journal_is_refused_without_any_host_mutation(self, journal, tmp_path):
        root = _fake_cgroup_root(tmp_path)
        result = placement.recover_journal(
            journal,
            cgroup_root=str(root),
            gates_cgroup=GATES_CGROUP,
            proc_root="/proc",
            state_write=lambda _value: pytest.fail("invalid journal must not be rewritten"),
        )
        assert result == placement.REFUSED_STATE_UNAVAILABLE
        assert not list((root / "dev.slice" / "dev-gates.slice").glob("rg-profile-*.scope"))


class TestLeafReadingsAndKill:
    def test_leaf_readings_come_from_the_leaf(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)
        plc = _placement(root)
        plc.apply([101])
        (_leaf(root) / "memory.pressure").write_text(
            "some avg10=40.00 avg60=0.00 avg300=0.00 total=0\n"
            "full avg10=33.00 avg60=0.00 avg300=0.00 total=0\n"
        )
        assert plc.leaf_readings() == {"psi_full_avg10": 33.0, "memory_high_applied": True}

    def test_without_memory_high_nothing_is_throttling(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)
        plc = _placement(root, request=placement.PlacementRequest(cpu_weight=100))
        plc.apply([101])
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

    def test_incomplete_placement_is_refused_without_optional_logger(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)
        plc = _placement(root)
        plc.apply([101])
        kill_file = _leaf(root) / "cgroup.kill"
        kill_file.write_text("0")
        plc.error = placement.write_failed("simulated-incomplete-placement")

        assert plc.kill() is False
        assert kill_file.read_text() == "0"

    def test_a_preexisting_leaf_is_refused_even_without_caps(self, tmp_path):
        root = _fake_cgroup_root(tmp_path)
        def plant_preexisting_leaf(_unit):
            write_cgroup(root, f"{SCOPE_UNIT_CGROUP}/{LEAF_NAME}", cgroup_files())

        plc = _placement(
            root, request=placement.PlacementRequest(),
            scope_created=plant_preexisting_leaf,
        )
        plc.apply([101])
        assert plc.placed is False
        assert plc.error is not None
        assert _leaf(root).exists()

    def test_a_memory_max_equal_to_the_slice_ceiling_is_allowed(self, tmp_path):
        root = _fake_cgroup_root(tmp_path, slice_memory_max=SLICE_MAX)
        plc = _placement(root, request=placement.PlacementRequest(memory_max=SLICE_MAX))
        plc.apply([101])
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
    @pytest.mark.parametrize(
        "recovery_result",
        [None, placement.REFUSED_IDENTITY_UNAVAILABLE],
    )
    def test_startup_recovers_a_placement_journal_without_a_manifest(
        self, tmp_path, monkeypatch, recovery_result,
    ):
        root = _fake_cgroup_root(tmp_path)
        session_dir = tmp_path / "sessions" / SESSION_ID
        session_dir.mkdir(parents=True)
        journal = {"schema": 1, "state": "creating-scope", "token": TOKEN}
        journal_path = session_dir / "placement-state.json"
        journal_path.write_text(json.dumps(journal))
        calls: List[Any] = []

        def recover(record, **kwargs):
            calls.append((record, kwargs["cgroup_root"], kwargs["gates_cgroup"]))
            if recovery_result is None:
                kwargs["state_write"]({**record, "state": "complete"})
            return recovery_result

        monkeypatch.setattr(serve.placement_mod, "recover_journal", recover)
        _server(tmp_path, root)

        assert calls == [(journal, str(root), GATES_CGROUP)]
        result = json.loads((session_dir / "placement-recovery.json").read_text())
        assert result["session"] == SESSION_ID
        assert result["status"] == ("restored" if recovery_result is None else "failed")
        assert result["error"] == recovery_result
        assert not (session_dir / "manifest.json").exists()
        if recovery_result is None:
            assert json.loads(journal_path.read_text())["state"] == "complete"

    def test_non_object_manifest_does_not_hide_a_placement_journal(
        self, tmp_path, monkeypatch,
    ):
        root = _fake_cgroup_root(tmp_path)
        session_dir = tmp_path / "sessions" / SESSION_ID
        session_dir.mkdir(parents=True)
        (session_dir / "manifest.json").write_text("[]")
        journal = {"schema": 1, "state": "creating-scope", "token": TOKEN}
        journal_path = session_dir / "placement-state.json"
        journal_path.write_text(json.dumps(journal))
        calls: List[Any] = []

        def recover(record, **kwargs):
            calls.append(record)
            kwargs["state_write"]({**record, "state": "complete"})
            return None

        monkeypatch.setattr(serve.placement_mod, "recover_journal", recover)

        _server(tmp_path, root)

        assert calls == [journal]
        assert json.loads(journal_path.read_text())["state"] == "complete"
        recovery = json.loads((session_dir / "placement-recovery.json").read_text())
        assert recovery["status"] == "restored"
        assert recovery["error"] is None
        assert json.loads((session_dir / "manifest.json").read_text()) == []

    @pytest.mark.parametrize(
        "recovery_result",
        [None, placement.REFUSED_IDENTITY_UNAVAILABLE],
    )
    def test_startup_retries_incomplete_placement_for_finished_manifest(
        self, tmp_path, monkeypatch, recovery_result,
    ):
        root = _fake_cgroup_root(tmp_path)
        session_dir = tmp_path / "sessions" / SESSION_ID
        session_dir.mkdir(parents=True)
        manifest_path = session_dir / "manifest.json"
        manifest = {"session": SESSION_ID, "status": "finished"}
        manifest_path.write_text(json.dumps(manifest))
        journal = {"schema": 1, "state": "restoring-pids", "token": TOKEN}
        journal_path = session_dir / "placement-state.json"
        journal_path.write_text(json.dumps(journal))
        calls: List[Any] = []
        finalized: List[Any] = []

        def recover(record, **kwargs):
            calls.append(record)
            if recovery_result is None:
                kwargs["state_write"]({**record, "state": "complete"})
            return recovery_result

        monkeypatch.setattr(serve.placement_mod, "recover_journal", recover)
        monkeypatch.setattr(
            serve.SessionServer, "_finalize_orphan",
            lambda _self, session_id, recovered_manifest:
                finalized.append((session_id, recovered_manifest)),
        )
        _server(tmp_path, root)

        assert calls == [journal]
        assert finalized == []
        recovered_manifest = json.loads(manifest_path.read_text())
        assert recovered_manifest["status"] == "finished"
        assert recovered_manifest["placement_recovery"] == (
            {"status": "restored", "error": None}
            if recovery_result is None else
            {"status": "failed", "error": recovery_result}
        )
        if recovery_result is None:
            assert json.loads(journal_path.read_text())["state"] == "complete"

    def test_startup_retries_a_corrupt_journal_for_finished_manifest(
        self, tmp_path, monkeypatch,
    ):
        root = _fake_cgroup_root(tmp_path)
        session_dir = tmp_path / "sessions" / SESSION_ID
        session_dir.mkdir(parents=True)
        manifest_path = session_dir / "manifest.json"
        manifest_path.write_text(json.dumps({"session": SESSION_ID, "status": "finished"}))
        (session_dir / "placement-state.json").write_text("{not json")

        _server(tmp_path, root)

        manifest = json.loads(manifest_path.read_text())
        assert manifest["status"] == "finished"
        assert manifest["placement_recovery"] == {
            "status": "failed", "error": placement.REFUSED_STATE_UNAVAILABLE,
        }

    @pytest.mark.parametrize(
        ("journal_present", "recovery_error"),
        [
            (True, None),
            (True, placement.REFUSED_IDENTITY_UNAVAILABLE),
            (False, None),
        ],
        ids=["restored-journal", "failed-journal", "missing-requested-journal"],
    )
    def test_live_orphan_records_placement_recovery_in_summary(
        self, tmp_path, monkeypatch, journal_present, recovery_error,
    ):
        root = _fake_cgroup_root(tmp_path)
        session_dir = tmp_path / "sessions" / SESSION_ID
        session_dir.mkdir(parents=True)
        manifest_path = session_dir / "manifest.json"
        manifest_path.write_text(json.dumps({
            "session": SESSION_ID,
            "status": "live",
            "scope": "container",
            "container_id": "a" * 64,
            "cgroup": "/dev.slice/x.scope",
            "token": TOKEN,
            "slice_name": None,
            "interval_seconds": 1.0,
            "started_at": "2026-01-01T00:00:00Z",
            "ended_at": None,
            "damon_enabled": False,
            "damon_kdamond": None,
            "damon_thresholds": None,
            "damon_unavailable_reason": None,
            "meta": {},
            "aborted_reason": None,
            "placement": {"requested": True},
        }))
        journal_path = session_dir / "placement-state.json"
        if journal_present:
            journal_path.write_text(json.dumps({"schema": 1, "state": "placed"}))
            monkeypatch.setattr(
                serve.SessionServer, "_recover_placement_file",
                lambda _self, _session, _path: recovery_error,
            )
        (session_dir / "samples.jsonl").write_text(json.dumps({
            "seq": 0,
            "cg": {"/dev.slice/x.scope": {"mem": {"current": 100, "peak": 100}}},
            "pids": [1],
            "mono": 0.0,
        }) + "\n")
        (session_dir / "host.jsonl").write_text(
            json.dumps({"host": {}, "slice": None}) + "\n"
        )

        _server(tmp_path, root)

        effective_error = (
            recovery_error if journal_present else placement.REFUSED_STATE_UNAVAILABLE
        )
        expected_recovery = (
            {"status": "failed", "error": effective_error}
            if effective_error is not None else
            {"status": "restored", "error": None}
        )
        summary_doc = json.loads((session_dir / "summary.json").read_text())
        manifest = json.loads(manifest_path.read_text())
        assert summary_doc["placement_recovery"] == expected_recovery
        assert summary_doc["placement"] == {
            "requested": True,
            "leaf": None,
            "applied": {},
            "pids_moved": 0,
            "error": effective_error,
        }
        assert manifest["placement_recovery"] == expected_recovery
        assert manifest["aborted_reason"] == (
            "daemon-restarted-placement-recovery-failed"
            if effective_error is not None else "daemon-restarted"
        )

    def test_startup_does_not_revisit_completed_journal_for_finished_manifest(
        self, tmp_path, monkeypatch,
    ):
        root = _fake_cgroup_root(tmp_path)
        session_dir = tmp_path / "sessions" / SESSION_ID
        session_dir.mkdir(parents=True)
        (session_dir / "manifest.json").write_text(json.dumps({
            "session": SESSION_ID, "status": "finished",
        }))
        (session_dir / "placement-state.json").write_text(json.dumps({
            "schema": 1, "state": "complete", "token": TOKEN,
        }))
        calls: List[Any] = []
        monkeypatch.setattr(
            serve.placement_mod, "recover_journal",
            lambda *args, **kwargs: calls.append(args),
        )

        _server(tmp_path, root)

        assert calls == []

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
        success_root = _fake_cgroup_root(tmp_path / "success", procs="101\n")
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
            placement.LanePlacement, "_restore_owned_processes",
            lambda _lane: False,
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
        assert block["leaf"] == PLACEMENT_CGROUP
        assert block["applied"] == {
            "memory.high": MEMORY_HIGH, "memory.max": MEMORY_MAX, "cpu.weight": 100,
        }
        assert block["error"] is None
        assert _leaf(root).is_dir()

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
        assert stop["summary"]["placement"]["leaf"] == PLACEMENT_CGROUP
        assert not _leaf(root).exists()

    def test_the_gates_slice_snapshot_counts_the_live_leaf(self, tmp_path):
        """C5 wrote `leaves`/`sessions_live` off disk against a tree that
        could not yet have a leaf in it — this is the first test where one
        really exists."""
        root = _fake_cgroup_root(tmp_path, procs="101\n")
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
    def plant_symlink(_unit):
        (_leaf(root)).symlink_to(
            root / "dev.slice" / "dev-background.slice" / "victim"
        )
    plc = _placement(root, scope_created=plant_symlink)
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
