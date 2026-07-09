from __future__ import annotations

import asyncio
import threading
from collections import deque
from collections.abc import Iterable, Iterator
from pathlib import Path

from rich.console import Group
from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Input, Static

from groop.config import GroopConfig, load
from groop.model import Frame
from groop.record.ring import HistoryRing
from groop.registry import REGISTRY
from groop.snapshot import create as create_snapshot

from .banner import render_banner
from .drill import DrillDownScreen
from .keys import BINDINGS, key_help
from .table import SORT_ORDER, RenderedRows, available_profiles, normalize_profile_name, render_container_table
from .tree import render_tree_table


class FilterScreen(Screen[str | None]):
    def __init__(self, value: str) -> None:
        super().__init__()
        self.value = value

    def compose(self) -> ComposeResult:
        yield VerticalScroll(Input(value=self.value, placeholder="filter by name or cgroup path", id="filter-input"))

    def on_mount(self) -> None:
        self.query_one("#filter-input", Input).focus()

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


class GroopApp(App[None]):
    CSS = """
    Screen {
        layout: vertical;
    }
    #banner {
        height: auto;
        padding: 0 1;
    }
    #body {
        height: 1fr;
        overflow: auto;
        padding: 0 1;
    }
    #status {
        height: auto;
        padding: 0 1;
    }
    """
    BINDINGS = BINDINGS

    def __init__(
        self,
        frame_source: Iterable[Frame] | Iterator[Frame],
        *,
        config: GroopConfig | None = None,
        cgroup_root: Path | None = None,
        proc_root: Path = Path("/proc"),
        ring: HistoryRing | None = None,
        profile: str | None = None,
    ) -> None:
        super().__init__()
        self.config = config or load()
        self.cgroup_root = cgroup_root or self.config.cgroup_root
        self.proc_root = proc_root
        self.ring = ring or HistoryRing.from_config(self.config)
        self._frame_source = iter(frame_source)
        self.current_frame: Frame | None = None
        self.frames_received = 0
        self.view_mode = self.config.default_view if self.config.default_view in {"tree", "container"} else "tree"
        self.profile_name = normalize_profile_name(self.config, profile)
        self.profile_order = available_profiles(self.config)
        self.sort_by = SORT_ORDER[0]
        self.filter_text = ""
        self.banner_collapsed = False
        self.selected_key: str | None = None
        self._visible_row_keys: tuple[str, ...] = ()
        self._worker_done = threading.Event()
        self._recent_frames: deque[Frame] = deque(maxlen=max(1, self.config.snapshots.frames))

    def compose(self) -> ComposeResult:
        yield Static(id="banner")
        yield Static(id="body")
        yield Static(id="status")

    def on_mount(self) -> None:
        self.run_worker(self._consume_frames, thread=True, exclusive=True)
        self._refresh_status("waiting for frames")

    def _consume_frames(self) -> None:
        try:
            for frame in self._frame_source:
                self.call_from_thread(self._apply_frame, frame)
        finally:
            self._worker_done.set()

    def _apply_frame(self, frame: Frame) -> None:
        self.current_frame = frame
        self.frames_received += 1
        self.ring.append_frame(frame)
        self._recent_frames.append(frame)
        if self.selected_key not in frame.entities:
            self.selected_key = None
        self._refresh_view()

    def _refresh_view(self) -> None:
        frame = self.current_frame
        if frame is None:
            return
        banner_snapshot = render_banner(frame, self.config, collapsed=self.banner_collapsed)
        self.query_one("#banner", Static).update("\n".join(banner_snapshot.lines))
        width = max(80, self.size.width or 80)
        rendered = self._render_rows(frame, width=width)
        self._visible_row_keys = rendered.row_keys
        if self.selected_key not in self._visible_row_keys and self._visible_row_keys:
            self.selected_key = self._visible_row_keys[0]
            rendered = self._render_rows(frame, width=width)
            self._visible_row_keys = rendered.row_keys
        self.query_one("#body", Static).update(Group(rendered.table))
        self._refresh_status(
            f"view={self.view_mode} profile={self.profile_name} sort={self.sort_by} "
            f"rows={len(self._visible_row_keys)} filter={self.filter_text or '-'} frames={self.frames_received}"
        )

    def _render_rows(self, frame: Frame, *, width: int) -> RenderedRows:
        kwargs = {
            "width": width,
            "profile": self.profile_name,
            "sort_by": self.sort_by,
            "filter_text": self.filter_text,
            "selected_key": self.selected_key,
        }
        if self.view_mode == "container":
            return render_container_table(frame, self.config, **kwargs)
        return render_tree_table(frame, self.config, **kwargs)

    def _refresh_status(self, text: str) -> None:
        self.query_one("#status", Static).update(text)

    def action_toggle_view(self) -> None:
        self.view_mode = "container" if self.view_mode == "tree" else "tree"
        self._refresh_view()

    def action_cycle_profile(self) -> None:
        index = self.profile_order.index(self.profile_name) if self.profile_name in self.profile_order else 0
        self.profile_name = self.profile_order[(index + 1) % len(self.profile_order)]
        self._refresh_view()

    def action_cycle_sort(self) -> None:
        index = SORT_ORDER.index(self.sort_by) if self.sort_by in SORT_ORDER else 0
        self.sort_by = SORT_ORDER[(index + 1) % len(SORT_ORDER)]
        self._refresh_view()

    def action_toggle_banner(self) -> None:
        self.banner_collapsed = not self.banner_collapsed
        self._refresh_view()

    def action_select_prev(self) -> None:
        self._move_selection(-1)

    def action_select_next(self) -> None:
        self._move_selection(1)

    def _move_selection(self, delta: int) -> None:
        if not self._visible_row_keys:
            return
        if self.selected_key not in self._visible_row_keys:
            self.selected_key = self._visible_row_keys[0]
        else:
            index = self._visible_row_keys.index(self.selected_key)
            self.selected_key = self._visible_row_keys[(index + delta) % len(self._visible_row_keys)]
        self._refresh_view()

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
            )
        )

    def action_create_snapshot(self) -> None:
        if self.current_frame is None or self.selected_key is None or self.selected_key not in self.current_frame.entities:
            return
        previous_frames = list(self._recent_frames)
        if previous_frames and previous_frames[-1] is self.current_frame:
            previous_frames = previous_frames[:-1]
        try:
            path = create_snapshot(
                self.selected_key,
                self.ring,
                self.current_frame,
                self.config,
                cgroup_root=self.cgroup_root,
                previous_frames=previous_frames,
                providers_status=_providers_status(self.current_frame, self.selected_key),
            )
        except OSError as exc:
            self._refresh_status(f"snapshot failed: {exc}")
            return
        self._refresh_status(f"snapshot saved: {path}")

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


def run_ui(
    frame_source: Iterable[Frame] | Iterator[Frame],
    *,
    config: GroopConfig | None = None,
    cgroup_root: Path | None = None,
    proc_root: Path = Path("/proc"),
    smoke: bool = False,
    profile: str | None = None,
) -> str | int:
    if smoke:
        return asyncio.run(_run_ui_smoke(frame_source, config=config, cgroup_root=cgroup_root, proc_root=proc_root, profile=profile))
    app = GroopApp(frame_source, config=config, cgroup_root=cgroup_root, proc_root=proc_root, profile=profile)
    app.run()
    return 0


async def _run_ui_smoke(
    frame_source: Iterable[Frame] | Iterator[Frame],
    *,
    config: GroopConfig | None,
    cgroup_root: Path | None,
    proc_root: Path,
    profile: str | None,
) -> str:
    app = GroopApp(frame_source, config=config, cgroup_root=cgroup_root, proc_root=proc_root, profile=profile)
    async with app.run_test(size=(140, 40)) as pilot:
        for _ in range(20):
            await pilot.pause()
            if app.frames_received:
                break
        if not app.frames_received:
            raise RuntimeError("ui smoke did not receive a frame")
    return f"ui smoke ok frames={app.frames_received} view={app.view_mode} profile={app.profile_name}"


def _render_glossary() -> str:
    lines = ["KEYS", *[f"  {line}" for line in key_help()], "", "GLOSSARY"]
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
            "  DAMON hot/warm/cold: passive snapshots from tried_regions, shown only when a readable session covers the entity.",
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
