from __future__ import annotations

import asyncio
import json
from pathlib import Path

from textual.widgets import Static
from textual.widgets import Input

from conftest import fixture_frame, fixture_root
from groop.config import GroopConfig, SnapshotConfig
from groop.damon.control import APPROVAL_TEXT
from groop.drift.origin import ShowResult
from groop.model import Frame
from groop.record.replay import ReplayDriver
from groop.snapshot.bundle import _extract_archive
from groop.ui.app import GroopApp
from groop.ui.damon_control import DamonConfirmScreen
from groop.ui.drill import DrillDownScreen
from groop.ui.hostmem import HostMemoryScreen

GAME_KEY = "system.slice/docker-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.scope"


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


def _damon_root(tmp_path: Path, *, slots: tuple[str, ...] = ("off", "off")) -> Path:
    root = tmp_path / "kdamonds"
    root.mkdir(parents=True)
    (root / "nr_kdamonds").write_text(f"{len(slots)}\n")
    for idx, state in enumerate(slots):
        slot = root / str(idx)
        slot.mkdir()
        (slot / "state").write_text(f"{state}\n")
    return root


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


def test_pilot_snapshot_hotkey_collects_fresh_systemd_and_docker_metadata(tmp_path: Path) -> None:
    async def run() -> None:
        def systemctl_show(unit: str, _properties: tuple[str, ...]) -> ShowResult:
            return ShowResult(stdout=f"Unit={unit}\nMemoryHigh=123\n", returncode=0)

        def docker_inspect(container_id: str):
            return [{"Id": container_id, "Name": "/demo", "Image": "image:latest", "Config": {"Env": ["SECRET=x"], "Labels": {"secret": "y"}, "User": "1000"}}]

        app = GroopApp(
            iter([fixture_frame()]),
            config=GroopConfig(snapshots=SnapshotConfig(dir=tmp_path, redact=True)),
            cgroup_root=fixture_root() / "cgroupfs" / "gstammtisch",
            proc_root=fixture_root() / "procfs" / "network",
            snapshot_systemctl_show=systemctl_show,
            snapshot_docker_inspect=docker_inspect,
        )
        async with app.run_test(size=(140, 40)) as pilot:
            await _wait_for_frame(app)(pilot)
            app.selected_key = GAME_KEY
            app._refresh_view()
            await pilot.press("x")
            await pilot.pause()

    asyncio.run(run())
    bundles = list(tmp_path.glob("groop-incident-*"))
    assert len(bundles) == 1
    root = tmp_path / "bundle"
    root.mkdir()
    _extract_archive(bundles[0], root)
    assert (root / "entity" / "systemctl-show.txt").read_text() == "Unit=docker-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.scope\nMemoryHigh=123\n"
    docker = json.loads((root / "entity" / "docker-inspect.json").read_text())
    assert docker["Config"] == {"User": "1000"}
    providers = json.loads((root / "providers-status.json").read_text())
    assert providers["snapshot"]["systemctl"]["status"] == "ok"
    assert providers["snapshot"]["docker"]["status"] == "ok"


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


def test_pilot_damon_vaddr_modal_requires_confirmation_and_starts_fixture_session(tmp_path: Path) -> None:
    async def run() -> None:
        damon_root = _damon_root(tmp_path)
        state_dir = tmp_path / "state"
        app = GroopApp(
            iter([fixture_frame()]),
            config=GroopConfig(default_view="tree", default_column_profile="auto"),
            cgroup_root=fixture_root() / "cgroupfs" / "gstammtisch",
            proc_root=fixture_root() / "procfs" / "network",
            damon_root=damon_root,
            damon_state_dir=state_dir,
            damon_require_root=False,
        )
        async with app.run_test(size=(140, 40)) as pilot:
            await _wait_for_frame(app)(pilot)
            app.selected_key = GAME_KEY
            app._refresh_view()
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, DrillDownScreen)
            await pilot.press("d")
            await pilot.pause()
            assert isinstance(app.screen, DamonConfirmScreen)
            input_widget = app.screen.query_one("#damon-confirm-input", Input)
            input_widget.value = "NO"
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, DamonConfirmScreen)
            assert "typed confirmation" in str(app.screen.query_one("#damon-confirm-body", Static).renderable)
            input_widget = app.screen.query_one("#damon-confirm-input", Input)
            input_widget.value = APPROVAL_TEXT
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, DrillDownScreen)

        assert (damon_root / "0" / "contexts" / "0" / "operations").read_text().strip() == "vaddr"
        assert (state_dir / "damon" / "kdamond-0.json").exists()

    asyncio.run(run())


def test_pilot_damon_paddr_modal_starts_and_duplicate_is_reported(tmp_path: Path) -> None:
    async def run() -> None:
        damon_root = _damon_root(tmp_path)
        state_dir = tmp_path / "state"
        app = GroopApp(
            iter([fixture_frame()]),
            config=GroopConfig(default_view="tree", default_column_profile="auto"),
            cgroup_root=fixture_root() / "cgroupfs" / "gstammtisch",
            proc_root=fixture_root() / "procfs" / "network",
            damon_root=damon_root,
            damon_state_dir=state_dir,
            damon_require_root=False,
        )
        async with app.run_test(size=(140, 40)) as pilot:
            await _wait_for_frame(app)(pilot)
            await pilot.press("m")
            await pilot.pause()
            assert isinstance(app.screen, HostMemoryScreen)
            await pilot.press("p")
            await pilot.pause()
            assert isinstance(app.screen, DamonConfirmScreen)
            app.screen.query_one("#damon-confirm-input", Input).value = APPROVAL_TEXT
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, HostMemoryScreen)
            await pilot.press("p")
            await pilot.pause()
            assert isinstance(app.screen, HostMemoryScreen)
            assert "paddr DAMON session already exists" in str(app.screen.query_one("#hostmem-body", Static).renderable)

        assert (damon_root / "0" / "contexts" / "0" / "operations").read_text().strip() == "paddr"
        assert (state_dir / "damon" / "kdamond-0.json").exists()

    asyncio.run(run())


def test_pilot_damon_stop_surface_stops_only_groop_owned_sessions(tmp_path: Path) -> None:
    async def run() -> None:
        damon_root = _damon_root(tmp_path, slots=("on", "off"))
        state_dir = tmp_path / "state"
        app = GroopApp(
            iter([fixture_frame()]),
            config=GroopConfig(default_view="tree", default_column_profile="auto"),
            cgroup_root=fixture_root() / "cgroupfs" / "gstammtisch",
            proc_root=fixture_root() / "procfs" / "network",
            damon_root=damon_root,
            damon_state_dir=state_dir,
            damon_require_root=False,
        )
        async with app.run_test(size=(140, 40)) as pilot:
            await _wait_for_frame(app)(pilot)
            app.selected_key = GAME_KEY
            app._refresh_view()
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("d")
            await pilot.pause()
            app.screen.query_one("#damon-confirm-input", Input).value = APPROVAL_TEXT
            await pilot.press("enter")
            await pilot.pause()
            assert (state_dir / "damon" / "kdamond-1.json").exists()
            await pilot.press("s")
            await pilot.pause()
            assert isinstance(app.screen, DrillDownScreen)

        assert (damon_root / "0" / "state").read_text().strip() == "on"
        assert (damon_root / "1" / "state").read_text().strip() == "off"
        assert not (state_dir / "damon" / "kdamond-1.json").exists()

    asyncio.run(run())
