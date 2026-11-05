"""Semantic conformance of the Rust sampler against the Python collector.

Runs the built ``topos-sampler`` binary (``--once``: two ticks, no socket, no store) on a
fixture tree and compares its metric values with what ``topos.collect`` reads from the very
same tree, so the two implementations cannot drift while both exist (D-026).

The binary is not part of the wheel and not built by pytest. It is found via
``TOPOS_SAMPLER_BIN`` or at ``topos/sampler/topos-sampler`` / ``topos/sampler/target/*/release``.
Build it in a container: ``docker build -f sampler/Dockerfile --target artifact -o type=local,dest=<dir> .``
(from ``topos/``). When it is absent every test here is SKIPPED with that reason; the gate
report must say so.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
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
pytestmark = pytest.mark.skipif(
    BIN is None,
    reason="topos-sampler binary not found (set TOPOS_SAMPLER_BIN or build it: docker build -f sampler/Dockerfile --target artifact -o type=local,dest=sampler .)",
)

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
def tree(tmp_path_factory: pytest.TempPathFactory) -> dict:
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
    out = subprocess.run([str(BIN), "--once", str(cfg)], capture_output=True, text=True, timeout=60, check=True)
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
    assert set(tree["rust"]["entities"]) >= {_ent(k) for k in walk_entities(tree["root"] / "cg")}


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
