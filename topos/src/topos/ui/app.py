from __future__ import annotations

import asyncio
import math
import threading
from collections import deque
from collections.abc import Iterable, Iterator, Sequence
from pathlib import Path

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.timer import Timer
from textual.widgets import Footer, Input, Static

from topos.config import ToposConfig, load
from topos.damon.passive import DEFAULT_DAMON_ROOT
from topos.model import Frame
from topos.record.replay import ReplayDriver
from topos.record.ring import HistoryRing
from topos.registry import REGISTRY
from topos.snapshot import create as create_snapshot
from topos.snapshot.enrich import (
    DockerSnapshotInspect,
    SystemctlSnapshotRunner,
    collect_docker_inspect,
    collect_systemctl_show,
)

from . import columns as cols
from .columns import CellCtx, Column, ColumnConfigError, Plan
from .data_table import MouseTable
from .drill import DrillDownScreen
from .hostmem import HostMemoryScreen
from .keys import BINDINGS, key_help
from .meters import host_verdict, meter_height, render_meters, unprivileged_count
from .overlay import Overlay, ProcRow
from .pane import pane_lines, pane_text
from .rows import (
    NOISE_LEVELS,
    VIEW_MODES,
    Row,
    RowSet,
    blocked_by_cgroup,
    build_rows,
    default_expanded,
)

MIN_REPLAY_DELAY_S = 0.001
DEFAULT_RETRY_MAX_S = 30.0
TREND_FRAMES = 20
_SEVERITY_RANK = {"crit": 3, "critical": 3, "warn": 2, "warning": 2}


def _default_retry_delay(attempt: int) -> float:
    """Exponential backoff (1s, 2s, 4s ... capped) for sources without their own policy."""
    return min(DEFAULT_RETRY_MAX_S, float(2 ** min(attempt, 5)))


def _is_sentinel(row_key: str | None) -> bool:
    return row_key is not None and row_key.startswith("__empty__")


class FilterScreen(Screen[str | None]):
    def __init__(self, value: str) -> None:
        super().__init__()
        self.value = value

    def compose(self) -> ComposeResult:
        yield VerticalScroll(Input(value=self.value, placeholder="filter by name, cgroup path or image", id="filter-input"))

    def on_mount(self) -> None:
        self.query_one("#filter-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)

    def key_escape(self) -> None:
        self.dismiss(None)


class JumpScreen(Screen[str | None]):
    def __init__(self) -> None:
        super().__init__()
        self.value = ""

    def compose(self) -> ComposeResult:
        yield VerticalScroll(Input(value=self.value, placeholder="frame number or epoch timestamp (e.g. 42 or 1720000000.25)", id="jump-input"))

    def on_mount(self) -> None:
        self.query_one("#jump-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)

    def key_escape(self) -> None:
        self.dismiss(None)


class GlossaryScreen(Screen[None]):
    def compose(self) -> ComposeResult:
        yield VerticalScroll(Static(id="glossary-body"))

    def on_mount(self) -> None:
        self.query_one("#glossary-body", Static).update(_render_glossary())

    def key_escape(self) -> None:
        self.dismiss(None)


class PaneScroll(VerticalScroll, can_focus=False):
    """Side pane container: scrolls when the config chain is taller than the screen."""


class RowCells(Sequence):
    """Cells of the rows, built on first access (only visible lines are ever asked for).

    Rebuilt rows are reused when the entity frame, the layout and the row's tree
    state are unchanged since the previous frame.
    """

    def __init__(self, rows: list[Row], plan: Plan, blk: dict[str, int] | None, overlay_label: str, reuse: dict) -> None:
        self._rows = rows
        self._plan = plan
        self._blk = blk
        self._label = overlay_label
        self._made: dict[int, list] = {}
        self._reuse = reuse
        self._sig = tuple((c.id, w) for c, w in zip(plan.columns, plan.widths))

    def __len__(self) -> int:
        return len(self._rows)

    def __getitem__(self, index):
        if isinstance(index, slice):
            return [self[i] for i in range(*index.indices(len(self)))]
        cells = self._made.get(index)
        if cells is None:
            row = self._rows[index]
            blk = None if self._blk is None else self._blk.get(row.owner or "", 0)
            stamp = (self._sig, row.kind, row.depth, row.has_kids, row.open, row.label, blk, row.proc)
            hit = self._reuse.get(row.key)
            if hit is not None and hit[0] == stamp and (hit[1] is row.ef or hit[1] == row.ef):
                cells = hit[2]
            else:
                cells = self._build(row, blk)
            self._reuse[row.key] = (stamp, row.ef, cells)
            self._made[index] = cells
        return cells

    def _build(self, row: Row, blk: int | None) -> list:
        plan = self._plan
        if row.kind == "group":
            return [Text(f"  {row.label}", style="bold cyan", no_wrap=True)] + [""] * (len(plan.columns) - 1)
        out: list = []
        for col, width in zip(plan.columns, plan.widths):
            if row.kind in ("proc", "flat"):
                out.append(self._proc_cell(row, col, width))
            elif col.id == "name":
                out.append(_name_cell(row, width))
            else:
                out.append(col.spec.cell(CellCtx(row.ef, blk, width)))
        return out

    def _proc_cell(self, row: Row, col: Column, width: int):
        p = row.proc
        style = "bold magenta" if p.state == "D" else "magenta"
        if col.id == "name":
            owner = ""
            if row.kind == "flat" and row.ef is not None:
                owner = f"  ‹{row.ef.entity.key.rsplit('/', 1)[-1] or '/'}›"
            return Text(" " * (row.depth * 2) + f"{p.state} {p.comm} {p.pid} {self._label}{owner}", style=style, no_wrap=True)
        if col.id == "cpu":
            return (cols.fmt_pct(p.cpu), style)
        if col.id == "blk":
            return ("D" if p.state == "D" else "", "bold magenta")
        return ""


def _name_cell(row: Row, width: int) -> Text:
    from .rows import row_name

    ef = row.ef
    glyph = "▾ " if row.has_kids and row.open else "▸ " if row.has_kids else "  "
    indent = " " * (row.depth * 2)
    mark = " !" if any(f.severity != "info" for f in ef.findings) else ""
    suffix = f" {row.label}" if row.label else ""
    room = max(1, width - len(indent) - 2 - len(mark) - len(suffix))
    name = row_name(ef.entity)
    if len(name) > room:
        name = name[: room - 1] + "…" if room > 1 else "…"
    kind = ef.entity.kind
    style = "bold white" if kind in ("slice", "root") else "light_sky_blue1" if kind == "scope" else "light_green" if kind == "service" else "grey70"
    out = Text(no_wrap=True)
    out.append(indent)
    out.append(glyph, "cyan")
    out.append(name, style)
    if suffix:
        out.append(suffix, "dim italic")
    if mark:
        out.append(mark, "bold yellow")
    return out


# Integer-valued jump inputs at or above this (about 1973 as epoch seconds) are
# timestamps, never frame numbers.
_EPOCH_LIKE_MIN = 1e8


class ToposApp(App[None]):
    CSS = """
    Screen {
        layout: vertical;
    }
    #meters {
        height: 0;
    }
    #why {
        height: 1;
        display: none;
    }
    #info {
        height: 1;
    }
    #body {
        height: 1fr;
    }
    #body-table {
        width: 1fr;
        height: 100%;
    }
    #pane {
        width: 46;
        height: 100%;
        border-left: solid $primary-darken-2;
        padding: 0 1;
        display: none;
    }
    #status {
        height: 1;
        padding: 0 1;
    }
    """
    BINDINGS = BINDINGS

    def __init__(
        self,
        frame_source: Iterable[Frame] | Iterator[Frame],
        *,
        config: ToposConfig | None = None,
        cgroup_root: Path | None = None,
        proc_root: Path = Path("/proc"),
        damon_root: Path = DEFAULT_DAMON_ROOT,
        damon_state_dir: Path | None = None,
        damon_require_root: bool = True,
        ring: HistoryRing | None = None,
        source_label: str = "LIVE",
        replay_driver: ReplayDriver | None = None,
        replay_step: bool = False,
        replay_speed: float = 1.0,
        snapshot_systemctl_show: SystemctlSnapshotRunner | None = None,
        snapshot_docker_inspect: DockerSnapshotInspect | None = None,
        overlay: Overlay | None = None,
    ) -> None:
        super().__init__()
        self.config = config or load()
        self.cgroup_root = cgroup_root or self.config.cgroup_root
        self.proc_root = proc_root
        self.damon_root = damon_root
        self.damon_state_dir = damon_state_dir
        self.damon_require_root = damon_require_root
        self.ring = ring or HistoryRing.from_config(self.config)
        self.overlay = overlay
        # A one-shot iterator cannot be restarted after a failure; a re-iterable
        # source (``__iter__`` without ``__next__``) is asked for a fresh
        # stream on every retry.
        self._frame_source_raw = frame_source
        self._frame_source = iter(frame_source)
        self._source_error: str | None = None
        self._stop_event = threading.Event()
        self._history_last_ts: float | None = None
        self._table_width = 0
        self._source_label = source_label
        self._replay_driver = replay_driver
        self._replay_paused = replay_driver is not None and replay_step
        self._replay_speed = replay_speed if replay_speed > 0 else 1.0
        self._replay_speed_levels = (1.0, 2.0, 4.0, 8.0)
        self._replay_timer: Timer | None = None
        self._frozen = False
        self.current_frame: Frame | None = None
        self.frames_received = 0
        self.view_mode = self.config.default_view if self.config.default_view in VIEW_MODES else "tree"
        self.columns, self.columns_warning = self._load_columns()
        self.noise = "middle"
        self.sort_by = "cpu"
        self.sort_reverse = True
        self.filter_text = ""
        self.procs_on = True
        self.flat_procs = False
        self.pane_mode = "auto"
        self.selected_key: str | None = None
        self._expanded: set[str] | None = None
        self._rowset = RowSet()
        self._plan: Plan | None = None
        self._visible_row_keys: tuple[str, ...] = ()
        self._row_owner: dict[str, str | None] = {}
        self._procs: dict[str, list[ProcRow]] = {}
        self._blk: dict[str, int] | None = None
        self._reuse: dict = {}
        self._pane_w = 0
        self._notice = ""
        self._recent_frames: deque[Frame] = deque(maxlen=max(TREND_FRAMES * 3, self.config.snapshots.frames))
        self._snapshot_systemctl_show = snapshot_systemctl_show
        self._snapshot_docker_inspect = snapshot_docker_inspect
        self._snapshot_in_progress: bool = False
        self._snapshot_entity_key: str | None = None
        self._snapshot_frame: Frame | None = None
        self._snapshot_previous_frames: list[Frame] = []

    def _load_columns(self) -> tuple[list[Column], str]:
        path = self.config.ui.columns_file
        try:
            return cols.load_columns(path), ""
        except ColumnConfigError as exc:
            return cols.load_columns(None), f"columns file ignored, using the shipped one: {exc}"

    def compose(self) -> ComposeResult:
        yield Static(id="meters")
        yield Static(id="why")
        yield Static(id="info")
        with Horizontal(id="body"):
            yield MouseTable(id="body-table")
            yield PaneScroll(Static(id="pane-body"), id="pane")
        yield Static(id="status")
        yield Footer()

    def on_mount(self) -> None:
        if self._replay_driver is not None:
            self._apply_frame(self._replay_driver.current)
            if not self._replay_paused:
                self._schedule_replay_tick()
            return
        self.run_worker(self._consume_frames, thread=True, exclusive=True)
        self._refresh_status(f"mode={self._source_label} waiting for frames")

    def on_unmount(self) -> None:
        # Stop the frame-source worker: the app's own retry wait and, when the
        # source exposes one, its collection loop.
        self._stop_event.set()
        source_stop = getattr(self._frame_source_raw, "stop_event", None)
        if isinstance(source_stop, threading.Event):
            source_stop.set()

    def _consume_frames(self) -> None:
        stream = self._frame_source
        attempt = 0
        while not self._stop_event.is_set():
            try:
                for frame in stream:
                    if self._stop_event.is_set():
                        return
                    attempt = 0
                    self.call_from_thread(self._apply_frame, frame)
                return
            except Exception as exc:  # noqa: BLE001 - any source failure must stay in the UI
                if self._stop_event.is_set():
                    return
                retryable = not hasattr(self._frame_source_raw, "__next__")
                delay = 0.0
                if retryable:
                    policy = getattr(self._frame_source_raw, "retry_delay", _default_retry_delay)
                    delay = float(policy(attempt))
                attempt += 1
                message = str(exc) or type(exc).__name__
                retry_note = f"retrying in {delay:.0f}s" if retryable and delay >= 1 else ("retrying" if retryable else "not retrying")
                self.call_from_thread(self._on_source_failed, message, retry_note)
                if not retryable:
                    return
                if self._stop_event.wait(delay):
                    return
                try:
                    stream = iter(self._frame_source_raw)
                except Exception as restart_exc:  # noqa: BLE001
                    stream = iter(())
                    self.call_from_thread(self._on_source_failed, str(restart_exc) or type(restart_exc).__name__, "retrying")

    def _on_source_failed(self, message: str, retry_note: str) -> None:
        self._source_error = f"{message} ({retry_note})"
        if self.current_frame is not None:
            self._refresh_view()
        else:
            self._refresh_status(self._status_text())

    def _apply_frame(self, frame: Frame) -> None:
        self.frames_received += 1
        self._source_error = None
        # Replay stepping can revisit or rewind frames; only strictly newer
        # frames extend the history so sparklines never see duplicates or
        # out-of-order samples.
        if self._history_last_ts is None or frame.ts > self._history_last_ts:
            self.ring.append_frame(frame)
            self._history_last_ts = frame.ts
        self._recent_frames.append(frame)
        if self._frozen and self._replay_driver is None:
            self._refresh_status(self._status_text())
            return
        self._show_frame(frame)

    def _show_frame(self, frame: Frame) -> None:
        self.current_frame = frame
        if self._expanded is None:
            self._expanded = default_expanded(frame)
        else:
            self._expanded.intersection_update(frame.entities)
        if self.selected_key not in frame.entities:
            self.selected_key = None
        self._procs = self.overlay.processes(frame) if self.overlay is not None else {}
        self._blk = blocked_by_cgroup(frame, self._procs) if self.overlay is not None else None
        self._refresh_view()

    # ── layout ────────────────────────────────────────────────────────

    def _pane_width_for(self, total: int) -> int:
        ui = self.config.ui
        if self.pane_mode == "off":
            return 0
        if self.pane_mode == "auto" and total < ui.pane_auto_min_width:
            return 0
        return min(ui.pane_width, total // 2)

    def on_table_width_changed(self, width: int) -> None:
        """The table widget got a new width: re-plan the columns for it."""
        if width != self._table_width:
            self._table_width = width
            self._refresh_view()

    def on_resize(self) -> None:
        if self.current_frame is not None:
            self._refresh_view()

    def _apply_layout(self) -> int:
        total = self.size.width or 80
        pane_w = self._pane_width_for(total)
        pane = self.query_one("#pane", PaneScroll)
        if pane_w != self._pane_w:
            self._pane_w = pane_w
            pane.display = pane_w > 0
            if pane_w:
                pane.styles.width = pane_w
        meters = self.query_one("#meters", Static)
        height = meter_height(self.size.height or 24)
        meters.styles.height = height
        table_w = self.query_one("#body-table", MouseTable).scrollable_content_region.width
        expected = total - pane_w - 1  # the table's own scrollbar column
        return table_w if table_w and table_w <= expected else expected

    def _refresh_view(self) -> None:
        frame = self.current_frame
        if frame is None:
            return
        width = self._apply_layout()
        self._table_width = width
        height = meter_height(self.size.height or 24)
        self.query_one("#meters", Static).update(
            render_meters(frame, width=self.size.width or 80, height=height, trend=self._trend, overlay=self.overlay)
        )
        self._refresh_why(frame)
        self._populate_table(frame, width=width)
        table = self.query_one("#body-table", MouseTable)
        cursor_owner = self._row_owner.get(table.row_key_at_cursor() or "")
        if self.selected_key is not None and self.selected_key != cursor_owner and self.selected_key in self._row_owner:
            table.update_cursor_from_key(self.selected_key)  # a programmatic selection wins
        else:
            self.selected_key = cursor_owner
        self._refresh_info(frame)
        self._refresh_pane()
        self._refresh_status(self._status_text())

    def _trend(self, key: str) -> list[float]:
        current = self.current_frame
        if current is None:
            return []
        by_ts = {f.ts: f for f in self._recent_frames if f.ts <= current.ts}
        values: list[float] = []
        for ts in sorted(by_ts)[-TREND_FRAMES:]:
            m = by_ts[ts].host.get(key)
            values.append(float(m.v) if m is not None and m.v is not None and m.src not in ("unavail_perm", "unavail_kernel") else 0.0)
        return values

    def _refresh_why(self, frame: Frame) -> None:
        best: tuple[int, int, str, str] | None = None
        for ef in frame.entities.values():
            for f in ef.findings:
                rank = _SEVERITY_RANK.get(f.severity.lower(), 0)
                if rank and (best is None or rank > best[0]):
                    best = (rank, len(ef.entity.key), f.message, ef.entity.key or "/")
        why = self.query_one("#why", Static)
        if best is None:
            why.display = False
            return
        why.display = True
        line = Text(no_wrap=True, overflow="ellipsis")
        line.append("WHY ", "bold cyan")
        line.append(f"{best[2]}", "yellow")
        line.append(f"  ({best[3]})", "bright_black")
        why.update(line)

    def _refresh_info(self, frame: Frame) -> None:
        line = Text(no_wrap=True, overflow="ellipsis")
        verdict = host_verdict(frame, self.config)
        line.append(f" HOST {verdict}", {"OK": "green", "WARN": "yellow"}.get(verdict, "bold red"))
        unpriv = unprivileged_count(frame)
        if unpriv:
            line.append(f" (unprivileged: {unpriv} n/a)", "bright_black")
        if self._source_error is not None:
            line.append(f"  !! STALE - source failed: {self._source_error}", "bold red")
        hidden = self._rowset.hidden
        line.append(f"  noise:{self.noise} ({hidden} hidden)", "grey70")
        arrow = "▼" if self.sort_reverse else "▲"
        line.append(f"  sort:{self._sort_head()}{arrow}", "grey70")
        if self.view_mode != "tree":
            line.append(f"  view:{self.view_mode}", "grey70")
        if self.filter_text:
            line.append(f'  filter:"{self.filter_text}"', "yellow")
        if self.flat_procs:
            line.append(f"  FLAT process ranking ({self.overlay.label if self.overlay else 'n/a'})", "magenta")
        if self._frozen:
            line.append("  FROZEN", "black on yellow")
        if self.columns_warning:
            line.append(f"  {self.columns_warning}", "yellow")
        if self._plan is not None:
            line.append(f"  cols {len(self._plan.columns)}/{self._plan.total} ({self._plan.dropped} dropped) rows {len(self._real_row_keys())}", "bright_black")
        self.query_one("#info", Static).update(line)

    def _sort_head(self) -> str:
        col = next((c for c in self.columns if c.id == self.sort_by), None)
        return col.head if col is not None else self.sort_by.upper()

    def _refresh_pane(self) -> None:
        if not self._pane_w or self.current_frame is None:
            return
        body = self.query_one("#pane-body", Static)
        ef = self.current_frame.entities.get(self.selected_key) if self.selected_key is not None else None
        if ef is None:
            body.update(Text("no row selected", style="bright_black"))
            return
        blk = None if self._blk is None else self._blk.get(ef.entity.key, 0)
        procs = self._procs.get(ef.entity.key) if self.procs_on else None
        label = self.overlay.label if self.overlay is not None else ""
        body.update(pane_text(pane_lines(ef, procs=procs, blk=blk, overlay_label=label)))

    def _real_row_keys(self) -> tuple[str, ...]:
        return tuple(key for key in self._visible_row_keys if not _is_sentinel(key) and not key.startswith("__group__") and key != "__ungrouped__")

    def _populate_table(self, frame: Frame, *, width: int) -> None:
        plan = cols.plan(self.columns, width)
        self._plan = plan
        self._rowset = build_rows(
            frame,
            mode=self.view_mode,
            noise=self.noise,
            filter_text=self.filter_text,
            sort_id=self.sort_by,
            desc=self.sort_reverse,
            expanded=self._expanded,
            procs=self._procs,
            procs_on=self.procs_on,
            flat_procs=self.flat_procs,
            blk=self._blk,
        )
        rows = self._rowset.rows
        arrow = "▼" if self.sort_reverse else "▲"
        keys = tuple(c.id for c in plan.columns)
        labels = tuple(c.head + (arrow if c.id == self.sort_by else "") for c in plan.columns)
        left = frozenset(c.id for c in plan.columns if c.spec.left)
        if rows:
            row_keys = tuple(r.key for r in rows)
            self._row_owner = {r.key: r.owner for r in rows}
            cells: Sequence = RowCells(rows, plan, self._blk, self.overlay.label if self.overlay else "", self._reuse)
            if len(self._reuse) > 4 * len(rows) + 64:
                live = set(row_keys)
                self._reuse = {k: v for k, v in self._reuse.items() if k in live}
                cells._reuse = self._reuse
        else:
            row_keys = ("__empty__",)
            self._row_owner = {"__empty__": None}
            cells = [[Text("no rows match (noise level / filter)", style="bright_black")] + [""] * (len(keys) - 1)]
        self._visible_row_keys = row_keys
        self.query_one("#body-table", MouseTable).populate(
            column_keys=keys, column_labels=labels, row_keys=row_keys, rows=cells, widths=plan.widths, left_keys=left
        )

    def _refresh_status(self, text: str) -> None:
        self.query_one("#status", Static).update(text)

    # ── table events ──────────────────────────────────────────────────

    def on_mouse_table_header_selected(self, event: MouseTable.HeaderSelected) -> None:
        """Handle a header click: sort by that column, toggling direction if already active."""
        event.stop()
        self._set_sort(event.column_key)

    def _set_sort(self, column_id: str) -> None:
        if not cols.is_sortable(column_id):
            return
        if column_id == self.sort_by:
            self.sort_reverse = not self.sort_reverse
        else:
            self.sort_by = column_id
            self.sort_reverse = column_id != "name"  # name asc by default; numeric desc
        self._refresh_view()

    def on_mouse_table_row_highlighted(self, event: MouseTable.RowHighlighted) -> None:
        """Handle cursor move: the side pane follows without rebuilding rows."""
        event.stop()
        if event.row_key is None:
            return
        self.selected_key = self._row_owner.get(event.row_key)
        self._refresh_pane()
        self._refresh_status(self._status_text())

    def on_mouse_table_row_selected(self, event: MouseTable.RowSelected) -> None:
        """Enter on a row opens the drill-down."""
        event.stop()
        owner = self._row_owner.get(event.row_key)
        if owner is not None:
            self.selected_key = owner
            self.action_open_drill()

    def on_mouse_table_row_clicked(self, event: MouseTable.RowClicked) -> None:
        """A click on the fold glyph of a tree row folds it; elsewhere it only selects."""
        event.stop()
        row = self._row_by_key(event.row_key)
        if row is None or row.kind != "cg" or not row.has_kids:
            return
        start = row.depth * 2
        if start <= event.x < start + 2 and self.view_mode == "tree":
            self._toggle_row(row)

    def _row_by_key(self, key: str | None) -> Row | None:
        if key is None:
            return None
        return next((r for r in self._rowset.rows if r.key == key), None)

    def _cursor_row(self) -> Row | None:
        return self._row_by_key(self.query_one("#body-table", MouseTable).row_key_at_cursor())

    # ── actions: view ─────────────────────────────────────────────────

    def action_toggle_view(self) -> None:
        index = VIEW_MODES.index(self.view_mode) if self.view_mode in VIEW_MODES else 0
        self.view_mode = VIEW_MODES[(index + 1) % len(VIEW_MODES)]
        self._refresh_view()

    def action_cycle_noise(self) -> None:
        self.noise = NOISE_LEVELS[(NOISE_LEVELS.index(self.noise) + 1) % len(NOISE_LEVELS)]
        self._refresh_view()

    def action_cycle_sort(self) -> None:
        shown = [c.id for c in (self._plan.columns if self._plan else self.columns) if cols.is_sortable(c.id)]
        if not shown:
            return
        index = shown.index(self.sort_by) + 1 if self.sort_by in shown else 0
        self.sort_by = shown[index % len(shown)]
        self.sort_reverse = self.sort_by != "name"
        self._refresh_view()

    def action_reverse_sort(self) -> None:
        self.sort_reverse = not self.sort_reverse
        self._refresh_view()

    def action_toggle_pane(self) -> None:
        self.pane_mode = "off" if self._pane_w else "on"
        self._refresh_view()
        self.call_after_refresh(self._refresh_view)

    def action_toggle_procs(self) -> None:
        self.procs_on = not self.procs_on
        self._refresh_view()

    def action_toggle_flat_procs(self) -> None:
        if self.overlay is None and not self._procs:
            self._refresh_status("no process source: the flat process ranking needs per-process data")
            return
        self.flat_procs = not self.flat_procs
        self._refresh_view()

    def action_expand_all(self) -> None:
        if self.current_frame is not None:
            self._expanded = set(self.current_frame.entities)
            self._refresh_view()

    def action_collapse_all(self) -> None:
        if self.current_frame is not None:
            self._expanded = {""} if "" in self.current_frame.entities else set()
            self._refresh_view()

    def action_select_prev(self) -> None:
        self._move_selection(-1)

    def action_select_next(self) -> None:
        self._move_selection(1)

    def _move_selection(self, delta: int) -> None:
        """Move the cursor by *delta* real rows, wrapping around."""
        real = self._real_row_keys()
        if not real:
            return
        table = self.query_one("#body-table", MouseTable)
        current = table.row_key_at_cursor()
        index = real.index(current) if current in real else 0
        target = real[(index + delta) % len(real)] if current in real else real[0]
        table.update_cursor_from_key(target)

    def _toggle_row(self, row: Row) -> None:
        if self._expanded is None:
            self._expanded = set()
        if row.key in self._expanded:
            self._expanded.discard(row.key)
        else:
            self._expanded.add(row.key)
        self._refresh_view()

    def action_collapse_tree(self) -> None:
        row = self._cursor_row()
        if self.view_mode != "tree" or row is None:
            return
        if row.kind == "cg" and row.open and row.has_kids:
            self._toggle_row(row)
            return
        rows = self._rowset.rows
        index = rows.index(row)
        for j in range(index - 1, -1, -1):
            if rows[j].depth < row.depth:
                self.query_one("#body-table", MouseTable).move_cursor(row=j)
                return

    def action_expand_tree(self) -> None:
        row = self._cursor_row()
        if self.view_mode != "tree" or row is None:
            return
        if row.kind == "cg" and row.has_kids and not row.open:
            self._toggle_row(row)
        else:
            self.query_one("#body-table", MouseTable).action_cursor_down()

    # ── actions: time ─────────────────────────────────────────────────

    def action_toggle_replay_pause(self) -> None:
        """Space freezes the display; in replay it also stops the playback."""
        if self._replay_driver is None:
            self._frozen = not self._frozen
            if not self._frozen and self._recent_frames:
                self._show_frame(self._recent_frames[-1])
            else:
                self._refresh_view()
            return
        self._replay_paused = not self._replay_paused
        if self._replay_paused:
            self._cancel_replay_timer()
            self._refresh_view()
            return
        self._schedule_replay_tick()
        self._refresh_view()

    def _live_step(self, delta: int) -> None:
        frames = list(self._recent_frames)
        if not frames:
            return
        self._frozen = True
        current = self.current_frame
        index = next((i for i, f in enumerate(frames) if f is current), len(frames) - 1)
        self._show_frame(frames[max(0, min(len(frames) - 1, index + delta))])

    def action_replay_step_back(self) -> None:
        if self._replay_driver is None:
            self._live_step(-1)
            return
        self._replay_paused = True
        self._cancel_replay_timer()
        self._apply_frame(self._replay_driver.step(-1))

    def action_replay_step_forward(self) -> None:
        if self._replay_driver is None:
            self._live_step(1)
            return
        self._replay_paused = True
        self._cancel_replay_timer()
        self._apply_frame(self._replay_driver.step(1))

    def action_replay_first(self) -> None:
        if self._replay_driver is None:
            self._refresh_status("replay jump is only available in --replay mode")
            return
        self._replay_paused = True
        self._cancel_replay_timer()
        self._apply_frame(self._replay_driver.seek(0))

    def action_replay_last(self) -> None:
        if self._replay_driver is None:
            self._refresh_status("replay jump is only available in --replay mode")
            return
        self._replay_paused = True
        self._cancel_replay_timer()
        self._apply_frame(self._replay_driver.seek(self._replay_driver.total - 1))

    def action_replay_jump_prompt(self) -> None:
        if self._replay_driver is None:
            self._refresh_status("replay jump is only available in --replay mode")
            return
        self.push_screen(JumpScreen(), self._on_jump_applied)

    def _on_jump_applied(self, value: str | None) -> None:
        if self._replay_driver is None or value is None:
            return
        stripped = value.strip()
        if not stripped:
            return
        try:
            parsed = float(stripped)
        except ValueError:
            self._refresh_status(f"invalid jump input: '{stripped}' — use a frame number or epoch timestamp")
            return
        if not math.isfinite(parsed):
            self._refresh_status(f"invalid jump input: '{stripped}' — use a finite frame number or epoch timestamp")
            return
        if parsed.is_integer() and 1 <= parsed <= self._replay_driver.total:
            self._replay_paused = True
            self._cancel_replay_timer()
            self._apply_frame(self._replay_driver.seek(int(parsed) - 1))
            return
        if parsed.is_integer() and parsed < _EPOCH_LIKE_MIN:
            self._refresh_status(
                f"invalid frame number: {int(parsed)} — must be between 1 and {self._replay_driver.total}"
            )
            return
        # Anything else (including integer-valued epoch timestamps) is a timestamp.
        self._replay_paused = True
        self._cancel_replay_timer()
        self._apply_frame(self._replay_driver.seek_timestamp(parsed))

    def action_replay_speed_up(self) -> None:
        self._set_replay_speed(1)

    def action_replay_speed_down(self) -> None:
        self._set_replay_speed(-1)

    # ── actions: row ──────────────────────────────────────────────────

    def action_open_drill(self) -> None:
        if self.current_frame is None or self.selected_key is None or self.selected_key not in self.current_frame.entities:
            return
        self.push_screen(
            DrillDownScreen(
                self.current_frame,
                self.selected_key,
                config=self.config,
                ring=self.ring,
                cgroup_root=self.cgroup_root,
                proc_root=self.proc_root,
                damon_root=self.damon_root,
                damon_state_dir=self.damon_state_dir,
                damon_require_root=self.damon_require_root,
            )
        )

    def action_create_snapshot(self) -> None:
        if self.current_frame is None or self.selected_key is None or self.selected_key not in self.current_frame.entities:
            return
        if self._snapshot_in_progress:
            self._refresh_status("snapshot already running")
            return
        self._snapshot_entity_key = self.selected_key
        self._snapshot_frame = self.current_frame
        # Snapshots retain only frames earlier than the one shown.
        self._snapshot_previous_frames = [f for f in self._recent_frames if f.ts < self.current_frame.ts]
        self._snapshot_in_progress = True
        self._refresh_status(f"snapshot running: {self._snapshot_entity_key}")
        self.run_worker(self._run_snapshot_worker, thread=True)

    def _run_snapshot_worker(self) -> None:
        entity_key = self._snapshot_entity_key or ""
        frame = self._snapshot_frame
        previous_frames = self._snapshot_previous_frames
        try:
            systemctl_show, systemctl_status = collect_systemctl_show(
                entity_key, runner=self._snapshot_systemctl_show
            )
            docker_inspect, docker_status = collect_docker_inspect(
                entity_key, docker_inspect=self._snapshot_docker_inspect
            )
            providers_status = _providers_status(frame, entity_key)
            providers_status["snapshot"] = {
                "systemctl": systemctl_status,
                "docker": docker_status,
            }
            path = create_snapshot(
                entity_key,
                self.ring,
                frame,
                self.config,
                cgroup_root=self.cgroup_root,
                previous_frames=previous_frames,
                providers_status=providers_status,
                systemctl_show=systemctl_show,
                docker_inspect=docker_inspect,
            )
            self.call_from_thread(self._on_snapshot_done, path)
        except (OSError, RuntimeError, ValueError) as exc:
            self.call_from_thread(self._on_snapshot_failed, exc)

    def _on_snapshot_done(self, path: Path) -> None:
        self._snapshot_in_progress = False
        self._refresh_status(f"snapshot saved: {path}")

    def _on_snapshot_failed(self, exc: Exception) -> None:
        self._snapshot_in_progress = False
        self._refresh_status(f"snapshot failed: {exc}")

    def action_open_host_memory(self) -> None:
        if self.current_frame is None:
            return
        self.push_screen(
            HostMemoryScreen(
                self.current_frame,
                config=self.config,
                damon_root=self.damon_root,
                state_dir=self.damon_state_dir,
                require_root=self.damon_require_root,
            )
        )

    def action_open_filter(self) -> None:
        self.push_screen(FilterScreen(self.filter_text), self._on_filter_applied)

    def _on_filter_applied(self, value: str | None) -> None:
        if value is None:
            return
        self.filter_text = value.strip()
        self._refresh_view()

    def action_open_help(self) -> None:
        self.push_screen(GlossaryScreen())

    def action_close_overlay(self) -> None:
        if len(self.screen_stack) > 1:
            self.pop_screen()
        elif getattr(self, "filter_text", ""):
            self.filter_text = ""
            self._refresh_view()

    def _cancel_replay_timer(self) -> None:
        if self._replay_timer is not None:
            self._replay_timer.stop()
            self._replay_timer = None

    def _schedule_replay_tick(self) -> None:
        if self._replay_driver is None or self._replay_paused:
            return
        if self._replay_driver.index >= self._replay_driver.total - 1:
            self._replay_paused = True
            self._refresh_view()
            return
        self._cancel_replay_timer()
        current = self._replay_driver.current
        upcoming = self._replay_driver.frames[self._replay_driver.index + 1]
        delay_s = (upcoming.ts - current.ts) / self._replay_speed
        if delay_s <= 0.0:
            delay_s = MIN_REPLAY_DELAY_S
        self._replay_timer = self.set_timer(delay_s, self._advance_replay)

    def _advance_replay(self) -> None:
        self._replay_timer = None
        if self._replay_driver is None or self._replay_paused:
            return
        self._apply_frame(self._replay_driver.step(1))
        self._schedule_replay_tick()

    def _set_replay_speed(self, delta: int) -> None:
        if self._replay_driver is None:
            self._refresh_status("replay speed is only available in --replay mode")
            return
        speed = min(self._replay_speed_levels, key=lambda candidate: abs(candidate - self._replay_speed))
        index = self._replay_speed_levels.index(speed)
        next_index = min(max(0, index + delta), len(self._replay_speed_levels) - 1)
        self._replay_speed = self._replay_speed_levels[next_index]
        if not self._replay_paused:
            self._schedule_replay_tick()
        self._refresh_view()

    def _status_text(self) -> str:
        frame = self.current_frame
        rows = len(self._real_row_keys())
        dir_char = "v" if self.sort_reverse else "^"
        core = f"view={self.view_mode} noise={self.noise} sort={dir_char}{self.sort_by} rows={rows} filter={self.filter_text or '-'}"
        if self._replay_driver is None:
            if self._source_error is not None:
                prefix = f"mode={self._source_label} source failed: {self._source_error}"
                if frame is None:
                    return prefix
                return f"{prefix} | STALE ts={frame.ts:.3f} frames={self.frames_received}"
            if frame is None:
                return f"mode={self._source_label} waiting for frames"
            state = " FROZEN" if self._frozen else ""
            return f"mode={self._source_label}{state} {core} frames={self.frames_received} ts={frame.ts:.3f}"
        if frame is None:
            return "mode=REPLAY waiting for frames"
        state = "paused" if self._replay_paused else "playing"
        return (
            f"mode=REPLAY {state} speed={self._replay_speed:g}x frame={self._replay_driver.index + 1}/{self._replay_driver.total} "
            f"{core} ts={frame.ts:.3f} controls=space ,/. +/- home/end j"
        )


def run_ui(
    frame_source: Iterable[Frame] | Iterator[Frame],
    *,
    config: ToposConfig | None = None,
    cgroup_root: Path | None = None,
    proc_root: Path = Path("/proc"),
    smoke: bool = False,
    replay_driver: ReplayDriver | None = None,
    replay_step: bool = False,
    replay_speed: float = 1.0,
    source_label: str = "LIVE",
    overlay: Overlay | None = None,
) -> str | int:
    if smoke:
        return asyncio.run(
            _run_ui_smoke(
                frame_source,
                config=config,
                cgroup_root=cgroup_root,
                proc_root=proc_root,
                source_label=source_label,
                replay_driver=replay_driver,
                replay_step=replay_step,
                replay_speed=replay_speed,
                overlay=overlay,
            )
        )
    app = ToposApp(
        frame_source,
        config=config,
        cgroup_root=cgroup_root,
        proc_root=proc_root,
        source_label=source_label,
        replay_driver=replay_driver,
        replay_step=replay_step,
        replay_speed=replay_speed,
        overlay=overlay,
    )
    app.run()
    return 0


async def _run_ui_smoke(
    frame_source: Iterable[Frame] | Iterator[Frame],
    *,
    config: ToposConfig | None,
    cgroup_root: Path | None,
    proc_root: Path,
    source_label: str,
    replay_driver: ReplayDriver | None,
    replay_step: bool,
    replay_speed: float,
    overlay: Overlay | None = None,
) -> str:
    app = ToposApp(
        frame_source,
        config=config,
        cgroup_root=cgroup_root,
        proc_root=proc_root,
        source_label=source_label,
        replay_driver=replay_driver,
        replay_step=replay_step,
        replay_speed=replay_speed,
        overlay=overlay,
    )
    async with app.run_test(size=(140, 40)) as pilot:
        deadline = asyncio.get_event_loop().time() + 10.0
        while asyncio.get_event_loop().time() < deadline:
            await pilot.pause()
            if app.frames_received:
                break
        if not app.frames_received:
            raise RuntimeError("ui smoke did not receive a frame")
    return f"ui smoke ok frames={app.frames_received} view={app.view_mode}"


def _render_glossary() -> str:
    lines = ["KEYS", *[f"  {line}" for line in key_help()], "", "CELLS", "  · = not set (no limit)   n/a = not available   0 = a real zero",
             "  colour bands: grey ok, yellow warn, orange high, red critical", "", "GLOSSARY"]
    for name in sorted(REGISTRY):
        spec = REGISTRY[name]
        lines.append(
            f"  {name}: unit={spec.unit} kind={spec.kind} locality={spec.locality} branch={spec.branch_policy} sources={', '.join(spec.sources)}"
        )
        lines.append(f"    {spec.glossary}")
    lines.extend(
        (
            "",
            "STATIC CONCEPTS",
            "  origin: unit file vs. runtime drop-in vs. unmanaged raw write.",
            "  network source labels: net:BPF exact, net:NS approximation, net:HOST host truth, net:N/A unavailable.",
            "  DAMON vaddr hot/warm/cold: entity-attributed tried_regions, shown only when a readable session covers the entity.",
            "  DAMON paddr heat: host-wide physical DRAM heat; it is never attributed to individual cgroups.",
        )
    )
    return "\n".join(lines)


def _providers_status(frame: Frame, entity_key: str) -> dict[str, object]:
    entity_frame = frame.entities.get(entity_key)
    if entity_frame is None:
        return {}
    return {
        "network": entity_frame.network or {},
        "damon": entity_frame.damon or {},
    }
