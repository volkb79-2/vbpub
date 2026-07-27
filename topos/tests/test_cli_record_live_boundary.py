from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import topos.cli as cli
from conftest import fixture_frame
from topos.collect.dockerjoin import ContainerResolveError


@pytest.fixture(autouse=True)
def fake_config(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    config = SimpleNamespace(interval=7.5)
    monkeypatch.setattr(cli, "load", lambda _path: config)
    return config


class FakeCollector:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.config = SimpleNamespace(interval=kwargs["config"].interval)

    def collect_once(self) -> object:
        raise AssertionError("collect_once was not configured")


class FakeWriter:
    instances: list["FakeWriter"] = []

    def __init__(self, path: Path, *, config: object) -> None:
        self.path = path
        self.config = config
        self.frames: list[object] = []
        self.__class__.instances.append(self)

    def __enter__(self) -> "FakeWriter":
        return self

    def __exit__(self, *args: object) -> None:
        return None


def test_record_json_requires_once_before_boundaries(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "Collector", lambda **_kwargs: pytest.fail("collector reached"))
    monkeypatch.setattr(cli, "RecordWriter", lambda *_args, **_kwargs: pytest.fail("writer reached"))

    assert cli.main(["--record", "out.jsonl", "--json"]) == 2
    assert capsys.readouterr().err.strip() == "--json is supported with --record only when --once is also set"


def test_headless_once_forwards_writer_config_and_prints_frame(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    frame = fixture_frame()
    collector = FakeCollector(config=SimpleNamespace(interval=7.5))
    calls: list[object] = []
    monkeypatch.setattr(cli, "Collector", lambda **kwargs: calls.append(("collector", kwargs)) or collector)
    monkeypatch.setattr(cli, "RecordWriter", FakeWriter)
    monkeypatch.setattr(cli, "live_frame_stream", lambda c, *, writer: calls.append(("stream", c, writer)) or iter([frame]))

    assert cli.main(["--record", "out.jsonl", "--headless", "--once", "--json"]) == 0
    assert FakeWriter.instances[-1].config is collector.config
    assert calls[1][0] == "stream"
    assert json.loads(capsys.readouterr().out)["schema_version"] == frame.schema_version


def test_headless_bounded_forwards_all_options(monkeypatch: pytest.MonkeyPatch) -> None:
    collector = FakeCollector(config=SimpleNamespace(interval=7.5))
    calls: list[object] = []
    monkeypatch.setattr(cli, "Collector", lambda **kwargs: collector)
    monkeypatch.setattr(cli, "RecordWriter", FakeWriter)
    monkeypatch.setattr(cli, "run_headless_record", lambda *args, **kwargs: calls.append((args, kwargs)) or 0)

    assert cli.main(["--record", "out.jsonl", "--headless", "--interval", "2", "--frames", "3"]) == 0
    args, kwargs = calls[0]
    assert args == (collector, FakeWriter.instances[-1])
    assert kwargs == {"interval": 2.0, "duration": None, "max_frames": 3}


def test_record_once_forwards_filters_and_prints_json(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    frame = fixture_frame()
    collector = FakeCollector(config=SimpleNamespace(interval=7.5))
    calls: list[object] = []
    monkeypatch.setattr(cli, "Collector", lambda **kwargs: calls.append(kwargs) or collector)
    monkeypatch.setattr(cli, "RecordWriter", FakeWriter)
    monkeypatch.setattr(cli, "live_frame_stream", lambda *_args, **_kwargs: iter([frame]))

    assert cli.main(["--record", "out.jsonl", "--once", "--json", "--entities", "svc*"]) == 0
    assert calls[0]["entities_globs"] == ("svc*",)
    assert json.loads(capsys.readouterr().out)["ts"] == frame.ts


@pytest.mark.parametrize("ui_code", [0, 1])
def test_record_ui_maps_success_and_fallback(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], ui_code: int) -> None:
    collector = FakeCollector(config=SimpleNamespace(interval=7.5))
    calls: list[object] = []
    monkeypatch.setattr(cli, "Collector", lambda **_kwargs: collector)
    monkeypatch.setattr(cli, "RecordWriter", FakeWriter)
    monkeypatch.setattr(cli, "live_frame_stream", lambda *_args, **_kwargs: iter(()))
    monkeypatch.setattr(cli, "_run_ui", lambda source, **kwargs: calls.append((source, kwargs)) or ui_code)

    assert cli.main(["--record", "out.jsonl", "--profile", "compact"]) == (0 if ui_code == 0 else 2)
    assert calls[0][1]["source_label"] == "LIVE"
    if ui_code:
        assert "textual is not installed" in capsys.readouterr().err


@pytest.mark.parametrize("argv", [["--record", "out.jsonl"], ["--record", "out.jsonl", "--once", "--json"]])
def test_record_boundaries_map_resolve_error(monkeypatch: pytest.MonkeyPatch, argv: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    collector = FakeCollector(config=SimpleNamespace(interval=7.5))
    monkeypatch.setattr(cli, "Collector", lambda **_kwargs: collector)
    monkeypatch.setattr(cli, "RecordWriter", lambda *_args, **_kwargs: (_ for _ in ()).throw(ContainerResolveError("missing container")))
    assert cli.main(argv) == 2
    assert capsys.readouterr().err.strip() == "missing container"


def test_record_writer_resolve_error_and_interrupt(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    collector = FakeCollector(config=SimpleNamespace(interval=7.5))
    monkeypatch.setattr(cli, "Collector", lambda **_kwargs: collector)
    monkeypatch.setattr(cli, "RecordWriter", lambda *_args, **_kwargs: (_ for _ in ()).throw(ContainerResolveError("writer target")))
    assert cli.main(["--record", "out.jsonl"]) == 2
    assert capsys.readouterr().err.strip() == "writer target"

    monkeypatch.setattr(cli, "RecordWriter", lambda *_args, **_kwargs: (_ for _ in ()).throw(KeyboardInterrupt))
    assert cli.main(["--record", "out.jsonl"]) == 0


def test_live_ui_once_json_and_incomplete_invocation(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    frame = fixture_frame()
    collector = FakeCollector(config=SimpleNamespace(interval=7.5))
    calls: list[object] = []
    monkeypatch.setattr(cli, "Collector", lambda **kwargs: calls.append(kwargs) or collector)
    collector.collect_once = lambda: frame  # type: ignore[method-assign]
    assert cli.main(["--once", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["ts"] == frame.ts

    monkeypatch.setattr(cli, "_run_ui", lambda source, **kwargs: calls.append((source, kwargs)) or 0)
    assert cli.main([]) == 0
    assert calls[-1][1]["source_label"] == "LIVE"

    assert cli.main(["--json"]) == 2
    assert "--once --json" in capsys.readouterr().err


def test_live_ui_fallback_and_resolve_error(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    collector = FakeCollector(config=SimpleNamespace(interval=7.5))
    monkeypatch.setattr(cli, "Collector", lambda **_kwargs: collector)
    monkeypatch.setattr(cli, "_run_ui", lambda *_args, **_kwargs: 1)
    assert cli.main([]) == 2
    assert "textual is not installed" in capsys.readouterr().err

    monkeypatch.setattr(cli, "Collector", lambda **_kwargs: collector)
    collector.collect_once = lambda: (_ for _ in ()).throw(ContainerResolveError("live missing"))  # type: ignore[method-assign]
    assert cli.main(["--once", "--json"]) == 2
    assert capsys.readouterr().err.strip() == "live missing"
