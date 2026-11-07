"""Small synchronous Streamable HTTP client for PWMCP's existing MCP tools.

The consumer wheel deliberately does not depend on the ``mcp`` SDK. It needs only
the JSON-RPC subset used by ``pwmcp eval`` and ``inspect --format html``.
"""

from __future__ import annotations

import base64
import json
import math
import os
import urllib.error
import urllib.request
from json import JSONDecodeError
from urllib.parse import urlsplit

DEFAULT_MCP_URL = "http://pwmcp:8931/mcp"
_PROTOCOL_VERSION = "2024-11-05"


class McpError(RuntimeError):
    """An MCP request failed or the server returned an invalid response."""


class McpUnavailable(McpError):
    """The configured MCP endpoint did not accept a request."""


class McpToolError(McpError):
    """The server reported an error while running an MCP tool."""


def is_browser_crash(error: BaseException) -> bool:
    """Recognise Playwright browser/page lifecycle failures, not network failures."""

    message = str(error).lower()
    return any(
        signature in message
        for signature in (
            "page crashed",
            "target closed",
            "execution context was destroyed",
            "browser has been closed",
            "browser closed",
            "target page, context or browser has been closed",
        )
    )


def configured_mcp_url() -> str:
    """Read the configured endpoint without masking an explicitly empty value."""

    value = os.environ.get("PWMCP_MCP_URL")
    if value is None:
        return DEFAULT_MCP_URL
    if not value.strip():
        raise McpUnavailable("PWMCP_MCP_URL is set but empty")
    return value


def page_diagnostics_function() -> str:
    """Read the bounded page signals needed to recognize a bot-protection interstitial."""

    return (
        "() => { const navigation = performance.getEntriesByType('navigation')[0]; "
        "const status = Number(navigation?.responseStatus); "
        "return {title: (document.title || '').slice(0, 300), "
        "text: (document.body?.innerText || '').slice(0, 6001), "
        "status: Number.isInteger(status) && status > 0 ? status : null, "
        "final_url: location.href, "
        "frames: Array.from(document.querySelectorAll('iframe')).slice(0, 100).map((frame) => frame.src)}; }"
    )


def _sse_message(body: bytes, expected_id: int) -> dict[str, object]:
    """Read the JSON-RPC response event for one POST from an SSE response body."""

    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise McpError("MCP server returned non-UTF-8 event data") from exc
    data_lines: list[str] = []
    mismatched_response: dict[str, object] | None = None
    for line in (*text.splitlines(), ""):
        if line == "":
            if data_lines:
                data = "\n".join(data_lines)
                data_lines.clear()
                if data == "[DONE]":
                    continue
                try:
                    message = json.loads(data)
                except JSONDecodeError as exc:
                    raise McpError("MCP server returned invalid JSON event data") from exc
                if isinstance(message, dict):
                    if message.get("id") == expected_id:
                        return message
                    if "id" in message and ("result" in message or "error" in message):
                        mismatched_response = message
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    if mismatched_response is not None:
        return mismatched_response
    raise McpError(f"MCP event stream had no response for request {expected_id}")


class StreamableHttpClient:
    """The initialize, tools/list, tools/call, and session-close MCP operations."""

    def __init__(self, endpoint: str | None = None, *, timeout: float = 30.0) -> None:
        self.endpoint = endpoint if endpoint is not None else configured_mcp_url()
        try:
            parsed = urlsplit(self.endpoint)
        except ValueError as exc:
            raise McpUnavailable(f"MCP endpoint URL is malformed ({exc})") from exc
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise McpUnavailable("MCP endpoint must be an absolute http:// or https:// URL")
        if not math.isfinite(timeout) or timeout <= 0:
            raise McpUnavailable("MCP timeout must be positive")
        self.timeout = timeout
        self.protocol_version = _PROTOCOL_VERSION
        self._protocol_negotiated = False
        self.session_id: str | None = None
        self.tools: dict[str, dict[str, object]] = {}
        self._next_id = 0
        self._initialized = False

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        if self.session_id is not None:
            headers["Mcp-Session-Id"] = self.session_id
        if self._protocol_negotiated:
            headers["MCP-Protocol-Version"] = self.protocol_version
        return headers

    def _send(self, message: dict[str, object], *, expected_id: int | None) -> dict[str, object] | None:
        data = json.dumps(message, separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(self.endpoint, data=data, headers=self._headers(), method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                status = response.status
                headers = response.headers
                body = response.read()
        except urllib.error.HTTPError as exc:
            if expected_id is None:
                # urllib turns non-2xx notification replies into HTTPError. The
                # server still answered, so let the notification status check
                # classify it as an MCP protocol error rather than unreachable.
                status = exc.code
                headers = exc.headers
                body = b""
            else:
                raise McpUnavailable(f"MCP endpoint rejected the request (HTTP {exc.code})") from exc
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", "connection failed")
            raise McpUnavailable(f"MCP endpoint is unreachable ({reason})") from exc
        except OSError as exc:
            raise McpUnavailable(f"MCP endpoint request failed ({exc})") from exc

        session_id = headers.get("Mcp-Session-Id")
        if session_id:
            self.session_id = str(session_id)
        if expected_id is None:
            if status not in {200, 202, 204}:
                raise McpError(f"MCP notification returned unexpected HTTP status {status}")
            return None
        if not body:
            raise McpError("MCP server returned an empty response")

        content_type = str(headers.get("Content-Type", "")).split(";", 1)[0].strip().lower()
        if content_type == "text/event-stream":
            response_message = _sse_message(body, expected_id)
        else:
            try:
                decoded = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, JSONDecodeError) as exc:
                raise McpError("MCP server returned invalid JSON") from exc
            if not isinstance(decoded, dict):
                raise McpError("MCP server returned a non-object JSON-RPC response")
            response_message = decoded
        if response_message.get("id") != expected_id:
            raise McpError(f"MCP server returned a response for the wrong request id (expected {expected_id})")
        return response_message

    def _rpc(self, method: str, params: dict[str, object]) -> dict[str, object]:
        self._next_id += 1
        request_id = self._next_id
        message = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }
        response = self._send(message, expected_id=request_id)
        if response is None:
            raise McpError(f"MCP server did not answer {method}")
        error = response.get("error")
        if isinstance(error, dict):
            detail = error.get("message") or "unknown JSON-RPC error"
            raise McpError(f"MCP {method} failed: {detail}")
        result = response.get("result")
        if not isinstance(result, dict):
            raise McpError(f"MCP {method} returned no result object")
        return result

    def _notify(self, method: str, params: dict[str, object]) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params}, expected_id=None)

    def initialize(self) -> None:
        if self._initialized:
            return
        try:
            result = self._rpc(
                "initialize",
                {
                    "protocolVersion": _PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "pwmcp-client", "version": "1"},
                },
            )
            version = result.get("protocolVersion")
            if not isinstance(version, str) or not version:
                raise McpError("MCP initialize response has no protocol version")
            if version != _PROTOCOL_VERSION:
                raise McpUnavailable(
                    f"MCP server selected unsupported protocol version {version!r}; "
                    f"client supports {_PROTOCOL_VERSION}"
                )
            self.protocol_version = version
            self._protocol_negotiated = True
            self._notify("notifications/initialized", {})
            tool_result = self._rpc("tools/list", {})
            tools = tool_result.get("tools")
            if not isinstance(tools, list):
                raise McpError("MCP tools/list response has no tools array")
            self.tools = {
                str(tool["name"]): tool
                for tool in tools
                if isinstance(tool, dict) and isinstance(tool.get("name"), str)
            }
            self._initialized = True
        except Exception:
            # `__enter__` raising does not trigger `__exit__`; close a session
            # already allocated by initialize instead of leaking it.
            self.close()
            raise

    def __enter__(self) -> "StreamableHttpClient":
        self.initialize()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        if self.session_id is None:
            return
        request = urllib.request.Request(
            self.endpoint,
            headers={
                "Mcp-Session-Id": self.session_id,
                **({"MCP-Protocol-Version": self.protocol_version} if self._protocol_negotiated else {}),
            },
            method="DELETE",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout):
                pass
        except (urllib.error.HTTPError, urllib.error.URLError, OSError):
            # Session deletion is best-effort; the server also expires abandoned sessions.
            pass
        finally:
            self.session_id = None

    def call_tool(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
        if not self._initialized:
            raise McpError("MCP client is not initialized")
        if name not in self.tools:
            raise McpUnavailable(f"MCP server does not expose required tool {name!r}")
        result = self._rpc("tools/call", {"name": name, "arguments": arguments})
        if result.get("isError") is True:
            content = result.get("content")
            texts = [str(block["text"]) for block in content if isinstance(block, dict) and "text" in block] \
                if isinstance(content, list) else []
            detail = "; ".join(texts) if texts else "tool call failed"
            raise McpToolError(f"{name}: {detail}")
        return result


def decode_tool_value(result: dict[str, object]) -> object:
    """Decode text returned by ``browser_evaluate`` into a JSON-compatible value."""

    content = result.get("content")
    if not isinstance(content, list):
        raise McpError("MCP tool result has no content array")
    values: list[object] = []
    decoder = json.JSONDecoder()
    for block in content:
        if not isinstance(block, dict) or not isinstance(block.get("text"), str):
            continue
        text = block["text"]
        payload = text.split("### Result", 1)[-1]
        payload = payload.split("\n### ", 1)[0].strip()
        try:
            value, _end = decoder.raw_decode(payload)
        except JSONDecodeError:
            value = payload
        values.append(value)
    if not values:
        raise McpError("MCP tool result contains no text value")
    return values[0] if len(values) == 1 else values


def evaluation_function(
    expression: str | None,
    *,
    selector: str | None = None,
    attribute: str | None = None,
    property_name: str | None = None,
    all_matches: bool = False,
) -> str:
    """Build the ``browser_evaluate`` function for caller code or selector-based DOM reads."""

    if selector is not None:
        if attribute is None and property_name is None:
            raise ValueError("--selector requires --attr NAME or --property NAME")
        if attribute is not None and property_name is not None:
            raise ValueError("choose only one of --attr and --property")
        if expression is not None:
            raise ValueError("do not combine an expression with --selector")
        if attribute is not None:
            read = f"element.getAttribute({json.dumps(attribute)})"
        else:
            read = f"element[{json.dumps(property_name)}]"
        result = "values" if all_matches else "(values.length ? values[0] : null)"
        return (
            "() => { const elements = Array.from(document.querySelectorAll("
            f"{json.dumps(selector)})); const values = elements.map((element) => {read}); "
            f"return {result}; }}"
        )
    if attribute is not None or property_name is not None:
        raise ValueError("--attr and --property require --selector")
    if all_matches:
        raise ValueError("--all requires --selector with --attr or --property")
    if expression is None or not expression.strip():
        raise ValueError("provide a JavaScript expression or function")
    source = expression.strip()
    if source.startswith(("function", "async ")):
        return source
    head, separator, _body = source.partition("=>")
    stripped_head = head.strip()
    if separator and (stripped_head.isidentifier() or (stripped_head.startswith("(") and stripped_head.endswith(")"))):
        return source
    return f"() => ({source})"


def visible_selector_function(selector: str, timeout_ms: int) -> str:
    """Wait in the page until a CSS selector matches a visible element."""

    encoded = json.dumps(selector)
    return (
        "async () => { const selector = " + encoded + "; const timeout = " + str(timeout_ms) + "; "
        "const visible = () => Array.from(document.querySelectorAll(selector)).some((element) => { "
        "const style = getComputedStyle(element); const box = element.getBoundingClientRect(); "
        "return style.display !== 'none' && style.visibility !== 'hidden' && box.width > 0 && box.height > 0; }); "
        "if (visible()) return true; return await new Promise((resolve, reject) => { let timer; "
        "const observer = new MutationObserver(() => { if (visible()) { observer.disconnect(); clearTimeout(timer); resolve(true); } }); "
        "observer.observe(document.documentElement || document, {subtree: true, childList: true, attributes: true}); "
        "timer = setTimeout(() => { observer.disconnect(); reject(new Error(`selector not visible: ${selector}`)); }, timeout); }); }"
    )


def html_extraction_function(selector: str | None, mode: str, max_bytes: int) -> str:
    """Build a page function that bounds and base64-encodes HTML before the MCP reply."""

    if mode not in {"outer", "inner"}:
        raise ValueError("HTML mode must be outer or inner")
    if max_bytes <= 0:
        raise ValueError("HTML size limit must be positive")
    element = "document.documentElement" if selector is None else f"document.querySelector({json.dumps(selector)})"
    html_property = "outerHTML" if mode == "outer" else "innerHTML"
    return (
        "() => { const element = " + element + "; if (!element) return {missing: true}; "
        f"const source = element.{html_property}; const limit = {max_bytes}; "
        "let included = ''; let included_bytes = 0; let total_bytes = 0; let prefix = true; "
        "for (const character of source) { const point = character.codePointAt(0); "
        "const width = point <= 0x7f ? 1 : point <= 0x7ff ? 2 : point <= 0xffff ? 3 : 4; "
        "total_bytes += width; if (prefix && included_bytes + width <= limit) { included += character; "
        "included_bytes += width; } else { prefix = false; } } "
        "const bytes = new TextEncoder().encode(included); let binary = ''; "
        "for (let offset = 0; offset < bytes.length; offset += 32768) { "
        "binary += String.fromCharCode(...bytes.subarray(offset, Math.min(offset + 32768, bytes.length))); } "
        "return {data: btoa(binary), included_bytes, total_bytes, truncated: total_bytes > included_bytes}; }"
    )


def decode_html_value(value: object, *, max_bytes: int) -> tuple[str, int, bool]:
    """Validate and decode the bounded result returned by ``html_extraction_function``."""

    if not isinstance(value, dict):
        raise McpError("MCP HTML extraction returned a non-object result")
    if value.get("missing") is True:
        raise McpToolError("the HTML selector matched no element")
    data = value.get("data")
    total = value.get("total_bytes")
    included = value.get("included_bytes")
    truncated = value.get("truncated")
    if (
        not isinstance(data, str)
        or not isinstance(total, int)
        or isinstance(total, bool)
        or not isinstance(included, int)
        or isinstance(included, bool)
        or not isinstance(truncated, bool)
    ):
        raise McpError("MCP HTML extraction returned invalid size metadata")
    try:
        raw = base64.b64decode(data, validate=True)
        html = raw.decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise McpError("MCP HTML extraction returned invalid base64 or UTF-8") from exc
    if (
        len(raw) > max_bytes
        or included != len(raw)
        or total < len(raw)
        or truncated != (total > len(raw))
    ):
        raise McpError("MCP HTML extraction exceeded or contradicted its size limit")
    return html, total, truncated

