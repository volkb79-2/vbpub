from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Static

from groop.collect.procs import list_processes
from groop.config import GroopConfig
from groop.diag import pressure_breakdown
from groop.model import EntityFrame, Frame
from groop.record.ring import HistoryRing
from groop.registry import REGISTRY


class DrillDownScreen(Screen[None]):
    BINDINGS = (
        Binding("escape", "close", "Back"),
        Binding("q", "close", "Back", show=False),
    )

    def __init__(
        self,
        frame: Frame,
        entity_key: str,
        *,
        config: GroopConfig,
        ring: HistoryRing,
        cgroup_root: Path,
        proc_root: Path,
    ) -> None:
        super().__init__()
        self.frame = frame
        self.entity_key = entity_key
        self.config = config
        self.ring = ring
        self.cgroup_root = cgroup_root
        self.proc_root = proc_root

    def compose(self):
        yield VerticalScroll(Static(id="drill-body"))

    def on_mount(self) -> None:
        self.query_one("#drill-body", Static).update(
            render_drill_text(
                self.frame,
                self.entity_key,
                config=self.config,
                ring=self.ring,
                cgroup_root=self.cgroup_root,
                proc_root=self.proc_root,
            )
        )

    def action_close(self) -> None:
        self.dismiss(None)


def render_drill_text(frame: Frame, entity_key: str, *, config: GroopConfig, ring: HistoryRing, cgroup_root: Path, proc_root: Path) -> str:
    entity_frame = frame.entities[entity_key]
    entity = entity_frame.entity
    lines = [
        f"DETAIL {entity_key or '/'}",
        f"name: {entity.docker.name if entity.docker is not None else (entity_key.rsplit('/', 1)[-1] if entity_key else '/')}",
        f"tier: {entity.tier or '-'} | protected: {'yes' if entity.is_protected else 'no'} | docker: {'yes' if entity.docker is not None else 'no'}",
        "",
        "METRICS",
    ]
    for title, names in _metric_groups(entity_frame).items():
        lines.append(f"[{title}]")
        for name in names:
            metric = entity_frame.metrics.get(name)
            spec = REGISTRY.get(name)
            lines.append(f"  {name:<24} {_format_metric(metric)} [{metric.src if metric is not None else 'n/a'}]{'' if spec is None else f' {spec.unit}'}")
        lines.append("")

    lines.extend(_governance_block(entity_frame))
    lines.append("")
    lines.extend(_network_block(entity_frame))
    lines.append("")
    lines.extend(_pressure_block(entity_frame, config))
    lines.append("")
    lines.extend(_history_block(entity_key, ring))
    lines.append("")
    lines.extend(_findings_block(entity_frame))
    lines.append("")
    lines.extend(_process_block(cgroup_root, entity_key, proc_root))
    return "\n".join(lines)


def _metric_groups(entity_frame: EntityFrame) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = defaultdict(list)
    for name in sorted(entity_frame.metrics):
        if name.startswith(("ram", "anon", "file", "shmem", "sock", "z_", "swap_", "rf_", "mem_", "headroom_", "effective_memory_min")):
            groups["memory"].append(name)
        elif name.startswith(("cpu", "psi_cpu")):
            groups["cpu"].append(name)
        elif name.startswith(("io_", "psi_io", "pg", "restore_")):
            groups["io"].append(name)
        elif name.startswith(("net_", "pressure")):
            groups["network"].append(name)
        elif name.startswith(("governance_", "pids_")):
            groups["governance"].append(name)
        else:
            groups["other"].append(name)
    return dict(groups)


def _governance_block(entity_frame: EntityFrame) -> list[str]:
    governance = entity_frame.governance or {}
    summary = governance.get("summary", {})
    lines = [
        "GOVERNANCE",
        f"  origin: {summary.get('origin', '-')}",
        f"  drift: {summary.get('drift', False)} severity={summary.get('severity', '-')}",
    ]
    limits = governance.get("limits", {})
    for name in ("mem_min", "mem_low", "mem_high", "mem_max", "cpu_weight", "io_weight"):
        detail = limits.get(name)
        if isinstance(detail, dict):
            lines.append(
                f"  {name:<10} live={detail.get('live_value')} recorded={detail.get('recorded_value')} "
                f"origin={detail.get('origin')} severity={detail.get('severity')}"
            )
    return lines


def _network_block(entity_frame: EntityFrame) -> list[str]:
    network = entity_frame.network or {}
    source = network.get("source_label", "net:N/A")
    confidence = network.get("confidence", "n/a")
    aggregation = network.get("aggregation", "none")
    reason = network.get("unavailable_reason")
    lines = [
        "NETWORK",
        f"  source: [{source}] [{confidence}] [{aggregation}]",
    ]
    if reason:
        lines.append(f"  reason: {reason}")
    proto = network.get("proto")
    if proto:
        lines.append(f"  proto: {proto}")
    return lines


def _pressure_block(entity_frame: EntityFrame, config) -> list[str]:
    lines = ["PRESSURE BREAKDOWN"]
    for item in pressure_breakdown(entity_frame, config):
        thresholds = item["thresholds"]
        threshold_text = ""
        if isinstance(thresholds, dict):
            threshold_text = f" warn={thresholds['warn']} crit={thresholds['crit']}"
        lines.append(
            f"  {str(item['label']):<22} +{int(item['contribution']):>2} "
            f"value={item['value']} [{item['src']}/{item['confidence']}]{threshold_text}"
        )
    return lines


def _history_block(entity_key: str, ring: HistoryRing) -> list[str]:
    tracked = ("rf_d_per_s", "cpu_pct", "ram")
    lines = ["HISTORY"]
    for name in tracked:
        if ring.has_series(entity_key, name):
            lines.append(f"  {name:<12} {_sparkline(ring.last(entity_key, name, 16))}")
        else:
            lines.append(f"  {name:<12} no history")
    return lines


def _findings_block(entity_frame: EntityFrame) -> list[str]:
    if not entity_frame.findings:
        return ["FINDINGS", "  none"]
    lines = ["FINDINGS"]
    for finding in entity_frame.findings:
        remedy = f" | remedy: {finding.remedy}" if finding.remedy else ""
        lines.append(f"  [{finding.severity}] {finding.message}{remedy}")
    return lines


def _process_block(cgroup_root: Path, entity_key: str, proc_root: Path) -> list[str]:
    rows = list_processes(cgroup_root, entity_key, proc_root)
    rows.sort(key=lambda row: row.get("rss") or 0, reverse=True)
    lines = ["PROCESSES"]
    if not rows:
        lines.append("  no visible processes")
        return lines
    for row in rows[:12]:
        lines.append(
            f"  pid={row['pid']} rss={row.get('rss')} swap={row.get('swap')} comm={row.get('comm')} cmd={row.get('cmdline')}"
        )
    return lines


def _format_metric(metric) -> str:
    if metric is None or metric.v is None:
        return "-"
    return str(metric.v)


def _sparkline(values: list[float | None]) -> str:
    if not values:
        return "no history"
    finite = [value for value in values if value is not None]
    if not finite:
        return "no history"
    lo = min(finite)
    hi = max(finite)
    blocks = "▁▂▃▄▅▆▇█"
    chars: list[str] = []
    for value in values:
        if value is None:
            chars.append("·")
            continue
        if hi == lo:
            chars.append(blocks[len(blocks) // 2])
            continue
        index = int(round((value - lo) / (hi - lo) * (len(blocks) - 1)))
        chars.append(blocks[max(0, min(len(blocks) - 1, index))])
    return "".join(chars)
