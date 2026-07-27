"""Boundary contracts for the in-process MCP smoke client session."""

from __future__ import annotations

import asyncio
import builtins
import json
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
    """Route just the deferred MCP SDK imports to deterministic fakes."""
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


def test_run_mcp_smoke_returns_typed_skip_without_mcp_extra(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An optional-extra absence is a successful typed skip, never a spawn attempt."""
    import importlib.util
    import topos.acceptance as acceptance

    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)

    result = acceptance.run_mcp_smoke()

    assert result.ok is True
    assert result.extra_installed is False
    assert result.checks == []
    assert result.max_response_bytes is None


def test_mcp_session_turns_tool_discovery_exception_into_typed_hello_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A live SDK discovery error remains structured output for JSON consumers."""
    import topos.acceptance as acceptance

    class _DiscoveryFailureSession:
        def __init__(self, _read: object, _write: object) -> None:
            pass

        async def __aenter__(self) -> "_DiscoveryFailureSession":
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def initialize(self) -> None:
            return None

        async def list_tools(self) -> object:
            raise RuntimeError("transport closed")

    _install_mcp_imports(monkeypatch, _DiscoveryFailureSession)

    checks, max_bytes = asyncio.run(
        acceptance._run_mcp_client_session(Path("/tmp/topos.sock"), {}, None)
    )

    assert max_bytes is None
    assert [(check.name, check.ok, check.details) for check in checks] == [
        ("hello", False, {"error": "transport closed"}),
    ]
    assert "Tool discovery failed: transport closed" in checks[0].message


def test_mcp_session_reports_typed_tool_failure_and_missing_overview_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bad tool result cannot be laundered when later calls lack an entity key."""
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
                tools=[SimpleNamespace(name=name) for name in sorted(acceptance._MCP_SMOKE_TOOLS)]
            )

        async def call_tool(self, name: str, arguments: dict[str, object]) -> object:
            if name == "topos_health":
                return _result({"error": {"code": "daemon-unavailable", "message": "offline"}})
            if name == "topos_overview":
                if arguments["limit"] == 1:
                    return _result({"error": {"code": "daemon-unavailable"}})
                return _result({"data": {"rows": []}})
            if name == "topos_entity":
                return _result({"error": {"code": "invalid-selector"}})
            raise AssertionError(f"unexpected MCP call: {name}")

    _install_mcp_imports(monkeypatch, _Session)

    checks, max_bytes = asyncio.run(
        acceptance._run_mcp_client_session(Path("/tmp/topos.sock"), {}, None)
    )

    by_name = {check.name: check for check in checks}
    assert by_name["tool_calls"].ok is False
    assert "daemon-unavailable" in by_name["tool_calls"].details["topos_health"]["failure"]
    assert by_name["tool_calls"].details["topos_entity"] == (
        "skipped: topos_overview yielded no entity key to drive this tool"
    )
    assert by_name["tool_calls"].details["topos_history"] == (
        "skipped: topos_overview yielded no entity key to drive this tool"
    )
    assert by_name["response_cap"].ok is True
    assert max_bytes is not None
    assert by_name["invalid_selector"].ok is True
    assert by_name["daemon_loss"].ok is True
