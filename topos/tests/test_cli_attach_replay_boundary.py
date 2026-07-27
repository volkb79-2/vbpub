from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import topos.cli as cli
from conftest import fixture_frame
from topos.daemon.client import DaemonClientError


def _config() -> object:
    return SimpleNamespace(interval=7.5)


@pytest.fixture(autouse=True)
def fake_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "load", lambda _path: _config())


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        (["--replay", "record.jsonl"], "choose either --attach or --replay"),
        (["--step"], "--attach does not accept replay pacing flags"),
        (["--speed", "2"], "--attach does not accept replay pacing flags"),
        (["--cgroup-root", "/cg"], "--attach does not accept --cgroup-root"),
        (["--record", "out.jsonl"], "--attach does not support --record in this build"),
        (["--json"], "--json is supported with --attach only when --once is also set"),
        (["--entities", "a"], "--attach does not accept --entities/--slice/--metrics/--container"),
        (["--slice", "a"], "--attach does not accept --entities/--slice/--metrics/--container"),
        (["--metrics", "compact"], "--attach does not accept --entities/--slice/--metrics/--container"),
        (["--container", "a"], "--attach does not accept --entities/--slice/--metrics/--container"),
    ],
)
def test_attach_rejects_incompatible_options_before_live_boundaries(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    extra: list[str],
    message: str,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(cli, "current_frame", lambda *_args, **_kwargs: calls.append("frame"))
    monkeypatch.setattr(cli, "_run_ui", lambda *_args, **_kwargs: calls.append("ui"))

    assert cli.main(["--attach", "/daemon.sock", *extra]) == 2
    assert calls == []
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.strip() == message


def test_attach_once_json_forwards_socket_and_prints_frame(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    frame = fixture_frame()
    calls: list[object] = []
    monkeypatch.setattr(cli, "current_frame", lambda socket: calls.append(socket) or frame)

    assert cli.main(["--attach", "/daemon.sock", "--once", "--json"]) == 0
    assert calls == [Path("/daemon.sock")]
    assert '"schema_version":1' in capsys.readouterr().out


def test_attach_ui_forwards_options_and_maps_success(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[object] = []
    monkeypatch.setattr(cli, "_attach_frame_source", lambda socket, *, poll_interval_s: calls.append(("source", socket, poll_interval_s)) or iter(()))
    monkeypatch.setattr(cli, "_run_ui", lambda source, **kwargs: calls.append(("ui", source, kwargs)) or 0)

    assert cli.main(["--attach", "/daemon.sock", "--ui-smoke", "--profile", "compact"]) == 0
    assert calls[0] == ("source", Path("/daemon.sock"), 7.5)
    assert calls[1][2] == {
        "config": calls[1][2]["config"], "cgroup_root": None, "smoke": True,
        "profile": "compact", "source_label": "ATTACH",
    }
    assert capsys.readouterr().out == ""


def test_attach_ui_fallback_daemon_error_and_keyboard_interrupt(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "_attach_frame_source", lambda *_args, **_kwargs: iter(()))
    monkeypatch.setattr(cli, "_run_ui", lambda *_args, **_kwargs: 1)
    assert cli.main(["--attach", "/daemon.sock"]) == 2
    assert "textual is not installed" in capsys.readouterr().err

    monkeypatch.setattr(cli, "current_frame", lambda *_args: (_ for _ in ()).throw(DaemonClientError("broken")))
    assert cli.main(["--attach", "/daemon.sock", "--once", "--json"]) == 2
    assert "broken" in capsys.readouterr().err

    monkeypatch.setattr(cli, "current_frame", lambda *_args: (_ for _ in ()).throw(KeyboardInterrupt))
    assert cli.main(["--attach", "/daemon.sock", "--once", "--json"]) == 0
    assert capsys.readouterr().err == ""


def test_replay_rejects_collection_filters_before_driver(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    calls: list[str] = []
    monkeypatch.setattr(cli.ReplayDriver, "from_path", lambda *_args, **_kwargs: calls.append("driver"))

    assert cli.main(["--replay", "record.jsonl", "--entities", "a"]) == 2
    assert calls == []
    assert capsys.readouterr().err.strip() == "--replay does not accept --entities/--slice/--metrics/--container"


def test_replay_forwards_driver_and_ui_options_and_maps_success(monkeypatch: pytest.MonkeyPatch) -> None:
    driver = object()
    calls: list[object] = []
    monkeypatch.setattr(cli.ReplayDriver, "from_path", lambda path, *, config: calls.append(("from", path, config)) or driver)
    monkeypatch.setattr(cli, "_run_ui", lambda source, **kwargs: calls.append(("ui", source, kwargs)) or 0)

    assert cli.main(["--replay", "record.jsonl", "--step", "--speed", "2.5", "--cgroup-root", "/cg", "--ui-smoke", "--profile", "minimal"]) == 0
    assert calls[0][1] == Path("record.jsonl")
    assert calls[1] == ("ui", (), {
        "config": calls[0][2], "cgroup_root": Path("/cg"), "smoke": True,
        "profile": "minimal", "source_label": "REPLAY", "replay_driver": driver,
        "replay_step": True, "replay_speed": 2.5,
    })


def test_replay_ui_fallback_prints_textual_frames(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    replay_frame = SimpleNamespace(frame=fixture_frame(), index=0, total=1, delay_s=0.0)
    class Driver:
        def play(self, *, speed: float, step: bool):
            assert (speed, step) == (1.0, False)
            return [replay_frame]

    monkeypatch.setattr(cli.ReplayDriver, "from_path", lambda *_args, **_kwargs: Driver())
    monkeypatch.setattr(cli, "_run_ui", lambda *_args, **_kwargs: 1)
    assert cli.main(["--replay", "record.jsonl"]) == 0
    assert capsys.readouterr().out.startswith("frame 1/1 ts=")
