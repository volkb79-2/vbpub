"""Environment-only resolution for the host-owned observe socket."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from cli_extended import CliFailure

from topos import cli
from topos.daemon.socket_path import SOCKET_ENV, resolve_observe_socket


def test_resolver_requires_the_named_environment_variable() -> None:
    with pytest.raises(CliFailure) as caught:
        resolve_observe_socket(environ={})

    assert caught.value.exit_code == 2
    assert SOCKET_ENV in caught.value.message
    assert "log in again" in caught.value.message


def test_resolver_reads_environment_and_an_explicit_path_overrides_it(tmp_path: Path) -> None:
    environment_path = tmp_path / "from-environment.sock"
    override_path = tmp_path / "from-flag.sock"
    environ = {SOCKET_ENV: str(environment_path)}

    assert resolve_observe_socket(environ=environ) == environment_path
    assert resolve_observe_socket(override_path, environ={}) == override_path
    assert resolve_observe_socket(override_path, environ=environ) == override_path


def test_resolver_rejects_empty_explicit_and_environment_paths() -> None:
    with pytest.raises(CliFailure, match="socket path cannot be empty") as explicit:
        resolve_observe_socket("")
    assert explicit.value.exit_code == 2

    with pytest.raises(CliFailure) as environment:
        resolve_observe_socket(environ={SOCKET_ENV: ""})
    assert environment.value.exit_code == 2
    assert SOCKET_ENV in environment.value.message


def test_wrong_environment_path_is_used_without_a_fallback(tmp_path: Path) -> None:
    missing = tmp_path / "missing.sock"

    assert resolve_observe_socket(environ={SOCKET_ENV: str(missing)}) == missing


def test_attach_path_override_does_not_require_the_environment(monkeypatch, tmp_path: Path) -> None:
    socket_path = tmp_path / "explicit.sock"
    resolved = []
    monkeypatch.delenv(SOCKET_ENV, raising=False)
    monkeypatch.setattr(cli, "current_frame", lambda path: resolved.append(Path(path)) or object())
    monkeypatch.setattr(cli, "_print_frame_json", lambda *_args: None)

    assert cli.main(["--attach", str(socket_path), "--once", "--json"]) == 0
    assert resolved == [socket_path]


def test_cli_missing_environment_is_a_named_usage_error(monkeypatch, capsys) -> None:
    monkeypatch.delenv(SOCKET_ENV, raising=False)

    assert cli.main(["daemon", "current"]) == 2
    assert SOCKET_ENV + " is unset" in capsys.readouterr().err


def test_cli_uses_a_wrong_environment_path_without_falling_back(monkeypatch, capsys, tmp_path: Path) -> None:
    missing = tmp_path / "wrong-environment.sock"
    monkeypatch.setenv(SOCKET_ENV, str(missing))

    assert cli.main(["daemon", "current"]) == 2
    assert str(missing) in capsys.readouterr().err


def test_daemon_clients_have_no_socket_default_in_their_help_or_source() -> None:
    source_root = Path(__file__).resolve().parents[1] / "src" / "topos"
    client_modules = (
        source_root / "cli.py",
        source_root / "daemon" / "status.py",
        source_root / "daemon" / "socket_path.py",
        source_root / "mcp" / "server.py",
    )
    socket_path = re.compile(r"/run/topos/(?:observe/)?topos\.sock")
    for module in client_modules:
        assert socket_path.search(module.read_text(encoding="utf-8")) is None, module

    current = cli.parse_daemon_args(["current"])
    status = cli.parse_daemon_args(["status"])
    health = cli.parse_daemon_args(["health"])
    mcp = cli.parse_mcp_args(["serve"])
    gateway = cli.parse_gateway_args(["serve", "--principal", "reader:public"])
    assert current.socket is status.socket is health.socket is None
    assert mcp.socket is None
    assert gateway.daemon_socket is None


def test_client_help_explains_environment_without_printing_a_socket_path(capsys) -> None:
    parsers = (
        lambda: cli.parse_args(["--help"]),
        lambda: cli.parse_daemon_args(["current", "--help"]),
        lambda: cli.parse_daemon_args(["status", "--help"]),
        lambda: cli.parse_daemon_args(["health", "--help"]),
        lambda: cli.parse_mcp_args(["serve", "--help"]),
        lambda: cli.parse_gateway_args(["serve", "--principal", "reader:public", "--help"]),
    )
    for parse in parsers:
        with pytest.raises(SystemExit) as caught:
            parse()
        assert caught.value.code == 0
        output = capsys.readouterr().out
        assert "/run/topos/observe/topos.sock" not in output
        assert "TOPOS_OBSERVE_SOCKET" in output
