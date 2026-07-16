from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import fixture_root, systemctl_fixture_runner
from topos.collect.collector import Collector
from topos.config import DamonConfig, ToposConfig
from topos.damon.control import APPROVAL_TEXT, NoFreeKdamond, RootRequired, stop_owned_sessions
from topos.damon.paddr import plan_start_paddr_session, start_paddr_session
from topos.model import MetricValue
from topos.ui.banner import render_banner
from topos.ui.hostmem import render_host_memory_text


def _host_stub() -> dict[str, MetricValue]:
    return {
        "host_mem_total": MetricValue(16000, "host"),
        "host_mem_available": MetricValue(8000, "host"),
        "host_swap_total": MetricValue(4000, "host"),
        "host_swap_free": MetricValue(2000, "host"),
        "host_swapcached": MetricValue(100, "host"),
        "host_zswap_pool": MetricValue(50, "host"),
        "host_zswap_stored": MetricValue(100, "host"),
        "host_zswap_ratio": MetricValue(2.0, "host"),
        "host_disk_swap": MetricValue(0, "host"),
        "host_load1": MetricValue(0.1, "host"),
        "host_load5": MetricValue(0.2, "host"),
        "host_load15": MetricValue(0.3, "host"),
        "host_uptime_s": MetricValue(1000, "host"),
        "host_psi_mem_some_avg10": MetricValue(0.0, "host"),
        "host_psi_mem_full_avg10": MetricValue(0.0, "host"),
        "host_psi_io_some_avg10": MetricValue(0.0, "host"),
        "host_psi_io_full_avg10": MetricValue(0.0, "host"),
        "host_psi_cpu_some_avg10": MetricValue(0.0, "host"),
        "host_zswap_enabled": MetricValue(1, "host"),
        "host_zswap_max_pool_percent": MetricValue(20, "host"),
    }


def _damon_root(tmp_path: Path, *, slots: tuple[str, ...] = ("off",)) -> Path:
    root = tmp_path / "kdamonds"
    root.mkdir(parents=True)
    (root / "nr_kdamonds").write_text(f"{len(slots)}\n")
    for idx, state in enumerate(slots):
        slot = root / str(idx)
        slot.mkdir()
        (slot / "state").write_text(f"{state}\n")
    return root


def _add_paddr_regions(damon_root: Path, idx: int, *, mtime: float = 90.0) -> None:
    tried = damon_root / str(idx) / "contexts" / "0" / "schemes" / "0" / "tried_regions"
    for region, start, end, accesses, age in (
        ("0", 0, 4096, 12, 1),
        ("1", 4096, 8192, 3, 2),
        ("2", 8192, 12288, 0, 5),
        ("3", 12288, 20480, 0, 20),
    ):
        region_dir = tried / region
        region_dir.mkdir(parents=True, exist_ok=True)
        (region_dir / "start").write_text(f"{start}\n")
        (region_dir / "end").write_text(f"{end}\n")
        (region_dir / "nr_accesses").write_text(f"{accesses}\n")
        (region_dir / "age").write_text(f"{age}\n")
    (tried / "total_bytes").write_text("20480\n")
    for path in tried.rglob("*"):
        if path.is_file():
            os.utime(path, (mtime, mtime))


def _collector(*, damon_root: Path, state_dir: Path | None = None, now: float = 100.0) -> Collector:
    return Collector(
        cgroup_root=fixture_root() / "cgroupfs" / "gstammtisch",
        config=ToposConfig(interval=5.0),
        docker_inspect=lambda _cid: None,
        host_collector=_host_stub,
        now=lambda: now,
        network_providers=(),
        proc_root=fixture_root() / "procfs" / "network",
        damon_root=damon_root,
        damon_state_dir=state_dir,
        systemctl_show_runner=systemctl_fixture_runner("gstammtisch"),
    )


def test_paddr_control_starts_owned_host_session_and_passive_reads_it(tmp_path: Path) -> None:
    damon_root = _damon_root(tmp_path)
    state_dir = tmp_path / "state"
    session = start_paddr_session(
        damon_root=damon_root,
        state_dir=state_dir,
        config=DamonConfig(),
        confirmed_text=APPROVAL_TEXT,
        now=lambda: 100.0,
        user="tester",
        require_root=False,
    )

    assert session.kdamond_idx == 0
    assert (damon_root / "0" / "contexts" / "0" / "operations").read_text().strip() == "paddr"
    assert (damon_root / "0" / "contexts" / "0" / "monitoring_attrs" / "intervals" / "sample_us").read_text().strip() == "400000"
    marker = json.loads((state_dir / "damon" / "kdamond-0.json").read_text())
    assert marker["mode"] == "paddr"
    assert json.loads((state_dir / "actions.log").read_text().splitlines()[0])["action"] == "damon-paddr-start"

    _add_paddr_regions(damon_root, session.kdamond_idx)
    frame = _collector(damon_root=damon_root, state_dir=state_dir).collect_once()
    assert frame.host["host_damon_hot_bytes"] == MetricValue(4096, "exact")
    assert frame.host["host_damon_warm_bytes"] == MetricValue(4096, "exact")
    assert frame.host["host_damon_cold_bytes"] == MetricValue(4096, "exact")
    assert frame.host["host_damon_idle_bytes"] == MetricValue(8192, "exact")
    assert frame.entities[""].damon["host_sessions"][0]["owner"] == "topos"

    stopped = stop_owned_sessions(damon_root=damon_root, state_dir=state_dir, all_mine=True, require_root=False)
    assert stopped == 1
    assert (damon_root / "0" / "state").read_text().strip() == "off"


def test_paddr_control_refuses_non_root_busy_and_duplicate(tmp_path: Path) -> None:
    with pytest.raises(RootRequired):
        plan_start_paddr_session(damon_root=_damon_root(tmp_path / "root"), state_dir=tmp_path / "state", config=DamonConfig(), is_root=lambda: False)
    with pytest.raises(NoFreeKdamond):
        plan_start_paddr_session(damon_root=_damon_root(tmp_path / "busy", slots=("on",)), state_dir=tmp_path / "busy-state", config=DamonConfig(), require_root=False)
    damon_root = _damon_root(tmp_path / "dup")
    state_dir = tmp_path / "dup-state"
    start_paddr_session(damon_root=damon_root, state_dir=state_dir, config=DamonConfig(), confirmed_text=APPROVAL_TEXT, require_root=False)
    with pytest.raises(NoFreeKdamond, match="paddr"):
        plan_start_paddr_session(damon_root=damon_root, state_dir=state_dir, config=DamonConfig(), require_root=False)


def test_paddr_cli_start_uses_fixture_root(tmp_path: Path) -> None:
    damon_root = _damon_root(tmp_path)
    state_dir = tmp_path / "state"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(fixture_root().parents[1] / "src")
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "topos.cli",
            "damon",
            "paddr",
            "start",
            "--damon-root",
            str(damon_root),
            "--state-dir",
            str(state_dir),
            "--confirm",
            APPROVAL_TEXT,
            "--allow-non-root-fixture",
        ],
        check=True,
        cwd=fixture_root().parents[1],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
    )
    assert proc.stdout.strip() == "started topos-owned paddr DAMON session on kdamond 0"
    assert (damon_root / "0" / "contexts" / "0" / "operations").read_text().strip() == "paddr"


def test_paddr_banner_and_host_memory_status_render_foreign_session(tmp_path: Path) -> None:
    damon_root = tmp_path / "kdamonds"
    src = fixture_root() / "damonfs" / "passive-paddr" / "kdamonds"
    import shutil

    shutil.copytree(src, damon_root)
    for path in (damon_root / "0" / "contexts" / "0" / "schemes" / "0" / "tried_regions").rglob("*"):
        if path.is_file():
            os.utime(path, (90.0, 90.0))
    frame = _collector(damon_root=damon_root).collect_once()

    banner = render_banner(frame, ToposConfig())
    heat_lines = [line for line in banner.lines if line.startswith("DRAM HEAT")]
    assert len(heat_lines) == 1
    assert "warm 4.0KiB/33.3%" in heat_lines[0]
    assert "idle 8.0KiB/66.7%" in heat_lines[0]
    assert "owner foreign" in heat_lines[0]

    text = render_host_memory_text(frame, config=ToposConfig(), damon_root=damon_root)
    assert "HOST MEMORY" in text
    assert "owner=foreign" in text
    assert "read-only foreign session" in text
