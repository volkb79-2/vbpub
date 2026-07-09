from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from conftest import fixture_root
from groop.bpf_gate import report_to_jsonable, render_report, run_bpf_gate
from groop.cli import main


def proc_fixture() -> Path:
    return fixture_root() / "procfs" / "network"


def qdisc_stub(_argv: list[str]) -> str:
    return "\n".join(
        (
            "qdisc fq_codel 0: dev eth0 root refcnt 2",
            " Sent 1 bytes 1 pkt (dropped 0, overlimits 0 requeues 0)",
            " backlog 0b 0p requeues 0",
        )
    )


def test_bpf_gate_reports_safe_noop_and_blockers(tmp_path: Path) -> None:
    pin_root = tmp_path / "groop"
    pin_root.mkdir()
    with patch("groop.bpf_gate.shutil.which", return_value=None):
        report = run_bpf_gate(
            proc_root=proc_fixture(),
            pin_root=pin_root,
            command_runner=qdisc_stub,
            uid=1003,
        )

    assert report.blockers == ("bpftool is not installed", "uid 1003 is not root")
    assert report.baseline["source_label"] == "net:HOST"
    assert report.baseline["rx_bytes"] == 15100
    assert report.baseline["tx_bytes"] == 27100
    assert report.baseline["provider_status"]["qdisc"]["eth0"]["dropped"] == 0
    text = render_report(report)
    assert "BPF gate: safe no-op" in text
    assert "live BPF loading: blocked" in text


def test_bpf_gate_cli_json_smoke(tmp_path: Path, capsys) -> None:
    pin_root = tmp_path / "groop"
    pin_root.mkdir()
    with patch("groop.bpf_gate.shutil.which", return_value=None):
        with patch("groop.bpf_gate.os.geteuid", return_value=1003):
            exit_code = main([
                "bpf",
                "gate",
                "--proc-root",
                str(proc_fixture()),
                "--pin-root",
                str(pin_root),
                "--json",
            ])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["blockers"] == ["bpftool is not installed", "uid 1003 is not root"]
    assert payload["baseline"]["source_label"] == "net:HOST"
    assert payload["baseline"]["rx_bytes"] == 15100
    assert isinstance(payload["baseline"]["provider_status"], dict)
    with patch("groop.bpf_gate.shutil.which", return_value=None):
        report = run_bpf_gate(proc_root=proc_fixture(), pin_root=pin_root, command_runner=qdisc_stub, uid=1003)
    assert report_to_jsonable(report)["pin_root"] == str(pin_root)
