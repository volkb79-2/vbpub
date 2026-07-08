from __future__ import annotations

import asyncio
from pathlib import Path

from conftest import fixture_frame, fixture_root
from groop.config import GroopConfig
from groop.ui.app import GroopApp
from groop.ui.drill import DrillDownScreen


def _make_app() -> GroopApp:
    return GroopApp(
        iter([fixture_frame()]),
        config=GroopConfig(default_view="tree", default_column_profile="auto"),
        cgroup_root=fixture_root() / "cgroupfs" / "gstammtisch",
        proc_root=fixture_root() / "procfs" / "network",
    )


def test_pilot_toggle_view_and_profile_cycle() -> None:
    async def run() -> None:
        app = _make_app()
        async with app.run_test(size=(140, 40)) as pilot:
            for _ in range(10):
                await pilot.pause()
                if app.frames_received:
                    break
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
            for _ in range(10):
                await pilot.pause()
                if app.frames_received:
                    break
            assert app.selected_key == ""
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, DrillDownScreen)
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, DrillDownScreen)

    asyncio.run(run())
