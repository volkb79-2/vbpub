from __future__ import annotations

from pathlib import Path

from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Static

from groop.config import GroopConfig
from groop.damon.control import APPROVAL_TEXT
from groop.damon.control import stop_owned_sessions
from groop.damon.paddr import paddr_confirmation_text, plan_start_paddr_session, start_planned_paddr_session
from groop.model import Frame

from .damon_control import DamonConfirmScreen


class HostMemoryScreen(Screen[None]):
    BINDINGS = (
        Binding("escape", "close", "Back"),
        Binding("q", "close", "Back", show=False),
        Binding("p", "show_paddr_start", "paddr", show=False),
        Binding("s", "stop_groop_damon", "Stop DAMON", show=False),
    )

    def __init__(
        self,
        frame: Frame,
        *,
        config: GroopConfig,
        damon_root: Path,
        state_dir: Path | None,
        require_root: bool = True,
    ) -> None:
        super().__init__()
        self.frame = frame
        self.config = config
        self.damon_root = damon_root
        self.state_dir = state_dir
        self.require_root = require_root
        self._notice = ""

    def compose(self):
        yield VerticalScroll(Static(id="hostmem-body"))

    def on_mount(self) -> None:
        self._refresh()

    def _refresh(self) -> None:
        self.query_one("#hostmem-body", Static).update(
            render_host_memory_text(self.frame, config=self.config, damon_root=self.damon_root)
            + self._notice
        )

    def action_close(self) -> None:
        self.dismiss(None)

    def action_show_paddr_start(self) -> None:
        try:
            plan = plan_start_paddr_session(
                damon_root=self.damon_root,
                state_dir=self.state_dir,
                config=self.config.damon,
                require_root=self.require_root,
            )
        except Exception as exc:
            self._notice = f"\n\nPADDR CONTROL\n  start unavailable: {exc}\n"
            self._refresh()
            return
        else:
            self.app.push_screen(
                DamonConfirmScreen(
                    title="DAMON PADDR CONTROL",
                    plan_text=paddr_confirmation_text(plan),
                    apply_confirmed=lambda value: _start_paddr_result(plan, value, require_root=self.require_root),
                ),
                self._on_control_result,
            )

    def action_stop_groop_damon(self) -> None:
        try:
            stopped = stop_owned_sessions(
                damon_root=self.damon_root,
                state_dir=self.state_dir,
                all_mine=True,
                require_root=self.require_root,
            )
        except Exception as exc:
            self._notice = f"\n\nPADDR CONTROL\n  stop unavailable: {exc}\n"
        else:
            self._notice = f"\n\nPADDR CONTROL\n  stopped {stopped} groop-owned DAMON session(s)\n"
        self._refresh()

    def _on_control_result(self, result: str | None) -> None:
        if result is None:
            self._notice = "\n\nPADDR CONTROL\n  start cancelled\n"
        else:
            self._notice = f"\n\nPADDR CONTROL\n  {result}\n"
        self._refresh()


def render_host_memory_text(frame: Frame, *, config: GroopConfig, damon_root: Path) -> str:
    sessions = _paddr_sessions(frame)
    lines = [
        "HOST MEMORY",
        f"damon_root: {damon_root}",
        (
            f"paddr defaults: sample_us={config.damon.paddr_sample_us} "
            f"aggr_us={config.damon.paddr_aggr_us} update_us={config.damon.paddr_update_us}"
        ),
        "overhead: paddr is host-wide DAMON sampling; it is read-only here unless a root-owned control action is explicitly confirmed.",
        "",
        "PADDR HEAT",
    ]
    if not sessions:
        lines.append("  no paddr session detected")
        lines.append(f"  start requires root and typed confirmation: {APPROVAL_TEXT}")
        lines.append("")
    else:
        lines.append(
            f"  hot={_fmt_bytes_metric(frame.host.get('host_damon_hot_bytes'))} "
            f"warm={_fmt_bytes_metric(frame.host.get('host_damon_warm_bytes'))} "
            f"cold={_fmt_bytes_metric(frame.host.get('host_damon_cold_bytes'))} "
            f"idle={_fmt_bytes_metric(frame.host.get('host_damon_idle_bytes'))} "
            f"age={_fmt_metric(frame.host.get('host_damon_sample_age_s'))}s"
        )
        lines.append("")
        lines.append("SESSIONS")
        for session in sessions:
            lines.extend(_session_lines(session))
        lines.append("")

    lines.extend(_zram_device_lines(frame))
    return "\n".join(lines)


def _start_paddr_result(plan, value: str, *, require_root: bool) -> str:
    session = start_planned_paddr_session(plan, confirmed_text=value, require_root=require_root)
    return f"DAMON paddr host session started on kdamond {session.kdamond_idx}"


def _paddr_sessions(frame: Frame) -> list[dict[str, object]]:
    root = frame.entities.get("")
    if root is None or not isinstance(root.damon, dict):
        return []
    sessions = root.damon.get("host_sessions")
    if not isinstance(sessions, list):
        return []
    return [session for session in sessions if isinstance(session, dict) and session.get("mode") == "paddr"]


def _session_lines(session: dict[str, object]) -> list[str]:
    lines = [
        (
            f"  owner={session.get('owner', 'foreign')} kdamond={session.get('kdamond_idx')} "
            f"ctx={session.get('context_idx')} scheme={session.get('scheme_idx')} state={session.get('state')}"
        ),
        (
            f"  intervals: sample_us={session.get('sample_us')} "
            f"aggr_us={session.get('aggr_us')} update_us={session.get('update_us')}"
        ),
    ]
    class_bytes = session.get("class_bytes")
    if isinstance(class_bytes, dict):
        total = sum(int(class_bytes.get(name, 0) or 0) for name in ("hot", "warm", "cold", "idle"))
        for name in ("hot", "warm", "cold", "idle"):
            value = int(class_bytes.get(name, 0) or 0)
            lines.append(f"  {name:<5} {_fmt_bytes(value):>8} {_bar(value, total)}")
    regions = session.get("regions")
    if isinstance(regions, list):
        lines.append(f"  regions={len(regions)}")
    if session.get("owner") == "groop":
        lines.append("  stop: groop damon stop --all-mine")
    else:
        lines.append("  stop: read-only foreign session")
    return lines


def _zram_device_lines(frame: Frame) -> list[str]:
    """Render per-device zram details from frame.host_meta."""
    devices = _get_zram_devices(frame)
    lines = ["ZRAM DEVICES"]
    if not devices:
        lines.append("  (no zram devices)")
        lines.append("  note: per-cgroup zram compression/cost attribution is unavailable in the kernel.")
        return lines

    lines.append(
        "  {:<12} {:>10} {:>10} {:>10} {:>6} {:>5} {:>5} {:>10}".format(
            "device", "orig", "compr", "mem_used", "ratio", "rd_er", "wr_er", "wb"
        )
    )
    for dev in devices:
        name = str(dev.get("name", "?"))
        orig = _fmt_bytes(int(dev.get("orig_bytes", 0)))
        compr = _fmt_bytes(int(dev.get("compr_bytes", 0)))
        mem_used = _fmt_bytes(int(dev.get("mem_used_bytes", 0)))
        ratio_val = dev.get("ratio")
        ratio_str = f"{ratio_val:.1f}" if isinstance(ratio_val, (int, float)) and ratio_val is not None else "-"
        failed_reads = int(dev.get("failed_reads", 0))
        failed_writes = int(dev.get("failed_writes", 0))
        wb = _fmt_bytes(int(dev.get("writeback_bytes", 0)))
        lines.append(
            "  {:<12} {:>10} {:>10} {:>10} {:>6} {:>5} {:>5} {:>10}".format(
                name, orig, compr, mem_used, ratio_str, failed_reads, failed_writes, wb
            )
        )
    lines.append("  note: per-cgroup zram compression/cost attribution is unavailable in the kernel.")
    return lines


def _get_zram_devices(frame: Frame) -> list[dict[str, object]]:
    """Safely extract zram_devices list from frame.host_meta."""
    meta = frame.host_meta
    if not isinstance(meta, dict):
        return []
    devices = meta.get("zram_devices")
    if not isinstance(devices, list):
        return []
    return [d for d in devices if isinstance(d, dict)]


def _bar(value: int, total: int, *, width: int = 24) -> str:
    if total <= 0:
        return "[" + "." * width + "]"
    count = max(0, min(width, int(round((value / total) * width))))
    return "[" + "#" * count + "." * (width - count) + "]"


def _fmt_metric(metric, *, digits: int = 1) -> str:
    if metric is None or metric.v is None:
        return "-"
    if isinstance(metric.v, int):
        return str(metric.v)
    return f"{metric.v:.{digits}f}"


def _fmt_bytes_metric(metric) -> str:
    if metric is None or metric.v is None:
        return "-"
    return _fmt_bytes(int(metric.v))


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
