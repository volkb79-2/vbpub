from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Static

from topos.collect.procs import list_processes
from topos.config import ToposConfig
from topos.damon.control import (
    confirmation_text,
    plan_start_session,
    start_planned_session,
    stop_owned_sessions,
)
from topos.diag import pressure_breakdown
from topos.model import EntityFrame, Frame
from topos.record.ring import HistoryRing
from topos.registry import REGISTRY

from .damon_control import DamonConfirmScreen


class DrillDownScreen(Screen[None]):
    BINDINGS = (
        Binding("escape", "close", "Back"),
        Binding("q", "close", "Back", show=False),
        Binding("d", "show_damon_control", "DAMON", show=False),
        Binding("s", "stop_topos_damon", "Stop DAMON", show=False),
    )

    def __init__(
        self,
        frame: Frame,
        entity_key: str,
        *,
        config: ToposConfig,
        ring: HistoryRing,
        cgroup_root: Path,
        proc_root: Path,
        damon_root: Path,
        damon_state_dir: Path | None,
        damon_require_root: bool = True,
    ) -> None:
        super().__init__()
        self.frame = frame
        self.entity_key = entity_key
        self.config = config
        self.ring = ring
        self.cgroup_root = cgroup_root
        self.proc_root = proc_root
        self.damon_root = damon_root
        self.damon_state_dir = damon_state_dir
        self.damon_require_root = damon_require_root
        self._control_notice = ""

    def compose(self):
        yield VerticalScroll(Static(id="drill-body"))

    def on_mount(self) -> None:
        self._refresh_body()

    def _refresh_body(self) -> None:
        self.query_one("#drill-body", Static).update(
            render_drill_text(
                self.frame,
                self.entity_key,
                config=self.config,
                ring=self.ring,
                cgroup_root=self.cgroup_root,
                proc_root=self.proc_root,
            )
            + self._control_notice
        )

    def action_close(self) -> None:
        self.dismiss(None)

    def action_show_damon_control(self) -> None:
        try:
            plan = plan_start_session(
                self.entity_key,
                cgroup_root=self.cgroup_root,
                damon_root=self.damon_root,
                state_dir=self.damon_state_dir,
                config=self.config.damon,
                require_root=self.damon_require_root,
            )
        except Exception as exc:
            self._control_notice = f"\n\nDAMON CONTROL\n  start unavailable: {exc}\n"
            self._refresh_body()
            return

        def apply_confirmed(value: str) -> str:
            session = start_planned_session(
                plan,
                confirmed_text=value,
                require_root=self.damon_require_root,
            )
            return f"DAMON vaddr started on kdamond {session.kdamond_idx} for {self.entity_key or '/'}"

        self.app.push_screen(
            DamonConfirmScreen(
                title="DAMON VADDR CONTROL",
                plan_text=confirmation_text(plan),
                apply_confirmed=apply_confirmed,
            ),
            self._on_control_result,
        )

    def action_stop_topos_damon(self) -> None:
        try:
            stopped = stop_owned_sessions(
                damon_root=self.damon_root,
                state_dir=self.damon_state_dir,
                all_mine=True,
                require_root=self.damon_require_root,
            )
        except Exception as exc:
            self._control_notice = f"\n\nDAMON CONTROL\n  stop unavailable: {exc}\n"
        else:
            self._control_notice = f"\n\nDAMON CONTROL\n  stopped {stopped} topos-owned DAMON session(s)\n"
        self._refresh_body()

    def _on_control_result(self, result: str | None) -> None:
        if result is None:
            self._control_notice = "\n\nDAMON CONTROL\n  start cancelled\n"
        else:
            self._control_notice = f"\n\nDAMON CONTROL\n  {result}\n"
        self._refresh_body()


def render_drill_text(frame: Frame, entity_key: str, *, config: ToposConfig, ring: HistoryRing, cgroup_root: Path, proc_root: Path) -> str:
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

    lines.extend(_damon_block(entity_frame))
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
        if name.startswith(("damon_",)):
            groups["damon"].append(name)
        elif name.startswith(("ram", "anon", "file", "shmem", "sock", "z_", "swap_", "rf_", "mem_", "headroom_", "effective_memory_min")):
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


def _damon_block(entity_frame: EntityFrame) -> list[str]:
    metadata = entity_frame.damon or {}
    sessions = metadata.get("sessions")
    host_sessions = metadata.get("host_sessions")
    mode_metric = entity_frame.metrics.get("damon_mode")
    lines = ["DAMON"]
    if not sessions and not host_sessions:
        lines.append(f"  state: unavailable [{mode_metric.src if mode_metric is not None else 'unavail_kernel'}]")
        return lines
    if isinstance(sessions, list) and sessions:
        summary = metadata.get("summary", {})
        total_bytes = int((summary.get("total_bytes") if isinstance(summary, dict) else 0) or 0)
        lines.append(f"  summary: total={_fmt_bytes(total_bytes)} hot={_format_metric(entity_frame.metrics.get('damon_hot_pct'))} warm={_format_metric(entity_frame.metrics.get('damon_warm_pct'))} cold={_format_metric(entity_frame.metrics.get('damon_cold_pct'))} idle={_format_metric(entity_frame.metrics.get('damon_idle_pct'))}")
        for session in sessions:
            lines.extend(_session_lines(session, prefix="  "))
    if isinstance(host_sessions, list) and host_sessions:
        lines.append("  host sessions:")
        for session in host_sessions:
            lines.extend(_session_lines(session, prefix="    "))
    return lines


def _session_lines(session: object, *, prefix: str) -> list[str]:
    if not isinstance(session, dict):
        return [f"{prefix}session: unavailable"]
    mode = session.get("mode", "-")
    kdamond_idx = session.get("kdamond_idx", "-")
    context_idx = session.get("context_idx", "-")
    scheme_idx = session.get("scheme_idx", "-")
    state = session.get("state", "-")
    target_pids = session.get("target_pids") or []
    covered_pid_count = session.get("covered_pid_count")
    entity_pid_count = session.get("entity_pid_count")
    sample_age_s = session.get("sample_age_s")
    class_bytes = session.get("class_bytes") or {}
    class_pct = session.get("class_pct") or {}
    lines = [
        f"{prefix}mode={mode} kdamond={kdamond_idx} ctx={context_idx} scheme={scheme_idx} state={state}",
        f"{prefix}target_pids: {', '.join(str(pid) for pid in target_pids) if target_pids else '-'}",
        f"{prefix}intervals: sample_us={session.get('sample_us')} aggr_us={session.get('aggr_us')} update_us={session.get('update_us')} schemes={session.get('scheme_count')}",
        f"{prefix}coverage: {_coverage_text(covered_pid_count, entity_pid_count)} | sample_age={_format_seconds(sample_age_s)}",
        f"{prefix}hot={_fmt_bytes(int(class_bytes.get('hot', 0) or 0))} ({_format_pct(class_pct.get('hot'))}) {_bar(class_pct.get('hot'))}",
        f"{prefix}warm={_fmt_bytes(int(class_bytes.get('warm', 0) or 0))} ({_format_pct(class_pct.get('warm'))}) {_bar(class_pct.get('warm'))}",
        f"{prefix}cold={_fmt_bytes(int(class_bytes.get('cold', 0) or 0))} ({_format_pct(class_pct.get('cold'))}) {_bar(class_pct.get('cold'))}",
        f"{prefix}idle={_fmt_bytes(int(class_bytes.get('idle', 0) or 0))} ({_format_pct(class_pct.get('idle'))}) {_bar(class_pct.get('idle'))}",
    ]
    regions = session.get("regions")
    if isinstance(regions, list) and regions:
        histogram = defaultdict(int)
        for region in regions:
            if isinstance(region, dict):
                histogram[str(region.get("class") or "other")] += 1
        lines.append(f"{prefix}regions: " + " ".join(f"{name}={histogram.get(name, 0)}" for name in ("hot", "warm", "cold", "idle")))
    return lines


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
    if isinstance(metric.v, float):
        return f"{metric.v:.1f}"
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


def _coverage_text(covered: object, total: object) -> str:
    if isinstance(covered, int) and isinstance(total, int):
        return f"session covers {covered}/{total} pids of this entity"
    if isinstance(covered, int):
        return f"covered_pids={covered}"
    return "unattributed"


def _fmt_bytes(value: int) -> str:
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    scaled = float(value)
    unit = units[0]
    for unit in units:
        if abs(scaled) < 1024.0 or unit == units[-1]:
            break
        scaled /= 1024.0
    if unit == "B":
        return f"{int(scaled)}{unit}"
    return f"{scaled:.1f}{unit}"


def _format_pct(value: object) -> str:
    if not isinstance(value, (int, float)):
        return "-"
    return f"{float(value):.1f}%"


def _format_seconds(value: object) -> str:
    if not isinstance(value, (int, float)):
        return "-"
    return f"{float(value):.1f}s"


def _bar(value: object, width: int = 12) -> str:
    if not isinstance(value, (int, float)):
        return "-" * width
    filled = max(0, min(width, int(round(float(value) / 100.0 * width))))
    return "#" * filled + "." * (width - filled)
