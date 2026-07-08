from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from conftest import fixture_root
from groop.config import GroopConfig
from groop.diag import annotate, pressure_breakdown
from groop.model import Entity, EntityFrame, Finding, Frame, MetricValue
from groop.record.replay import ReplayDriver
from groop.record.writer import RecordWriter

GAME_KEY = "system.slice/docker-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.scope"
PAKS_KEY = "soulmask.slice/soulmask-paks.slice"
CONFIG = GroopConfig()


def _base_metrics() -> dict[str, MetricValue]:
    return {
        "pressure": MetricValue(None, "unavail_kernel"),
        "ram": MetricValue(64, "exact"),
        "mem_high": MetricValue(128, "exact"),
        "psi_mem_full_avg10": MetricValue(0.0, "exact"),
        "psi_mem_some_avg10": MetricValue(0.0, "exact"),
        "psi_io_full_avg10": MetricValue(0.0, "exact"),
        "psi_io_some_avg10": MetricValue(0.0, "exact"),
        "psi_cpu_some_avg10": MetricValue(0.0, "exact"),
        "rf_d_per_s": MetricValue(0.0, "derived"),
        "rf_f_per_s": MetricValue(0.0, "derived"),
        "mem_events_high_per_s": MetricValue(0.0, "derived"),
        "mem_events_oom_kill_per_s": MetricValue(0.0, "derived"),
        "io_max_capped": MetricValue(0, "exact"),
        "sock": MetricValue(0, "exact"),
        "net_rx_pps": MetricValue(0.0, "netns"),
        "net_tx_pps": MetricValue(0.0, "netns"),
        "net_rx_bps": MetricValue(0.0, "netns"),
        "net_tx_bps": MetricValue(0.0, "netns"),
        "mem_min": MetricValue(0, "exact"),
        "mem_low": MetricValue(0, "exact"),
        "effective_memory_min": MetricValue(0, "derived"),
        "governance_drift": MetricValue(0, "derived"),
    }


def _frame(
    metrics: dict[str, MetricValue],
    *,
    protected: bool = False,
    governance: dict[str, object] | None = None,
    network: dict[str, object] | None = None,
    findings: list[Finding] | None = None,
) -> Frame:
    entity = Entity(key="svc.scope", kind="scope", parent="", tier="prod", is_protected=protected)
    entity_frame = EntityFrame(
        entity=entity,
        metrics={**_base_metrics(), **metrics},
        findings=list(findings or ()),
        governance=governance,
        network=network,
    )
    return Frame(1, 100.0, 5.0, {}, {"svc.scope": entity_frame})


def _finding_ids(frame: Frame) -> list[str]:
    return [finding.rule_id for finding in frame.entities["svc.scope"].findings]


def test_rule_protected_disk_refault_fires() -> None:
    frame = _frame({"rf_d_per_s": MetricValue(25.0, "derived")}, protected=True)
    annotate(frame, CONFIG)
    assert "protected_disk_refault" in _finding_ids(frame)


def test_rule_protected_file_refault_fires() -> None:
    frame = _frame({"rf_f_per_s": MetricValue(12.0, "derived")}, protected=True)
    annotate(frame, CONFIG)
    assert "protected_file_refault" in _finding_ids(frame)


def test_rule_memory_high_rising_fires() -> None:
    frame = _frame({"mem_events_high_per_s": MetricValue(1.2, "derived")})
    annotate(frame, CONFIG)
    assert "memory_high_rising" in _finding_ids(frame)


def test_rule_memory_high_user_visible_fires() -> None:
    frame = _frame(
        {
            "ram": MetricValue(512, "exact"),
            "mem_high": MetricValue(256, "exact"),
            "psi_mem_full_avg10": MetricValue(1.5, "exact"),
        }
    )
    annotate(frame, CONFIG)
    assert "memory_high_user_visible" in _finding_ids(frame)


def test_rule_io_cap_expected_throttle_fires() -> None:
    frame = _frame(
        {
            "psi_io_full_avg10": MetricValue(1.5, "exact"),
            "io_max_capped": MetricValue(1, "exact"),
        }
    )
    annotate(frame, CONFIG)
    assert "io_cap_expected_throttle" in _finding_ids(frame)


def test_rule_governance_drift_fires() -> None:
    frame = _frame(
        {},
        governance={
            "summary": {
                "origin": "raw_write",
                "drift": True,
                "severity": "warn",
                "drifted_limits": ["mem_high"],
                "reasons": ["systemd records MemoryHigh=1024 but the live cgroup has 2048"],
                "unit": "svc.scope",
            }
        },
    )
    annotate(frame, CONFIG)
    assert "governance_drift" in _finding_ids(frame)


def test_rule_socket_buffers_material_fires() -> None:
    frame = _frame(
        {
            "sock": MetricValue(300 * 1024 * 1024, "exact"),
            "net_rx_pps": MetricValue(4_000.0, "netns"),
            "net_tx_pps": MetricValue(2_000.0, "netns"),
        },
        network={"source_label": "net:NS", "confidence": "estimated", "aggregation": "exact", "unavailable_reason": None, "proto": None},
    )
    annotate(frame, CONFIG)
    assert "socket_buffers_material" in _finding_ids(frame)


def test_rule_host_netns_network_absent_fires() -> None:
    frame = _frame(
        {},
        network={"source_label": "net:N/A", "confidence": "n/a", "aggregation": "none", "unavailable_reason": "host netns", "proto": None},
    )
    annotate(frame, CONFIG)
    assert "host_netns_network_absent" in _finding_ids(frame)


def test_healthy_frame_has_no_findings_and_zero_pressure() -> None:
    frame = _frame({})
    annotate(frame, CONFIG)
    entity_frame = frame.entities["svc.scope"]
    assert entity_frame.findings == []
    assert entity_frame.metrics["pressure"].v == 0


def test_pressure_score_is_monotonic_for_more_psi_and_refaults() -> None:
    low = _frame({"psi_mem_some_avg10": MetricValue(1.0, "exact"), "rf_d_per_s": MetricValue(0.5, "derived")})
    high = _frame({"psi_mem_some_avg10": MetricValue(10.0, "exact"), "rf_d_per_s": MetricValue(25.0, "derived")})
    annotate(low, CONFIG)
    annotate(high, CONFIG)
    assert low.entities["svc.scope"].metrics["pressure"].v < high.entities["svc.scope"].metrics["pressure"].v


def test_breakdown_contributions_sum_to_pressure() -> None:
    frame = _frame(
        {
            "psi_mem_full_avg10": MetricValue(1.5, "exact"),
            "psi_io_some_avg10": MetricValue(7.0, "exact"),
            "rf_d_per_s": MetricValue(8.0, "derived"),
        }
    )
    annotate(frame, CONFIG)
    entity_frame = frame.entities["svc.scope"]
    contributions = pressure_breakdown(entity_frame, CONFIG)
    assert sum(int(item["contribution"]) for item in contributions) == entity_frame.metrics["pressure"].v


def test_cli_once_json_includes_pressure_and_findings() -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(fixture_root().parents[1] / "src")
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "groop.cli",
            "--once",
            "--json",
            "--cgroup-root",
            str(fixture_root() / "cgroupfs" / "gstammtisch"),
        ],
        check=True,
        cwd=fixture_root().parents[1],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
    )
    payload = json.loads(proc.stdout)
    assert payload["entities"][GAME_KEY]["metrics"]["pressure"][0] is not None
    assert payload["entities"][PAKS_KEY]["findings"]


def test_replay_recomputes_missing_diagnostics(tmp_path: Path) -> None:
    path = tmp_path / "missing-diag.jsonl"
    frame = _frame({"rf_d_per_s": MetricValue(25.0, "derived")}, protected=True)
    with RecordWriter(path, config=CONFIG, started_at=frame.ts) as writer:
        writer.write_frame(frame)
    replay = ReplayDriver.from_path(path, config=CONFIG)
    entity_frame = replay.current.entities["svc.scope"]
    assert entity_frame.metrics["pressure"].v and entity_frame.metrics["pressure"].v > 0
    assert _finding_ids(replay.current) == ["protected_disk_refault"]


def test_replay_keeps_existing_findings_but_fills_pressure(tmp_path: Path) -> None:
    path = tmp_path / "kept-diag.jsonl"
    frame = _frame(
        {"rf_d_per_s": MetricValue(25.0, "derived")},
        protected=True,
        findings=[Finding("already_present", "warn", "keep me")],
    )
    with RecordWriter(path, config=CONFIG, started_at=frame.ts) as writer:
        writer.write_frame(frame)
    replay = ReplayDriver.from_path(path, config=CONFIG)
    entity_frame = replay.current.entities["svc.scope"]
    assert [finding.rule_id for finding in entity_frame.findings] == ["already_present"]
    assert entity_frame.metrics["pressure"].v and entity_frame.metrics["pressure"].v > 0
