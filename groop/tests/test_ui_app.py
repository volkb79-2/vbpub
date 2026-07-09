from __future__ import annotations

import asyncio
from pathlib import Path

from textual.widgets import Static

from conftest import fixture_frame, fixture_root
from groop.config import GroopConfig, SnapshotConfig
from groop.model import Frame
from groop.record.replay import ReplayDriver
from groop.ui.app import GroopApp
from groop.ui.drill import DrillDownScreen
from groop.ui.hostmem import HostMemoryScreen


def _make_app() -> GroopApp:
    return GroopApp(
        iter([fixture_frame()]),
        config=GroopConfig(default_view="tree", default_column_profile="auto"),
        cgroup_root=fixture_root() / "cgroupfs" / "gstammtisch",
        proc_root=fixture_root() / "procfs" / "network",
    )


def _replay_app(*, step: bool = True) -> GroopApp:
    base = fixture_frame()
    later = Frame(
        schema_version=base.schema_version,
        ts=base.ts + base.interval_s,
        interval_s=base.interval_s,
        host=base.host,
        entities=base.entities,
    )
    return GroopApp(
        (),
        config=GroopConfig(default_view="tree", default_column_profile="auto"),
        cgroup_root=fixture_root() / "cgroupfs" / "gstammtisch",
        proc_root=fixture_root() / "procfs" / "network",
        replay_driver=ReplayDriver([base, later]),
        replay_step=step,
    )


def _wait_for_frame(app: GroopApp):
    async def wait(pilot) -> None:
        for _ in range(10):
            await pilot.pause()
            if app.frames_received:
                break

    return wait


def _status_text(app: GroopApp) -> str:
    return str(app.query_one("#status", Static).renderable)


def test_pilot_toggle_view_and_profile_cycle() -> None:
    async def run() -> None:
        app = _make_app()
        async with app.run_test(size=(140, 40)) as pilot:
            await _wait_for_frame(app)(pilot)
            assert app.view_mode == "tree"
            assert app.profile_name == "auto"
            await pilot.press("f5")
            await pilot.pause()
            assert app.view_mode == "container"
            await pilot.press("p")
            await pilot.pause()
            assert app.profile_name == "triage"

    asyncio.run(run())


def test_pilot_drilldown_open_and_close() -> None:
    async def run() -> None:
        app = _make_app()
        async with app.run_test(size=(140, 40)) as pilot:
            await _wait_for_frame(app)(pilot)
            assert app.selected_key == ""
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, DrillDownScreen)
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, DrillDownScreen)

    asyncio.run(run())


def test_pilot_tree_branch_collapse_and_expand_preserves_selection() -> None:
    async def run() -> None:
        app = _make_app()
        async with app.run_test(size=(140, 40)) as pilot:
            await _wait_for_frame(app)(pilot)
            assert app.selected_key == ""
            assert "soulmask.slice" in app._visible_row_keys
            await pilot.press("left")
            await pilot.pause()
            assert app.selected_key == ""
            assert app._visible_row_keys == ("",)
            await pilot.press("right")
            await pilot.pause()
            assert app.selected_key == ""
            assert "soulmask.slice" in app._visible_row_keys

    asyncio.run(run())


def test_collapsed_tree_filter_still_reveals_matching_descendants() -> None:
    async def run() -> None:
        app = _make_app()
        async with app.run_test(size=(140, 40)) as pilot:
            await _wait_for_frame(app)(pilot)
            await pilot.press("left")
            await pilot.pause()
            assert app._visible_row_keys == ("",)
            app.filter_text = "paks"
            app._refresh_view()
            await pilot.pause()
            assert "soulmask.slice/soulmask-paks.slice" in app._visible_row_keys

    asyncio.run(run())


def test_pilot_replay_status_and_step_controls() -> None:
    async def run() -> None:
        app = _replay_app()
        async with app.run_test(size=(140, 40)) as pilot:
            await _wait_for_frame(app)(pilot)
            assert "mode=REPLAY paused speed=1x frame=1/2" in _status_text(app)
            await pilot.press("space")
            await pilot.pause()
            assert "mode=REPLAY playing" in _status_text(app)
            await pilot.press("full_stop")
            await pilot.pause()
            assert "frame=2/2" in _status_text(app)

    asyncio.run(run())


def test_pilot_reserved_v2_action_reports_disabled_message() -> None:
    async def run() -> None:
        app = _make_app()
        async with app.run_test(size=(140, 40)) as pilot:
            await _wait_for_frame(app)(pilot)
            await pilot.press("k")
            await pilot.pause()
            assert "requires future --admin mode" in _status_text(app)

    asyncio.run(run())


def test_pilot_snapshot_hotkey_writes_bundle(tmp_path: Path) -> None:
    async def run() -> None:
        app = GroopApp(
            iter([fixture_frame()]),
            config=GroopConfig(snapshots=SnapshotConfig(dir=tmp_path)),
            cgroup_root=fixture_root() / "cgroupfs" / "gstammtisch",
            proc_root=fixture_root() / "procfs" / "network",
        )
        async with app.run_test(size=(140, 40)) as pilot:
            await _wait_for_frame(app)(pilot)
            await pilot.press("x")
            await pilot.pause()

        assert len(list(tmp_path.glob("groop-incident-*"))) == 1

    asyncio.run(run())


def test_pilot_host_memory_screen_open_and_close() -> None:
    async def run() -> None:
        app = _make_app()
        async with app.run_test(size=(140, 40)) as pilot:
            await _wait_for_frame(app)(pilot)
            await pilot.press("m")
            await pilot.pause()
            assert isinstance(app.screen, HostMemoryScreen)
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, HostMemoryScreen)

    asyncio.run(run())
