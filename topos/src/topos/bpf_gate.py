from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from topos.model import Entity
from topos.providers.net_host import NetHostProvider

CommandRunner = Callable[[list[str]], str]


@dataclass(frozen=True)
class BpfGateReport:
    uid: int
    bpftool: str | None
    pin_root: str
    pin_root_writable: bool
    blockers: tuple[str, ...]
    probe_commands: tuple[str, ...]
    live_commands: tuple[str, ...]
    baseline: dict[str, Any]


def run_bpf_gate(
    *,
    proc_root: Path = Path("/proc"),
    pin_root: Path = Path("/sys/fs/bpf/topos"),
    command_runner: CommandRunner | None = None,
    uid: int | None = None,
    bpftool_path: str | None = None,
) -> BpfGateReport:
    probe_uid = os.geteuid() if uid is None and hasattr(os, "geteuid") else (os.getuid() if uid is None else uid)
    probe_bpftool = shutil.which("bpftool") if bpftool_path is None else bpftool_path
    try:
        pin_root_writable = pin_root.exists() and os.access(pin_root, os.W_OK)
    except OSError:
        pin_root_writable = False
    blockers: list[str] = []
    if probe_bpftool is None:
        blockers.append("bpftool is not installed")
    if probe_uid != 0:
        blockers.append(f"uid {probe_uid} is not root")
    if not pin_root_writable:
        blockers.append(f"{pin_root} is not writable")

    root_entity = Entity(key="", kind="root", parent=None)
    provider = NetHostProvider(proc_root=proc_root, command_runner=command_runner)
    sample = provider.collect({"": root_entity}).get("")
    baseline = {
        "source_label": sample.source_label if sample is not None else "net:N/A",
        "confidence": sample.confidence if sample is not None else "n/a",
        "aggregation": sample.aggregation if sample is not None else "none",
        "rx_bytes": sample.rx_bytes if sample is not None else None,
        "tx_bytes": sample.tx_bytes if sample is not None else None,
        "rx_pkts": sample.rx_pkts if sample is not None else None,
        "tx_pkts": sample.tx_pkts if sample is not None else None,
        "proto": sample.proto if sample is not None else None,
        "unavailable_reason": sample.unavailable_reason if sample is not None else "missing baseline sample",
        "provider_status": provider.status(),
    }
    return BpfGateReport(
        uid=probe_uid,
        bpftool=probe_bpftool,
        pin_root=str(pin_root),
        pin_root_writable=pin_root_writable,
        blockers=tuple(blockers),
        probe_commands=(
            "id -u",
            "command -v bpftool",
            "mount | grep ' /sys/fs/bpf '",
            "topos bpf gate --proc-root <fixture-or-live-proc> --json",
        ),
        live_commands=(
            "bpftool prog load <ingress.o> /sys/fs/bpf/topos/<name>/ingress",
            "bpftool prog load <egress.o> /sys/fs/bpf/topos/<name>/egress",
            "bpftool cgroup attach /sys/fs/cgroup cgroup_skb ingress pinned /sys/fs/bpf/topos/<name>/ingress",
            "bpftool cgroup attach /sys/fs/cgroup cgroup_skb egress pinned /sys/fs/bpf/topos/<name>/egress",
        ),
        baseline=baseline,
    )


def report_to_jsonable(report: BpfGateReport) -> dict[str, Any]:
    return {
        "uid": report.uid,
        "bpftool": report.bpftool,
        "pin_root": report.pin_root,
        "pin_root_writable": report.pin_root_writable,
        "blockers": list(report.blockers),
        "probe_commands": list(report.probe_commands),
        "live_commands": list(report.live_commands),
        "baseline": report.baseline,
    }


def render_report(report: BpfGateReport) -> str:
    lines = [
        "BPF gate: safe no-op",
        f"uid: {report.uid}",
        f"bpftool: {report.bpftool or 'missing'}",
        f"pin root: {report.pin_root} writable={report.pin_root_writable}",
    ]
    if report.blockers:
        lines.append("live BPF loading: blocked")
        lines.extend(f"  - {blocker}" for blocker in report.blockers)
    else:
        lines.append("live BPF loading: not attempted")
    baseline = report.baseline
    lines.append(
        "baseline: "
        f"rx={baseline.get('rx_bytes')}B "
        f"tx={baseline.get('tx_bytes')}B "
        f"rx_pkts={baseline.get('rx_pkts')} "
        f"tx_pkts={baseline.get('tx_pkts')} "
        f"source={baseline.get('source_label')} "
        f"confidence={baseline.get('confidence')}"
    )
    provider_status = baseline.get("provider_status")
    if isinstance(provider_status, dict) and provider_status.get("errors"):
        lines.append(f"provider errors: {provider_status['errors']}")
    lines.append("probe commands:")
    lines.extend(f"  {cmd}" for cmd in report.probe_commands)
    lines.append("planned live commands:")
    lines.extend(f"  {cmd}" for cmd in report.live_commands)
    return "\n".join(lines)
