"""Fail-closed boundary contracts for the acceptance smoke helpers.

These tests keep the acceptance module's error reporting and cleanup paths
deterministic: no daemon, MCP server, socket, or timer is started for real.
"""

from __future__ import annotations

import asyncio
import builtins
import json
import socket
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest


class _ServerParameters:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


class _StdioClient:
    async def __aenter__(self) -> tuple[object, object]:
        return object(), object()

    async def __aexit__(self, *_args: object) -> None:
        return None


def _result(payload: object) -> object:
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=json.dumps(payload))],
        isError=False,
    )


def _install_mcp_imports(monkeypatch: pytest.MonkeyPatch, session_type: type[object]) -> None:
    """Replace only deferred MCP SDK imports with an in-process session fake."""
    original_import = builtins.__import__

    def _mcp_import(
        name: str,
        globals_: object = None,
        locals_: object = None,
        fromlist: object = (),
        level: int = 0,
    ) -> object:
        if name == "mcp.client.stdio":
            return SimpleNamespace(
                StdioServerParameters=_ServerParameters,
                stdio_client=lambda _params: _StdioClient(),
            )
        if name == "mcp.client.session":
            return SimpleNamespace(ClientSession=session_type)
        return original_import(name, globals_, locals_, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", _mcp_import)


def test_mcp_session_keeps_each_tool_and_post_loss_failure_typed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed tool, selector probe, and dead post-loss server stay inspectable."""
    import topos.acceptance as acceptance

    class _Proc:
        def __init__(self) -> None:
            self.killed = False

        def terminate(self) -> None:
            return None

        def wait(self, timeout: float | None = None) -> None:
            if timeout is not None:
                raise subprocess.TimeoutExpired("topos daemon", timeout)
            return None

        def kill(self) -> None:
            self.killed = True

    class _Session:
        def __init__(self, _read: object, _write: object) -> None:
            self.list_calls = 0

        async def __aenter__(self) -> "_Session":
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def initialize(self) -> None:
            return None

        async def list_tools(self) -> object:
            self.list_calls += 1
            if self.list_calls == 2:
                raise RuntimeError("MCP server disappeared after daemon loss")
            return SimpleNamespace(
                tools=[SimpleNamespace(name=name) for name in acceptance._MCP_SMOKE_CALL_ORDER]
            )

        async def call_tool(self, name: str, arguments: dict[str, object]) -> object:
            if name == "topos_health":
                raise RuntimeError("health response lost")
            if name == "topos_overview" and arguments["limit"] == 5:
                return _result({"data": {"rows": [{"key": "unit.scope"}]}})
            if name == "topos_entity" and arguments.get("selector") == "__nonexistent__":
                raise RuntimeError("selector probe transport lost")
            if name in {"topos_entity", "topos_history"}:
                return _result({"data": {"ok": True}})
            if name == "topos_overview":
                return _result({"error": {"code": "daemon-unavailable"}})
            raise AssertionError(f"unexpected call: {name!r}")

    _install_mcp_imports(monkeypatch, _Session)
    proc = _Proc()

    checks, _max_bytes = asyncio.run(
        acceptance._run_mcp_client_session(Path("/tmp/topos.sock"), {}, proc)  # type: ignore[arg-type]
    )

    by_name = {check.name: check for check in checks}
    assert by_name["tool_calls"].ok is False
    assert by_name["tool_calls"].details["topos_health"] == "health response lost"
    assert by_name["invalid_selector"].ok is False
    assert by_name["invalid_selector"].details == {"error": "selector probe transport lost"}
    assert by_name["daemon_loss"].ok is False
    assert by_name["daemon_loss"].details == {
        "typed_error_code": "daemon-unavailable",
        "server_alive": False,
    }
    assert proc.killed is True


def test_mcp_session_turns_loss_call_exception_into_typed_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A transport exception during the loss probe does not escape JSON output."""
    import topos.acceptance as acceptance

    class _Session:
        def __init__(self, _read: object, _write: object) -> None:
            pass

        async def __aenter__(self) -> "_Session":
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def initialize(self) -> None:
            return None

        async def list_tools(self) -> object:
            return SimpleNamespace(
                tools=[SimpleNamespace(name=name) for name in acceptance._MCP_SMOKE_CALL_ORDER]
            )

        async def call_tool(self, name: str, arguments: dict[str, object]) -> object:
            if name == "topos_overview" and arguments["limit"] == 1:
                raise RuntimeError("daemon-loss transport closed")
            if name == "topos_overview":
                return _result({"data": {"rows": [{"key": "unit.scope"}]}})
            return _result({"data": {"ok": True}})

    _install_mcp_imports(monkeypatch, _Session)

    checks, _max_bytes = asyncio.run(
        acceptance._run_mcp_client_session(Path("/tmp/topos.sock"), {}, None)
    )

    by_name = {check.name: check for check in checks}
    assert by_name["daemon_loss"].ok is False
    assert by_name["daemon_loss"].details == {"error": "daemon-loss transport closed"}


def test_run_mcp_smoke_cleans_its_own_socket_after_a_successful_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The wrapper removes only its generated socket after a typed session result."""
    import importlib.util
    import topos.acceptance as acceptance

    socket_path = tmp_path / "topos.sock"
    socket_path.touch()
    terminated: list[bool] = []

    class _Proc:
        returncode = None

        def poll(self) -> None:
            return None

        def terminate(self) -> None:
            terminated.append(True)

        def wait(self, timeout: float | None = None) -> None:
            return None

    class _Probe:
        def __enter__(self) -> "_Probe":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def settimeout(self, _timeout: float) -> None:
            return None

        def connect(self, _address: str) -> None:
            return None

    monkeypatch.setattr(importlib.util, "find_spec", lambda _name: object())
    monkeypatch.setattr(tempfile, "mkdtemp", lambda **_kwargs: str(tmp_path))
    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: _Proc())
    monkeypatch.setattr(socket, "socket", lambda *_args: _Probe())
    monkeypatch.setattr(acceptance.platform, "platform", lambda: "test-platform")

    def _return_session(coro: object) -> tuple[list[object], int]:
        assert hasattr(coro, "close")
        coro.close()  # type: ignore[union-attr]
        return [], 17

    monkeypatch.setattr(asyncio, "run", _return_session)

    result = acceptance.run_mcp_smoke(timeout_s=1)

    assert result.ok is True
    assert result.max_response_bytes == 17
    assert result.checks == []
    assert terminated == [True]
    assert not socket_path.exists()
