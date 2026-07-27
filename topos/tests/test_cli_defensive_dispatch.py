"""Defensive command-dispatch paths after parser-contract drift."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import topos.cli as cli


@pytest.mark.parametrize(
    ("parser", "entry", "expected"),
    [
        ("parse_damon_args", "_main_damon", "unknown damon command"),
        ("parse_snapshot_args", "_main_snapshot", "unknown snapshot command"),
        ("parse_action_args", "_main_action", "unknown action command"),
        ("parse_bpf_args", "_main_bpf", "unknown bpf command"),
    ],
)
def test_defensive_dispatch_rejects_post_parser_unknown_command(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    parser: str,
    entry: str,
    expected: str,
) -> None:
    args = SimpleNamespace(command="future-command")
    if parser == "parse_action_args":
        args.target = "container"
        args.container = None
    monkeypatch.setattr(cli, parser, lambda _argv: args)

    assert getattr(cli, entry)([]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == expected + "\n"


def test_gateway_defensive_unknown_command_does_not_start_server(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "parse_gateway_args", lambda _argv: SimpleNamespace(command="future-command"))

    assert cli._main_gateway([]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "unknown gateway command\n"


def test_mcp_defensive_unknown_command_does_not_import_optional_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "parse_mcp_args", lambda _argv: SimpleNamespace(command="future-command"))
    assert cli._main_mcp([]) == 2
