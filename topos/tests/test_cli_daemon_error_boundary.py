"""Daemon read-command failures keep a typed, command-level contract."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import topos.cli as cli
import topos.daemon.client as client_module
import topos.daemon.status as status_module
from topos.daemon.client import DaemonProtocolError, DaemonResponseError


def test_daemon_current_protocol_failure_is_a_typed_cli_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    socket_path = tmp_path / "daemon.sock"

    class _Client:
        def __init__(self, socket: Path) -> None:
            assert socket == socket_path

        def current_frame(self) -> object:
            raise DaemonProtocolError("malformed daemon envelope")

    monkeypatch.setattr(client_module, "DaemonClient", _Client)

    assert cli._main_daemon(["current", "--socket", str(socket_path)]) == 2
    assert "malformed daemon envelope" in capsys.readouterr().err


def test_daemon_health_response_failure_is_a_typed_cli_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    socket_path = tmp_path / "daemon.sock"

    class _Client:
        def __init__(self, socket: Path) -> None:
            assert socket == socket_path

        def request_health(self) -> object:
            raise DaemonResponseError("health service unavailable", code="unavailable")

    monkeypatch.setattr(client_module, "DaemonClient", _Client)

    assert cli._main_daemon(["health", "--socket", str(socket_path)]) == 2
    assert "health service unavailable" in capsys.readouterr().err


def test_daemon_status_operational_error_is_stderr_exit_two(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    socket_path = tmp_path / "daemon.sock"

    def _raise(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("status probe failed")

    monkeypatch.setattr(status_module, "build_daemon_status", _raise)

    assert cli._main_daemon(["status", "--socket", str(socket_path)]) == 2
    assert capsys.readouterr().err == "status probe failed\n"


def test_daemon_status_pretty_json_uses_indented_report_representation(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    socket_path = tmp_path / "daemon.sock"
    report = SimpleNamespace(ok=False, to_jsonable=lambda: {"ok": False, "socket": str(socket_path)})
    monkeypatch.setattr(status_module, "build_daemon_status", lambda *_args, **_kwargs: report)

    assert cli._main_daemon(["status", "--socket", str(socket_path), "--pretty-json"]) == 1
    output = capsys.readouterr().out
    assert json.loads(output) == {"ok": False, "socket": str(socket_path)}
    assert output.startswith("{\n")
    assert '  "ok": false' in output
