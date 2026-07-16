from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from topos.config import ToposConfig
from topos.model import EntityFrame, Finding


@dataclass(frozen=True)
class RuleMatch:
    severity: str
    message: str
    remedy: str | None
    confidence: str


@dataclass(frozen=True)
class Rule:
    rule_id: str
    severity: str
    source_metrics: tuple[str, ...]
    evaluator: Callable[[EntityFrame, ToposConfig], RuleMatch | None]


def evaluate_rules(entity_frame: EntityFrame, config: ToposConfig) -> list[Finding]:
    findings: list[Finding] = []
    for rule in RULES:
        match = rule.evaluator(entity_frame, config)
        if match is None:
            continue
        findings.append(
            Finding(
                rule_id=rule.rule_id,
                severity=match.severity,
                message=match.message,
                remedy=match.remedy,
                source_metrics=rule.source_metrics,
                confidence=match.confidence,
            )
        )
    return findings


def _protected_disk_refault(entity_frame: EntityFrame, config: ToposConfig) -> RuleMatch | None:
    if not entity_frame.entity.is_protected:
        return None
    rf_d = _metric_value(entity_frame, "rf_d_per_s")
    if rf_d is None or rf_d <= 0:
        return None
    band = config.threshold_band("rf_d_per_s", tier=entity_frame.entity.tier, warn=1.0, crit=20.0)
    severity = "red" if rf_d >= band.crit else "warn"
    return RuleMatch(
        severity=severity,
        message=f"Protected service is refaulting anonymous memory from swap device at {rf_d:.1f}/s; backend may be disk, zram, or mixed according to host classification.",
        remedy="Check writeback pressure, preserve memory.min for the service, and avoid shrinking its protected working set.",
        confidence=_confidence(entity_frame, ("rf_d_per_s",)),
    )


def _protected_file_refault(entity_frame: EntityFrame, config: ToposConfig) -> RuleMatch | None:
    if not entity_frame.entity.is_protected:
        return None
    rf_f = _metric_value(entity_frame, "rf_f_per_s")
    if rf_f is None:
        return None
    band = config.threshold_band("rf_f_per_s", tier=entity_frame.entity.tier, warn=1.0, crit=10.0)
    if rf_f < band.warn:
        return None
    severity = "red" if rf_f >= band.crit else "warn"
    return RuleMatch(
        severity=severity,
        message=f"Latency-critical workload is sustaining file-cache refaults at {rf_f:.1f}/s; the file cache is too small.",
        remedy="Give the workload more file-cache headroom and do not lower swappiness to chase this symptom.",
        confidence=_confidence(entity_frame, ("rf_f_per_s",)),
    )


def _memory_high_rising(entity_frame: EntityFrame, config: ToposConfig) -> RuleMatch | None:
    rate = _metric_value(entity_frame, "mem_events_high_per_s")
    if rate is None or rate <= 0:
        return None
    band = config.threshold_band("mem_events_high_per_s", tier=entity_frame.entity.tier, warn=0.1, crit=1.0)
    severity = "red" if rate >= band.crit else "warn"
    return RuleMatch(
        severity=severity,
        message=f"memory.high is actively throttling this cgroup ({rate:.2f} high events/s).",
        remedy="Raise memory.high or reduce the reclaim pressure competing with this workload.",
        confidence=_confidence(entity_frame, ("mem_events_high_per_s",)),
    )


def _memory_high_user_visible(entity_frame: EntityFrame, config: ToposConfig) -> RuleMatch | None:
    ram = _metric_value(entity_frame, "ram")
    mem_high = _metric_value(entity_frame, "mem_high")
    psi_full = _metric_value(entity_frame, "psi_mem_full_avg10")
    if ram is None or mem_high is None or psi_full is None:
        return None
    if ram <= mem_high or psi_full <= 0:
        return None
    band = config.threshold_band("psi_full_avg10", tier=entity_frame.entity.tier, warn=1.0, crit=2.0)
    severity = "red" if psi_full >= band.warn else "warn"
    return RuleMatch(
        severity=severity,
        message=(
            f"memory.current ({int(ram)}) is above memory.high ({int(mem_high)}) and memory PSI full is {psi_full:.2f}; reclaim is user-visible."
        ),
        remedy="Raise memory.high, reduce anonymous pressure, or restore effective memory.min/low protection.",
        confidence=_confidence(entity_frame, ("ram", "mem_high", "psi_mem_full_avg10")),
    )


def _io_cap_expected_throttle(entity_frame: EntityFrame, config: ToposConfig) -> RuleMatch | None:
    psi_full = _metric_value(entity_frame, "psi_io_full_avg10")
    io_capped = _metric_value(entity_frame, "io_max_capped")
    if psi_full is None or io_capped != 1:
        return None
    band = config.threshold_band("psi_full_avg10", tier=entity_frame.entity.tier, warn=1.0, crit=2.0)
    if psi_full < band.warn:
        return None
    return RuleMatch(
        severity="info",
        message=f"I/O PSI full is {psi_full:.2f} while io.max is capped; this looks like intentional throttling rather than an unexplained fault.",
        remedy="Leave the cap in place if the tradeoff is acceptable, or revisit io.max for this workload.",
        confidence=_confidence(entity_frame, ("psi_io_full_avg10", "io_max_capped")),
    )


def _governance_drift(entity_frame: EntityFrame, _config: ToposConfig) -> RuleMatch | None:
    governance = entity_frame.governance or {}
    summary = governance.get("summary")
    if not isinstance(summary, dict) or not summary.get("drift"):
        return None
    severity = "red" if summary.get("severity") == "red" else "warn"
    reasons = summary.get("reasons")
    if isinstance(reasons, list) and reasons:
        detail = str(reasons[0])
    else:
        detail = "systemd's recorded limits no longer match the live cgroup values"
    return RuleMatch(
        severity=severity,
        message=f"Governance drift detected: {detail}.",
        remedy="Move the limit into a systemd unit or set-property drop-in and stop relying on unmanaged raw sysfs writes.",
        confidence="exact",
    )


def _socket_buffers_material(entity_frame: EntityFrame, config: ToposConfig) -> RuleMatch | None:
    sock = _metric_value(entity_frame, "sock")
    rx_pps = _metric_value(entity_frame, "net_rx_pps") or 0.0
    tx_pps = _metric_value(entity_frame, "net_tx_pps") or 0.0
    total_pps = rx_pps + tx_pps
    if sock is None or total_pps <= 0:
        return None
    sock_band = config.threshold_band("sock", tier=entity_frame.entity.tier, warn=64 * 1024 * 1024, crit=256 * 1024 * 1024)
    pps_band = config.threshold_band("net_pps", tier=entity_frame.entity.tier, warn=500.0, crit=5_000.0)
    if sock < sock_band.warn or total_pps < pps_band.warn:
        return None
    severity = "red" if sock >= sock_band.crit or total_pps >= pps_band.crit else "warn"
    return RuleMatch(
        severity=severity,
        message=f"Socket buffers are material ({int(sock)} bytes) alongside {total_pps:.0f} packets/s of network traffic.",
        remedy="Budget socket memory into this service's headroom and inspect burst size or queueing before reclaiming RAM from it.",
        confidence=_confidence(entity_frame, ("sock", "net_rx_pps", "net_tx_pps")),
    )


def _host_netns_na(entity_frame: EntityFrame, _config: ToposConfig) -> RuleMatch | None:
    network = entity_frame.network or {}
    if network.get("source_label") != "net:N/A" or network.get("unavailable_reason") != "host netns":
        return None
    return RuleMatch(
        severity="info",
        message="Per-row network attribution is intentionally absent because this cgroup shares the host network namespace.",
        remedy="Use the host network row for truth, or move the workload into a private network namespace for per-row attribution.",
        confidence="n/a",
    )


RULES = (
    Rule("protected_disk_refault", "warn", ("rf_d_per_s", "mem_min", "mem_low", "psi_mem_full_avg10"), _protected_disk_refault),
    Rule("protected_file_refault", "warn", ("rf_f_per_s", "file", "psi_mem_some_avg10"), _protected_file_refault),
    Rule("memory_high_rising", "warn", ("mem_events_high_per_s", "mem_high"), _memory_high_rising),
    Rule("memory_high_user_visible", "red", ("ram", "mem_high", "psi_mem_full_avg10"), _memory_high_user_visible),
    Rule("io_cap_expected_throttle", "info", ("psi_io_full_avg10", "io_max_capped"), _io_cap_expected_throttle),
    Rule("governance_drift", "warn", ("governance_drift", "effective_memory_min"), _governance_drift),
    Rule("socket_buffers_material", "warn", ("sock", "net_rx_pps", "net_tx_pps"), _socket_buffers_material),
    Rule("host_netns_network_absent", "info", ("net_rx_bps", "net_tx_bps"), _host_netns_na),
)


def _metric_value(entity_frame: EntityFrame, metric_name: str) -> float | int | None:
    metric = entity_frame.metrics.get(metric_name)
    return None if metric is None else metric.v


def _confidence(entity_frame: EntityFrame, metrics: tuple[str, ...]) -> str:
    values: list[str] = []
    network_confidence = str((entity_frame.network or {}).get("confidence") or "n/a")
    for metric_name in metrics:
        metric = entity_frame.metrics.get(metric_name)
        if metric is None or metric.v is None:
            continue
        if metric.src == "netns":
            values.append("estimated")
        elif metric.src == "host" and metric_name.startswith("net_"):
            values.append(network_confidence)
        else:
            values.append("exact")
    if not values:
        return "n/a"
    if "estimated" in values:
        return "estimated"
    return "exact"
