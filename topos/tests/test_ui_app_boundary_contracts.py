from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import fixture_frame, fixture_root
from topos.config import ToposConfig
from topos.record.replay import ReplayDriver
from topos.ui import app as app_module
from topos.ui.app import ToposApp


def _config() -> ToposConfig:
    return ToposConfig(default_view="tree", default_column_profile="auto")


def _app(*, replay: bool = False) -> ToposApp:
    kwargs: dict[str, object] = {}
    if replay:
        frame = fixture_frame()
        kwargs["replay_driver"] = ReplayDriver([frame])
        kwargs["replay_step"] = True
    return ToposApp(
        (),
        config=_config(),
        cgroup_root=fixture_root() / "cgroupfs" / "gstammtisch",
        proc_root=fixture_root() / "procfs" / "network",
        **kwargs,
    )


def test_empty_state_helpers_preserve_live_and_replay_contracts() -> None:
    live = _app()
    live._refresh_view()
    assert live._status_text() == "mode=LIVE waiting for frames"

    replay = _app(replay=True)
    assert replay._status_text() == "mode=REPLAY waiting for frames"
    assert live._child_keys("anything") == ()


def test_jump_blank_input_is_a_noop() -> None:
    app = _app(replay=True)
    before = app._replay_driver.index

    app._on_jump_applied("   ")

    assert app._replay_driver.index == before
    assert app._replay_paused is True


def test_selection_and_overlay_commands_handle_stale_or_absent_state(monkeypatch: pytest.MonkeyPatch) -> None:
    selection = _app()
    selection._visible_row_keys = ("first", "second")
    selection.selected_key = "removed"
    moved: list[int] = []
    table = SimpleNamespace(move_cursor=lambda *, row: moved.append(row))
    monkeypatch.setattr(selection, "query_one", lambda *_args, **_kwargs: table)
    selection._move_selection(1)
    assert selection.selected_key == "first"
    assert moved == [0]

    app = object.__new__(ToposApp)
    popped: list[bool] = []
    monkeypatch.setattr(ToposApp, "screen_stack", property(lambda _self: (object(),)))
    monkeypatch.setattr(ToposApp, "pop_screen", lambda _self: popped.append(True))

    app.action_close_overlay()
    assert popped == []

    monkeypatch.setattr(ToposApp, "screen_stack", property(lambda _self: (object(), object())))
    app.action_close_overlay()
    assert popped == [True]


def test_replay_guards_and_active_speed_change_are_deterministic(monkeypatch: pytest.MonkeyPatch) -> None:
    live = _app()
    live._schedule_replay_tick()
    live._advance_replay()

    replay = _app(replay=True)
    replay._replay_paused = True
    replay._advance_replay()
    assert replay._replay_driver.index == 0

    app = _app(replay=True)
    app._replay_paused = False
    scheduled: list[bool] = []
    refreshed: list[bool] = []
    monkeypatch.setattr(app, "_schedule_replay_tick", lambda: scheduled.append(True))
    monkeypatch.setattr(app, "_refresh_view", lambda: refreshed.append(True))

    app._set_replay_speed(1)

    assert app._replay_speed == 2.0
    assert scheduled == [True]
    assert refreshed == [True]


def test_run_ui_non_smoke_runs_app_and_returns_success(monkeypatch: pytest.MonkeyPatch) -> None:
    constructed: list[tuple[tuple[object, ...], dict[str, object]]] = []

    class FakeApp:
        def __init__(self, *args: object, **kwargs: object) -> None:
            constructed.append((args, kwargs))

        def run(self) -> None:
            constructed.append(((), {"ran": True}))

    monkeypatch.setattr(app_module, "ToposApp", FakeApp)
    source = iter([fixture_frame()])

    assert app_module.run_ui(source, config=_config(), source_label="ARCHIVE") == 0
    assert constructed[0][0] == (source,)
    assert constructed[0][1]["source_label"] == "ARCHIVE"
    assert constructed[1] == ((), {"ran": True})


def test_smoke_fails_closed_when_no_frame_arrives_before_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeApp:
        frames_received = 0
        view_mode = "tree"
        profile_name = "auto"

        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def run_test(self, **_kwargs: object):
            class Context:
                async def __aenter__(self) -> object:
                    return object()

                async def __aexit__(self, *_args: object) -> None:
                    return None

            return Context()

    times = iter((0.0, 11.0))
    monkeypatch.setattr(app_module, "ToposApp", FakeApp)
    monkeypatch.setattr(app_module.asyncio, "get_event_loop", lambda: SimpleNamespace(time=lambda: next(times)))

    with pytest.raises(RuntimeError, match="ui smoke did not receive a frame"):
        asyncio.run(
            app_module._run_ui_smoke(
                (),
                config=_config(),
                cgroup_root=Path("/cgroups"),
                proc_root=Path("/proc"),
                profile=None,
                source_label="LIVE",
                replay_driver=None,
                replay_step=False,
                replay_speed=1.0,
            )
        )
