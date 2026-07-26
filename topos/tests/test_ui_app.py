from __future__ import annotations

import asyncio
import json
import time as _time
from dataclasses import replace
from pathlib import Path

from textual.widgets import Static
from textual.widgets import Input

from conftest import fixture_frame, fixture_root
from topos.config import ToposConfig, SnapshotConfig
from topos.damon.control import APPROVAL_TEXT
from topos.drift.origin import ShowResult
from topos.model import Frame, MetricValue
from topos.record.replay import ReplayDriver
from topos.snapshot.bundle import _extract_archive
from topos.ui.app import ToposApp, _providers_status, run_ui
from topos.ui.damon_control import DamonConfirmScreen
from topos.ui.drill import DrillDownScreen
from topos.ui.hostmem import HostMemoryScreen

GAME_KEY = "system.slice/docker-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.scope"


def _make_app() -> ToposApp:
    return ToposApp(
        iter([fixture_frame()]),
        config=ToposConfig(default_view="tree", default_column_profile="auto"),
        cgroup_root=fixture_root() / "cgroupfs" / "gstammtisch",
        proc_root=fixture_root() / "procfs" / "network",
    )


def _replay_app(*, step: bool = True) -> ToposApp:
    base = fixture_frame()
    later = Frame(
        schema_version=base.schema_version,
        ts=base.ts + base.interval_s,
        interval_s=base.interval_s,
        host=base.host,
        entities=base.entities,
    )
    return ToposApp(
        (),
        config=ToposConfig(default_view="tree", default_column_profile="auto"),
        cgroup_root=fixture_root() / "cgroupfs" / "gstammtisch",
        proc_root=fixture_root() / "procfs" / "network",
        replay_driver=ReplayDriver([base, later]),
        replay_step=step,
    )


def test_run_ui_smoke_reports_fixture_success() -> None:
    result = run_ui(
        iter([fixture_frame()]),
        config=ToposConfig(default_view="tree", default_column_profile="auto"),
        cgroup_root=fixture_root() / "cgroupfs" / "gstammtisch",
        proc_root=fixture_root() / "procfs" / "network",
        smoke=True,
    )

    assert result == "ui smoke ok frames=1 view=tree profile=auto"


def test_providers_status_handles_absent_and_fixture_entities() -> None:
    frame = fixture_frame()

    assert _providers_status(frame, "missing.entity") == {}
    assert _providers_status(frame, "") == {
        "network": frame.entities[""].network,
        "damon": {},
    }


def _wait_for_frame(app: ToposApp):
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


def _status_text(app: ToposApp) -> str:
    return _static_text(app.query_one("#status", Static))


async def _wait_or_timeout(pilot, predicate, *, timeout: float = 10.0) -> None:
    """Wait for *predicate* to return ``True``, polling via ``pilot.pause()``,
    with a wall-clock *timeout* in seconds.

    The fixed-iteration ``pilot.pause()`` loop made the former snapshots and
    smoke tests flaky because ``pause()`` yields to the asyncio event loop and
    returns immediately when idle — it does **not** consume wall-clock time.
    Under CPU contention (parallel workers, loaded hosts) the iteration count
    could exhaust before the thread worker finishes. Wall-clock deadline
    removes the race (P85).
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        await pilot.pause()
        if predicate():
            return
    raise AssertionError(f"predicate {predicate!r} not satisfied within {timeout}s")


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


def test_p145_mounted_keys_toggle_banner_and_cycle_selection() -> None:
    async def run() -> None:
        app = _make_app()
        async with app.run_test(size=(140, 40)) as pilot:
            await _wait_for_frame(app)(pilot)
            table = app.query_one("#body-table")
            banner = app.query_one("#banner", Static)
            expanded_banner = _static_text(banner)
            initial = table.row_key_at_cursor()
            assert initial == app.selected_key

            await pilot.press("b")
            await pilot.pause()
            collapsed_banner = _static_text(banner)
            assert collapsed_banner != expanded_banner

            await pilot.press("down")
            await pilot.pause()
            next_key = table.row_key_at_cursor()
            assert next_key != initial
            assert next_key == app.selected_key

            await pilot.press("up")
            await pilot.pause()
            assert table.row_key_at_cursor() == initial
            assert app.selected_key == initial

    asyncio.run(run())


def test_p145_mounted_keys_collapse_child_and_return_to_parent() -> None:
    async def run() -> None:
        app = _make_app()
        async with app.run_test(size=(140, 40)) as pilot:
            await _wait_for_frame(app)(pilot)
            table = app.query_one("#body-table")

            # Root -> parent -> child using only mounted navigation keys.
            await pilot.press("down")
            await pilot.pause()
            parent = table.row_key_at_cursor()
            assert parent == app.selected_key
            await pilot.press("down")
            await pilot.pause()
            child = table.row_key_at_cursor()
            assert child == app.selected_key
            assert child is not None and child.startswith(parent + "/")

            rows_before = table.row_count
            await pilot.press("left")
            await pilot.pause()
            assert table.row_key_at_cursor() == parent
            assert app.selected_key == parent
            # First left moves selection to parent; second left collapses it.
            await pilot.press("left")
            await pilot.pause()
            assert table.row_count < rows_before

            await pilot.press("right")
            await pilot.pause()
            assert table.row_count == rows_before
            assert table.row_key_at_cursor() == parent
            assert app.selected_key == parent

            await pilot.press("down")
            await pilot.pause()
            assert table.row_key_at_cursor() == child
            assert app.selected_key == child
            await pilot.press("left")
            await pilot.pause()
            assert table.row_key_at_cursor() == parent
            assert app.selected_key == parent

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


def test_pilot_replay_space_pauses_before_positive_gap_frame_advances() -> None:
    async def run() -> None:
        app = _replay_app()
        async with app.run_test(size=(140, 40)) as pilot:
            await _wait_for_frame(app)(pilot)
            assert "mode=REPLAY paused speed=1x frame=1/2" in _status_text(app)

            await pilot.press("space")
            await pilot.pause()
            assert "mode=REPLAY playing" in _status_text(app)

            await pilot.press("space")
            await pilot.pause()
            assert "mode=REPLAY paused speed=1x frame=1/2" in _status_text(app)

    asyncio.run(run())


def test_pilot_replay_auto_advances_same_timestamp_frames_to_terminal_pause() -> None:
    async def run() -> None:
        base = fixture_frame()
        later = Frame(
            schema_version=base.schema_version,
            ts=base.ts,
            interval_s=base.interval_s,
            host=base.host,
            entities=base.entities,
        )
        app = ToposApp(
            (),
            config=ToposConfig(default_view="tree", default_column_profile="auto"),
            cgroup_root=fixture_root() / "cgroupfs" / "gstammtisch",
            proc_root=fixture_root() / "procfs" / "network",
            replay_driver=ReplayDriver([base, later]),
            replay_step=False,
        )
        async with app.run_test(size=(140, 40)) as pilot:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + 2.0
            while loop.time() < deadline:
                await pilot.pause()
                if "paused" in _status_text(app) and "frame=2/2" in _status_text(app):
                    break
            else:
                raise AssertionError("replay did not reach terminal pause")
            assert "mode=REPLAY paused" in _status_text(app)
            assert "frame=2/2" in _status_text(app)

    asyncio.run(run())


def test_pilot_replay_step_controls_are_bounded_and_show_state() -> None:
    async def run() -> None:
        app = _replay_app()
        async with app.run_test(size=(140, 40)) as pilot:
            await _wait_for_frame(app)(pilot)
            driver = app._replay_driver
            assert driver is not None
            assert driver.index == 0
            assert "mode=REPLAY paused speed=1x frame=1/2" in _status_text(app)

            await pilot.press("comma")
            await pilot.pause()
            assert driver.index == 0
            assert "paused speed=1x frame=1/2" in _status_text(app)

            await pilot.press("full_stop")
            await pilot.pause()
            assert driver.index == 1
            assert "paused speed=1x frame=2/2" in _status_text(app)

            await pilot.press("full_stop")
            await pilot.pause()
            assert driver.index == 1
            assert "paused speed=1x frame=2/2" in _status_text(app)

            await pilot.press("comma")
            await pilot.pause()
            assert driver.index == 0
            assert "paused speed=1x frame=1/2" in _status_text(app)

    asyncio.run(run())


def test_pilot_replay_speed_keys_cycle_and_clamp_with_visible_status() -> None:
    async def run() -> None:
        app = _replay_app()
        async with app.run_test(size=(140, 40)) as pilot:
            await _wait_for_frame(app)(pilot)
            driver = app._replay_driver
            assert driver is not None

            for speed in (2, 4, 8):
                await pilot.press("plus")
                await pilot.pause()
                assert driver.index == 0
                assert f"paused speed={speed}x frame=1/2" in _status_text(app)

            await pilot.press("plus")
            await pilot.pause()
            assert "paused speed=8x frame=1/2" in _status_text(app)

            for speed in (4, 2, 1):
                await pilot.press("minus")
                await pilot.pause()
                assert driver.index == 0
                assert f"paused speed={speed}x frame=1/2" in _status_text(app)

            await pilot.press("minus")
            await pilot.pause()
            assert "paused speed=1x frame=1/2" in _status_text(app)

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
            from topos.ui.app import JumpScreen
            assert isinstance(app.screen, JumpScreen)
            # Enter frame number 2
            input_widget = app.screen.query_one("#jump-input", Input)
            input_widget.value = "2"
            await pilot.press("enter")
            await pilot.pause()
            assert "frame=2/2" in _status_text(app)

    asyncio.run(run())


def test_pilot_filter_prompt_submits_text_and_focuses_input() -> None:
    async def run() -> None:
        app = _make_app()
        async with app.run_test(size=(140, 40)) as pilot:
            await _wait_for_frame(app)(pilot)
            await pilot.press("/")
            await pilot.pause()
            from topos.ui.app import FilterScreen

            assert isinstance(app.screen, FilterScreen)
            input_widget = app.screen.query_one("#filter-input", Input)
            assert input_widget.has_focus
            input_widget.value = "paks"
            await pilot.press("enter")
            await pilot.pause()
            assert app.filter_text == "paks"
            assert not isinstance(app.screen, FilterScreen)

    asyncio.run(run())


def test_pilot_filter_prompt_escape_clears_filter() -> None:
    async def run() -> None:
        app = _make_app()
        async with app.run_test(size=(140, 40)) as pilot:
            await _wait_for_frame(app)(pilot)
            await pilot.press("/")
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            assert app.filter_text == ""

    asyncio.run(run())


def test_pilot_jump_prompt_escape_cancels_and_glossary_renders_and_dismisses() -> None:
    async def run() -> None:
        app = _replay_app()
        async with app.run_test(size=(140, 40)) as pilot:
            await _wait_for_frame(app)(pilot)
            await pilot.press("j")
            await pilot.pause()
            from topos.ui.app import GlossaryScreen, JumpScreen

            assert isinstance(app.screen, JumpScreen)
            jump_input = app.screen.query_one("#jump-input", Input)
            assert jump_input.has_focus
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, JumpScreen)
            assert app._replay_driver.index == 0

            await pilot.press("f1")
            await pilot.pause()
            assert isinstance(app.screen, GlossaryScreen)
            glossary = app.screen.query_one("#glossary-body", Static)
            assert "GLOSSARY" in _static_text(glossary)
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, GlossaryScreen)

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
            from topos.ui.app import JumpScreen
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
        app = ToposApp(
            (),
            config=ToposConfig(default_view="tree", default_column_profile="auto"),
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


def test_pilot_replay_controls_in_live_mode_show_unavailable_status() -> None:
    async def run() -> None:
        app = _make_app()
        async with app.run_test(size=(140, 40)) as pilot:
            await _wait_for_frame(app)(pilot)
            for key in ("space", "comma", "full_stop", "plus", "minus"):
                await pilot.press(key)
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
        app = ToposApp(
            iter([fixture_frame()]),
            config=ToposConfig(snapshots=SnapshotConfig(dir=tmp_path)),
            cgroup_root=fixture_root() / "cgroupfs" / "gstammtisch",
            proc_root=fixture_root() / "procfs" / "network",
        )
        async with app.run_test(size=(140, 40)) as pilot:
            await _wait_for_frame(app)(pilot)
            await pilot.press("x")
            # The bundle is written by a worker thread; a single pause() gives
            # the event loop one tick and guarantees nothing (P85).
            await _wait_or_timeout(pilot, lambda: app._snapshot_in_progress is False)

        assert len(list(tmp_path.glob("topos-incident-*"))) == 1

    asyncio.run(run())


def test_pilot_snapshot_hotkey_collects_fresh_systemd_and_docker_metadata(tmp_path: Path) -> None:
    async def run() -> None:
        def systemctl_show(unit: str, _properties: tuple[str, ...]) -> ShowResult:
            return ShowResult(stdout=f"Unit={unit}\nMemoryHigh=123\n", returncode=0)

        def docker_inspect(container_id: str):
            return [{"Id": container_id, "Name": "/demo", "Image": "image:latest", "Config": {"Env": ["SECRET=x"], "Labels": {"secret": "y"}, "User": "1000"}}]

        app = ToposApp(
            iter([fixture_frame()]),
            config=ToposConfig(snapshots=SnapshotConfig(dir=tmp_path, redact=True)),
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
            # Same worker-thread race as test_pilot_snapshot_hotkey_writes_bundle.
            await _wait_or_timeout(pilot, lambda: app._snapshot_in_progress is False)

    asyncio.run(run())
    bundles = list(tmp_path.glob("topos-incident-*"))
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
        def slow_systemctl(unit: str, _properties: tuple[str, ...]) -> ShowResult:
            _time.sleep(0.5)
            return ShowResult(stdout=f"Unit={unit}\n", returncode=0)

        app = ToposApp(
            iter([fixture_frame()]),
            config=ToposConfig(snapshots=SnapshotConfig(dir=tmp_path)),
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
            await _wait_or_timeout(pilot, lambda: app._snapshot_in_progress is False, timeout=10.0)
            assert app._snapshot_in_progress is False

    asyncio.run(run())
    assert len(list(tmp_path.glob("topos-incident-*"))) == 1


def test_pilot_snapshot_duplicate_keypress_guard(tmp_path: Path) -> None:
    """Verify a second x while snapshot is in-progress shows 'already running'."""
    async def run() -> None:
        app = ToposApp(
            iter([fixture_frame()]),
            config=ToposConfig(snapshots=SnapshotConfig(dir=tmp_path)),
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
    assert len(list(tmp_path.glob("topos-incident-*"))) == 0


def test_pilot_snapshot_success_reports_path(tmp_path: Path) -> None:
    async def run() -> None:
        app = ToposApp(
            iter([fixture_frame()]),
            config=ToposConfig(snapshots=SnapshotConfig(dir=tmp_path)),
            cgroup_root=fixture_root() / "cgroupfs" / "gstammtisch",
            proc_root=fixture_root() / "procfs" / "network",
        )
        async with app.run_test(size=(140, 40)) as pilot:
            await _wait_for_frame(app)(pilot)
            await pilot.press("x")
            # Wait for the worker to finish and status to update
            await _wait_or_timeout(pilot, lambda: "snapshot saved:" in _status_text(app))
            assert "snapshot saved:" in _status_text(app)
            assert "topos-incident" in _status_text(app)

    asyncio.run(run())


def test_pilot_snapshot_handled_exception_reports_failure(tmp_path: Path) -> None:
    """Use a provider that raises RuntimeError (not caught by collect_systemctl_show)
    to trigger the failure path in the snapshot worker."""
    async def run() -> None:
        def failing_systemctl(unit: str, _properties: tuple[str, ...]) -> ShowResult:
            raise RuntimeError("simulated provider failure")

        app = ToposApp(
            iter([fixture_frame()]),
            config=ToposConfig(snapshots=SnapshotConfig(dir=tmp_path)),
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
            await _wait_or_timeout(pilot, lambda: "snapshot failed:" in _status_text(app))
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
        app = ToposApp(
            iter([fixture_frame()]),
            config=ToposConfig(default_view="tree", default_column_profile="auto"),
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
        app = ToposApp(
            iter([fixture_frame()]),
            config=ToposConfig(default_view="tree", default_column_profile="auto"),
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


def test_pilot_damon_stop_surface_stops_only_topos_owned_sessions(tmp_path: Path) -> None:
    async def run() -> None:
        damon_root = _damon_root(tmp_path, slots=("on", "off"))
        state_dir = tmp_path / "state"
        app = ToposApp(
            iter([fixture_frame()]),
            config=ToposConfig(default_view="tree", default_column_profile="auto"),
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


def test_pilot_hostmem_stop_action_shows_stopped_count_notice(tmp_path: Path) -> None:
    """Host-memory 's' action shows exact stopped-count notice in rendered output."""
    async def run() -> None:
        damon_root = _damon_root(tmp_path)
        state_dir = tmp_path / "state"
        app = ToposApp(
            iter([fixture_frame()]),
            config=ToposConfig(default_view="tree", default_column_profile="auto"),
            cgroup_root=fixture_root() / "cgroupfs" / "gstammtisch",
            proc_root=fixture_root() / "procfs" / "network",
            damon_root=damon_root,
            damon_state_dir=state_dir,
            damon_require_root=False,
        )
        async with app.run_test(size=(140, 40)) as pilot:
            await _wait_for_frame(app)(pilot)
            await pilot.press("m")  # open host memory screen
            await pilot.pause()
            assert isinstance(app.screen, HostMemoryScreen)
            await pilot.press("s")  # stop action
            await pilot.pause()
            body = _static_text(app.screen.query_one("#hostmem-body", Static))
            assert "stopped 0 topos-owned DAMON session(s)" in body

    asyncio.run(run())


def test_pilot_hostmem_stop_with_exception_shows_unavailable_message(tmp_path: Path, monkeypatch) -> None:
    """Host-memory stop with exception shows 'stop unavailable: <message>'."""
    async def run() -> None:
        from topos.ui import hostmem

        damon_root = _damon_root(tmp_path)
        state_dir = tmp_path / "state"
        app = ToposApp(
            iter([fixture_frame()]),
            config=ToposConfig(default_view="tree", default_column_profile="auto"),
            cgroup_root=fixture_root() / "cgroupfs" / "gstammtisch",
            proc_root=fixture_root() / "procfs" / "network",
            damon_root=damon_root,
            damon_state_dir=state_dir,
            damon_require_root=False,
        )

        # Patch stop_owned_sessions to raise an exception
        def mock_stop(*args, **kwargs):
            raise RuntimeError("test error message")

        monkeypatch.setattr(hostmem, "stop_owned_sessions", mock_stop)

        async with app.run_test(size=(140, 40)) as pilot:
            await _wait_for_frame(app)(pilot)
            await pilot.press("m")  # open host memory screen
            await pilot.pause()
            assert isinstance(app.screen, HostMemoryScreen)
            await pilot.press("s")  # stop action (will raise)
            await pilot.pause()
            body = _static_text(app.screen.query_one("#hostmem-body", Static))
            assert "stop unavailable: test error message" in body

    asyncio.run(run())


def test_pilot_hostmem_control_result_none_shows_cancelled(tmp_path: Path) -> None:
    """HostMemoryScreen._on_control_result(None) renders 'start cancelled'."""
    async def run() -> None:
        damon_root = _damon_root(tmp_path)
        state_dir = tmp_path / "state"
        app = ToposApp(
            iter([fixture_frame()]),
            config=ToposConfig(default_view="tree", default_column_profile="auto"),
            cgroup_root=fixture_root() / "cgroupfs" / "gstammtisch",
            proc_root=fixture_root() / "procfs" / "network",
            damon_root=damon_root,
            damon_state_dir=state_dir,
            damon_require_root=False,
        )
        async with app.run_test(size=(140, 40)) as pilot:
            await _wait_for_frame(app)(pilot)
            await pilot.press("m")  # open host memory screen
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, HostMemoryScreen)
            # Simulate the callback with None result
            screen._on_control_result(None)
            body = _static_text(screen.query_one("#hostmem-body", Static))
            assert "start cancelled" in body

    asyncio.run(run())


def test_pilot_hostmem_control_result_with_text_shows_result(tmp_path: Path) -> None:
    """HostMemoryScreen._on_control_result(text) renders that exact text."""
    async def run() -> None:
        damon_root = _damon_root(tmp_path)
        state_dir = tmp_path / "state"
        app = ToposApp(
            iter([fixture_frame()]),
            config=ToposConfig(default_view="tree", default_column_profile="auto"),
            cgroup_root=fixture_root() / "cgroupfs" / "gstammtisch",
            proc_root=fixture_root() / "procfs" / "network",
            damon_root=damon_root,
            damon_state_dir=state_dir,
            damon_require_root=False,
        )
        async with app.run_test(size=(140, 40)) as pilot:
            await _wait_for_frame(app)(pilot)
            await pilot.press("m")  # open host memory screen
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, HostMemoryScreen)
            # Simulate the callback with result text
            result_text = "DAMON paddr host session started on kdamond 0"
            screen._on_control_result(result_text)
            body = _static_text(screen.query_one("#hostmem-body", Static))
            assert result_text in body

    asyncio.run(run())


# ── P50: Mouse Table Interactions ─────────────────────────────────────────


GAME_KEY_2 = "besteffort.slice/docker-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.scope"


def _column_click_offset(table, column_key: str) -> tuple[int, int]:
    region = table._get_column_region(table._col_keys.index(column_key))
    return (
        region.x - table.scroll_offset.x + max(1, region.width // 2),
        0,
    )


def _row_click_offset(table, row_index: int) -> tuple[int, int]:
    region = table._get_row_region(row_index)
    return (
        max(1, min(5, table.size.width - 1)),
        region.y - table.scroll_offset.y,
    )


def test_p50_header_click_sorts_by_column() -> None:
    """Click a column header to sort by it; repeated click toggles direction."""
    async def run() -> None:
        app = _make_app()
        async with app.run_test(size=(140, 40)) as pilot:
            await _wait_for_frame(app)(pilot)
            mt = app.query_one("#body-table")

            # Default sort is pressure descending (vpressure)
            assert "vpressure" in _status_text(app)

            await pilot.click("#body-table", offset=_column_click_offset(mt, "ram"))
            await pilot.pause()
            assert "vram" in _status_text(app)  # ram descending by default

            # Click RAM again to toggle direction
            await pilot.click("#body-table", offset=_column_click_offset(mt, "ram"))
            await pilot.pause()
            assert "^ram" in _status_text(app)  # now ascending

    asyncio.run(run())


def test_p50_header_click_toggles_direction() -> None:
    """Repeated header click on same column toggles asc/desc."""
    async def run() -> None:
        app = _make_app()
        async with app.run_test(size=(140, 40)) as pilot:
            await _wait_for_frame(app)(pilot)
            mt = app.query_one("#body-table")

            # Sort by name (ascending by default)
            await pilot.click("#body-table", offset=_column_click_offset(mt, "name"))
            await pilot.pause()
            assert "^name" in _status_text(app)

            # Toggle to descending
            await pilot.click("#body-table", offset=_column_click_offset(mt, "name"))
            await pilot.pause()
            assert "vname" in _status_text(app)

            # Toggle back to ascending
            await pilot.click("#body-table", offset=_column_click_offset(mt, "name"))
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

            rk = app._visible_row_keys[1]
            assert not rk.startswith("__empty__")
            await pilot.click("#body-table", offset=_row_click_offset(mt, 1))
            await pilot.pause()
            assert isinstance(app.screen, DrillDownScreen)
            assert app.selected_key == rk
            assert len(app.screen_stack) == 2
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

            await pilot.click("#body-table", offset=_row_click_offset(mt, 0))
            await pilot.pause()
            assert not isinstance(app.screen, DrillDownScreen)

    asyncio.run(run())


def test_p50_live_refresh_preserves_nonzero_cursor_across_reorder() -> None:
    """A live refresh restores a nonzero selected key after row reordering."""
    async def run() -> None:
        app = _make_app()
        async with app.run_test(size=(140, 40)) as pilot:
            await _wait_for_frame(app)(pilot)
            mt = app.query_one("#body-table")

            await pilot.press("down")
            await pilot.pause()
            selected = app.selected_key
            assert selected is not None and selected != ""
            app.sort_by = "name"
            app.sort_reverse = True
            base = app.current_frame
            assert base is not None
            later = replace(base, ts=base.ts + base.interval_s)
            app._apply_frame(later)
            await pilot.pause()
            assert app.selected_key == selected
            assert mt.cursor_coordinate.row == app._visible_row_keys.index(selected)

    asyncio.run(run())


def test_p50_replay_refresh_preserves_nonzero_cursor() -> None:
    async def run() -> None:
        app = _replay_app()
        async with app.run_test(size=(140, 40)) as pilot:
            await _wait_for_frame(app)(pilot)
            mt = app.query_one("#body-table")
            await pilot.press("down")
            await pilot.pause()
            selected = app.selected_key
            assert selected is not None and selected != ""
            await pilot.press("full_stop")
            await pilot.pause()
            assert app.selected_key == selected
            assert mt.cursor_coordinate.row == app._visible_row_keys.index(selected)

    asyncio.run(run())


def test_p50_alias_backed_header_click_uses_canonical_sort_key() -> None:
    async def run() -> None:
        base = fixture_frame()
        entities = dict(base.entities)
        for key, value in (("besteffort.slice", 1.0), ("system.slice", 9.0)):
            entity_frame = entities[key]
            metrics = dict(entity_frame.metrics)
            metrics["rf_d_per_s"] = MetricValue(value, "exact")
            entities[key] = replace(entity_frame, metrics=metrics)
        frame = replace(base, entities=entities)
        config = ToposConfig(
            default_view="tree",
            default_column_profile="aliases",
            columns={"profiles": {"aliases": {"list": ["name", "rf_dev"]}}},
        )
        app = ToposApp(iter([frame]), config=config)
        async with app.run_test(size=(140, 40)) as pilot:
            await _wait_for_frame(app)(pilot)
            mt = app.query_one("#body-table")
            assert mt._col_keys == ("name", "rf_d_per_s")
            await pilot.click(
                "#body-table", offset=_column_click_offset(mt, "rf_d_per_s")
            )
            await pilot.pause()
            assert app.sort_by == "rf_d_per_s"
            assert app.sort_reverse is True
            assert app._visible_row_keys.index("system.slice") < app._visible_row_keys.index(
                "besteffort.slice"
            )

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


def test_p149_empty_source_up_down_is_safe() -> None:
    async def run() -> None:
        app = ToposApp(
            (),
            config=ToposConfig(default_view="tree", default_column_profile="auto"),
            cgroup_root=fixture_root() / "cgroupfs" / "gstammtisch",
            proc_root=fixture_root() / "procfs" / "network",
        )
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            base_screen = app.screen
            assert "mode=LIVE waiting for frames" in _status_text(app)

            await pilot.press("up")
            await pilot.pause()
            await pilot.press("down")
            await pilot.pause()

            assert app.screen is base_screen
            assert "mode=LIVE waiting for frames" in _status_text(app)

    asyncio.run(run())


def test_p155_empty_source_selection_actions_are_safe() -> None:
    async def run() -> None:
        app = ToposApp(
            (),
            config=ToposConfig(default_view="tree", default_column_profile="auto"),
            cgroup_root=fixture_root() / "cgroupfs" / "gstammtisch",
            proc_root=fixture_root() / "procfs" / "network",
        )
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            base_screen = app.screen
            assert "mode=LIVE waiting for frames" in _status_text(app)

            assert await app.run_action("select_prev")
            assert await app.run_action("select_next")
            await pilot.pause()

            assert app.screen is base_screen
            assert "mode=LIVE waiting for frames" in _status_text(app)

    asyncio.run(run())


def test_p155_populated_selection_actions_wrap_public_cursor() -> None:
    async def run() -> None:
        app = _make_app()
        async with app.run_test(size=(140, 40)) as pilot:
            await _wait_for_frame(app)(pilot)
            table = app.query_one("#body-table")
            first_key = table.row_key_at_cursor()
            assert first_key is not None
            assert table.row_count > 1

            assert await app.run_action("select_prev")
            await pilot.pause()
            last_key = table.row_key_at_cursor()
            assert last_key is not None
            assert last_key != first_key

            assert await app.run_action("select_next")
            await pilot.pause()
            assert table.row_key_at_cursor() == first_key

    asyncio.run(run())


def test_p150_empty_source_commands_are_safe() -> None:
    async def run() -> None:
        app = ToposApp(
            (),
            config=ToposConfig(default_view="tree", default_column_profile="auto"),
            cgroup_root=fixture_root() / "cgroupfs" / "gstammtisch",
            proc_root=fixture_root() / "procfs" / "network",
        )
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            base_screen = app.screen
            assert "mode=LIVE waiting for frames" in _status_text(app)

            for key in ("enter", "x", "m", "escape"):
                await pilot.press(key)
                await pilot.pause()
                assert app.screen is base_screen
                assert "mode=LIVE waiting for frames" in _status_text(app)

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
