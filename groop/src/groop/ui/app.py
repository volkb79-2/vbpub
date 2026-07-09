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
from textual.timer import Timer
from textual.widgets import Input, Static

from groop.config import GroopConfig, load
from groop.damon.passive import DEFAULT_DAMON_ROOT
from groop.model import Frame
from groop.record.replay import ReplayDriver
from groop.record.ring import HistoryRing
from groop.registry import REGISTRY
from groop.snapshot import create as create_snapshot
from groop.snapshot.enrich import DockerSnapshotInspect, SystemctlSnapshotRunner, collect_docker_inspect, collect_systemctl_show

from .banner import render_banner
from .drill import DrillDownScreen
from .hostmem import HostMemoryScreen
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
        damon_root: Path = DEFAULT_DAMON_ROOT,
        damon_state_dir: Path | None = None,
        damon_require_root: bool = True,
        ring: HistoryRing | None = None,
        profile: str | None = None,
        replay_driver: ReplayDriver | None = None,
        replay_step: bool = False,
        replay_speed: float = 1.0,
        snapshot_systemctl_show: SystemctlSnapshotRunner | None = None,
        snapshot_docker_inspect: DockerSnapshotInspect | None = None,
    ) -> None:
        super().__init__()
        self.config = config or load()
        self.cgroup_root = cgroup_root or self.config.cgroup_root
        self.proc_root = proc_root
        self.damon_root = damon_root
        self.damon_state_dir = damon_state_dir
        self.damon_require_root = damon_require_root
        self.ring = ring or HistoryRing.from_config(self.config)
        self._frame_source = iter(frame_source)
        self._replay_driver = replay_driver
        self._replay_paused = replay_driver is not None and replay_step
        self._replay_speed = replay_speed if replay_speed > 0 else 1.0
        self._replay_speed_levels = (1.0, 2.0, 4.0, 8.0)
        self._replay_timer: Timer | None = None
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
        self._collapsed_tree_keys: set[str] = set()
        self._worker_done = threading.Event()
        self._recent_frames: deque[Frame] = deque(maxlen=max(1, self.config.snapshots.frames))
        self._snapshot_systemctl_show = snapshot_systemctl_show
        self._snapshot_docker_inspect = snapshot_docker_inspect

    def compose(self) -> ComposeResult:
        yield Static(id="banner")
        yield Static(id="body")
        yield Static(id="status")

    def on_mount(self) -> None:
        if self._replay_driver is not None:
            self._apply_frame(self._replay_driver.current)
            if not self._replay_paused:
                self._schedule_replay_tick()
            return
        self.run_worker(self._consume_frames, thread=True, exclusive=True)
        self._refresh_status("mode=LIVE waiting for frames")

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
        self._refresh_status(self._status_text())

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
        return render_tree_table(frame, self.config, collapsed_keys=self._collapsed_tree_keys, **kwargs)

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

    def action_collapse_tree(self) -> None:
        if self.view_mode != "tree" or self.current_frame is None or self.selected_key is None:
            return
        children = self._child_keys(self.selected_key)
        if children and self.selected_key not in self._collapsed_tree_keys:
            self._collapsed_tree_keys.add(self.selected_key)
            self._refresh_view()
            return
        parent = self.current_frame.entities.get(self.selected_key).entity.parent if self.selected_key in self.current_frame.entities else None
        if parent is not None and parent in self.current_frame.entities:
            self.selected_key = parent
            self._refresh_view()

    def action_expand_tree(self) -> None:
        if self.view_mode != "tree" or self.selected_key is None:
            return
        if self.selected_key in self._collapsed_tree_keys:
            self._collapsed_tree_keys.remove(self.selected_key)
            self._refresh_view()

    def action_toggle_replay_pause(self) -> None:
        if self._replay_driver is None:
            self._refresh_status("replay controls are only available in --replay mode")
            return
        self._replay_paused = not self._replay_paused
        if self._replay_paused:
            self._cancel_replay_timer()
            self._refresh_view()
            return
        self._schedule_replay_tick()
        self._refresh_view()

    def action_replay_step_back(self) -> None:
        if self._replay_driver is None:
            self._refresh_status("replay step is only available in --replay mode")
            return
        self._replay_paused = True
        self._cancel_replay_timer()
        self._apply_frame(self._replay_driver.step(-1))

    def action_replay_step_forward(self) -> None:
        if self._replay_driver is None:
            self._refresh_status("replay step is only available in --replay mode")
            return
        self._replay_paused = True
        self._cancel_replay_timer()
        self._apply_frame(self._replay_driver.step(1))

    def action_replay_speed_up(self) -> None:
        self._set_replay_speed(1)

    def action_replay_speed_down(self) -> None:
        self._set_replay_speed(-1)

    def action_reserved_v2_action(self) -> None:
        self._refresh_status("v2 admin actions are not available in this build; requires future --admin mode")

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
                damon_root=self.damon_root,
                damon_state_dir=self.damon_state_dir,
                damon_require_root=self.damon_require_root,
            )
        )

    def action_create_snapshot(self) -> None:
        if self.current_frame is None or self.selected_key is None or self.selected_key not in self.current_frame.entities:
            return
        previous_frames = list(self._recent_frames)
        if previous_frames and previous_frames[-1] is self.current_frame:
            previous_frames = previous_frames[:-1]
        try:
            systemctl_show, systemctl_status = collect_systemctl_show(self.selected_key, runner=self._snapshot_systemctl_show)
            docker_inspect, docker_status = collect_docker_inspect(self.selected_key, docker_inspect=self._snapshot_docker_inspect)
            providers_status = _providers_status(self.current_frame, self.selected_key)
            providers_status["snapshot"] = {
                "systemctl": systemctl_status,
                "docker": docker_status,
            }
            path = create_snapshot(
                self.selected_key,
                self.ring,
                self.current_frame,
                self.config,
                cgroup_root=self.cgroup_root,
                previous_frames=previous_frames,
                providers_status=providers_status,
                systemctl_show=systemctl_show,
                docker_inspect=docker_inspect,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            self._refresh_status(f"snapshot failed: {exc}")
            return
        self._refresh_status(f"snapshot saved: {path}")

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

    def _child_keys(self, entity_key: str) -> tuple[str, ...]:
        if self.current_frame is None:
            return ()
        return tuple(key for key, entity_frame in self.current_frame.entities.items() if entity_frame.entity.parent == entity_key)

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
        delay_s = max(0.0, (upcoming.ts - current.ts) / self._replay_speed)
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
        rows = len(self._visible_row_keys)
        if self._replay_driver is None:
            if frame is None:
                return "mode=LIVE waiting for frames"
            return (
                f"mode=LIVE view={self.view_mode} profile={self.profile_name} sort={self.sort_by} "
                f"rows={rows} filter={self.filter_text or '-'} frames={self.frames_received} ts={frame.ts:.3f}"
            )
        if frame is None:
            return "mode=REPLAY waiting for frames"
        state = "paused" if self._replay_paused else "playing"
        return (
            f"mode=REPLAY {state} speed={self._replay_speed:g}x frame={self._replay_driver.index + 1}/{self._replay_driver.total} "
            f"view={self.view_mode} profile={self.profile_name} sort={self.sort_by} rows={rows} "
            f"filter={self.filter_text or '-'} ts={frame.ts:.3f} controls=space ,/. +/-"
        )


def run_ui(
    frame_source: Iterable[Frame] | Iterator[Frame],
    *,
    config: GroopConfig | None = None,
    cgroup_root: Path | None = None,
    proc_root: Path = Path("/proc"),
    smoke: bool = False,
    profile: str | None = None,
    replay_driver: ReplayDriver | None = None,
    replay_step: bool = False,
    replay_speed: float = 1.0,
) -> str | int:
    if smoke:
        return asyncio.run(
            _run_ui_smoke(
                frame_source,
                config=config,
                cgroup_root=cgroup_root,
                proc_root=proc_root,
                profile=profile,
                replay_driver=replay_driver,
                replay_step=replay_step,
                replay_speed=replay_speed,
            )
        )
    app = GroopApp(
        frame_source,
        config=config,
        cgroup_root=cgroup_root,
        proc_root=proc_root,
        profile=profile,
        replay_driver=replay_driver,
        replay_step=replay_step,
        replay_speed=replay_speed,
    )
    app.run()
    return 0


async def _run_ui_smoke(
    frame_source: Iterable[Frame] | Iterator[Frame],
    *,
    config: GroopConfig | None,
    cgroup_root: Path | None,
    proc_root: Path,
    profile: str | None,
    replay_driver: ReplayDriver | None,
    replay_step: bool,
    replay_speed: float,
) -> str:
    app = GroopApp(
        frame_source,
        config=config,
        cgroup_root=cgroup_root,
        proc_root=proc_root,
        profile=profile,
        replay_driver=replay_driver,
        replay_step=replay_step,
        replay_speed=replay_speed,
    )
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
