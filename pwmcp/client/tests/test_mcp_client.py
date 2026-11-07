"""PWMCP's standard-library MCP client and CLI browser-evaluation features."""

from __future__ import annotations

import base64
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from pwmcp_client import cli as cli_module
from pwmcp_client import mcp_client
from pwmcp_client.mcp_client import (
    McpUnavailable,
    McpError,
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


class _McpState:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self.navigate_errors: list[str] = []
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
                    elif name == "browser_navigate":
                        result = {"content": [{"type": "text", "text": "navigated"}]}
                    else:
                        function = payload["params"]["arguments"]["function"]
                        if "new TextEncoder()" in function:
                            value = state.html_value
                        elif "performance.getEntriesByType('navigation')" in function:
                            value = state.page_diagnostics
                        elif function.startswith("async "):
                            value = True
                        else:
                            value = state.eval_results.pop(0) if state.eval_results else state.eval_value
                        result = {"content": [{"type": "text", "text": "### Result\n" + json.dumps(value)}]}
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
                body = json.dumps(response_message).encode()
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
                            body = json.dumps(response_message).encode()
                        self.wfile.write(b"event: message\ndata: " + body + b"\n\n")
                    else:
                        if method in state.wrong_id_methods:
                            response_message["id"] = -1
                            body = json.dumps(response_message).encode()
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
    assert _call_args(server.state, "browser_evaluate") == [{"function": "() => (document.title)"}]
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
    assert output == ["a", "b"]
    function = _call_args(server.state, "browser_evaluate")[0]["function"]
    assert "document.querySelectorAll(\".card\")" in function
    assert "element.getAttribute(\"data-id\")" in function
    assert "return values" in function


def test_eval_mcp_error_keeps_browser_crash_hint(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with _FakeMcpServer() as server:
        server.state.navigate_errors = ["Page.goto: Page crashed"]
        monkeypatch.setenv("PWMCP_MCP_URL", server.url)
        assert cli_module.main(["eval", "https://example.test", "document.title"]) == 1
    error = capsys.readouterr().err
    assert "PWMCP is reachable, but the page or browser crashed" in error
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
        "format": "html",
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
            "inspect", "https://example.test", "--format", "html", "--output", str(output_path), "--json"
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
    with pytest.raises(McpUnavailable, match="timeout must be positive"):
        StreamableHttpClient("http://localhost/mcp", timeout=0)
    with pytest.raises(McpUnavailable, match="timeout must be positive"):
        StreamableHttpClient("http://localhost/mcp", timeout=float("nan"))


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
    monkeypatch.setattr(
        mcp_client.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(mcp_client.urllib.error.URLError("offline")),
    )
    with pytest.raises(McpUnavailable, match=r"MCP endpoint is unreachable \(offline\)"):
        client._send({"method": "initialize"}, expected_id=1)
    monkeypatch.setattr(
        mcp_client.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("socket broke")),
    )
    with pytest.raises(McpUnavailable, match=r"request failed \(socket broke\)"):
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
    monkeypatch.setattr(
        mcp_client.urllib.request,
        "urlopen",
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


def test_inspect_html_refuses_html_only_options_for_text_output(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli_module.main(["inspect", "https://example.test", "--selector", "main"]) == 1
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


def test_evaluation_function_supports_function_source_and_element_properties() -> None:
    assert evaluation_function("() => document.title") == "() => document.title"
    assert evaluation_function("async function() { return 3; }") == "async function() { return 3; }"
    assert evaluation_function("async item => item.title") == "async item => item.title"
    assert evaluation_function("[1, 2].map((item) => item * 2)") == "() => ([1, 2].map((item) => item * 2))"
    assert evaluation_function("item => item.title") == "item => item.title"
    assert evaluation_function(None, selector="h1", property_name="textContent") == (
        "() => { const elements = Array.from(document.querySelectorAll(\"h1\")); "
        "const values = elements.map((element) => element[\"textContent\"]); "
        "return (values.length ? values[0] : null); }"
    )
    assert evaluation_function("document.title") == "() => (document.title)"
    with pytest.raises(ValueError, match="only one"):
        evaluation_function(None, selector="a", attribute="href", property_name="href")
    with pytest.raises(ValueError, match="provide a JavaScript"):
        evaluation_function("   ")


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

