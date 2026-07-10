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


def _static_text(w: Static) -> str:
    """Get the displayed text content of a ``Static`` widget.

    ``Static.renderable`` exists in the declared Textual 0.x range but was
    removed by Textual 8. ``Widget.render()`` is the supported public method
    in both environments.
    """
    return str(w.render())


def _status_text(app: GroopApp) -> str:
    return _static_text(app.query_one("#status", Static))


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


def test_pilot_replay_first_and_last_jump() -> None:
    async def run() -> None:
        app = _replay_app()
        async with app.run_test(size=(140, 40)) as pilot:
            await _wait_for_frame(app)(pilot)
            assert "frame=1/2" in _status_text(app)
            # Jump to last
            await pilot.press("end")
            await pilot.pause()
            assert "frame=2/2" in _status_text(app)
            assert "paused" in _status_text(app)
            # Jump back to first
            await pilot.press("home")
            await pilot.pause()
            assert "frame=1/2" in _status_text(app)

    asyncio.run(run())


def test_pilot_replay_jump_prompt_with_frame_number() -> None:
    async def run() -> None:
        app = _replay_app()
        async with app.run_test(size=(140, 40)) as pilot:
            await _wait_for_frame(app)(pilot)
            assert "frame=1/2" in _status_text(app)
            # Open jump prompt
            await pilot.press("j")
            await pilot.pause()
            from groop.ui.app import JumpScreen
            assert isinstance(app.screen, JumpScreen)
            # Enter frame number 2
            input_widget = app.screen.query_one("#jump-input", Input)
            input_widget.value = "2"
            await pilot.press("enter")
            await pilot.pause()
            assert "frame=2/2" in _status_text(app)

    asyncio.run(run())


def test_pilot_replay_jump_prompt_invalid_input_preserves_current_frame() -> None:
    async def run() -> None:
        app = _replay_app()
        async with app.run_test(size=(140, 40)) as pilot:
            await _wait_for_frame(app)(pilot)
            assert "frame=1/2" in _status_text(app)
            # Open jump prompt
            await pilot.press("j")
            await pilot.pause()
            from groop.ui.app import JumpScreen
            assert isinstance(app.screen, JumpScreen)
            # Enter invalid input
            input_widget = app.screen.query_one("#jump-input", Input)
            input_widget.value = "not-a-number"
            await pilot.press("enter")
            await pilot.pause()
            # Frame unchanged, status shows error
            assert app._replay_driver.index == 0
            assert "invalid jump input" in _status_text(app)

    asyncio.run(run())


def test_pilot_replay_jump_prompt_rejects_nonfinite_input() -> None:
    async def run() -> None:
        app = _replay_app()
        async with app.run_test(size=(140, 40)) as pilot:
            await _wait_for_frame(app)(pilot)
            await pilot.press("j")
            await pilot.pause()
            input_widget = app.screen.query_one("#jump-input", Input)
            input_widget.value = "nan"
            await pilot.press("enter")
            await pilot.pause()
            assert app._replay_driver.index == 0
            assert "finite frame number or epoch timestamp" in _status_text(app)

    asyncio.run(run())


def test_pilot_replay_jump_out_of_range_frame_number() -> None:
    async def run() -> None:
        app = _replay_app()
        async with app.run_test(size=(140, 40)) as pilot:
            await _wait_for_frame(app)(pilot)
            assert "frame=1/2" in _status_text(app)
            await pilot.press("j")
            await pilot.pause()
            input_widget = app.screen.query_one("#jump-input", Input)
            input_widget.value = "99"
            await pilot.press("enter")
            await pilot.pause()
            # Frame unchanged
            assert app._replay_driver.index == 0
            assert "invalid frame number" in _status_text(app)

    asyncio.run(run())


def test_pilot_replay_jump_prompt_with_timestamp() -> None:
    async def run() -> None:
        base = fixture_frame()
        later = Frame(
            schema_version=base.schema_version,
            ts=base.ts + base.interval_s,
            interval_s=base.interval_s,
            host=base.host,
            entities=base.entities,
        )
        app = GroopApp(
            (),
            config=GroopConfig(default_view="tree", default_column_profile="auto"),
            cgroup_root=fixture_root() / "cgroupfs" / "gstammtisch",
            proc_root=fixture_root() / "procfs" / "network",
            replay_driver=ReplayDriver([base, later]),
            replay_step=True,
        )
        async with app.run_test(size=(140, 40)) as pilot:
            await _wait_for_frame(app)(pilot)
            assert "frame=1/2" in _status_text(app)
            await pilot.press("j")
            await pilot.pause()
            input_widget = app.screen.query_one("#jump-input", Input)
            input_widget.value = str(later.ts)
            await pilot.press("enter")
            await pilot.pause()
            assert "frame=2/2" in _status_text(app)

    asyncio.run(run())


def test_pilot_replay_jump_in_non_replay_mode_shows_unavailable_message() -> None:
    async def run() -> None:
        app = _make_app()
        async with app.run_test(size=(140, 40)) as pilot:
            await _wait_for_frame(app)(pilot)
            # home/end/j all show unavailable in live mode
            await pilot.press("home")
            await pilot.pause()
            assert "only available in --replay mode" in _status_text(app)
            await pilot.press("end")
            await pilot.pause()
            assert "only available in --replay mode" in _status_text(app)
            await pilot.press("j")
            await pilot.pause()
            assert "only available in --replay mode" in _status_text(app)

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


def test_pilot_snapshot_running_status_appears_immediately(tmp_path: Path) -> None:
    async def run() -> None:
        import time as _time

        def slow_systemctl(unit: str, _properties: tuple[str, ...]) -> ShowResult:
            _time.sleep(0.5)
            return ShowResult(stdout=f"Unit={unit}\n", returncode=0)

        app = GroopApp(
            iter([fixture_frame()]),
            config=GroopConfig(snapshots=SnapshotConfig(dir=tmp_path)),
            cgroup_root=fixture_root() / "cgroupfs" / "gstammtisch",
            proc_root=fixture_root() / "procfs" / "network",
            snapshot_systemctl_show=slow_systemctl,
        )
        async with app.run_test(size=(140, 40)) as pilot:
            await _wait_for_frame(app)(pilot)
            app.selected_key = GAME_KEY
            app._refresh_view()
            app.action_create_snapshot()
            assert "snapshot running:" in _status_text(app)
            assert GAME_KEY in _status_text(app)
            for _ in range(30):
                await pilot.pause()
                if app._snapshot_in_progress is False:
                    break
            assert app._snapshot_in_progress is False

    asyncio.run(run())
    assert len(list(tmp_path.glob("groop-incident-*"))) == 1


def test_pilot_snapshot_duplicate_keypress_guard(tmp_path: Path) -> None:
    """Verify a second x while snapshot is in-progress shows 'already running'."""
    async def run() -> None:
        app = GroopApp(
            iter([fixture_frame()]),
            config=GroopConfig(snapshots=SnapshotConfig(dir=tmp_path)),
            cgroup_root=fixture_root() / "cgroupfs" / "gstammtisch",
            proc_root=fixture_root() / "procfs" / "network",
        )
        async with app.run_test(size=(140, 40)) as pilot:
            await _wait_for_frame(app)(pilot)
            # Simulate an in-progress snapshot
            app._snapshot_in_progress = True
            await pilot.press("x")
            assert "snapshot already running" in _status_text(app)

    asyncio.run(run())
    # No bundle written since the guard blocked real work
    assert len(list(tmp_path.glob("groop-incident-*"))) == 0


def test_pilot_snapshot_success_reports_path(tmp_path: Path) -> None:
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
            # Wait for the worker to finish and status to update
            for _ in range(20):
                await pilot.pause()
                if "snapshot saved:" in _status_text(app):
                    break
            assert "snapshot saved:" in _status_text(app)
            assert "groop-incident" in _status_text(app)

    asyncio.run(run())


def test_pilot_snapshot_handled_exception_reports_failure(tmp_path: Path) -> None:
    """Use a provider that raises RuntimeError (not caught by collect_systemctl_show)
    to trigger the failure path in the snapshot worker."""
    async def run() -> None:
        def failing_systemctl(unit: str, _properties: tuple[str, ...]) -> ShowResult:
            raise RuntimeError("simulated provider failure")

        app = GroopApp(
            iter([fixture_frame()]),
            config=GroopConfig(snapshots=SnapshotConfig(dir=tmp_path)),
            cgroup_root=fixture_root() / "cgroupfs" / "gstammtisch",
            proc_root=fixture_root() / "procfs" / "network",
            snapshot_systemctl_show=failing_systemctl,
        )
        async with app.run_test(size=(140, 40)) as pilot:
            await _wait_for_frame(app)(pilot)
            app.selected_key = GAME_KEY
            app._refresh_view()
            await pilot.press("x")
            # Wait for the worker to finish
            for _ in range(20):
                await pilot.pause()
                if "snapshot failed:" in _status_text(app):
                    break
            assert "snapshot failed:" in _status_text(app)

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
            assert "typed confirmation" in _static_text(app.screen.query_one("#damon-confirm-body", Static))
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
            assert "paddr DAMON session already exists" in _static_text(app.screen.query_one("#hostmem-body", Static))

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


# ── P50: Mouse Table Interactions ─────────────────────────────────────────


GAME_KEY_2 = "besteffort.slice/docker-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.scope"


def test_p50_header_click_sorts_by_column() -> None:
    """Click a column header to sort by it; repeated click toggles direction."""
    async def run() -> None:
        from textual.widgets._data_table import ColumnKey
        from rich.text import Text

        app = _make_app()
        async with app.run_test(size=(140, 40)) as pilot:
            await _wait_for_frame(app)(pilot)
            mt = app.query_one("#body-table")

            # Default sort is pressure descending (vpressure)
            assert "vpressure" in _status_text(app)

            # Post a HeaderSelected for the ram column
            ram_idx = mt._col_keys.index("ram")
            mt.post_message(mt.HeaderSelected(mt, ColumnKey("ram"), ram_idx, Text("RAM")))
            await pilot.pause()
            assert "vram" in _status_text(app)  # ram descending by default

            # Click RAM again to toggle direction
            mt.post_message(mt.HeaderSelected(mt, ColumnKey("ram"), ram_idx, Text("RAM")))
            await pilot.pause()
            assert "^ram" in _status_text(app)  # now ascending

    asyncio.run(run())


def test_p50_header_click_toggles_direction() -> None:
    """Repeated header click on same column toggles asc/desc."""
    async def run() -> None:
        from textual.widgets._data_table import ColumnKey
        from rich.text import Text

        app = _make_app()
        async with app.run_test(size=(140, 40)) as pilot:
            await _wait_for_frame(app)(pilot)
            mt = app.query_one("#body-table")

            # Sort by name (ascending by default)
            name_idx = mt._col_keys.index("name")
            mt.post_message(mt.HeaderSelected(mt, ColumnKey("name"), name_idx, Text("NAME")))
            await pilot.pause()
            assert "^name" in _status_text(app)

            # Toggle to descending
            mt.post_message(mt.HeaderSelected(mt, ColumnKey("name"), name_idx, Text("NAME")))
            await pilot.pause()
            assert "vname" in _status_text(app)

            # Toggle back to ascending
            mt.post_message(mt.HeaderSelected(mt, ColumnKey("name"), name_idx, Text("NAME")))
            await pilot.pause()
            assert "^name" in _status_text(app)

    asyncio.run(run())


def test_p50_row_highlight_updates_selected_key() -> None:
    """Cursor movement (click row / up/down) updates selected_key."""
    async def run() -> None:
        app = _make_app()
        async with app.run_test(size=(140, 40)) as pilot:
            await _wait_for_frame(app)(pilot)
            mt = app.query_one("#body-table")

            # Initially cursor is at row 0 (root entity "")
            assert app.selected_key in ("", app._visible_row_keys[0])

            # Press down to move cursor
            await pilot.press("down")
            await pilot.pause()
            assert app.selected_key == app._visible_row_keys[1]

    asyncio.run(run())


def test_p50_row_click_drilldown() -> None:
    """Row click or Enter opens drill-down for real entities."""
    async def run() -> None:
        app = _make_app()
        async with app.run_test(size=(140, 40)) as pilot:
            await _wait_for_frame(app)(pilot)
            mt = app.query_one("#body-table")

            # Navigate to a visible entity row and press Enter
            await pilot.press("down")
            await pilot.pause()
            # Row 1 should be a real entity
            rk = app._visible_row_keys[1]
            assert not rk.startswith("__empty__")
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, DrillDownScreen)
            await pilot.press("escape")
            await pilot.pause()

    asyncio.run(run())


def test_p50_empty_placeholder_does_not_open_drill() -> None:
    """Enter on an empty placeholder row is a no-op (no drill-down)."""
    async def run() -> None:
        app = _make_app()
        async with app.run_test(size=(140, 40)) as pilot:
            await _wait_for_frame(app)(pilot)
            mt = app.query_one("#body-table")

            # Force a filter that produces no matches to create empty state
            app.filter_text = "ZZZZ_NONEXISTENT_ZZZZ"
            app._refresh_view()
            await pilot.pause()
            assert len(app._visible_row_keys) == 1
            assert app._visible_row_keys[0].startswith("__empty__")

            # Enter on the empty placeholder
            await pilot.press("enter")
            await pilot.pause()
            assert not isinstance(app.screen, DrillDownScreen)

    asyncio.run(run())


def test_p50_refresh_preserves_cursor() -> None:
    """Refresh (live update) restores cursor to the previously selected row."""
    async def run() -> None:
        app = _make_app()
        async with app.run_test(size=(140, 40)) as pilot:
            await _wait_for_frame(app)(pilot)
            mt = app.query_one("#body-table")

            initial_key = app.selected_key

            # Trigger a refresh
            app._refresh_view()
            await pilot.pause()
            assert app.selected_key == initial_key

    asyncio.run(run())


def test_p50_keyboard_parity_up_down_native() -> None:
    """Up/Down keys navigate rows via DataTable native cursor."""
    async def run() -> None:
        app = _make_app()
        async with app.run_test(size=(140, 40)) as pilot:
            await _wait_for_frame(app)(pilot)
            mt = app.query_one("#body-table")

            initial = app.selected_key
            # Press down
            await pilot.press("down")
            await pilot.pause()
            after_down = app.selected_key
            assert after_down != initial, "down should change selection"
            # Press up to return
            await pilot.press("up")
            await pilot.pause()
            assert app.selected_key == initial

    asyncio.run(run())


def test_p50_keyboard_parity_enter_drilldown() -> None:
    """Enter key opens drill-down (parity with old keyboard workflow)."""
    async def run() -> None:
        app = _make_app()
        async with app.run_test(size=(140, 40)) as pilot:
            await _wait_for_frame(app)(pilot)
            # Navigate to a real entity row
            for _ in range(2):
                await pilot.press("down")
                await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, DrillDownScreen)

    asyncio.run(run())


def test_p50_keyboard_parity_left_right_tree() -> None:
    """Left/Right keys collapse/expand tree branches."""
    async def run() -> None:
        app = _make_app()
        async with app.run_test(size=(140, 40)) as pilot:
            await _wait_for_frame(app)(pilot)
            assert app.view_mode == "tree"
            full_count = len(app._visible_row_keys)
            assert full_count > 1  # has children

            # Left on root should collapse all children
            await pilot.press("left")
            await pilot.pause()
            assert app._visible_row_keys == ("",)

            # Right on root should expand all children
            await pilot.press("right")
            await pilot.pause()
            assert len(app._visible_row_keys) == full_count

    asyncio.run(run())


def test_p50_container_view_keys_work() -> None:
    """In container view, left/right/home/end are harmless (or replay-only)."""
    async def run() -> None:
        app = _make_app()
        async with app.run_test(size=(140, 40)) as pilot:
            await _wait_for_frame(app)(pilot)
            app.view_mode = "container"
            app._refresh_view()
            await pilot.pause()
            assert app.view_mode == "container"
            assert len(app._visible_row_keys) > 0

            # left/right should not break anything in container mode
            await pilot.press("left")
            await pilot.pause()
            await pilot.press("right")
            await pilot.pause()
            assert app.view_mode == "container"

    asyncio.run(run())
