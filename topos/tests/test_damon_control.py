from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import fixture_root, systemctl_fixture_runner
from topos.collect.collector import Collector
from topos.config import DamonConfig, ToposConfig
from topos.damon.control import (
    APPROVAL_TEXT,
    DamonControlError,
    NoEntityPids,
    NoFreeKdamond,
    OwnershipError,
    RootRequired,
    StaleEntityPids,
    confirmation_text,
    plan_start_session,
    start_entity_session,
    start_planned_session,
    stop_owned_sessions,
)
from topos.model import MetricValue

GAME_KEY = "system.slice/docker-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.scope"


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


def _damon_root(tmp_path: Path, *, slots: tuple[str, ...] = ("on", "off")) -> Path:
    root = tmp_path / "kdamonds"
    root.mkdir(parents=True)
    (root / "nr_kdamonds").write_text(f"{len(slots)}\n")
    for idx, state in enumerate(slots):
        slot = root / str(idx)
        slot.mkdir()
        (slot / "state").write_text(f"{state}\n")
    return root


def _state_dir(tmp_path: Path) -> Path:
    return tmp_path / "state"


def _add_tried_regions(damon_root: Path, idx: int) -> None:
    tried = damon_root / str(idx) / "contexts" / "0" / "schemes" / "0" / "tried_regions"
    for region, start, end, accesses, age in (
        ("0", 0, 4096, 18, 1),
        ("1", 4096, 8192, 0, 80),
    ):
        region_dir = tried / region
        region_dir.mkdir(parents=True, exist_ok=True)
        (region_dir / "start").write_text(f"{start}\n")
        (region_dir / "end").write_text(f"{end}\n")
        (region_dir / "nr_accesses").write_text(f"{accesses}\n")
        (region_dir / "age").write_text(f"{age}\n")
    (tried / "total_bytes").write_text("8192\n")
    for path in tried.rglob("*"):
        if path.is_file():
            os.utime(path, (90.0, 90.0))


def _collector(*, damon_root: Path, now: float) -> Collector:
    return Collector(
        cgroup_root=fixture_root() / "cgroupfs" / "gstammtisch",
        config=ToposConfig(interval=5.0, tiers={"prod": ["system.slice"]}),
        docker_inspect=lambda _cid: None,
        host_collector=_host_stub,
        now=lambda: now,
        network_providers=(),
        proc_root=fixture_root() / "procfs" / "network",
        damon_root=damon_root,
        systemctl_show_runner=systemctl_fixture_runner("gstammtisch"),
    )


def test_start_writes_owned_vaddr_session_and_passive_reads_it(tmp_path: Path) -> None:
    damon_root = _damon_root(tmp_path)
    state_dir = _state_dir(tmp_path)
    session = start_entity_session(
        GAME_KEY,
        cgroup_root=fixture_root() / "cgroupfs" / "gstammtisch",
        damon_root=damon_root,
        state_dir=state_dir,
        config=DamonConfig(),
        confirmed_text=APPROVAL_TEXT,
        now=lambda: 100.0,
        user="tester",
        require_root=False,
    )
    assert session.kdamond_idx == 1
    assert (damon_root / "0" / "state").read_text().strip() == "on"
    assert (damon_root / "1" / "contexts" / "0" / "operations").read_text().strip() == "vaddr"
    assert (damon_root / "1" / "contexts" / "0" / "targets" / "nr_targets").read_text().strip() == "2"
    assert json.loads(session.marker_path.read_text())["owner"] == "topos"
    assert json.loads((state_dir / "actions.log").read_text().splitlines()[0])["action"] == "damon-start"

    _add_tried_regions(damon_root, session.kdamond_idx)
    frame = _collector(damon_root=damon_root, now=100.0).collect_once()
    game = frame.entities[GAME_KEY]
    assert game.metrics["damon_hot_bytes"].v == 4096
    assert game.metrics["damon_idle_bytes"].v == 4096
    assert game.damon is not None
    assert game.damon["sessions"][0]["target_pids"] == [1001, 1002]


def test_stop_owned_sessions_leaves_foreign_session_untouched(tmp_path: Path) -> None:
    damon_root = _damon_root(tmp_path)
    state_dir = _state_dir(tmp_path)
    start_entity_session(
        GAME_KEY,
        cgroup_root=fixture_root() / "cgroupfs" / "gstammtisch",
        damon_root=damon_root,
        state_dir=state_dir,
        config=DamonConfig(),
        confirmed_text=APPROVAL_TEXT,
        now=lambda: 100.0,
        require_root=False,
    )
    stopped = stop_owned_sessions(damon_root=damon_root, state_dir=state_dir, all_mine=True, now=lambda: 105.0, require_root=False)
    assert stopped == 1
    assert (damon_root / "0" / "state").read_text().strip() == "on"
    assert (damon_root / "1" / "state").read_text().strip() == "off"
    assert (damon_root / "1" / "contexts" / "nr_contexts").read_text().strip() == "0"
    assert not (state_dir / "damon" / "kdamond-1.json").exists()
    assert [json.loads(line)["action"] for line in (state_dir / "actions.log").read_text().splitlines()] == ["damon-start", "damon-stop"]


def test_refuses_non_root_no_free_slot_and_no_pids(tmp_path: Path) -> None:
    with pytest.raises(RootRequired):
        plan_start_session(
            GAME_KEY,
            cgroup_root=fixture_root() / "cgroupfs" / "gstammtisch",
            damon_root=_damon_root(tmp_path / "root-check"),
            state_dir=_state_dir(tmp_path),
            config=DamonConfig(),
            is_root=lambda: False,
        )
    with pytest.raises(NoFreeKdamond):
        plan_start_session(
            GAME_KEY,
            cgroup_root=fixture_root() / "cgroupfs" / "gstammtisch",
            damon_root=_damon_root(tmp_path / "busy", slots=("on", "on")),
            state_dir=_state_dir(tmp_path / "busy"),
            config=DamonConfig(),
            require_root=False,
        )
    cgroup_root = tmp_path / "cgroup"
    (cgroup_root / "empty.scope").mkdir(parents=True)
    (cgroup_root / "empty.scope" / "cgroup.procs").write_text("")
    with pytest.raises(NoEntityPids):
        plan_start_session("empty.scope", cgroup_root=cgroup_root, damon_root=_damon_root(tmp_path / "empty"), state_dir=_state_dir(tmp_path / "empty"), config=DamonConfig(), require_root=False)


def test_typed_confirmation_and_stale_pids_are_enforced(tmp_path: Path) -> None:
    cgroup_root = tmp_path / "cgroup"
    shutil.copytree(fixture_root() / "cgroupfs" / "gstammtisch", cgroup_root)
    damon_root = _damon_root(tmp_path)
    plan = plan_start_session(GAME_KEY, cgroup_root=cgroup_root, damon_root=damon_root, state_dir=_state_dir(tmp_path), config=DamonConfig(), require_root=False)
    assert "Type START" in confirmation_text(plan)
    with pytest.raises(Exception, match="typed confirmation"):
        start_planned_session(plan, confirmed_text="YES", require_root=False)
    (cgroup_root / GAME_KEY / "cgroup.procs").write_text("1001\n")
    with pytest.raises(StaleEntityPids):
        start_planned_session(plan, confirmed_text=APPROVAL_TEXT, require_root=False)


def test_cli_damon_stop_all_mine_uses_fixture_root(tmp_path: Path) -> None:
    damon_root = _damon_root(tmp_path)
    state_dir = _state_dir(tmp_path)
    start_entity_session(
        GAME_KEY,
        cgroup_root=fixture_root() / "cgroupfs" / "gstammtisch",
        damon_root=damon_root,
        state_dir=state_dir,
        config=DamonConfig(),
        confirmed_text=APPROVAL_TEXT,
        require_root=False,
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(fixture_root().parents[1] / "src")
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "topos.cli",
            "damon",
            "stop",
            "--all-mine",
            "--damon-root",
            str(damon_root),
            "--state-dir",
            str(state_dir),
            "--allow-non-root-fixture",
        ],
        check=True,
        cwd=fixture_root().parents[1],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
    )
    assert proc.stdout.strip() == "stopped 1 topos-owned DAMON session(s)"


def test_capacity_and_marker_ownership_fail_closed_before_writes(tmp_path: Path) -> None:
    # Capacity check fails before writes
    damon_root = _damon_root(tmp_path / "part1")
    state_dir = _state_dir(tmp_path / "part1")
    state_dir.mkdir(parents=True)
    (state_dir / "damon").mkdir()
    (state_dir / "damon" / "kdamond-99.json").write_text("{}")

    with pytest.raises(NoFreeKdamond):
        plan_start_session(
            GAME_KEY,
            cgroup_root=fixture_root() / "cgroupfs" / "gstammtisch",
            damon_root=damon_root,
            state_dir=state_dir,
            config=DamonConfig(max_concurrent_targets=1),
            require_root=False,
        )

    # Marker ownership check fails before writes
    damon_root2 = _damon_root(tmp_path / "part2")
    state_dir2 = _state_dir(tmp_path / "part2")
    plan = plan_start_session(
        GAME_KEY,
        cgroup_root=fixture_root() / "cgroupfs" / "gstammtisch",
        damon_root=damon_root2,
        state_dir=state_dir2,
        config=DamonConfig(),
        require_root=False,
    )
    marker_path = state_dir2 / "damon" / f"kdamond-{plan.kdamond_idx}.json"
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    marker_path.write_text("{}")

    with pytest.raises(OwnershipError):
        start_planned_session(plan, confirmed_text=APPROVAL_TEXT, require_root=False)
    assert json.loads(marker_path.read_text()) == {}


def test_stop_explicit_selection_foreign_owner_and_root_isolation(tmp_path: Path) -> None:
    damon_root = _damon_root(tmp_path)
    state_dir = _state_dir(tmp_path)

    # Ambiguous scope (no all_mine and no kdamond_idx)
    with pytest.raises(DamonControlError):
        stop_owned_sessions(
            damon_root=damon_root,
            state_dir=state_dir,
            all_mine=False,
            kdamond_idx=None,
            require_root=False,
        )

    # Foreign owner rejection
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "damon").mkdir(parents=True, exist_ok=True)
    marker_path = state_dir / "damon" / "kdamond-0.json"
    marker_path.write_text(json.dumps({"owner": "foreign", "kdamond_idx": 0, "damon_root": str(damon_root)}))

    with pytest.raises(OwnershipError):
        stop_owned_sessions(
            damon_root=damon_root,
            state_dir=state_dir,
            all_mine=True,
            require_root=False,
        )
    assert json.loads(marker_path.read_text())["owner"] == "foreign"

    # Root isolation (different damon_root in marker)
    marker_path.write_text(json.dumps({"owner": "topos", "kdamond_idx": 0, "damon_root": str(tmp_path / "other-root")}))

    result = stop_owned_sessions(
        damon_root=damon_root,
        state_dir=state_dir,
        all_mine=True,
        require_root=False,
    )
    assert result == 0
    assert json.loads(marker_path.read_text())["owner"] == "topos"


def test_missing_pids_and_unreadable_slot_count_fail_closed(tmp_path: Path) -> None:
    # Empty cgroup with no cgroup.procs fails with NoEntityPids
    cgroup_root = tmp_path / "cgroup_empty"
    (cgroup_root / "empty.scope").mkdir(parents=True)
    with pytest.raises(NoEntityPids):
        plan_start_session(
            "empty.scope",
            cgroup_root=cgroup_root,
            damon_root=_damon_root(tmp_path / "damon1"),
            state_dir=_state_dir(tmp_path / "state1"),
            config=DamonConfig(),
            require_root=False,
        )

    # Unreadable nr_kdamonds fails with NoFreeKdamond
    damon_root = _damon_root(tmp_path / "damon2")
    (damon_root / "nr_kdamonds").write_text("not-a-number\n")
    with pytest.raises(NoFreeKdamond):
        plan_start_session(
            GAME_KEY,
            cgroup_root=fixture_root() / "cgroupfs" / "gstammtisch",
            damon_root=damon_root,
            state_dir=_state_dir(tmp_path / "state2"),
            config=DamonConfig(),
            require_root=False,
        )


def test_reserved_slot_and_malformed_marker_handled_safely(tmp_path: Path) -> None:
    damon_root = _damon_root(tmp_path, slots=("off", "off"))
    state_dir = _state_dir(tmp_path)
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "damon").mkdir(parents=True, exist_ok=True)

    # Mark slot 0 as reserved
    (state_dir / "damon" / "kdamond-0.json").write_text("{}")
    # Add malformed marker that should be ignored
    (state_dir / "damon" / "kdamond-not-an-index.json").write_text("{}")

    plan = plan_start_session(
        GAME_KEY,
        cgroup_root=fixture_root() / "cgroupfs" / "gstammtisch",
        damon_root=damon_root,
        state_dir=state_dir,
        config=DamonConfig(),
        require_root=False,
    )
    assert plan.kdamond_idx == 1


def test_teardown_recreates_absent_context_state_safely(tmp_path: Path) -> None:
    damon_root = _damon_root(tmp_path, slots=("off",))
    state_dir = _state_dir(tmp_path)
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "damon").mkdir(parents=True, exist_ok=True)

    marker_data = {
        "owner": "topos",
        "kdamond_idx": 0,
        "damon_root": str(damon_root),
    }
    marker_path = state_dir / "damon" / "kdamond-0.json"
    marker_path.write_text(json.dumps(marker_data))

    result = stop_owned_sessions(
        damon_root=damon_root,
        state_dir=state_dir,
        all_mine=True,
        require_root=False,
    )
    assert result == 1
    assert not marker_path.exists()
    assert (damon_root / "0" / "contexts" / "nr_contexts").read_text().strip() == "0"


def test_nonpositive_pids_fail_closed(tmp_path: Path) -> None:
    cgroup_root = tmp_path / "cgroup"
    (cgroup_root / "nonpositive.scope").mkdir(parents=True)
    (cgroup_root / "nonpositive.scope" / "cgroup.procs").write_text("0\n-5\n")

    with pytest.raises(NoEntityPids):
        plan_start_session(
            "nonpositive.scope",
            cgroup_root=cgroup_root,
            damon_root=_damon_root(tmp_path / "damon"),
            state_dir=_state_dir(tmp_path / "state"),
            config=DamonConfig(),
            require_root=False,
        )
