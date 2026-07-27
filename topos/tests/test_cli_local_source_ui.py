import builtins
import sys
import types
from pathlib import Path

import pytest

import topos.cli as cli


def test_attach_source_uses_floor_and_preserves_daemon_frames(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frames = ["first", "second"]
    sleeps: list[float] = []

    monkeypatch.setattr(cli, "current_frame", lambda _socket: frames.pop(0))
    monkeypatch.setattr(cli.time, "sleep", sleeps.append)

    source = cli._attach_frame_source(Path("/run/topos.sock"), poll_interval_s=0)
    assert [next(source), next(source)] == ["first", "second"]
    assert sleeps == [0.1]


def test_replay_source_yields_driver_frames_in_order() -> None:
    calls: list[tuple[float, bool]] = []

    class FakeDriver:
        def play(self, *, speed: float, step: bool):
            calls.append((speed, step))
            return [types.SimpleNamespace(frame="one"), types.SimpleNamespace(frame="two")]

    assert list(cli._replay_frame_source(FakeDriver(), speed=2.5, step=True)) == ["one", "two"]
    assert calls == [(2.5, True)]


@pytest.mark.parametrize("result", ["ui finished", 17])
def test_run_ui_passes_boundary_contract_and_prints_string_result(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    result: object,
) -> None:
    calls: list[tuple[object, dict[str, object]]] = []
    app = types.ModuleType("topos.ui.app")

    def run_ui(frame_source, **kwargs):
        calls.append((frame_source, kwargs))
        return result

    app.run_ui = run_ui
    monkeypatch.setitem(sys.modules, "topos.ui.app", app)
    frame_source = object()
    config = object()
    cgroup_root = Path("/sys/fs/cgroup")

    assert cli._run_ui(
        frame_source,
        config=config,
        cgroup_root=cgroup_root,
        smoke=True,
        profile="compact",
        source_label="REPLAY",
        replay_driver="driver",
        replay_step=True,
        replay_speed=3.0,
    ) == 0
    assert calls == [
        (
            frame_source,
            {
                "config": config,
                "cgroup_root": cgroup_root,
                "smoke": True,
                "profile": "compact",
                "source_label": "REPLAY",
                "replay_driver": "driver",
                "replay_step": True,
                "replay_speed": 3.0,
            },
        )
    ]
    assert capsys.readouterr().out == ("ui finished\n" if isinstance(result, str) else "")


@pytest.mark.parametrize(
    ("smoke", "expected_code", "output"),
    [(False, -1, ""), (True, 2, "textual is required for --ui-smoke\n")],
)
def test_run_ui_handles_missing_textual_by_mode(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    smoke: bool,
    expected_code: int,
    output: str,
) -> None:
    original_import = builtins.__import__

    def missing_textual(name, *args, **kwargs):
        if name == "topos.ui.app":
            raise ModuleNotFoundError("No module named 'textual.widgets'", name="textual.widgets")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing_textual)
    assert cli._run_ui(None, config=None, cgroup_root=None, smoke=smoke, profile=None) == expected_code
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == output


def test_run_ui_reraises_unrelated_missing_module(monkeypatch: pytest.MonkeyPatch) -> None:
    original_import = builtins.__import__

    def missing_other(name, *args, **kwargs):
        if name == "topos.ui.app":
            raise ModuleNotFoundError("No module named 'other_dependency'", name="other_dependency")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing_other)
    with pytest.raises(ModuleNotFoundError, match="other_dependency"):
        cli._run_ui(None, config=None, cgroup_root=None, smoke=False, profile=None)
