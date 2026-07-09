from __future__ import annotations

import asyncio
from pathlib import Path

from conftest import fixture_frame, fixture_root
from groop.config import GroopConfig, SnapshotConfig
from groop.ui.app import GroopApp
from groop.ui.drill import DrillDownScreen


def _make_app() -> GroopApp:
    return GroopApp(
        iter([fixture_frame()]),
        config=GroopConfig(default_view="tree", default_column_profile="auto"),
        cgroup_root=fixture_root() / "cgroupfs" / "gstammtisch",
        proc_root=fixture_root() / "procfs" / "network",
    )


def _wait_for_frame(app: GroopApp):
    async def wait(pilot) -> None:
        for _ in range(10):
            await pilot.pause()
            if app.frames_received:
                break

    return wait


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
