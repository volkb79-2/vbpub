"""Remaining deterministic CLI option boundaries.

These tests deliberately exercise the public command handler with every
collector/writer dependency injected.  They prove that the supported
one-frame recording contract stays silent without ``--json`` and that an
unsupported attach shape is rejected before any daemon connection is made.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import topos.cli as cli
from conftest import fixture_frame


class _Writer:
    def __init__(self, _path: Path, *, config: object) -> None:
        self.config = config

    def __enter__(self) -> "_Writer":
        return self

    def __exit__(self, *_args: object) -> None:
        return None


@pytest.fixture(autouse=True)
def _fake_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "load", lambda _path: SimpleNamespace(interval=1.0))


@pytest.mark.parametrize("extra", ([], ["--headless"]))
def test_record_once_without_json_writes_one_frame_but_emits_no_json(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    extra: list[str],
) -> None:
    """Both one-frame record modes consume the frame without printing it."""
    frame = fixture_frame()
    collector = SimpleNamespace(config=SimpleNamespace(interval=1.0))
    stream_calls: list[tuple[object, object]] = []
    monkeypatch.setattr(cli, "Collector", lambda **_kwargs: collector)
    monkeypatch.setattr(cli, "RecordWriter", _Writer)
    monkeypatch.setattr(
        cli,
        "live_frame_stream",
        lambda actual_collector, *, writer: stream_calls.append((actual_collector, writer))
        or iter((frame,)),
    )
    monkeypatch.setattr(
        cli,
        "_print_frame_json",
        lambda *_args: pytest.fail("--json renderer must not run"),
    )

    assert cli.main(["--record", "capture.jsonl", "--once", *extra]) == 0
    assert len(stream_calls) == 1
    assert capsys.readouterr().out == ""


def test_attach_once_without_json_rejects_before_daemon_read(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The advertised attach one-shot format is canonical JSON only."""
    monkeypatch.setattr(
        cli,
        "current_frame",
        lambda *_args: pytest.fail("unsupported attach invocation read daemon"),
    )

    assert cli.main(["--attach", "/run/topos.sock", "--once"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.strip() == (
        "topos --attach implements --once --json for canonical daemon frames"
    )


def test_daemon_preflight_text_uses_renderer_and_exit_contract(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A usable preflight renders human text and exits successfully."""
    report = SimpleNamespace(usable=True)
    calls: list[tuple[object, str]] = []
    monkeypatch.setattr(
        cli,
        "preflight_daemon_deployment",
        lambda socket, group_name: calls.append((socket, group_name)) or report,
    )
    monkeypatch.setattr(
        cli,
        "render_preflight_text",
        lambda actual: "daemon deployment is ready" if actual is report else "wrong report",
    )

    assert cli._main_daemon(["preflight", "--socket", "/run/custom.sock", "--group", "ops"]) == 0
    assert calls == [(Path("/run/custom.sock"), "ops")]
    assert capsys.readouterr().out == "daemon deployment is ready\n"
