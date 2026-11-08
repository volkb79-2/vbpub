"""PWMCP's standard-library MCP client and CLI browser-evaluation features."""

from __future__ import annotations

import base64
import http.client
import json
import random
import subprocess
import sys
import threading
from types import SimpleNamespace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest

from pwmcp_client import cli as cli_module
from pwmcp_client import mcp_client
from pwmcp_client.mcp_client import (
    McpUnavailable,
    McpError,
    McpTimeout,
    McpToolError,
    StreamableHttpClient,
    decode_html_value,
    decode_tool_value,
    evaluation_function,
    html_extraction_function,
    is_browser_crash,
    page_diagnostics_function,
    visible_selector_function,
)
from _testdeps import require_binary


class _McpState:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self.navigate_errors: list[str] = []
        self.tool_errors: dict[str, str] = {}
        self.eval_value: object = {"title": "Sample page"}
        self.eval_results: list[object] = []
        self.html_value: object = None
        self.page_diagnostics: object = {
            "title": "Sample page",
            "text": "Page body",
            "status": 200,
            "final_url": "https://example.test/page",
            "frames": [],
        }
        self.tools: list[dict[str, object]] = [
            {"name": "browser_navigate", "inputSchema": {"type": "object"}},
            {"name": "browser_evaluate", "inputSchema": {"type": "object"}},
        ]
        self.sse_methods: set[str] = {"tools/call"}
        self.deleted_sessions = 0
        self.request_headers: list[dict[str, str]] = []
        self.delete_headers: list[dict[str, str]] = []
        self.protocol_version = "2024-11-05"
        self.session_enabled = True
        self.http_error_status: int | None = None
        self.rpc_error_methods: set[str] = set()
        self.bad_tools_result: object | None = None
        self.notification_status = 202
        self.response_overrides: dict[str, tuple[str, bytes]] = {}
        self.wrong_id_methods: set[str] = set()
        self.empty_body_methods: set[str] = set()
        self.delete_status = 200
        self.sse_line_ending = b"\n"
        self.tool_error_sequences: dict[str, list[str]] = {}


class _FakeMcpServer:
    def __init__(self) -> None:
        self.state = _McpState()
        state = self.state

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                state.request_headers.append({name.lower(): value for name, value in self.headers.items()})
                if state.http_error_status is not None:
                    self.send_response(state.http_error_status)
                    self.end_headers()
                    return
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                state.messages.append(payload)
                method = payload["method"]
                if method == "notifications/initialized":
                    self.send_response(state.notification_status)
                    self.end_headers()
                    return
                if method == "initialize":
                    result: object = {
                        "protocolVersion": state.protocol_version,
                        "capabilities": {},
                        "serverInfo": {},
                    }
                elif method == "tools/list":
                    result = state.bad_tools_result if state.bad_tools_result is not None else {"tools": state.tools}
                elif method == "tools/call":
                    name = payload["params"]["name"]
                    if name == "browser_navigate" and state.navigate_errors:
                        text = state.navigate_errors.pop(0)
                        result = {"isError": True, "content": [{"type": "text", "text": text}]}
                    elif name in state.tool_error_sequences and state.tool_error_sequences[name]:
                        text = state.tool_error_sequences[name].pop(0)
                        result = {"isError": True, "content": [{"type": "text", "text": text}]}
                    elif name in state.tool_errors:
                        text = state.tool_errors[name]
                        result = {"isError": True, "content": [{"type": "text", "text": text}]}
                    elif name == "browser_navigate":
                        result = {"content": [{"type": "text", "text": "navigated"}]}
                    else:
                        function = payload["params"]["arguments"]["function"]
                        if (
                            "const source = element.outerHTML" in function
                            or "const source = element.innerHTML" in function
                        ):
                            value = state.html_value
                        elif "performance.getEntriesByType('navigation')" in function:
                            value = state.page_diagnostics
                        elif function.startswith("async ") and "MutationObserver" in function:
                            value = True
                        else:
                            value = state.eval_results.pop(0) if state.eval_results else state.eval_value
                        result = {
                            "content": [
                                {"type": "text", "text": "### Result\n" + json.dumps(value, ensure_ascii=False)}
                            ]
                        }
                else:
                    result = None
                if method in state.rpc_error_methods:
                    response_message = {
                        "jsonrpc": "2.0",
                        "id": payload.get("id"),
                        "error": {"code": -32601, "message": "fixture RPC error"},
                    }
                else:
                    response_message = {"jsonrpc": "2.0", "id": payload.get("id"), "result": result}
                body = json.dumps(response_message, ensure_ascii=False).encode(
                    "utf-8", errors="backslashreplace"
                )
                self.send_response(200)
                default_type = "text/event-stream" if method in state.sse_methods else "application/json"
                content_type, override_body = state.response_overrides.get(method, (default_type, body))
                self.send_header("Content-Type", content_type)
                if method == "initialize" and state.session_enabled:
                    self.send_header("Mcp-Session-Id", "fixture-session")
                self.end_headers()
                if method not in state.empty_body_methods:
                    if method in state.response_overrides:
                        self.wfile.write(override_body)
                    elif method in state.sse_methods:
                        if method in state.wrong_id_methods:
                            response_message["id"] = -1
                            body = json.dumps(response_message, ensure_ascii=False).encode(
                                "utf-8", errors="backslashreplace"
                            )
                        ending = state.sse_line_ending
                        self.wfile.write(b"event: message" + ending + b"data: " + body + ending + ending)
                    else:
                        if method in state.wrong_id_methods:
                            response_message["id"] = -1
                            body = json.dumps(response_message, ensure_ascii=False).encode(
                                "utf-8", errors="backslashreplace"
                            )
                        self.wfile.write(body)

            def do_DELETE(self) -> None:  # noqa: N802
                state.deleted_sessions += 1
                state.delete_headers.append({name.lower(): value for name, value in self.headers.items()})
                self.send_response(state.delete_status)
                self.end_headers()

            def log_message(self, *_args: object) -> None:
                return None

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}/mcp"

    def __enter__(self) -> "_FakeMcpServer":
        self.thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


def _call_args(state: _McpState, name: str) -> list[dict[str, object]]:
    return [
        message["params"]["arguments"]
        for message in state.messages
        if message["method"] == "tools/call" and message["params"]["name"] == name
    ]


def _patch_urlopen(monkeypatch: pytest.MonkeyPatch, opener: Any) -> None:
    """Patch both the legacy convenience call and the client-owned opener path."""

    monkeypatch.setattr(mcp_client.urllib.request, "urlopen", opener)
    monkeypatch.setattr(
        mcp_client.urllib.request.OpenerDirector,
        "open",
        lambda _self, *args, **kwargs: opener(*args, **kwargs),
    )


def test_eval_uses_browser_evaluate_without_the_mcp_python_sdk(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setitem(sys.modules, "mcp", None)
    with _FakeMcpServer() as server:
        monkeypatch.setenv("PWMCP_MCP_URL", server.url)
        assert cli_module.main(["eval", "https://example.test/page", "document.title"]) == 0
        output = json.loads(capsys.readouterr().out)
    assert output == {"title": "Sample page"}
    assert [message["method"] for message in server.state.messages] == [
        "initialize",
        "notifications/initialized",
        "tools/list",
        "tools/call",
        "tools/call",
    ]
    assert _call_args(server.state, "browser_navigate") == [{"url": "https://example.test/page"}]
    evaluated_function = _call_args(server.state, "browser_evaluate")[0]["function"]
    assert evaluated_function.startswith("async () => { const value = await (\n() => (")
    assert "document.title" in evaluated_function and "JSON.stringify(value)" in evaluated_function
    assert server.state.deleted_sessions == 1


def test_eval_selector_attribute_all_matches_returns_json_array(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with _FakeMcpServer() as server:
        server.state.eval_value = ["a", "b"]
        monkeypatch.setenv("PWMCP_MCP_URL", server.url)
        assert cli_module.main([
            "eval", "https://example.test", "--selector", ".card", "--attr", "data-id", "--all", "--json"
        ]) == 0
        output = json.loads(capsys.readouterr().out)
    assert output == {
        "content": ["a", "b"],
        "diagnostics": [],
        "format": "eval",
        "http_error": False,
        "http_status": 200,
    }
    function = _call_args(server.state, "browser_evaluate")[0]["function"]
    assert "document.querySelectorAll(\".card\")" in function
    assert "element.getAttribute(\"data-id\")" in function
    assert "return values" in function


@pytest.mark.parametrize("separator", ["\u2028", "\u2029", "\u0085"])
def test_eval_preserves_raw_unicode_line_separators_in_sse_json(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    separator: str,
) -> None:
    expected = f"before{separator}after"
    with _FakeMcpServer() as server:
        server.state.eval_value = expected
        monkeypatch.setenv("PWMCP_MCP_URL", server.url)
        assert cli_module.main(["eval", "https://example.test", "document.title"]) == 0
        actual = json.loads(capsys.readouterr().out)
    assert actual == expected


@pytest.mark.parametrize("separator", ["\u2028", "\u2029", "\u0085"])
def test_inspect_html_block_probe_preserves_raw_unicode_line_separators_in_sse(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    separator: str,
) -> None:
    html = "<main>fixture</main>"
    with _FakeMcpServer() as server:
        server.state.page_diagnostics["text"] = f"before{separator}after"
        server.state.html_value = {
            "data": base64.b64encode(html.encode()).decode(),
            "included_bytes": len(html.encode()),
            "total_bytes": len(html.encode()),
            "truncated": False,
        }
        monkeypatch.setenv("PWMCP_MCP_URL", server.url)
        assert cli_module.main(["inspect", "https://example.test", "--format", "html"]) == 0
        actual = capsys.readouterr().out
    assert actual == html + "\n"


def test_eval_output_limit_marks_truncation_in_text_and_json(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with _FakeMcpServer() as server:
        server.state.eval_value = "x" * 100
        monkeypatch.setenv("PWMCP_MCP_URL", server.url)
        assert cli_module.main([
            "eval", "https://example.test", "document.body.innerText", "--max-bytes", "16"
        ]) == 0
        text_result = capsys.readouterr()
        text_output = text_result.out
        assert "[truncated" not in text_output
        assert "[truncated after 16 bytes]" in text_result.err
        assert len(text_output.encode("utf-8")) < 100

        assert cli_module.main([
            "eval", "https://example.test", "document.body.innerText", "--max-bytes", "16", "--json"
        ]) == 0
        json_output = json.loads(capsys.readouterr().out)
    assert json_output["truncated"] is True
    assert json_output["max_bytes"] == 16
    assert len(json_output["content"].encode("utf-8")) <= 16
    assert json_output["http_error"] is False
    assert len(json_output["preview"].encode("utf-8")) <= 16


def test_eval_json_has_status_fields_and_http_errors_exit_distinctly(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with _FakeMcpServer() as server:
        server.state.page_diagnostics["status"] = 404
        server.state.eval_value = "Not Found"
        monkeypatch.setenv("PWMCP_MCP_URL", server.url)
        assert cli_module.main(["eval", "https://example.test", "document.title", "--json"]) == 5
        captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result["content"] == "Not Found"
    assert result["http_status"] == 404 and result["http_error"] is True
    assert any("main document HTTP 404" in item for item in result["diagnostics"])
    assert "--allow-http-error" in captured.err


def test_eval_http_error_exit_also_applies_without_json_mode(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with _FakeMcpServer() as server:
        server.state.page_diagnostics["status"] = 404
        server.state.eval_value = "Not Found"
        monkeypatch.setenv("PWMCP_MCP_URL", server.url)
        assert cli_module.main(["eval", "https://example.test", "document.title"]) == 5
        captured = capsys.readouterr()
    assert json.loads(captured.out) == "Not Found"
    assert "main document HTTP 404" in captured.err


def test_eval_json_without_diagnostics_uses_the_stable_object_shape(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with _FakeMcpServer() as server:
        server.state.eval_value = "Title"
        monkeypatch.setenv("PWMCP_MCP_URL", server.url)
        assert cli_module.main(["eval", "https://example.test", "document.title", "--json"]) == 0
        captured = capsys.readouterr()
    assert json.loads(captured.out) == {
        "content": "Title",
        "diagnostics": [],
        "format": "eval",
        "http_error": False,
        "http_status": 200,
    }
    assert captured.err == ""


@pytest.mark.parametrize(
    ("page_diagnostics", "message"),
    [
        ("invalid diagnostics", "non-object result"),
        ({"status": True}, "invalid HTTP status"),
    ],
)
def test_eval_rejects_malformed_page_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    page_diagnostics: object,
    message: str,
) -> None:
    with _FakeMcpServer() as server:
        server.state.page_diagnostics = page_diagnostics
        monkeypatch.setenv("PWMCP_MCP_URL", server.url)
        assert cli_module.main([
            "eval", "https://example.test", "document.title", "--json",
        ]) == 1
        captured = capsys.readouterr()
    assert message in captured.err
    assert captured.out == ""


def test_eval_allow_http_error_returns_the_page_value(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with _FakeMcpServer() as server:
        server.state.page_diagnostics["status"] = 404
        server.state.eval_value = "Not Found"
        monkeypatch.setenv("PWMCP_MCP_URL", server.url)
        assert cli_module.main([
            "eval", "https://example.test", "document.title", "--allow-http-error",
        ]) == 0
        captured = capsys.readouterr()
    assert json.loads(captured.out) == "Not Found"
    assert "main document HTTP 404" in captured.err


def test_eval_text_truncation_notice_goes_to_stderr_and_keeps_success_status(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with _FakeMcpServer() as server:
        server.state.eval_value = "x" * 100
        monkeypatch.setenv("PWMCP_MCP_URL", server.url)
        assert cli_module.main([
            "eval", "https://example.test", "document.body.innerText", "--max-bytes", "16"
        ]) == 0
    captured = capsys.readouterr()
    assert "[truncated after 16 bytes]" not in captured.out
    assert "[truncated after 16 bytes]" in captured.err
    assert len(captured.out.rstrip("\n").encode("utf-8")) <= 16


def test_eval_exact_byte_cap_is_not_marked_truncated(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with _FakeMcpServer() as server:
        # Compact JSON for this value is exactly seven UTF-8 bytes, including quotes.
        server.state.eval_value = {
            "__pwmcp_eval_result__": True,
            "truncated": False,
            "value": "abcde",
        }
        monkeypatch.setenv("PWMCP_MCP_URL", server.url)
        assert cli_module.main([
            "eval", "https://example.test", "document.body.innerText", "--max-bytes", "7"
        ]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out) == "abcde"
    assert "truncated" not in captured.err


def test_eval_page_truncation_envelope_uses_the_json_preview_shape(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with _FakeMcpServer() as server:
        server.state.eval_value = {
            "__pwmcp_eval_result__": True,
            "truncated": True,
            "preview": '"x' * 8,
            "max_bytes": 16,
        }
        monkeypatch.setenv("PWMCP_MCP_URL", server.url)
        assert cli_module.main([
            "eval", "https://example.test", "document.body.innerText", "--max-bytes", "16", "--json"
        ]) == 0
        output = json.loads(capsys.readouterr().out)
    assert output == {
        "content": '"x' * 8,
        "diagnostics": [],
        "format": "eval",
        "http_error": False,
        "http_status": 200,
        "preview": '"x' * 8,
        "truncated": True,
        "max_bytes": 16,
    }


def test_eval_reports_transport_oversize_with_a_size_hint(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with _FakeMcpServer() as server:
        # This fake deliberately ignores the in-page cap function, exercising the
        # independent HTTP response ceiling and its user-facing diagnosis.
        server.state.eval_value = "x" * 100_000
        monkeypatch.setenv("PWMCP_MCP_URL", server.url)
        assert cli_module.main([
            "eval", "https://example.test", "document.body.innerText", "--max-bytes", "16"
        ]) == 1
    error = capsys.readouterr().err
    assert "MCP response exceeded" in error
    assert "return a smaller or aggregated value" in error
    assert "check that the PWMCP MCP endpoint is reachable" not in error


def test_eval_response_limit_scales_with_the_requested_output_limit(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with _FakeMcpServer() as server:
        # The value fits under --max-bytes, while JSON-RPC/SSE framing makes
        # the MCP response exceed 1000 bytes.
        server.state.eval_value = "x" * 900
        response_text = "### Result\n" + json.dumps(server.state.eval_value, ensure_ascii=False)
        response_message = {
            "jsonrpc": "2.0",
            "id": 4,
            "result": {"content": [{"type": "text", "text": response_text}]},
        }
        assert len(json.dumps(response_message, separators=(",", ":")).encode()) + len(
            b"event: message\ndata: \n\n"
        ) > 1000
        monkeypatch.setenv("PWMCP_MCP_URL", server.url)
        assert cli_module.main([
            "eval", "https://example.test", "document.body.innerText", "--max-bytes", "1024"
        ]) == 0
        output = json.loads(capsys.readouterr().out)
    assert output == "x" * 900


@pytest.mark.parametrize("json_mode", [False, True])
def test_eval_outputs_lone_surrogates_without_crashing(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    json_mode: bool,
) -> None:
    value = "a\ud800b"
    with _FakeMcpServer() as server:
        server.state.eval_value = value
        monkeypatch.setenv("PWMCP_MCP_URL", server.url)
        args = ["eval", "https://example.test", "document.body.innerText", "--max-bytes", "4"]
        if json_mode:
            args.append("--json")
        assert cli_module.main(args) == 0
        captured = capsys.readouterr()
    if json_mode:
        output = json.loads(captured.out)
        assert output == {
            "content": '"a\\u',
            "diagnostics": [],
            "format": "eval",
            "http_error": False,
            "http_status": 200,
            "preview": '"a\\u',
            "truncated": True,
            "max_bytes": 4,
        }
    else:
        assert captured.out == '"a\\u\n'
        assert "[truncated after 4 bytes]" in captured.err


def test_eval_caps_a_large_page_value_inside_the_page_function() -> None:
    node = require_binary("node", "Node.js executes the generated eval size guard")
    function = evaluation_function("'large value'.repeat(1000)", max_bytes=16)
    program = (
        "const fs = require('fs'); const input = JSON.parse(fs.readFileSync(0, 'utf8')); "
        "(async () => { const fn = eval('(' + input.function + ')'); "
        "console.log(JSON.stringify(await fn())); })();"
    )
    completed = subprocess.run(
        [node, "-e", program],
        input=json.dumps({"function": function}),
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(completed.stdout)
    assert result["__pwmcp_eval_result__"] is True
    assert result["truncated"] is True
    assert result["max_bytes"] == 16
    assert len(result["preview"].encode("utf-8")) <= 16


def test_eval_page_cap_includes_exact_boundary_and_shortens_utf8_safely() -> None:
    node = require_binary("node", "Node.js executes generated eval cap boundary cases")
    program = (
        "const fs = require('fs'); const input = JSON.parse(fs.readFileSync(0, 'utf8')); "
        "(async () => { const fn = eval('(' + input.function + ')'); "
        "console.log(JSON.stringify(await fn())); })();"
    )
    for expression, limit, expected_truncated in (("'abcde'", 7, False), ("'😀x'", 4, True)):
        function = evaluation_function(expression, max_bytes=limit)
        completed = subprocess.run(
            [node, "-e", program],
            input=json.dumps({"function": function}),
            check=True,
            capture_output=True,
            text=True,
        )
        result = json.loads(completed.stdout)
        assert result["truncated"] is expected_truncated
        if expected_truncated:
            assert result["preview"] == '"'
            assert len(result["preview"].encode("utf-8")) <= limit
        else:
            assert result["value"] == "abcde"


def test_eval_page_function_preserves_undefined_results() -> None:
    node = require_binary("node", "Node.js executes generated eval size handling")
    function = evaluation_function("undefined", max_bytes=16)
    program = (
        "const fs = require('fs'); const input = JSON.parse(fs.readFileSync(0, 'utf8')); "
        "(async () => { const fn = eval('(' + input.function + ')'); "
        "const result = await fn(); console.log(JSON.stringify({result})); })();"
    )
    completed = subprocess.run(
        [node, "-e", program],
        input=json.dumps({"function": function}),
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(completed.stdout) == {}


@pytest.mark.parametrize("line_ending", [b"\r\n", b"\r"])
def test_eval_parses_crlf_and_bare_cr_sse_frames(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    line_ending: bytes,
) -> None:
    with _FakeMcpServer() as server:
        server.state.sse_line_ending = line_ending
        server.state.eval_value = 2
        monkeypatch.setenv("PWMCP_MCP_URL", server.url)
        assert cli_module.main(["eval", "https://example.test", "1 + 1"]) == 0
        assert json.loads(capsys.readouterr().out) == 2


def test_mcp_redirects_are_refused_without_forwarding_credentials_or_session_id() -> None:
    forwarded: list[tuple[str, dict[str, str]]] = []
    first_requests: list[tuple[str, dict[str, str]]] = []

    class TargetHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            forwarded.append(("GET", {key.lower(): value for key, value in self.headers.items()}))
            self.send_response(204)
            self.end_headers()

        def do_POST(self) -> None:  # noqa: N802
            forwarded.append(("POST", {key.lower(): value for key, value in self.headers.items()}))
            self.send_response(204)
            self.end_headers()

        def do_DELETE(self) -> None:  # noqa: N802
            forwarded.append(("DELETE", {key.lower(): value for key, value in self.headers.items()}))
            self.send_response(204)
            self.end_headers()

        def log_message(self, *_args: object) -> None:
            return None

    target = ThreadingHTTPServer(("127.0.0.1", 0), TargetHandler)
    target.daemon_threads = True
    target_thread = threading.Thread(target=target.serve_forever, daemon=True)
    target_thread.start()
    target_url = f"http://127.0.0.1:{target.server_port}/mcp"

    class RedirectHandler(BaseHTTPRequestHandler):
        def _redirect(self, method: str) -> None:
            first_requests.append((method, {key.lower(): value for key, value in self.headers.items()}))
            self.send_response(302)
            self.send_header("Location", target_url)
            self.end_headers()

        def do_POST(self) -> None:  # noqa: N802
            self._redirect("POST")

        def do_DELETE(self) -> None:  # noqa: N802
            self._redirect("DELETE")

        def log_message(self, *_args: object) -> None:
            return None

    redirect = ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
    redirect.daemon_threads = True
    redirect_thread = threading.Thread(target=redirect.serve_forever, daemon=True)
    redirect_thread.start()
    try:
        endpoint = f"http://user:secret@127.0.0.1:{redirect.server_port}/mcp"
        client = StreamableHttpClient(endpoint)
        with pytest.raises(McpUnavailable, match=r"HTTP 302.*redirects are not followed"):
            client._send({"method": "initialize"}, expected_id=1)
        client.session_id = "fixture-session"
        client.close()
    finally:
        redirect.shutdown()
        redirect.server_close()
        redirect_thread.join()
        target.shutdown()
        target.server_close()
        target_thread.join()

    assert [method for method, _headers in first_requests] == ["POST", "DELETE"]
    assert first_requests[0][1]["authorization"].startswith("Basic ")
    assert first_requests[1][1]["mcp-session-id"] == "fixture-session"
    assert forwarded == []


def test_mcp_client_ignores_http_proxy_environment_for_credentialed_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proxy_requests: list[str] = []

    class ProxyHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            proxy_requests.append(self.command)
            self.send_response(502)
            self.end_headers()

        def do_DELETE(self) -> None:  # noqa: N802
            proxy_requests.append(self.command)
            self.send_response(502)
            self.end_headers()

        def log_message(self, *_args: object) -> None:
            return None

    proxy = ThreadingHTTPServer(("127.0.0.1", 0), ProxyHandler)
    proxy.daemon_threads = True
    proxy_thread = threading.Thread(target=proxy.serve_forever, daemon=True)
    proxy_thread.start()
    try:
        proxy_url = f"http://127.0.0.1:{proxy.server_port}"
        for name in ("http_proxy", "HTTP_PROXY", "all_proxy", "ALL_PROXY"):
            monkeypatch.setenv(name, proxy_url)
        for name in ("no_proxy", "NO_PROXY"):
            monkeypatch.setenv(name, "")
        # urlopen builds its process-global opener lazily. Reset it after setting
        # the proxy so a regression in close() cannot inherit a stale no-proxy
        # configuration from an earlier test.
        monkeypatch.setattr(mcp_client.urllib.request, "_opener", None)
        with _FakeMcpServer() as server:
            endpoint = server.url.replace("http://", "http://user:secret@", 1)
            client = StreamableHttpClient(endpoint)
            with client:
                client.call_tool("browser_navigate", {"url": "https://example.test"})
                client.close()
            assert server.state.deleted_sessions == 1
    finally:
        proxy.shutdown()
        proxy.server_close()
        proxy_thread.join()
    assert proxy_requests == []


@pytest.mark.parametrize("value", ["0", "8388609"])
def test_eval_output_limit_rejects_values_outside_the_supported_range(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    value: str,
) -> None:
    monkeypatch.setenv("PWMCP_MCP_URL", "not-used")
    assert cli_module.main(["eval", "https://example.test", "1", "--max-bytes", value]) == 1
    assert "--max-bytes must be between" in capsys.readouterr().err


def test_eval_mcp_url_userinfo_becomes_basic_auth_and_is_removed_from_endpoint() -> None:
    username = "pwmcp consumer"
    password = " odd:@/+#[]%$!"
    userinfo = f"{quote(username, safe='')}:{quote(password, safe='')}"
    with _FakeMcpServer() as server:
        endpoint = server.url.replace("http://", f"http://{userinfo}@", 1)
        with StreamableHttpClient(endpoint) as client:
            assert client.endpoint == server.url
            client.call_tool("browser_navigate", {"url": "https://example.test"})
        expected = "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()
        assert all(headers["authorization"] == expected for headers in server.state.request_headers)
        assert server.state.delete_headers[0]["authorization"] == expected


def test_eval_mcp_url_errors_do_not_expose_odd_userinfo(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    username = "pwmcp"
    password = " pass:@/?#[]%$! "
    encoded_password = quote(password, safe="")
    endpoint = f"http://{quote(username, safe='')}:{encoded_password}@example.test:bad/mcp"
    monkeypatch.setenv("PWMCP_MCP_URL", endpoint)
    assert cli_module.main(["eval", "https://example.test", "document.title"]) == 1
    captured = capsys.readouterr()
    with pytest.raises(McpUnavailable) as raised:
        StreamableHttpClient(endpoint)
    exception_text = f"{raised.value}\n{raised.value!r}"
    for secret in (password, encoded_password):
        assert secret not in captured.out
        assert secret not in captured.err
        assert secret not in exception_text


@pytest.mark.parametrize("exception_type", [http.client.InvalidURL, ValueError, RuntimeError])
def test_eval_mcp_transport_errors_are_safe_with_credentialed_urls(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    exception_type: type[Exception],
) -> None:
    username = "consumer"
    password = "odd :@/?#[]% value"
    encoded_user = quote(username, safe="")
    encoded_password = quote(password, safe="")
    endpoint = f"http://{encoded_user}:{encoded_password}@127.0.0.1:8931/mcp"
    requests: list[str] = []

    def reject_request(request: object, **_kwargs: object) -> object:
        request_url = str(getattr(request, "full_url"))
        requests.append(request_url)
        raise exception_type(f"rejected {request_url}")

    monkeypatch.setenv("PWMCP_MCP_URL", endpoint)
    _patch_urlopen(monkeypatch, reject_request)
    assert cli_module.main(["eval", "https://example.test", "document.title"]) == 1
    captured = capsys.readouterr()
    with pytest.raises(McpUnavailable) as raised:
        StreamableHttpClient(endpoint).initialize()
    exception_text = f"{raised.value}\n{raised.value!r}"
    assert requests and all("consumer:" not in request for request in requests)
    for secret in (password, encoded_password):
        assert secret not in captured.out
        assert secret not in captured.err
        assert secret not in exception_text


def test_sse_multiline_data_fields_are_joined_with_newlines(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = b'event: message\ndata: {"jsonrpc":"2.0","id":7,\ndata: "result":{"ok":true}}\n\n'
    loaded: list[str] = []
    real_loads = json.loads

    def record_loads(value: str | bytes | bytearray, *args: object, **kwargs: object) -> object:
        loaded.append(value.decode() if isinstance(value, bytes) else str(value))
        return real_loads(value, *args, **kwargs)

    monkeypatch.setattr(mcp_client.json, "loads", record_loads)
    assert mcp_client._sse_message(payload, 7) == {
        "jsonrpc": "2.0",
        "id": 7,
        "result": {"ok": True},
    }
    assert loaded == ['{"jsonrpc":"2.0","id":7,\n"result":{"ok":true}}']


def test_eval_javascript_syntax_error_keeps_its_position(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with _FakeMcpServer() as server:
        server.state.tool_errors["browser_evaluate"] = "SyntaxError: Unexpected token ')' at position 5"
        monkeypatch.setenv("PWMCP_MCP_URL", server.url)
        assert cli_module.main(["eval", "https://example.test", "1 + )"]) == 1
    error = capsys.readouterr().err
    assert "SyntaxError" in error
    assert "position 5" in error
    assert "MCP endpoint replied" in error


def test_eval_reports_a_real_javascript_syntax_error_with_its_source_location(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    node = require_binary("node", "Node.js checks generated eval syntax and source locations")
    function = evaluation_function("1 + )")
    program = (
        "const fs = require('fs'); const vm = require('vm'); "
        "const input = JSON.parse(fs.readFileSync(0, 'utf8')); "
        "try { new vm.Script('(' + input.function + ')', {filename: 'pwmcp-eval.js'}); "
        "console.log(JSON.stringify({name: null, stack: ''})); } "
        "catch (error) { console.log(JSON.stringify({name: error.name, stack: error.stack})); }"
    )
    completed = subprocess.run(
        [node, "-e", program],
        input=json.dumps({"function": function}),
        check=True,
        capture_output=True,
        text=True,
    )
    syntax = json.loads(completed.stdout)
    assert syntax["name"] == "SyntaxError"
    assert "pwmcp-eval.js:" in syntax["stack"] and "^" in syntax["stack"]

    with _FakeMcpServer() as server:
        server.state.tool_errors["browser_evaluate"] = syntax["stack"]
        monkeypatch.setenv("PWMCP_MCP_URL", server.url)
        assert cli_module.main(["eval", "https://example.test", "1 + )"]) == 1
    error = capsys.readouterr().err
    assert "SyntaxError" in error
    assert "pwmcp-eval.js:" in error
    assert "^" in error


def test_eval_timeout_hint_does_not_suggest_a_browser_crash_retry(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def timeout(*_args: object, **_kwargs: object) -> object:
        raise McpTimeout("MCP endpoint request timed out")

    monkeypatch.setattr(cli_module, "StreamableHttpClient", timeout)
    assert cli_module.main(["eval", "https://example.test", "document.title"]) == 1
    error = capsys.readouterr().err
    assert "timed out" in error
    assert "increase --timeout" in error
    assert "page or browser crashed" not in error


def test_browser_closure_hint_does_not_claim_pwmcp_is_reachable() -> None:
    hint = cli_module._failure_hint(RuntimeError("Browser has been closed"))
    assert "crashed or was closed" in hint
    assert "check that PWMCP is still running" in hint
    assert "PWMCP is reachable" not in hint


def test_inspect_html_passes_margin_for_page_selector_wait_timeout(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    timeouts: list[float] = []
    real_client = cli_module.StreamableHttpClient

    def recording_client(*args: object, **kwargs: object) -> StreamableHttpClient:
        timeouts.append(float(kwargs["timeout"]))
        return real_client(*args, **kwargs)

    with _FakeMcpServer() as server:
        server.state.html_value = {
            "data": base64.b64encode(b"<main>ok</main>").decode(),
            "included_bytes": len(b"<main>ok</main>"),
            "total_bytes": len(b"<main>ok</main>"),
            "truncated": False,
        }
        monkeypatch.setenv("PWMCP_MCP_URL", server.url)
        monkeypatch.setattr(cli_module, "StreamableHttpClient", recording_client)
        assert cli_module.main([
            "inspect", "https://example.test", "--format", "html", "--wait-for", "main", "--timeout-ms", "1000"
        ]) == 0
        capsys.readouterr()
    assert timeouts == [3.0]


def test_eval_mcp_error_keeps_browser_crash_hint(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with _FakeMcpServer() as server:
        server.state.navigate_errors = ["Page.goto: Page crashed"]
        monkeypatch.setenv("PWMCP_MCP_URL", server.url)
        assert cli_module.main(["eval", "https://example.test", "document.title"]) == 1
    error = capsys.readouterr().err
    assert "MCP endpoint replied, but the page or browser crashed" in error
    assert "pwmcp eval failed: browser_navigate: Page.goto: Page crashed" in error


def test_eval_endpoint_failure_hint_names_the_mcp_endpoint(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def unavailable(*_args: object, **_kwargs: object) -> object:
        raise McpUnavailable("MCP endpoint is unreachable (connection refused)")

    monkeypatch.setattr(cli_module, "StreamableHttpClient", unavailable)
    assert cli_module.main(["eval", "https://example.test", "document.title"]) == 1
    error = capsys.readouterr().err
    assert "MCP endpoint is unreachable" in error
    assert "check that the PWMCP MCP endpoint is reachable" in error


def test_eval_tool_error_says_the_endpoint_replied(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with _FakeMcpServer() as server:
        server.state.tool_errors["browser_evaluate"] = "Uncaught ReferenceError: missingValue"
        monkeypatch.setenv("PWMCP_MCP_URL", server.url)
        assert cli_module.main(["eval", "https://example.test", "missingValue"]) == 1
    error = capsys.readouterr().err
    assert "MCP endpoint replied" in error
    assert "requested JavaScript" in error
    assert "check that the PWMCP MCP endpoint is reachable" not in error


def test_eval_shared_browser_busy_hint_recommends_serial_calls(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with _FakeMcpServer() as server:
        server.state.navigate_errors = ["Browser is already in use by another MCP caller"]
        monkeypatch.setenv("PWMCP_MCP_URL", server.url)
        assert cli_module.main(["eval", "https://example.test", "document.title"]) == 1
    error = capsys.readouterr().err
    assert "MCP endpoint replied" in error
    assert "serialize calls" in error


@pytest.mark.parametrize(
    "arguments,message",
    [
        (["--selector", "#a"], "requires --attr NAME or --property NAME"),
        (["--attr", "href"], "require --selector"),
        (["--selector", "#a", "--attr", "href", "--property", "href"], "choose only one"),
        (["--selector", "#a", "--attr", "href", "document.title"], "do not combine"),
        (["--all", "document.title"], "--all requires --selector"),
        ([], "provide a JavaScript expression or function"),
    ],
)
def test_eval_rejects_incompatible_selector_modes_before_connecting(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    arguments: list[str],
    message: str,
) -> None:
    monkeypatch.setenv("PWMCP_MCP_URL", "not-used")
    assert cli_module.main(["eval", "https://example.test", *arguments]) == 1
    captured = capsys.readouterr()
    assert message in captured.err
    assert "MCP endpoint" not in captured.err


@pytest.mark.parametrize("timeout", ["0", "nan"])
def test_eval_requires_a_finite_positive_timeout(
    capsys: pytest.CaptureFixture[str], timeout: str
) -> None:
    assert cli_module.main(["eval", "https://example.test", "1", "--timeout", timeout]) == 1
    assert "--timeout must be a positive number" in capsys.readouterr().err


@pytest.mark.parametrize("url", ["example.test", "ftp://example.test", "http://["])
def test_eval_refuses_non_http_or_malformed_urls(capsys: pytest.CaptureFixture[str], url: str) -> None:
    assert cli_module.main(["eval", url, "document.title"]) == 1
    error = capsys.readouterr().err
    assert "absolute http:// or https:// URL" in error or "URL is malformed" in error


def test_inspect_html_reads_element_html_and_waits_for_a_visible_selector(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    html = "<main><h1>Ready</h1></main>"
    with _FakeMcpServer() as server:
        server.state.html_value = {
            "data": base64.b64encode(html.encode()).decode(),
            "included_bytes": len(html.encode()),
            "total_bytes": len(html.encode()),
            "truncated": False,
        }
        monkeypatch.setenv("PWMCP_MCP_URL", server.url)
        assert cli_module.main([
            "inspect", "https://example.test", "--format", "html", "--selector", "main",
            "--html-mode", "outer", "--wait-for", "h1", "--json",
        ]) == 0
        output = json.loads(capsys.readouterr().out)
    assert output == {
        "content": html,
        "diagnostics": [],
        "format": "html",
        "http_error": False,
        "http_status": 200,
        "truncated": False,
        "size_bytes": len(html.encode()),
        "included_bytes": len(html.encode()),
    }
    functions = [call["function"] for call in _call_args(server.state, "browser_evaluate")]
    assert len(functions) == 3
    assert "performance.getEntriesByType('navigation')" in functions[0]
    assert "MutationObserver" in functions[1] and "h1" in functions[1]
    assert "document.querySelector(\"main\")" in functions[2]
    assert "element.outerHTML" in functions[2]


@pytest.mark.parametrize(("allow", "expected_exit"), [(False, 5), (True, 0)])
def test_inspect_html_http_error_is_in_json_and_has_the_distinct_exit_code(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    allow: bool,
    expected_exit: int,
) -> None:
    html = "<main>Not Found</main>"
    with _FakeMcpServer() as server:
        server.state.page_diagnostics["status"] = 404
        server.state.html_value = {
            "data": base64.b64encode(html.encode()).decode(),
            "included_bytes": len(html.encode()),
            "total_bytes": len(html.encode()),
            "truncated": False,
        }
        monkeypatch.setenv("PWMCP_MCP_URL", server.url)
        args = ["inspect", "https://example.test", "--format", "html", "--json"]
        if allow:
            args.append("--allow-http-error")
        assert cli_module.main(args) == expected_exit
        captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result["content"] == html
    assert result["http_status"] == 404 and result["http_error"] is True
    assert any("main document HTTP 404" in item for item in result["diagnostics"])
    if not allow:
        assert expected_exit == cli_module.EXIT_HTTP_ERROR == 5
        assert "--allow-http-error" in captured.err


def test_inspect_html_http_error_text_mode_returns_the_body_with_exit_5(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    html = "<main>Not Found</main>"
    with _FakeMcpServer() as server:
        server.state.page_diagnostics["status"] = 404
        server.state.html_value = {
            "data": base64.b64encode(html.encode()).decode(),
            "included_bytes": len(html.encode()),
            "total_bytes": len(html.encode()),
            "truncated": False,
        }
        monkeypatch.setenv("PWMCP_MCP_URL", server.url)
        assert cli_module.main(["inspect", "https://example.test", "--format", "html"]) == 5
        captured = capsys.readouterr()
    assert captured.out == html + "\n"
    assert "main document HTTP 404" in captured.err


def test_inspect_html_truncates_with_a_notice_and_can_write_a_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    html = "<main>sample</main>"
    output_path = tmp_path / "page.html"
    with _FakeMcpServer() as server:
        server.state.html_value = {
            "data": base64.b64encode(html.encode()).decode(),
            "included_bytes": len(html.encode()),
            "total_bytes": len(html.encode()) + 900,
            "truncated": True,
        }
        monkeypatch.setenv("PWMCP_MCP_URL", server.url)
        assert cli_module.main([
            "inspect", "https://example.test", "--format", "html", "--output", str(output_path),
            "--max-html-bytes", "20", "--json",
        ]) == 0
        captured = capsys.readouterr()
    assert output_path.read_text(encoding="utf-8") == html
    assert "HTML result truncated" in captured.err
    assert f"wrote html output to {output_path}" in captured.err
    assert json.loads(captured.out)["output"] == str(output_path)


def test_inspect_html_writes_a_file_without_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    html = "<main>saved</main>"
    output_path = tmp_path / "page.html"
    with _FakeMcpServer() as server:
        server.state.html_value = {
            "data": base64.b64encode(html.encode()).decode(),
            "included_bytes": len(html.encode()),
            "total_bytes": len(html.encode()),
            "truncated": False,
        }
        monkeypatch.setenv("PWMCP_MCP_URL", server.url)
        assert cli_module.main([
            "inspect", "https://example.test", "--format", "html", "--output", str(output_path)
        ]) == 0
        captured = capsys.readouterr()
    assert output_path.read_text(encoding="utf-8") == html
    assert captured.out == ""
    assert f"wrote html output to {output_path}" in captured.err


def test_inspect_html_reports_file_write_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output_path = tmp_path / "directory.html"
    output_path.mkdir()
    html = "<main>cannot save</main>"
    with _FakeMcpServer() as server:
        server.state.html_value = {
            "data": base64.b64encode(html.encode()).decode(),
            "included_bytes": len(html.encode()),
            "total_bytes": len(html.encode()),
            "truncated": False,
        }
        monkeypatch.setenv("PWMCP_MCP_URL", server.url)
        assert cli_module.main([
            "inspect", "https://example.test", "--format", "html", "--output", str(output_path)
        ]) == 1
        captured = capsys.readouterr()
    assert "cannot write html output" in captured.err


def test_inspect_html_retries_one_browser_crash_automatically(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    html = "<html><body>ok</body></html>"
    with _FakeMcpServer() as server:
        server.state.navigate_errors = ["Page.goto: Page crashed"]
        server.state.html_value = {
            "data": base64.b64encode(html.encode()).decode(),
            "included_bytes": len(html.encode()),
            "total_bytes": len(html.encode()),
            "truncated": False,
        }
        monkeypatch.setenv("PWMCP_MCP_URL", server.url)
        assert cli_module.main(["inspect", "https://example.test", "--format", "html"]) == 0
        captured = capsys.readouterr()
    assert captured.out == html + "\n"
    assert "retrying automatically once" in captured.err
    assert len(_call_args(server.state, "browser_navigate")) == 2


def test_inspect_html_retries_zero_disables_the_automatic_retry(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with _FakeMcpServer() as server:
        server.state.navigate_errors = ["Page.goto: Page crashed"]
        monkeypatch.setenv("PWMCP_MCP_URL", server.url)
        assert cli_module.main([
            "inspect", "https://example.test", "--format", "html", "--retries", "0"
        ]) == 1
        error = capsys.readouterr().err
    assert "pwmcp inspect failed" in error
    assert "retrying" not in error
    assert len(_call_args(server.state, "browser_navigate")) == 1


def test_inspect_html_explicit_retries_cover_a_tool_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    html = "<html><body>recovered</body></html>"
    with _FakeMcpServer() as server:
        server.state.navigate_errors = ["temporary navigation failure"]
        server.state.html_value = {
            "data": base64.b64encode(html.encode()).decode(),
            "included_bytes": len(html.encode()),
            "total_bytes": len(html.encode()),
            "truncated": False,
        }
        monkeypatch.setenv("PWMCP_MCP_URL", server.url)
        assert cli_module.main([
            "inspect", "https://example.test", "--format", "html", "--retries", "1"
        ]) == 0
        captured = capsys.readouterr()
    assert captured.out == html + "\n"
    assert "attempt 1/2 failed: browser_navigate: temporary navigation failure; retrying" in captured.err
    assert len(_call_args(server.state, "browser_navigate")) == 2


def test_inspect_html_does_not_retry_a_generic_tool_error_by_default(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with _FakeMcpServer() as server:
        server.state.navigate_errors = ["temporary navigation failure"]
        monkeypatch.setenv("PWMCP_MCP_URL", server.url)
        assert cli_module.main(["inspect", "https://example.test", "--format", "html"]) == 1
        captured = capsys.readouterr()
    assert "pwmcp inspect failed: browser_navigate: temporary navigation failure" in captured.err
    assert len(_call_args(server.state, "browser_navigate")) == 1


@pytest.mark.parametrize(
    ("error", "options", "expected_evaluations"),
    [
        ("Target closed", [], 1),
        ("Target closed", ["--retries", "2"], 1),
        ("Page crashed", ["--retries", "0"], 1),
        ("Page crashed", [], 2),
    ],
)
def test_inspect_html_retry_guard_distinguishes_page_crashes_from_closed_browser_lease(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    error: str,
    options: list[str],
    expected_evaluations: int,
) -> None:
    with _FakeMcpServer() as server:
        server.state.tool_error_sequences["browser_evaluate"] = [error] * 2
        monkeypatch.setenv("PWMCP_MCP_URL", server.url)
        assert cli_module.main([
            "inspect", "https://example.test", "--format", "html", *options
        ]) == 1
        captured = capsys.readouterr()
    assert error in captured.err
    assert len(_call_args(server.state, "browser_evaluate")) == expected_evaluations


@pytest.mark.parametrize(
    "page_diagnostics",
    [
        ["not an object"],
        {"title": None, "text": "body", "status": 200, "final_url": "https://example.test", "frames": []},
        {"title": "title", "text": None, "status": 200, "final_url": "https://example.test", "frames": []},
        {"title": "title", "text": "body", "status": True, "final_url": "https://example.test", "frames": []},
        {"title": "title", "text": "body", "status": 200, "final_url": None, "frames": []},
        {"title": "title", "text": "body", "status": 200, "final_url": "https://example.test", "frames": None},
        {"title": "title", "text": "body", "status": 200, "final_url": "https://example.test", "frames": [3]},
    ],
)
def test_inspect_html_rejects_malformed_page_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    page_diagnostics: object,
) -> None:
    with _FakeMcpServer() as server:
        server.state.page_diagnostics = page_diagnostics
        monkeypatch.setenv("PWMCP_MCP_URL", server.url)
        assert cli_module.main(["inspect", "https://example.test", "--format", "html"]) == 1
        error = capsys.readouterr().err
    assert "page diagnostics returned" in error
    evaluated_functions = [
        call["function"] for call in _call_args(server.state, "browser_evaluate")
    ]
    assert len(evaluated_functions) == 1
    assert "new TextEncoder()" not in evaluated_functions[0]


def test_inspect_html_has_a_guard_for_exhausted_internal_attempts() -> None:
    # The public CLI rejects --retries outside 0..10 before this handler. Keep
    # the handler's no-result guard tested if another caller bypasses that check.
    args = SimpleNamespace(
        max_html_bytes=512 * 1024,
        wait_until="load",
        ignore_https_errors=False,
        user_agent=None,
        storage_state=None,
        retries=-1,
        timeout_ms=30_000,
        url="https://example.test",
        selector=None,
        html_mode="outer",
        wait_for=None,
        output=None,
    )
    runtime = SimpleNamespace(output=None)
    with pytest.raises(cli_module.CliFailure, match="MCP browser returned no HTML result"):
        cli_module._inspect_html(args, runtime)  # type: ignore[arg-type]


def test_inspect_html_reports_a_challenge_without_returning_or_writing_html(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output_path = tmp_path / "challenge.html"
    with _FakeMcpServer() as server:
        server.state.page_diagnostics = {
            "title": "Just a moment...",
            "text": "Performing security verification",
            "status": 403,
            "final_url": "https://example.test/challenge",
            "frames": ["https://challenges.cloudflare.com/widget"],
        }
        monkeypatch.setenv("PWMCP_MCP_URL", server.url)
        assert cli_module.main([
            "inspect", "https://example.test", "--format", "html", "--output", str(output_path), "--json",
            "--allow-http-error",
        ]) == 4
        captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report["blocked"] is True and report["provider"] == "Cloudflare"
    assert "challenge page, not the target" in report["message"]
    assert "not written" in captured.err and not output_path.exists()
    assert len(_call_args(server.state, "browser_navigate")) == 1


def test_inspect_html_reports_blocked_page_in_text_mode(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    with _FakeMcpServer() as server:
        server.state.page_diagnostics = {
            "title": "Just a moment...",
            "text": "Performing security verification",
            "status": 403,
            "final_url": "https://example.test/challenge",
            "frames": [],
        }
        monkeypatch.setenv("PWMCP_MCP_URL", server.url)
        assert cli_module.main(["inspect", "https://example.test", "--format", "html"]) == 4
        captured = capsys.readouterr()
    assert "blocked by bot protection" in captured.out
    assert "Hint: report this to the user as BLOCKED" in captured.err


def test_streamable_http_client_negotiates_version_on_followup_requests_and_delete() -> None:
    with _FakeMcpServer() as server:
        with StreamableHttpClient(server.url) as client:
            client.initialize()
            client.call_tool("browser_navigate", {"url": "https://example.test"})
        headers = server.state.request_headers
        assert "mcp-protocol-version" not in headers[0]
        assert all(header["mcp-protocol-version"] == "2024-11-05" for header in headers[1:])
        assert all(header["mcp-session-id"] == "fixture-session" for header in headers[1:])
        assert server.state.delete_headers[0]["mcp-protocol-version"] == "2024-11-05"


def test_streamable_http_client_supports_stateless_server_sessions() -> None:
    with _FakeMcpServer() as server:
        server.state.session_enabled = False
        with StreamableHttpClient(server.url) as client:
            client.call_tool("browser_navigate", {"url": "https://example.test"})
        assert all("mcp-session-id" not in header for header in server.state.request_headers)
        assert server.state.request_headers[1]["mcp-protocol-version"] == "2024-11-05"
        assert server.state.deleted_sessions == 0


def test_streamable_http_client_accepts_only_the_protocol_version_it_implements() -> None:
    with _FakeMcpServer() as server:
        server.state.protocol_version = "2025-03-26"
        with pytest.raises(McpUnavailable, match="unsupported protocol version"):
            with StreamableHttpClient(server.url):
                pass
        assert server.state.deleted_sessions == 1


def test_streamable_http_client_times_out_without_echoing_endpoint_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def timeout(*_args: object, **_kwargs: object) -> object:
        raise TimeoutError("timed out")

    _patch_urlopen(monkeypatch, timeout)
    with pytest.raises(McpTimeout, match="request timed out"):
        StreamableHttpClient("http://user:secret@127.0.0.1:8931/mcp").initialize()
    _patch_urlopen(
        monkeypatch,
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            mcp_client.urllib.error.URLError(TimeoutError("timed out"))
        ),
    )
    with pytest.raises(McpTimeout, match="request timed out"):
        StreamableHttpClient("http://127.0.0.1:8931/mcp").initialize()


def test_streamable_http_client_caps_the_raw_response_body() -> None:
    with _FakeMcpServer() as server:
        server.state.response_overrides["initialize"] = (
            "application/json",
            b'{"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2024-11-05"}}',
        )
        with pytest.raises(McpError, match="response exceeded the 8-byte limit"):
            StreamableHttpClient(server.url, max_response_bytes=8).initialize()


def test_streamable_http_client_refuses_unavailable_and_malformed_endpoints(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PWMCP_MCP_URL", raising=False)
    assert mcp_client.configured_mcp_url() == "http://pwmcp:8931/mcp"
    monkeypatch.setenv("PWMCP_MCP_URL", "http://configured.test/mcp")
    assert mcp_client.configured_mcp_url() == "http://configured.test/mcp"
    monkeypatch.setenv("PWMCP_MCP_URL", "  ")
    with pytest.raises(McpUnavailable, match="set but empty"):
        mcp_client.configured_mcp_url()
    with pytest.raises(McpUnavailable, match="absolute http"):
        StreamableHttpClient("file:///tmp/mcp")
    with pytest.raises(McpUnavailable, match="absolute http"):
        StreamableHttpClient("http:///mcp")
    with pytest.raises(McpUnavailable, match="malformed"):
        StreamableHttpClient("http://[bad/mcp")
    with pytest.raises(McpUnavailable, match="username cannot contain"):
        StreamableHttpClient("http://foo%3Abar:password@localhost/mcp")
    with pytest.raises(McpUnavailable, match="invalid percent-encoded credentials"):
        StreamableHttpClient("http://user:%FF@localhost/mcp")
    with pytest.raises(McpUnavailable, match="timeout must be positive"):
        StreamableHttpClient("http://localhost/mcp", timeout=0)
    with pytest.raises(McpUnavailable, match="timeout must be positive"):
        StreamableHttpClient("http://localhost/mcp", timeout=float("nan"))
    with pytest.raises(McpUnavailable, match="response byte limit"):
        StreamableHttpClient("http://localhost/mcp", max_response_bytes=0)
    with pytest.raises(McpUnavailable, match="response byte limit"):
        StreamableHttpClient("http://localhost/mcp", max_response_bytes=64 * 1024 * 1024 + 1)


def test_streamable_http_client_closes_session_when_initialization_does_not_finish() -> None:
    with _FakeMcpServer() as server:
        server.state.protocol_version = ""
        with pytest.raises(McpError, match="no protocol version"):
            with StreamableHttpClient(server.url):
                pass
        assert server.state.deleted_sessions == 1
    with _FakeMcpServer() as server:
        server.state.bad_tools_result = {"tools": None}
        with pytest.raises(McpError, match="no tools array"):
            with StreamableHttpClient(server.url):
                pass
        assert server.state.deleted_sessions == 1
    with _FakeMcpServer() as server:
        server.state.rpc_error_methods.add("tools/list")
        with pytest.raises(McpError, match="fixture RPC error"):
            with StreamableHttpClient(server.url):
                pass
        assert server.state.deleted_sessions == 1


def test_streamable_http_client_rejects_invalid_transport_responses() -> None:
    with _FakeMcpServer() as server:
        server.state.empty_body_methods.add("initialize")
        with pytest.raises(McpError, match="empty response"):
            with StreamableHttpClient(server.url):
                pass
    with _FakeMcpServer() as server:
        server.state.response_overrides["initialize"] = ("application/json", b"not json")
        with pytest.raises(McpError, match="invalid JSON"):
            with StreamableHttpClient(server.url):
                pass
    with _FakeMcpServer() as server:
        server.state.response_overrides["initialize"] = ("application/json", b"\xff")
        with pytest.raises(McpError, match="invalid JSON"):
            with StreamableHttpClient(server.url):
                pass
    with _FakeMcpServer() as server:
        server.state.response_overrides["initialize"] = ("application/json", b"[]")
        with pytest.raises(McpError, match="non-object"):
            with StreamableHttpClient(server.url):
                pass
    with _FakeMcpServer() as server:
        server.state.wrong_id_methods.add("initialize")
        with pytest.raises(McpError, match="wrong request id"):
            with StreamableHttpClient(server.url):
                pass
    with _FakeMcpServer() as server:
        server.state.http_error_status = 503
        with pytest.raises(McpUnavailable, match="HTTP 503"):
            with StreamableHttpClient(server.url):
                pass
    with _FakeMcpServer() as server:
        server.state.notification_status = 500
        with pytest.raises(McpError, match="notification returned unexpected HTTP status 500"):
            with StreamableHttpClient(server.url):
                pass
    with _FakeMcpServer() as server:
        server.state.wrong_id_methods.add("tools/call")
        with StreamableHttpClient(server.url) as client:
            with pytest.raises(McpError, match="wrong request id"):
                client.call_tool("browser_navigate", {})


def test_sse_parser_rejects_invalid_and_unmatched_events() -> None:
    with pytest.raises(McpError, match="non-UTF-8"):
        mcp_client._sse_message(b"data: \xff\n\n", 1)
    with pytest.raises(McpError, match="invalid JSON event"):
        mcp_client._sse_message(b"data: {bad}\n\n", 1)
    with pytest.raises(McpError, match="no response"):
        mcp_client._sse_message(b"data: [DONE]\n\ndata: {\"id\":2}\n\n", 1)
    with pytest.raises(McpError, match="no response"):
        mcp_client._sse_message(b"data: []\n\n", 1)
    assert mcp_client._sse_message(
        b"data: [DONE]\n\ndata: {\"id\":1,\"result\":{}}\n\n", 1
    ) == {"id": 1, "result": {}}


def test_streamable_http_client_classifies_http_unavailability_and_tool_errors() -> None:
    with _FakeMcpServer() as server:
        server.state.rpc_error_methods.add("initialize")
        with pytest.raises(McpError, match="fixture RPC error"):
            with StreamableHttpClient(server.url):
                pass
    with _FakeMcpServer() as server:
        server.state.tools = [{"name": "browser_navigate"}]
        with StreamableHttpClient(server.url) as client:
            with pytest.raises(McpUnavailable, match="does not expose"):
                client.call_tool("browser_evaluate", {"function": "() => 1"})
    client = StreamableHttpClient("http://localhost/mcp")
    with pytest.raises(McpError, match="not initialized"):
        client.call_tool("browser_navigate", {})
    with _FakeMcpServer() as server:
        server.state.bad_tools_result = {"tools": [None, {}, {"name": 3}, {"name": "browser_navigate"}]}
        with StreamableHttpClient(server.url) as initialized:
            assert initialized.tools == {"browser_navigate": {"name": "browser_navigate"}}


def test_streamable_http_client_maps_url_and_os_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    client = StreamableHttpClient("http://example.test/mcp")
    _patch_urlopen(
        monkeypatch,
        lambda *_args, **_kwargs: (_ for _ in ()).throw(mcp_client.urllib.error.URLError("offline")),
    )
    with pytest.raises(McpUnavailable, match="MCP endpoint is unreachable"):
        client._send({"method": "initialize"}, expected_id=1)
    _patch_urlopen(
        monkeypatch,
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("socket broke")),
    )
    with pytest.raises(McpUnavailable, match="MCP endpoint request failed"):
        client._send({"method": "initialize"}, expected_id=1)


def test_streamable_http_notifications_and_rpc_shape_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    client = StreamableHttpClient("http://example.test/mcp")
    client._send = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
    with pytest.raises(McpError, match="did not answer ping"):
        client._rpc("ping", {})
    client._send = lambda *_args, **_kwargs: {"id": 1, "result": None}  # type: ignore[method-assign]
    with pytest.raises(McpError, match="no result object"):
        client._rpc("ping", {})
    client._send = lambda *_args, **_kwargs: {"id": 1, "error": "malformed", "result": {}}  # type: ignore[method-assign]
    assert client._rpc("ping", {}) == {}
    with _FakeMcpServer() as server:
        server.state.notification_status = 200
        with StreamableHttpClient(server.url):
            pass


def test_streamable_http_client_close_is_best_effort(monkeypatch: pytest.MonkeyPatch) -> None:
    client = StreamableHttpClient("http://example.test/mcp")
    client.close()  # no session id: no request
    client.session_id = "session"
    with monkeypatch.context() as scoped:
        _patch_urlopen(
            scoped,
            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("close failed")),
        )
        client.close()
    assert client.session_id is None
    with _FakeMcpServer() as server:
        server.state.delete_status = 503
        with StreamableHttpClient(server.url):
            pass
        assert server.state.deleted_sessions == 1


def test_streamable_http_client_tool_error_without_text_gets_a_fallback() -> None:
    with _FakeMcpServer() as server:
        with StreamableHttpClient(server.url) as client:
            result = {"isError": True, "content": [{"type": "image"}]}
            client._rpc = lambda *_args, **_kwargs: result  # type: ignore[method-assign]
            with pytest.raises(McpToolError, match="tool call failed"):
                client.call_tool("browser_navigate", {})
            client._rpc = lambda *_args, **_kwargs: {"isError": True, "content": "not blocks"}  # type: ignore[method-assign]
            with pytest.raises(McpToolError, match="tool call failed"):
                client.call_tool("browser_navigate", {})


def test_html_block_diagnostics_function_reads_only_bounded_page_signals() -> None:
    function = page_diagnostics_function()
    assert "document.title" in function and "slice(0, 300)" in function
    assert "document.body?.innerText" in function and "slice(0, 6001)" in function
    assert "responseStatus" in function and "location.href" in function
    assert "iframe" in function and "slice(0, 100)" in function


@pytest.mark.parametrize(
    "extra,message",
    [
        (["--wait-until", "networkidle"], "--wait-until must be load"),
        (["--ignore-https-errors"], "cannot apply --ignore-https-errors"),
        (["--user-agent", "chrome-windows"], "cannot apply --ignore-https-errors"),
        (["--storage-state", "state.json"], "cannot apply --ignore-https-errors"),
        (["--max-html-bytes", "524289"], "--max-html-bytes must be between"),
    ],
)
def test_inspect_html_refuses_unsupported_or_oversized_options(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    extra: list[str],
    message: str,
) -> None:
    monkeypatch.setenv("PWMCP_MCP_URL", "not-used")
    assert cli_module.main(["inspect", "https://example.test", "--format", "html", *extra]) == 1
    assert message in capsys.readouterr().err


def test_inspect_html_only_options_are_refused_for_text_output(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli_module.main(["inspect", "https://example.test", "--html-mode", "inner"]) == 1
    assert "require --format html" in capsys.readouterr().err


def test_html_capture_script_modes_and_result_validation() -> None:
    inner = html_extraction_function("#content", "inner", 50)
    assert "document.querySelector(\"#content\")" in inner and "element.innerHTML" in inner
    assert "limit = 50" in inner and "btoa(binary)" in inner
    assert "document.documentElement" in html_extraction_function(None, "outer", 50)
    assert "querySelector" in visible_selector_function("main", 1000)
    assert ".some((element)" in visible_selector_function("main", 1000)
    assert "codePointAt(0)" in inner and "prefix = false" in inner
    with pytest.raises(ValueError, match="mode"):
        html_extraction_function(None, "bad", 50)
    with pytest.raises(ValueError, match="positive"):
        html_extraction_function(None, "outer", 0)
    html, total, truncated = decode_html_value(
        {
            "data": base64.b64encode("ok".encode()).decode(),
            "included_bytes": 2,
            "total_bytes": 2,
            "truncated": False,
        },
        max_bytes=2,
    )
    assert (html, total, truncated) == ("ok", 2, False)
    with pytest.raises(McpToolError, match="matched no element"):
        decode_html_value({"missing": True}, max_bytes=2)
    with pytest.raises(McpError, match="non-object"):
        decode_html_value("bad", max_bytes=2)
    with pytest.raises(McpError, match="invalid size metadata"):
        decode_html_value(
            {"data": "", "included_bytes": 0, "total_bytes": True, "truncated": False}, max_bytes=2
        )
    with pytest.raises(McpError, match="invalid base64 or UTF-8"):
        decode_html_value(
            {"data": "?", "included_bytes": 1, "total_bytes": 1, "truncated": False}, max_bytes=2
        )
    with pytest.raises(McpError, match="invalid base64 or UTF-8"):
        decode_html_value(
            {
                "data": base64.b64encode(b"\xff").decode(),
                "included_bytes": 1,
                "total_bytes": 1,
                "truncated": False,
            },
            max_bytes=2,
        )
    with pytest.raises(McpError, match="size limit"):
        decode_html_value(
            {"data": "b2s=", "included_bytes": 2, "total_bytes": 4, "truncated": False}, max_bytes=1
        )
    for invalid_size in (
        {"data": "b2s=", "included_bytes": 1, "total_bytes": 2, "truncated": False},
        {"data": "b2s=", "included_bytes": 2, "total_bytes": 1, "truncated": False},
        {"data": "b2s=", "included_bytes": 2, "total_bytes": 2, "truncated": True},
    ):
        with pytest.raises(McpError, match="size limit"):
            decode_html_value(invalid_size, max_bytes=2)
    for invalid_metadata in (
        {"data": None, "included_bytes": 0, "total_bytes": 0, "truncated": False},
        {"data": "", "included_bytes": True, "total_bytes": 0, "truncated": False},
        {"data": "", "included_bytes": 0, "total_bytes": 0, "truncated": 0},
    ):
        with pytest.raises(McpError, match="invalid size metadata"):
            decode_html_value(invalid_metadata, max_bytes=2)


def _python_html_prefix(source: str, limit: int) -> tuple[str, int, bool]:
    included: list[str] = []
    included_bytes = 0
    total_bytes = 0
    prefix = True
    for character in source:
        width = len(character.encode("utf-8"))
        total_bytes += width
        if prefix and included_bytes + width <= limit:
            included.append(character)
            included_bytes += width
        else:
            prefix = False
    return "".join(included), total_bytes, total_bytes > included_bytes


def test_html_capture_javascript_and_python_byte_prefix_oracle_agree() -> None:
    extraction = html_extraction_function(None, "inner", 7)
    width_formula = (
        "point <= 0x7f ? 1 : point <= 0x7ff ? 2 : point <= 0xffff ? 3 : 4"
    )
    assert f"const width = {width_formula};" in extraction
    assert "included_bytes + width <= limit" in extraction
    assert "truncated: total_bytes > included_bytes" in extraction
    for source, limit in (
        ("Aé!", 2),
        ("Aé中𝄞", 7),
        ("é" * 9, 10),
        ("𝄞" * 80_000, 131_071),
    ):
        expected_prefix, total_bytes, truncated = _python_html_prefix(source, limit)
        assert len(expected_prefix.encode("utf-8")) <= limit
        assert total_bytes == len(source.encode("utf-8"))
        assert truncated == (len(expected_prefix) != len(source))


def test_generated_html_capture_javascript_executes_in_node_when_available() -> None:
    node = require_binary("node", "Node.js executes the generated HTML truncation function")
    for source, limit in (
        ("Aé!", 2),
        ("Aé中𝄞", 7),
        ("é" * 9, 10),
        ("𝄞" * 80_000, 131_071),
    ):
        function = html_extraction_function(None, "inner", limit)
        program = (
            "const fs = require('fs'); "
            "const input = JSON.parse(fs.readFileSync(0, 'utf8')); "
            "global.document = {documentElement: {innerHTML: input.source}}; "
            "global.btoa = (value) => Buffer.from(value, 'binary').toString('base64'); "
            "console.log(JSON.stringify((eval('(' + input.function + ')'))()));"
        )
        payload = json.dumps({"function": function, "source": source}, ensure_ascii=True)
        completed = subprocess.run(
            [node, "-e", program], input=payload, check=True, capture_output=True, text=True
        )
        actual = json.loads(completed.stdout)
        prefix, total, truncated = _python_html_prefix(source, limit)
        assert base64.b64decode(actual["data"]).decode("utf-8") == prefix
        assert actual["included_bytes"] == len(prefix.encode("utf-8"))
        assert actual["total_bytes"] == total
        assert actual["truncated"] is truncated


def test_evaluation_function_supports_function_source_and_element_properties() -> None:
    assert evaluation_function("() => document.title") == "() => document.title"
    assert evaluation_function("async function() { return 3; }") == "async function() { return 3; }"
    assert evaluation_function("function count() { return 3; }") == "function count() { return 3; }"
    assert evaluation_function("async item => item.title") == "async item => item.title"
    assert evaluation_function("[1, 2].map((item) => item * 2)") == (
        "() => (\n[1, 2].map((item) => item * 2)\n)"
    )
    assert evaluation_function("item => item.title") == "item => item.title"
    assert evaluation_function(None, selector="h1", property_name="textContent") == (
        "() => { const elements = Array.from(document.querySelectorAll(\"h1\")); "
        "const values = elements.map((element) => element[\"textContent\"]); "
        "return (values.length ? values[0] : null); }"
    )
    assert evaluation_function("document.title") == "() => (\ndocument.title\n)"
    assert evaluation_function("document.title;") == "() => (\ndocument.title\n)"
    assert evaluation_function("document.title; // trailing comment") == (
        "() => (\ndocument.title\n)"
    )
    assert evaluation_function("1 // trailing comment") == "() => (\n1\n)"
    assert evaluation_function("functionality.value") == "() => (\nfunctionality.value\n)"
    assert evaluation_function("functionCount + 1") == "() => (\nfunctionCount + 1\n)"
    with pytest.raises(ValueError, match="only one"):
        evaluation_function(None, selector="a", attribute="href", property_name="href")
    with pytest.raises(ValueError, match="provide a JavaScript"):
        evaluation_function("   ")
    with pytest.raises(ValueError, match="size limit must be positive"):
        evaluation_function("1", max_bytes=0)


def test_evaluation_scanner_treats_regex_keywords_after_member_access_as_properties() -> None:
    node = require_binary("node", "Node.js executes JavaScript member-access division cases")
    expressions = (
        "o.in / 2;",
        "(o.in / 2);",
        "o?.of / 2;",
        "a.typeof / 2;",
        "x => o.do / 2",
        "x => (o.in / 2)",
    )
    functions = [evaluation_function(expression) for expression in expressions]
    program = (
        "const fs = require('fs'); const input = JSON.parse(fs.readFileSync(0, 'utf8')); "
        "globalThis.o = {in: 6, of: 8, do: 12}; globalThis.a = {typeof: 10}; "
        "const values = input.functions.map((source) => { "
        "const fn = eval('(' + source + ')'); return fn(0); }); "
        "console.log(JSON.stringify(values));"
    )
    completed = subprocess.run(
        [node, "-e", program],
        input=json.dumps({"functions": functions}),
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(completed.stdout) == [3, 3, 4, 5, 6, 3]


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("'x; // y'", "x; // y"),
        ("'a;//'", "a;//"),
        ('"say hi; // bye"', "say hi; // bye"),
        (r"'say \' hi; // bye'", "say ' hi; // bye"),
        ("\"line\\\r\nnext\"", "linenext"),
        ("`template; // text`", "template; // text"),
        (r"`escaped \`; // text`", "escaped `; // text"),
        ("`line\\\r\nnext`", "linenext"),
        ("`value ${'x; // y'}`", "value x; // y"),
        ("`value ${ {item: 'x; // y'} /* block */ // line\n.item}`", "value x; // y"),
        ("`outer ${`inner ${'nested'}`}`", "outer inner nested"),
        ("`value ${a / b}`;", "value 3"),
        ("`value ${(6) / 2}`;", "value 3"),
        ("`value ${[6][0] / 2}`;", "value 3"),
        ("`value ${a // c\n}`;", "value 6"),
        ("(a // c\n);", 6),
        (r"`value ${/a\//.test('a/')}`;", "value true"),
        ("`outer ${`inner ${6 / 2}`}`;", "outer inner 3"),
        (r"/a;\/\/b/.test('a;//b');", True),
        (r"/[;/]+/gi.test(';/');", True),
        (r"/\//.test('/');", True),
        ("1 === 1;", True),
        ("1 == 1;", True),
        ("() => { return /x/.test('x'); };", True),
        ("typeof /x/.source;", "string"),
        ("6 / 2;", 3),
        ("({value: 2}).value / 2;", 1),
        ("({$value_2: 3}).$value_2;", 3),
        ("({café: 4}).café;", 4),
        ("1 /* c */ + 2", 3),
        ("1 + 1; /* c */", 2),
        ("1 /* c */;", 1),
        ("1;;", 1),
        ("// leading comment\n1 + 1", 2),
        ("1 + 1 // trailing comment", 2),
        ("() => 3 // trailing function comment", 3),
        ("/* leading comment */ function () { return 5; }", 5),
    ],
)
def test_evaluation_function_preserves_literals_and_strips_only_trailing_syntax(
    expression: str,
    expected: object,
) -> None:
    node = require_binary("node", "Node.js executes generated JavaScript evaluation functions")
    function = evaluation_function(expression)
    program = (
        "const fs = require('fs'); const input = JSON.parse(fs.readFileSync(0, 'utf8')); "
        "const a = 6, b = 2; "
        "const fn = eval('(' + input.function + ')'); console.log(JSON.stringify(fn()));"
    )
    completed = subprocess.run(
        [node, "-e", program],
        input=json.dumps({"function": function}),
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(completed.stdout) == expected


def test_passthrough_call_wrapper_keeps_closers_after_a_line_comment() -> None:
    node = require_binary("node", "Node.js executes the bounded eval wrapper")
    function = mcp_client._bounded_evaluation_function("() => 3 // trailing function comment", 16)
    program = (
        "const fs = require('fs'); const input = JSON.parse(fs.readFileSync(0, 'utf8')); "
        "(async () => { const fn = eval('(' + input.function + ')'); "
        "console.log(JSON.stringify(await fn())); })();"
    )
    completed = subprocess.run(
        [node, "-e", program],
        input=json.dumps({"function": function}),
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(completed.stdout)["value"] == 3


@pytest.mark.parametrize(
    ("expression", "arguments", "result_kind", "expected"),
    [
        ("(1) + '(x) => y'", [], "value", "1(x) => y"),
        ("(document.title) || (() => 2)", [], "returned-function", 2),
        ("x => x, 4", [], "value", 4),
        ("async x => x", [7], "value", 7),
        ("async (a, b) => a + b", [3, 4], "value", 7),
        ("x => x / 2", [8], "value", 4),
        ("x => { return x + 1; }", [2], "value", 3),
        ("x => /a,b/.test(x)", ["a,b"], "value", True),
        ("x => 'a,b'", [], "value", "a,b"),
        ("(a // c\n) => 6", [], "value", 6),
        ("function () { return 5; }", [], "value", 5),
        ("function(){return 1}()", [], "value", 1),
        ("function(){} + 1", [], "value", "function(){}1"),
        ("function* () { yield 5; }", [], "generator", {"value": 5, "done": False}),
        ("functionality()", [], "value", 9),
    ],
)
def test_only_whole_function_expressions_pass_through(
    expression: str,
    arguments: list[object],
    result_kind: str,
    expected: object,
) -> None:
    node = require_binary("node", "Node.js checks generated eval expression semantics")
    function = evaluation_function(expression)
    program = (
        "const fs = require('fs'); const input = JSON.parse(fs.readFileSync(0, 'utf8')); "
        "globalThis.document = {title: ''}; globalThis.functionality = () => 9; "
        "(async () => { const fn = eval('(' + input.function + ')'); "
        "let value = await fn(...input.arguments); "
        "if (input.result_kind === 'returned-function' && typeof value === 'function') value = await value(); "
        "if (input.result_kind === 'generator') value = value.next(); "
        "console.log(JSON.stringify(value)); })();"
    )
    completed = subprocess.run(
        [node, "-e", program],
        input=json.dumps({
            "function": function,
            "arguments": arguments,
            "result_kind": result_kind,
        }),
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(completed.stdout) == expected


def test_javascript_scanner_differential_fuzz_matches_node_for_fixed_seeds() -> None:
    node = require_binary("node", "Node.js compares original and transformed JavaScript expressions")
    seeds = (0xC0FFEE, 0x5EED, 0xA11CE)
    expressions = [
        "`value ${6 / 2}`;",
        "`value ${a // c\n}`;",
        "(a // c\n);",
        "o.in / 2;",
        "(o.in / 2);",
        "o?.of / 2;",
        "a.typeof / 2;",
        "x => o.do / 2",
        "x => (o.in / 2)",
        "(a // c\n) => 6",
        "function(){return 1}()",
        "function(){} + 1",
    ]

    def make_expression(rng: random.Random, depth: int = 0) -> str:
        if depth >= 3 or rng.randrange(4) == 0:
            leaf = rng.randrange(4)
            if leaf == 0:
                return str(rng.randrange(1, 20))
            if leaf == 1:
                return json.dumps(rng.choice(["x;//y", "quote'and\"slash/", "plain"]))
            if leaf == 2:
                left, right = rng.randrange(1, 10), rng.randrange(1, 10)
                return f"`value ${{{left} / {right}}}`"
            left, middle, right = rng.randrange(1, 10), rng.randrange(1, 10), rng.randrange(1, 10)
            return f"`value ${{({left} + {middle}) / {right}}}`"

        left = make_expression(rng, depth + 1)
        right = make_expression(rng, depth + 1)
        operator = rng.choice(("+", "*", "===", "/"))
        return f"({left} /* fuzz */ {operator} {right})"

    for seed in seeds:
        rng = random.Random(seed)
        for _ in range(24):
            expression = make_expression(rng)
            expression = "/* fixed-seed fuzz */ " + expression
            suffix = rng.choice(("", ";", ";;;", " // trailing comment\n", "; /* trailing comment */"))
            expressions.append(expression + suffix)

    functions = [evaluation_function(expression) for expression in expressions]
    program = (
        "const fs = require('fs'); const input = JSON.parse(fs.readFileSync(0, 'utf8')); "
        "globalThis.a = {typeof: 10}; globalThis.b = 2; "
        "globalThis.o = {in: 6, of: 8, do: 12}; "
        "const pairs = input.expressions.map((source, index) => { "
        "const original = source.startsWith('function') ? eval('(' + source + ')') : eval(source); "
        "const fn = eval('(' + input.functions[index] + ')'); "
        "const expected = typeof original === 'function' ? original() : original; "
        "return [expected, fn()]; }); console.log(JSON.stringify(pairs));"
    )
    completed = subprocess.run(
        [node, "-e", program],
        input=json.dumps({"expressions": expressions, "functions": functions}),
        check=True,
        capture_output=True,
        text=True,
    )
    pairs = json.loads(completed.stdout)
    assert len(pairs) == len(expressions)
    assert all(original == transformed for original, transformed in pairs)


def test_javascript_scanner_preserves_uncertain_or_incomplete_expressions() -> None:
    for source in (
        "{value: 1} / 2;",
        "/[unterminated/;",
        "'unterminated",
        "'trailing\\",
        "'line\nbreak'",
        "1 /* unterminated",
        "1 /**",
        "/trailing\\",
        "/line\nbreak/;",
        "`unterminated",
        "`trailing\\",
        "`value ${1 /* unterminated",
        "`value ${'unterminated",
        "`value ${answer",
    ):
        normalized, first_token = mcp_client._strip_trailing_javascript(source)
        assert normalized == source
        assert first_token == 0

    for source in (";", "// only comment", "/* only comment */"):
        with pytest.raises(ValueError, match="no JavaScript expression"):
            mcp_client._strip_trailing_javascript(source)

    assert mcp_client._strip_trailing_javascript("value /= 2;") == ("value /= 2", 0)
    assert mcp_client._strip_trailing_javascript("a /* c */ + b") == ("a /* c */ + b", 0)
    ambiguous_template = "`value ${{value: 1} / 2}`;"
    assert mcp_client._strip_trailing_javascript(ambiguous_template) == (ambiguous_template, 0)


def test_javascript_balanced_group_scanner_handles_tokens_and_uncertainty() -> None:
    assert mcp_client._identifier_end("", 0) is None
    assert mcp_client._identifier_end("9value", 0) is None
    assert mcp_client._identifier_end("name_2", 0) == len("name_2")
    assert mcp_client._skip_js_trivia(" \n/* block */ // line\nvalue", 0) == len(" \n/* block */ // line\n")
    assert mcp_client._skip_js_trivia("/* unterminated", 0) is None

    for source in (
        "(a / b)",
        "(2 / b)",
        "((2) / b)",
        "([2][0] / b)",
        "(value /= 2)",
        "({value: /[()]/, nested: `v${2 / 1}`})",
        "(a /* block */ + // line\n b)",
    ):
        assert mcp_client._skip_js_balanced_group(source, 0) == len(source)

    for source in (
        "",
        "value",
        "(]",
        "('unterminated)",
        "(/* unterminated",
        "(/unterminated)",
        "(`unterminated)",
        "({value: 1} / 2)",
        "(value",
    ):
        assert mcp_client._skip_js_balanced_group(source, 0) is None

    assert mcp_client._arrow_body_has_top_level_comma("", 0) is False
    assert mcp_client._arrow_body_has_top_level_comma(" /* trailing trivia */", 0) is False
    assert mcp_client._arrow_body_has_top_level_comma("a, b", 0) is True
    assert mcp_client._arrow_body_has_top_level_comma("a; b", 0) is True
    assert mcp_client._arrow_body_has_top_level_comma("(a, b)", 0) is False
    assert mcp_client._arrow_body_has_top_level_comma("'a,b'", 0) is False
    assert mcp_client._arrow_body_has_top_level_comma("`value ${1, 2}`", 0) is False
    assert mcp_client._arrow_body_has_top_level_comma("/[a,b]/.test(value)", 0) is False
    assert mcp_client._arrow_body_has_top_level_comma("/unterminated", 0) is True
    assert mcp_client._arrow_body_has_top_level_comma("a / b", 0) is False
    assert mcp_client._arrow_body_has_top_level_comma("{value: 1} / 2", 0) is True
    assert mcp_client._arrow_body_has_top_level_comma("123 + 456", 0) is False
    assert mcp_client._arrow_body_has_top_level_comma("a === b", 0) is False
    assert mcp_client._arrow_body_has_top_level_comma("({value: 1} / 2)", 0) is True
    assert mcp_client._arrow_body_has_top_level_comma("'unterminated", 0) is True
    assert mcp_client._arrow_body_has_top_level_comma("/* unterminated", 0) is True
    assert mcp_client._arrow_body_has_top_level_comma("`unterminated", 0) is True

    assert not mcp_client._is_whole_arrow_function("")
    assert not mcp_client._is_whole_arrow_function("/* unterminated")
    assert not mcp_client._is_whole_arrow_function("async")
    assert not mcp_client._is_whole_arrow_function("(x")
    assert not mcp_client._is_whole_arrow_function("x =>")
    assert mcp_client._is_whole_arrow_function("async (a, b) => a + b")
    assert mcp_client._is_whole_arrow_function("async => async")
    assert mcp_client._is_whole_arrow_function("x => {}")
    assert not mcp_client._is_whole_arrow_function("(1) + '=>'")
    assert not mcp_client._is_whole_arrow_function("x => x, 2")
    assert not mcp_client._is_whole_arrow_function("x => {} + 1")
    assert not mcp_client._is_whole_arrow_function("x => {")
    assert not mcp_client._is_whole_function_expression("")
    assert not mcp_client._is_whole_function_expression("async")
    assert not mcp_client._is_whole_function_expression("function/* unterminated")
    assert not mcp_client._is_whole_function_expression("function*/* unterminated")
    assert not mcp_client._is_whole_function_expression("function 1() {}")
    assert not mcp_client._is_whole_function_expression("function named/* unterminated")
    assert not mcp_client._is_whole_function_expression("function")
    assert not mcp_client._is_whole_function_expression("function(")
    assert not mcp_client._is_whole_function_expression("function()")
    assert not mcp_client._is_whole_function_expression("function() 1")
    assert not mcp_client._is_whole_function_expression("function() {")
    assert mcp_client._is_whole_function_expression("async function* named() { return /x/; }")
    assert not mcp_client._is_whole_function_expression("functionality()")


def test_javascript_scanner_caps_nested_template_recursion_conservatively() -> None:
    source = "`value ${" * 65 + "1" + "}`" * 65 + ";"
    normalized, first_token = mcp_client._strip_trailing_javascript(source)
    assert normalized == source
    assert first_token == 0


def test_evaluation_function_rejects_a_lone_comment_with_a_clear_error() -> None:
    with pytest.raises(ValueError, match="no JavaScript expression"):
        evaluation_function("// only comment")


def test_javascript_trailing_scanner_reads_a_megabyte_in_linear_operations() -> None:
    class CountingString(str):
        def __new__(cls, value: str) -> "CountingString":
            instance = super().__new__(cls, value)
            instance.reads = 0
            return instance

        def __getitem__(self, key: object) -> object:
            self.reads += 1
            return super().__getitem__(key)  # type: ignore[arg-type]

    source = CountingString(";//" * 350_000)
    with pytest.raises(ValueError, match="no JavaScript expression"):
        mcp_client._strip_trailing_javascript(source)
    assert len(source) >= 1_000_000
    assert source.reads <= len(source) * 6


@pytest.mark.parametrize(
    "message",
    [
        "Page.goto: Page crashed",
        "Target closed",
        "Execution context was destroyed",
        "Browser has been closed",
        "Target page, context or browser has been closed",
    ],
)
def test_browser_crash_classifier_matches_only_page_lifecycle_errors(message: str) -> None:
    assert is_browser_crash(RuntimeError(message))
    assert not is_browser_crash(RuntimeError("connection refused"))


def test_decode_tool_value_handles_json_plain_text_multiple_blocks_and_empty_content() -> None:
    assert decode_tool_value({"content": [{"text": "### Result\n42\n### More"}]}) == 42
    assert decode_tool_value({"content": [{"text": "page text"}]}) == "page text"
    assert decode_tool_value({"content": [{"text": "1"}, {"text": "2"}]}) == [1, 2]
    with pytest.raises(McpError, match="no content array"):
        decode_tool_value({})
    with pytest.raises(McpError, match="no text value"):
        decode_tool_value({"content": [{"type": "image"}]})
