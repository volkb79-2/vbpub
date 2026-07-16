from __future__ import annotations

import shutil
from pathlib import Path

from conftest import fixture_root, systemctl_fixture_runner
from topos.collect.collector import Collector
from topos.config import ToposConfig

GAME_KEY = "system.slice/docker-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.scope"
PAKS_KEY = "soulmask.slice/soulmask-paks.slice"
FOOTGUN_KEY = "transient-footgun.slice"


def _collector(root: Path, runner_name: str, protected: tuple[str, ...] = ("soulmask-paks.slice",)) -> Collector:
    return Collector(
        cgroup_root=root,
        config=ToposConfig(interval=5.0, protected_services=protected),
        host_collector=lambda: {},
        now=lambda: 100.0,
        network_providers=(),
        systemctl_show_runner=systemctl_fixture_runner(runner_name),
    )


def test_origin_clean_runtime_dropin_band() -> None:
    frame = _collector(fixture_root() / "cgroupfs" / "gstammtisch", "gstammtisch").collect_once()
    game = frame.entities[GAME_KEY]
    assert game.governance is not None
    assert game.governance["summary"] == {
        "origin": "systemd_runtime_dropin",
        "drift": False,
        "severity": "none",
        "drifted_limits": [],
        "reasons": [],
        "unit": "docker-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.scope",
    }
    assert game.governance["limits"]["mem_high"]["origin"] == "systemd_runtime_dropin"
    assert game.governance["limits"]["mem_high"]["drift"] is False


def test_origin_flags_raw_write_drift_against_systemd_record(tmp_path: Path) -> None:
    root = tmp_path / "cg"
    shutil.copytree(fixture_root() / "cgroupfs" / "gstammtisch", root)
    (root / GAME_KEY / "memory.high").write_text("2147483648")
    frame = _collector(root, "gstammtisch").collect_once()
    game = frame.entities[GAME_KEY]
    assert game.metrics["governance_drift"].v == 1
    assert game.governance is not None
    assert game.governance["summary"]["severity"] == "warn"
    assert game.governance["limits"]["mem_high"]["origin"] == "raw_write"
    assert game.governance["limits"]["mem_high"]["severity"] == "warn"


def test_effective_memory_min_turns_red_when_ancestor_caps_protected_entity(tmp_path: Path) -> None:
    root = tmp_path / "cg"
    shutil.copytree(fixture_root() / "cgroupfs" / "gstammtisch", root)
    (root / "soulmask.slice" / "memory.min").write_text("0")
    frame = _collector(root, "gstammtisch").collect_once()
    paks = frame.entities[PAKS_KEY]
    assert paks.metrics["effective_memory_min"].v == 0
    assert paks.metrics["governance_drift"].v == 2
    assert paks.governance is not None
    assert paks.governance["summary"]["severity"] == "red"
    assert paks.governance["limits"]["mem_min"]["severity"] == "red"
    assert paks.governance["effective_memory_min"]["clamped_by"] == {"key": "soulmask.slice", "value": 0}


def test_transient_slice_without_unit_record_is_flagged_as_raw_write() -> None:
    frame = _collector(fixture_root() / "cgroupfs" / "transient-footgun", "footgun", protected=()).collect_once()
    footgun = frame.entities[FOOTGUN_KEY]
    assert footgun.governance is not None
    assert footgun.governance["summary"]["origin"] == "raw_write"
    assert footgun.governance["limits"]["mem_min"]["origin"] == "raw_write"
    assert footgun.governance["limits"]["mem_min"]["severity"] == "warn"
