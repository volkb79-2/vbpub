"""Semantic conformance of the Rust sampler against the Python collector.

Two layers. ``--once`` (two ticks, no socket, no store) on a fixture tree is compared with what
``topos.collect`` reads from the very same tree, so the two implementations cannot drift while
both exist (D-026). The ``live`` tests run the daemon on a fixture, change the fixture between
two ticks and compare deltas, resets, process rows and events (S-13).

The binary is not part of the wheel and not built by pytest. It is found via
``TOPOS_SAMPLER_BIN`` or at ``topos/sampler/topos-sampler`` / ``topos/sampler/target/*/release``.
Build it in a container: ``docker build -f sampler/Dockerfile --target artifact -o type=local,dest=<dir> .``
(from ``topos/``). When it is absent every test here is SKIPPED with that reason, unless
``TOPOS_REQUIRE_SAMPLER=1`` is set (the gate lane sets it): then the missing binary FAILS.
A binary whose ``--version`` differs from ``sampler/Cargo.toml`` is stale and always fails.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import time
from pathlib import Path

import pytest

from topos.collect.cgroup import BlockStackClassifier, collect_cgroup, walk_entities
from topos.collect.host import collect_host

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_CG = ROOT / "tests" / "fixtures" / "cgroupfs" / "gstammtisch"


def _find_binary() -> Path | None:
    env = os.environ.get("TOPOS_SAMPLER_BIN")
    cands = [Path(env)] if env else []
    cands += [ROOT / "sampler" / "topos-sampler", ROOT / "sampler" / "target" / "x86_64-unknown-linux-musl" / "release" / "topos-sampler"]
    for c in cands:
        if c.is_file() and os.access(c, os.X_OK):
            return c
    return None


BIN = _find_binary()
NOT_FOUND = "topos-sampler binary not found (set TOPOS_SAMPLER_BIN or build it: docker build -f sampler/Dockerfile --target artifact -o type=local,dest=sampler .)"


def _cargo_version() -> str:
    m = re.search(r'^version\s*=\s*"([^"]+)"', (ROOT / "sampler" / "Cargo.toml").read_text(), re.M)
    assert m, "no version in sampler/Cargo.toml"
    return m.group(1)


@pytest.fixture(scope="module")
def sampler_bin() -> Path:
    """The binary under test: missing = skip (fail with TOPOS_REQUIRE_SAMPLER=1), stale = fail."""
    if BIN is None:
        if os.environ.get("TOPOS_REQUIRE_SAMPLER") == "1":
            pytest.fail(NOT_FOUND + " [TOPOS_REQUIRE_SAMPLER=1]")
        pytest.skip(NOT_FOUND)
    out = subprocess.run([str(BIN), "--version"], capture_output=True, text=True, timeout=30)
    want = f"topos-sampler {_cargo_version()}"
    if out.stdout.strip() != want:
        pytest.fail(f"stale binary {BIN}: --version says {out.stdout.strip()!r} (rc {out.returncode}), sampler/Cargo.toml says {want!r}")
    return BIN

CONFIG = """
[sampler]
tick_ms = 50
slow_every_s = 1
cgroup_root = "{r}/cg"
proc_root = "{r}/proc"
sys_root = "{r}/sys"
socket_path = "{r}/s.sock"
duration_s = 0
stats_path = ""
[process]
scan = false
top_cpu = 20
top_io = 20
cap = 64
full_sweep_every_s = 10
cmdline_max = 256
cmdline_backoff_ms = 30000
[events]
psi_cpu_some_avg10 = 25.0
psi_memory_some_avg10 = 10.0
psi_io_some_avg10 = 25.0
events_path = "{r}/ev.jsonl"
max_bytes = 1000000
[protocol]
max_request_bytes = 4096
max_response_bytes = 4000000
max_clients = 4
client_idle_ms = 1000
client_write_ms = 1000
[store]
enabled = false
tmpfs_dir = "{r}/tm"
cold_dir = "{r}/cold"
cold_cap_bytes = 1000000
require_tmpfs = false
writer_queue_bytes = 1048576
[[tier]]
name = "hot"
step_s = 1
keep_s = 60
segment_s = 10
medium = "tmpfs"
"""


def _put(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


@pytest.fixture(scope="module")
def tree(tmp_path_factory: pytest.TempPathFactory, sampler_bin: Path) -> dict:
    r = tmp_path_factory.mktemp("sampler-conf")
    shutil.copytree(FIXTURE_CG, r / "cg")
    # real shape: stacked dm device over vda, an (unknown) line, io.cost keys on the root
    _put(
        r / "cg" / "io.stat",
        "253:0 rbytes=100 wbytes=200 rios=3 wios=4 dbytes=0 dios=0 cost.usage=999\n"
        "254:0 rbytes=1000000 wbytes=2000000 rios=10 wios=20 dbytes=0 dios=0 cost.vrate=12.50 cost.usage=77\n"
        "(unknown) \n",
    )
    (r / "sys" / "dev" / "block" / "253:0" / "slaves" / "vda").mkdir(parents=True)
    (r / "sys" / "dev" / "block" / "254:0" / "slaves").mkdir(parents=True)
    _put(r / "cg" / "memory.swap.max", "0\n")
    p = r / "proc"
    _put(p / "stat", "cpu  100 0 50 800 10 5 5 0 0 0\ncpu0 50 0 25 400 5 2 3 0 0 0\nctxt 1000\nprocesses 500\nprocs_running 2\nprocs_blocked 1\n")
    _put(p / "meminfo", "MemTotal:       1000000 kB\nMemFree:         100000 kB\nMemAvailable:    500000 kB\nSwapCached:        1000 kB\nSwapTotal:      2000000 kB\nSwapFree:       1500000 kB\nZswap:             1000 kB\nZswapped:          3000 kB\n")
    _put(p / "vmstat", "pgmajfault 10\n")
    _put(p / "loadavg", "0.50 0.40 0.30 1/100 999\n")
    _put(p / "uptime", "1000.00 900.00\n")
    for res in ("cpu", "memory", "io"):
        _put(p / "pressure" / res, "some avg10=1.50 avg60=1.00 avg300=0.50 total=1000\nfull avg10=0.25 avg60=1.00 avg300=0.50 total=5\n")
    cfg = r / "sampler.toml"
    cfg.write_text(CONFIG.format(r=r))
    out = subprocess.run([str(sampler_bin), "--once", str(cfg)], capture_output=True, text=True, timeout=60, check=True)
    return {"root": r, "rust": json.loads(out.stdout), "stdout": out.stdout}


def _ent(key: str) -> str:
    return "cg:/" if key == "" else f"cg:/{key}"


def _same(py_v, rust: dict | None, what: str) -> None:
    """py_v is a Python MetricValue.v (None = not available); rust a value-object or None."""
    if py_v is None:
        assert rust is None or rust["s"] != "value", f"{what}: python has no value, rust says {rust}"
    else:
        assert rust is not None and rust["s"] == "value", f"{what}: python {py_v}, rust {rust}"
        assert rust["v"] == pytest.approx(py_v), what


def test_rust_output_is_a_complete_untruncated_response(tree: dict) -> None:
    d = tree["rust"]
    assert d["ok"] is True and d["truncated"] is False and d["protocol"] == 1
    walked = walk_entities(tree["root"] / "cg")
    assert len(walked) >= 4, "the fixture must have a tree; an empty walk would make these tests vacuous"
    assert set(tree["rust"]["entities"]) >= {_ent(k) for k in walked}


def test_cgroup_gauges_and_limits_match_python(tree: dict) -> None:
    cg = tree["root"] / "cg"
    stack = BlockStackClassifier(tree["root"] / "sys")
    ents = tree["rust"]["entities"]
    for key, entity in walk_entities(cg).items():
        s = collect_cgroup(cg, key, entity, stack)
        r = ents[_ent(key)]
        m = s.metrics
        for py, rs in (("ram", "mem.current"), ("anon", "mem.anon"), ("file", "mem.file"), ("shmem", "mem.shmem"), ("pids_current", "pids.current"), ("cpu_weight", "limit.cpu.weight")):
            _same(m[py].v, r.get(rs), f"{key or '/'}:{py}")
        for py, rs in (("mem_min", "limit.memory.min"), ("mem_low", "limit.memory.low"), ("mem_high", "limit.memory.high"), ("mem_max", "limit.memory.max"), ("cpu_quota_us", "limit.cpu.max.quota")):
            if m[py].src == "unlimited":
                assert r[rs]["s"] == "value" and r[rs]["v"] == -1, f"{key or '/'}:{py} unlimited is -1"
            else:
                _same(m[py].v, r.get(rs), f"{key or '/'}:{py}")
        _same(m["cpu_period_us"].v, r.get("limit.cpu.max.period"), f"{key or '/'}:period")
        for res, rs in (("mem", "memory"), ("io", "io"), ("cpu", "cpu")):
            for kind in ("some", "full"):
                _same(m[f"psi_{res}_{kind}_avg10"].v, r.get(f"psi.{rs}.{kind}.avg10"), f"{key or '/'}:psi {res} {kind}")


def test_cgroup_counters_match_python_raw_readings(tree: dict) -> None:
    cg = tree["root"] / "cg"
    stack = BlockStackClassifier(tree["root"] / "sys")
    ents = tree["rust"]["entities"]
    pairs = (
        ("cpu.stat:usage_usec", "cpu.usage_usec"),
        ("memory.events:oom_kill", "memev.oom_kill"),
        ("memory.stat:pgscan", "mem.pgscan"),
        ("io.stat:rbytes", "io.rbytes"),
        ("io.stat:wbytes", "io.wbytes"),
        ("io.stat:rios", "io.rios"),
        ("io.stat:wios", "io.wios"),
        ("io.stat:cost_usage", "io.cost.usage"),
    )
    checked = 0
    for key, entity in walk_entities(cg).items():
        s = collect_cgroup(cg, key, entity, stack)
        r = ents[_ent(key)]
        for py, rs in pairs:
            if py in s.raw_counters:
                assert r[rs]["raw"] == s.raw_counters[py], f"{key or '/'}:{py}"
                checked += 1
    assert checked >= 8
    # the stacked device (253:0) must be excluded by both implementations
    assert ents["cg:/"]["io.rbytes"]["raw"] == 1_000_000
    assert ents["cg:/"]["io.cost.vrate"]["v"] == 12.5


def test_host_values_match_python(tree: dict) -> None:
    host = collect_host(tree["root"] / "proc", tree["root"] / "sys")
    r = tree["rust"]["entities"]["host"]
    for py, rs in (("host_mem_total", "meminfo.MemTotal"), ("host_mem_available", "meminfo.MemAvailable"), ("host_swap_total", "meminfo.SwapTotal"), ("host_swap_free", "meminfo.SwapFree"), ("host_swapcached", "meminfo.SwapCached")):
        _same(host[py].v, r.get(rs), py)
    for py, rs in (("host_load1", "host.load1"), ("host_load5", "host.load5"), ("host_load15", "host.load15")):
        _same(host[py].v, r.get(rs), py)
    # zswap without debugfs: both fall back to meminfo Zswap / Zswapped
    _same(host["host_zswap_pool"].v, r.get("zswap.pool_bytes"), "zswap pool")
    _same(host["host_zswap_stored"].v, r.get("zswap.stored_bytes"), "zswap stored")
    assert r["zswap.debugfs_ok"]["s"] == "unavailable"


# ---------------------------------------------------------------- live daemon: deltas, resets, rows, events

LIVE_CONFIG = """
[sampler]
tick_ms = 1000
slow_every_s = 1
cgroup_root = "{r}/cg"
proc_root = "{r}/proc"
sys_root = "{r}/sys"
socket_path = "{r}/run/s.sock"
duration_s = 0
stats_path = ""
[process]
scan = true
top_cpu = 20
top_io = 20
cap = 64
full_sweep_every_s = 1
cmdline_max = 256
cmdline_backoff_ms = 30000
[events]
psi_cpu_some_avg10 = 25.0
psi_memory_some_avg10 = 10.0
psi_io_some_avg10 = 25.0
events_path = "{r}/state/events.jsonl"
max_bytes = 1000000
[protocol]
max_request_bytes = 4096
max_response_bytes = 4000000
max_clients = 4
client_idle_ms = 5000
client_write_ms = 2000
[store]
enabled = true
tmpfs_dir = "{r}/tm"
cold_dir = "{r}/state"
cold_cap_bytes = 100000000
require_tmpfs = false
writer_queue_bytes = 8388608
[[tier]]
name = "hot"
step_s = 1
keep_s = 120
segment_s = 30
medium = "tmpfs"
"""

IO_A = "253:0 rbytes=100 wbytes=200 rios=3 wios=4 dbytes=0 dios=0\n254:0 rbytes=1000000 wbytes=2000000 rios=10 wios=20 dbytes=0 dios=0\n"
IO_B = "253:0 rbytes=100 wbytes=200 rios=3 wios=4 dbytes=0 dios=0\n254:0 rbytes=400000 wbytes=2000050 rios=10 wios=21 dbytes=0 dios=0\n"
STAT_A = "cpu  100 0 50 800 10 5 5 0 0 0\ncpu0 50 0 25 400 5 2 3 0 0 0\nctxt 1000\nprocesses 500\nprocs_running 2\nprocs_blocked 1\n"
STAT_B = "cpu  200 0 100 900 4 9 9 0 0 0\ncpu0 100 0 50 450 2 4 6 0 0 0\nctxt 3000\nprocesses 510\nprocs_running 2\nprocs_blocked 1\n"


def _pstat(pid: int, comm: str, state: str, ut: int, wb_blk: int = 0) -> str:
    f = ["0"] * 46
    f[0], f[11], f[17], f[19], f[21], f[39] = state, str(ut), "1", "77", "10", str(wb_blk)
    return f"{pid} ({comm}) {' '.join(f)}\n"


def _atomic(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".new")
    tmp.write_text(text)
    os.replace(tmp, p)


def _proc(root: Path, pid: int, comm: str, state: str, ut: int, wb: int, cg: str) -> None:
    d = root / "proc" / str(pid)
    _atomic(d / "stat", _pstat(pid, comm, state, ut))
    _atomic(d / "io", f"rchar: 0\nread_bytes: 0\nwrite_bytes: {wb}\n")
    _atomic(d / "schedstat", "100 2000000 3\n")
    _atomic(d / "cgroup", f"0::{cg}\n")
    _atomic(d / "cmdline", f"{comm}\0--flag\0")


class Live:
    def __init__(self, root: Path, proc: subprocess.Popen) -> None:
        self.root, self.proc, self.sock = root, proc, str(root / "run" / "s.sock")

    def rpc(self, req: dict) -> dict:
        s = socket.socket(socket.AF_UNIX)
        s.settimeout(20)
        s.connect(self.sock)
        s.sendall(json.dumps(req).encode() + b"\n")
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(1 << 20)
            if not chunk:
                break
            buf += chunk
        s.close()
        return json.loads(buf)

    def wait(self, what: str, cond, timeout: float = 40.0):  # noqa: ANN001
        end = time.monotonic() + timeout
        last = None
        while time.monotonic() < end:
            if self.proc.poll() is not None:
                pytest.fail(f"sampler exited ({self.proc.returncode}) while waiting for {what}")
            try:
                last = cond()
            except (OSError, ValueError, KeyError):
                last = None
            if last:
                return last
            time.sleep(0.1)
        pytest.fail(f"timed out waiting for {what}; last: {last!r}")

    def ticks(self) -> int:
        return int(self.rpc({"req": "status"})["ticks"])

    def points(self, entity: str, metric: str) -> list:
        r = self.rpc({"req": "series", "entity": entity, "metric": metric, "tier": "hot", "window_s": 120})
        return r["points"] if r.get("ok") else []


@pytest.fixture(scope="module")
def live(tmp_path_factory: pytest.TempPathFactory, sampler_bin: Path):  # noqa: ANN201
    r = tmp_path_factory.mktemp("sampler-live")
    shutil.copytree(FIXTURE_CG, r / "cg")
    _put(r / "cg" / "io.stat", IO_A)
    for dev, slaves in (("253:0", True), ("254:0", False)):
        (r / "sys" / "dev" / "block" / dev / "slaves").mkdir(parents=True)
        if slaves:
            (r / "sys" / "dev" / "block" / dev / "slaves" / "vda").mkdir()
    p = r / "proc"
    _put(p / "stat", STAT_A)
    _put(p / "meminfo", "MemTotal:       1000000 kB\nMemFree:         100000 kB\nMemAvailable:    500000 kB\n")
    _put(p / "vmstat", "pgmajfault 10\n")
    _put(p / "loadavg", "0.50 0.40 0.30 1/100 999\n")
    for res in ("cpu", "memory", "io"):
        _put(p / "pressure" / res, "some avg10=1.50 avg60=1.00 avg300=0.50 total=1000\nfull avg10=0.25 avg60=1.00 avg300=0.50 total=5\n")
    _put(p / "sys" / "kernel" / "task_delayacct", "0\n")
    _put(p / "self" / "stat", _pstat(1, "topos-sampler", "S", 5))
    _put(p / "self" / "statm", "1000 250 10 0 0 0 0\n")
    _put(p / "self" / "cgroup", "0::/system.slice/topos.service\n")
    _proc(r, 100, "busy", "R", 0, 0, "/system.slice")
    _proc(r, 300, "stuck", "D", 0, 0, "/system.slice")
    cfg = r / "sampler.toml"
    cfg.write_text(LIVE_CONFIG.format(r=r))
    proc = subprocess.Popen([str(sampler_bin), str(cfg)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    lv = Live(r, proc)
    try:
        lv.wait("the socket", lambda: Path(lv.sock).exists() and lv.ticks() >= 3)
        yield lv
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture(scope="module")
def changed(live: Live) -> dict:
    """Read the fixture with the Python collector, change it between two ticks, read it again."""
    cg = live.root / "cg"
    stack = BlockStackClassifier(live.root / "sys")
    before = collect_cgroup(cg, "", "cg:/", stack).raw_counters
    t0 = live.ticks()
    cpu_stat = (cg / "cpu.stat").read_text().splitlines()
    new = []
    for line in cpu_stat:
        k, _, v = line.partition(" ")
        new.append(f"{k} {int(v) + 500}" if k == "usage_usec" else line)
    _atomic(cg / "cpu.stat", "\n".join(new) + "\n")
    _atomic(cg / "io.stat", IO_B)  # rbytes of the bottom device goes BACKWARDS: a reset
    _atomic(cg / "memory.events", "low 0\nhigh 1\nmax 0\noom 2\noom_kill 2\n")
    _atomic(live.root / "proc" / "stat", STAT_B)  # iowait 10 -> 4: backwards on a NO_HZ kernel
    _proc(live.root, 100, "busy", "R", 200, 0, "/system.slice")
    _proc(live.root, 300, "stuck", "D", 0, 3_000_000, "/system.slice")
    after = collect_cgroup(cg, "", "cg:/", stack).raw_counters
    live.wait("three more ticks", lambda: live.ticks() >= t0 + 3)
    return {"before": before, "after": after}


def test_live_counter_deltas_equal_the_difference_of_the_python_readings(live: Live, changed: dict) -> None:
    b, a = changed["before"], changed["after"]
    want = a["cpu.stat:usage_usec"] - b["cpu.stat:usage_usec"]
    assert want == 500

    def total() -> bool:
        pts = [p for p in live.points("cg:/", "cpu.usage_usec") if isinstance(p[1], (int, float))]
        return sum(p[1] for p in pts) == want and bool(pts)

    live.wait("the +500 delta in the stored series", total)
    pts = live.points("cg:/", "cpu.usage_usec")
    assert sum(1 for p in pts if isinstance(p[1], (int, float)) and p[1] == want) == 1, "exactly one tick carries the change"


def test_live_a_backwards_counter_is_a_reset_state_not_a_spike(live: Live, changed: dict) -> None:
    b, a = changed["before"], changed["after"]
    assert a["io.stat:rbytes"] < b["io.stat:rbytes"], "the fixture really goes backwards"

    def reset_seen() -> bool:
        return any(isinstance(p[1], dict) and p[1].get("why") == "counter_reset" for p in live.points("cg:/", "io.rbytes"))

    live.wait("a counter_reset point", reset_seen)
    pts = live.points("cg:/", "io.rbytes")
    assert all(not isinstance(p[1], (int, float)) or p[1] < b["io.stat:rbytes"] for p in pts), f"no spike of the whole counter: {pts}"
    assert live.rpc({"req": "hello"})["state_codes"][12]["why"] == "counter_reset"


def test_live_iowait_going_backwards_is_clamped_to_zero(live: Live, changed: dict) -> None:
    def seen() -> bool:
        pts = live.points("cpu:all", "cpu.iowait")
        return len(pts) >= 5

    live.wait("cpu:all iowait points", seen)
    vals = [p[1] for p in live.points("cpu:all", "cpu.iowait") if not isinstance(p[1], dict)]
    assert len(vals) >= 3 and all(isinstance(v, (int, float)) and 0 <= v <= 6 for v in vals), vals


def test_live_process_rows_and_cmdline_come_from_the_fixture(live: Live, changed: dict) -> None:
    def rows() -> dict | None:
        cur = live.rpc({"req": "current"})
        got = {p["pid"]: p for p in cur["procs"]}
        ok = 300 in got and got[300].get("cmdline_state", {}).get("s") == "value"
        return got if ok else None

    # pid 100 is in the union only in the one tick where its CPU time rose; pid 300 (D) always
    got = live.wait("process rows with a command line", rows)
    stuck = got[300]
    assert (stuck["why"], stuck["state"], stuck["d_tasks"]) == ("D", "D", 1)
    assert stuck["comm"] == "stuck" and stuck["cmdline"] == "stuck --flag" and stuck["cgroup"] == "cg:/system.slice"
    assert stuck["rss_bytes"] > 0 and stuck["threads"] == 1


def test_live_events_come_from_the_fixture(live: Live, changed: dict) -> None:
    def kinds() -> list | None:
        ev = live.rpc({"req": "events", "window_s": 300})["events"]
        oom = [e for e in ev if e["type"] == "oom_kill"]
        return oom or None

    oom = live.wait("the oom_kill event", kinds)
    assert len(oom) == 1 and oom[0]["entity"] == "cg:/" and oom[0]["detail"]["delta"] == 2, oom
