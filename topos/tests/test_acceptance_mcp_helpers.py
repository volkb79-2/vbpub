"""Deterministic contracts for MCP acceptance helper functions."""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

from topos.acceptance import (
    Check,
    McpSmokeResult,
    _parse_tool_content,
    _terminate_process,
    _tool_call_failure,
    _update_byte_size,
    format_mcp_smoke_json,
    format_mcp_smoke_text,
)


def _result(*texts: str, is_error: bool = False) -> object:
    return SimpleNamespace(
        isError=is_error,
        content=[SimpleNamespace(text=text) for text in texts],
    )


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (SimpleNamespace(content=[]), {}),
        (_result("not-json"), "not-json"),
        (_result('{"data":{"ok":true}}'), {"data": {"ok": True}}),
    ],
)
def test_parse_tool_content_preserves_public_payload_or_empty(result: object, expected: object) -> None:
    assert _parse_tool_content(result) == expected


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (_result('{"data":{}}'), None),
        (_result('{"error":{"code":"daemon-unavailable"}}'), "typed error: daemon-unavailable"),
        (_result('{"error":"bad"}'), "malformed error field: 'bad'"),
        (_result("plain text"), "non-dict payload: 'plain text'"),
        (_result('{"data":{}}', is_error=True), "transport reported isError"),
    ],
)
def test_tool_failure_distinguishes_transport_typed_and_malformed_errors(result: object, expected: str | None) -> None:
    assert _tool_call_failure(result) == expected


def test_update_byte_size_counts_all_text_content_bytes() -> None:
    details: dict[str, object] = {}
    _update_byte_size(_result("plain", "é"), "overview", details)
    assert details == {"overview": {"bytes": len("plain".encode()) + len("é".encode())}}


class _Process:
    def __init__(self, *, running: bool, timeout: bool = False) -> None:
        self.running = running
        self.timeout = timeout
        self.terminated = 0
        self.killed = 0
        self.waits = 0

    def poll(self):
        return None if self.running else 0

    def terminate(self) -> None:
        self.terminated += 1

    def kill(self) -> None:
        self.killed += 1

    def wait(self, *, timeout: float) -> None:
        self.waits += 1
        if self.timeout and self.waits == 1:
            raise subprocess.TimeoutExpired("fake", timeout)


def test_terminate_process_handles_none_finished_and_timeout_kill_fallback() -> None:
    _terminate_process(None)
    finished = _Process(running=False)
    _terminate_process(finished)
    assert (finished.terminated, finished.killed) == (0, 0)

    slow = _Process(running=True, timeout=True)
    _terminate_process(slow)
    assert (slow.terminated, slow.killed, slow.waits) == (1, 1, 2)


def _mcp_result(*, extra_installed: bool, max_bytes: int | None, ok: bool) -> McpSmokeResult:
    return McpSmokeResult(
        ok=ok,
        version="1.0",
        python="3.14 final",
        platform="linux",
        extra_installed=extra_installed,
        checks=[Check("hello", ok, "reachable")],
        max_response_bytes=max_bytes,
        measurements={"wall_s": 1.0, "user_s": 0.5, "sys_s": 0.2, "rss_kb": 100.0},
    )


def test_mcp_formatters_preserve_skip_bytes_and_failure_verdict_contracts() -> None:
    skipped = format_mcp_smoke_text(_mcp_result(extra_installed=False, max_bytes=None, ok=True))
    assert "SKIPPED" in skipped and "ALL CHECKS PASSED" in skipped

    failed = _mcp_result(extra_installed=True, max_bytes=123, ok=False)
    text = format_mcp_smoke_text(failed)
    assert "Largest response: 123 bytes" in text
    assert "SOME CHECKS FAILED  (exit code 1)" in text
    assert json.loads(format_mcp_smoke_json(failed)) ["max_response_bytes"] == 123
