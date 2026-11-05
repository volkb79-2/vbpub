"""The real ``topos-sampler`` binary served over its socket into the client, adapter and source.

Uses the binary detection and fixture tree of ``test_sampler_conformance.py``. Skipped, with
the build command, when the binary is absent (the gate report must say so).
"""
from __future__ import annotations

import shutil
import subprocess
import time

import pytest
from test_sampler_conformance import BIN, CONFIG, FIXTURE_CG, _put

from topos.collect.cgroup import walk_entities
from topos.sampler_client import SamplerClient
from topos.sampler_source import SamplerSource
from topos.ui.columns import REGISTRY as COLUMNS
from topos.ui.columns import CellCtx

pytestmark = pytest.mark.skipif(
    BIN is None,
    reason="topos-sampler binary not found (set TOPOS_SAMPLER_BIN or build it: docker build -f sampler/Dockerfile --target artifact -o type=local,dest=sampler .)",
)


def test_real_sampler_to_frames(tmp_path) -> None:
    r = tmp_path
    shutil.copytree(FIXTURE_CG, r / "cg")
    _put(r / "cg" / "memory.swap.max", "0\n")
    p = r / "proc"
    _put(p / "stat", "cpu  100 0 50 800 10 5 5 0 0 0\ncpu0 50 0 25 400 5 2 3 0 0 0\nctxt 1000\nprocesses 500\nprocs_running 2\nprocs_blocked 1\n")
    _put(p / "meminfo", "MemTotal:       1000000 kB\nMemFree:         100000 kB\nMemAvailable:    500000 kB\nSwapCached:        1000 kB\nSwapTotal:      2000000 kB\nSwapFree:       1500000 kB\n")
    _put(p / "vmstat", "pgmajfault 10\n")
    _put(p / "loadavg", "0.50 0.40 0.30 1/100 999\n")
    for res in ("cpu", "memory", "io"):
        _put(p / "pressure" / res, "some avg10=1.50 avg60=1.00 avg300=0.50 total=1000\nfull avg10=0.25 avg60=1.00 avg300=0.50 total=5\n")
    (r / "sys").mkdir()
    cfg = r / "sampler.toml"
    cfg.write_text(CONFIG.format(r=r))
    proc = subprocess.Popen([str(BIN), str(cfg)], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        sock = r / "s.sock"
        for _ in range(500):  # wait for the socket to exist; a condition, not a timing claim
            if sock.exists() or proc.poll() is not None:
                break
            time.sleep(0.02)
        assert proc.poll() is None, proc.stderr.read().decode()
        source = SamplerSource(SamplerClient(sock, timeout_s=5.0), poll_s=0.02)
        stream = iter(source)
        frames = [next(stream) for _ in range(3)]
        stream.close()
    finally:
        proc.terminate()
        proc.wait(timeout=10)
    frame = frames[-1]
    assert source.hello["protocol"] == 1 and source.hello["cgroup_view"]["mode"] in ("host", "namespaced", "unknown")
    # The fixture tree keyed like the collector. A directory with no kernel file at all has no metric to
    # project, so a `fields` request omits it (a real cgroup always has files).
    def has_files(key: str) -> bool:
        return any(f.name != ".keep" for f in (r / "cg" / key).iterdir() if f.is_file())

    assert set(frame.entities) == {k for k in walk_entities(r / "cg") if k == "" or has_files(k)}
    root = frame.entities[""]
    assert root.metrics["swap_max"].v == 0 and COLUMNS["swap_max"].cell(CellCtx(root))[0] == "0"
    assert frame.host["host_mem_total"].v == 1_000_000 * 1024
    extras = frame.host_meta["sampler"]
    assert extras["scanned"] is False and source.overlay.blocked(frame) is None  # scan off: BLK stays n/a
    assert len(extras["cpus"]) == 1
    assert source.backfill == {}  # the store is disabled in this config: no history, no invented history
    assert frames[-1].ts > frames[0].ts
