"""Small synchronous Streamable HTTP client for PWMCP's existing MCP tools.

The consumer wheel deliberately does not depend on the ``mcp`` SDK. It needs only
the JSON-RPC subset used by ``pwmcp eval`` and ``inspect --format html``.
"""

from __future__ import annotations

import base64
import http.client
import json
import math
import os
import re
import urllib.error
import urllib.request
from json import JSONDecodeError
from urllib.parse import unquote, urlsplit, urlunsplit

DEFAULT_MCP_URL = "http://pwmcp:8931/mcp"
_PROTOCOL_VERSION = "2024-11-05"
_DEFAULT_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_MAX_RESPONSE_BYTES = 64 * 1024 * 1024
_EVAL_RESULT_MARKER = "__pwmcp_eval_result__"
_REGEX_PREFIX_WORDS = frozenset(
    {
        "await",
        "case",
        "delete",
        "do",
        "else",
        "in",
        "instanceof",
        "new",
        "of",
        "return",
        "throw",
        "typeof",
        "void",
        "yield",
    }
)
_THREE_CHARACTER_JS_OPERATORS = frozenset({"===", "!==", ">>>", "**=", "&&=", "||=", "??="})
_TWO_CHARACTER_JS_OPERATORS = frozenset(
    {
        "++",
        "--",
        "=>",
        "?.",
        "&&",
        "||",
        "??",
        "==",
        "!=",
        "<=",
        ">=",
        "+=",
        "-=",
        "*=",
        "/=",
        "%=",
        "**",
        "<<",
        ">>",
        "&=",
        "|=",
        "^=",
        "??=",
    }
)


class McpError(RuntimeError):
    """An MCP request failed or the server returned an invalid response."""


class McpUnavailable(McpError):
    """The configured MCP endpoint did not accept a request."""


class McpTimeout(McpUnavailable):
    """An MCP request exceeded its configured timeout."""


class McpToolError(McpError):
    """The server reported an error while running an MCP tool."""


class McpResponseTooLarge(McpError):
    """The MCP HTTP response exceeded its configured byte limit."""


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """MCP credentials and session headers stay on the configured endpoint."""

    def redirect_request(self, *_args: object, **_kwargs: object) -> None:
        return None


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


def is_retryable_page_crash(error: BaseException) -> bool:
    """Recognise page-local failures that can be retried on the same browser lease."""

    message = str(error).lower()
    return "page crashed" in message or "execution context was destroyed" in message


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
    for line in (*re.split(r"\r\n|\r|\n", text), ""):
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

    def __init__(
        self,
        endpoint: str | None = None,
        *,
        timeout: float = 30.0,
        max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
    ) -> None:
        configured = endpoint if endpoint is not None else configured_mcp_url()
        try:
            parsed = urlsplit(configured)
        except ValueError:
            raise McpUnavailable("MCP endpoint URL is malformed") from None
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise McpUnavailable("MCP endpoint must be an absolute http:// or https:// URL")
        try:
            parsed.port
        except ValueError:
            raise McpUnavailable("MCP endpoint URL has an invalid port") from None
        username = parsed.username
        password = parsed.password
        self._authorization: str | None = None
        if username is not None:
            try:
                username = unquote(username, errors="strict")
                password = unquote(password or "", errors="strict")
            except UnicodeDecodeError:
                raise McpUnavailable("MCP endpoint URL has invalid percent-encoded credentials") from None
            if ":" in username:
                raise McpUnavailable("MCP endpoint Basic Auth username cannot contain ':'") from None
            credentials = f"{username}:{password}".encode("utf-8")
            self._authorization = "Basic " + base64.b64encode(credentials).decode("ascii")
        authority = parsed.netloc.rsplit("@", 1)[-1]
        self.endpoint = urlunsplit((parsed.scheme, authority, parsed.path, parsed.query, ""))
        if not math.isfinite(timeout) or timeout <= 0:
            raise McpUnavailable("MCP timeout must be positive")
        if (
            not isinstance(max_response_bytes, int)
            or isinstance(max_response_bytes, bool)
            or max_response_bytes <= 0
            or max_response_bytes > _MAX_RESPONSE_BYTES
        ):
            raise McpUnavailable(f"MCP response byte limit must be between 1 and {_MAX_RESPONSE_BYTES}")
        self.timeout = timeout
        self.max_response_bytes = max_response_bytes
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            _NoRedirectHandler(),
        )
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
        if self._authorization is not None:
            headers["Authorization"] = self._authorization
        if self.session_id is not None:
            headers["Mcp-Session-Id"] = self.session_id
        if self._protocol_negotiated:
            headers["MCP-Protocol-Version"] = self.protocol_version
        return headers

    def _send(self, message: dict[str, object], *, expected_id: int | None) -> dict[str, object] | None:
        data = json.dumps(message, separators=(",", ":")).encode("utf-8")
        try:
            request = urllib.request.Request(self.endpoint, data=data, headers=self._headers(), method="POST")
            with self._opener.open(request, timeout=self.timeout) as response:
                status = response.status
                headers = response.headers
                body = response.read(self.max_response_bytes + 1)
                if len(body) > self.max_response_bytes:
                    raise McpResponseTooLarge(
                        f"MCP response exceeded the {self.max_response_bytes}-byte limit (transport ceiling)"
                    )
        except urllib.error.HTTPError as exc:
            if 300 <= exc.code < 400:
                raise McpUnavailable(
                    f"MCP endpoint returned HTTP {exc.code}; redirects are not followed"
                ) from None
            if expected_id is None:
                # urllib turns non-2xx notification replies into HTTPError. The
                # server still answered, so let the notification status check
                # classify it as an MCP protocol error rather than unreachable.
                status = exc.code
                headers = exc.headers
                body = b""
            else:
                raise McpUnavailable(f"MCP endpoint rejected the request (HTTP {exc.code})") from None
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", "connection failed")
            if isinstance(reason, TimeoutError) or "timed out" in str(reason).lower():
                raise McpTimeout("MCP endpoint request timed out") from None
            raise McpUnavailable("MCP endpoint is unreachable") from None
        except McpError:
            raise
        except TimeoutError:
            raise McpTimeout("MCP endpoint request timed out") from None
        except (http.client.HTTPException, ValueError):
            raise McpUnavailable("MCP endpoint rejected an invalid request") from None
        except OSError:
            raise McpUnavailable("MCP endpoint request failed") from None
        except Exception:
            # Request builders and third-party URL handlers must never leak the
            # configured endpoint or its Basic Auth userinfo into diagnostics.
            raise McpUnavailable("MCP endpoint request failed") from None

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
            headers={**self._headers(), "Mcp-Session-Id": self.session_id},
            method="DELETE",
        )
        try:
            with self._opener.open(request, timeout=self.timeout):
                pass
        except Exception:
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


def _skip_js_quoted(source: str, start: int, quote: str) -> int | None:
    """Return the end of a quoted string, or None for an incomplete literal."""

    index = start + 1
    while index < len(source):
        character = source[index]
        if character == "\\":
            if index + 1 >= len(source):
                return None
            if source[index + 1] == "\r" and index + 2 < len(source) and source[index + 2] == "\n":
                index += 3
            else:
                index += 2
        elif character == quote:
            return index + 1
        elif character in "\r\n\u2028\u2029":
            return None
        else:
            index += 1
    return None


def _skip_js_block_comment(source: str, start: int) -> int | None:
    """Return the end of a block comment, or None when it is unterminated."""

    index = start + 2
    while index < len(source):
        if source[index] == "*" and index + 1 < len(source) and source[index + 1] == "/":
            return index + 2
        index += 1
    return None


def _skip_js_regex(source: str, start: int) -> int | None:
    """Skip a JavaScript regex literal, respecting escapes and character classes."""

    index = start + 1
    in_character_class = False
    while index < len(source):
        character = source[index]
        if character in "\r\n\u2028\u2029":
            return None
        if character == "\\":
            if index + 1 >= len(source):
                return None
            index += 2
        elif character == "[" and not in_character_class:
            in_character_class = True
            index += 1
        elif character == "]" and in_character_class:
            in_character_class = False
            index += 1
        elif character == "/" and not in_character_class:
            index += 1
            while index < len(source) and (source[index].isalpha() or source[index].isdigit()):
                index += 1
            return index
        else:
            index += 1
    return None


def _skip_js_template_expression(source: str, start: int, nesting: int) -> int | None:
    """Skip `${...}` code inside a template, returning after its closing brace."""

    depth = 1
    index = start
    while index < len(source):
        character = source[index]
        if character.isspace():
            index += 1
        elif character in "'\"":
            end = _skip_js_quoted(source, index, character)
            if end is None:
                return None
            index = end
        elif character == "`":
            end = _skip_js_template(source, index, nesting + 1)
            if end is None:
                return None
            index = end
        elif character == "/" and index + 1 < len(source) and source[index + 1] == "/":
            index += 2
            while index < len(source) and source[index] not in "\r\n\u2028\u2029":
                index += 1
        elif character == "/" and index + 1 < len(source) and source[index + 1] == "*":
            end = _skip_js_block_comment(source, index)
            if end is None:
                return None
            index = end
        elif character == "/":
            # Distinguishing division from a regex here needs a token parser.
            # Preserve the source untouched rather than guessing at a delimiter.
            return None
        elif character == "{":
            depth += 1
            index += 1
        elif character == "}":
            depth -= 1
            index += 1
            if depth == 0:
                return index
        else:
            index += 1
    return None


def _skip_js_template(source: str, start: int, nesting: int = 0) -> int | None:
    """Skip a template literal and its interpolations without treating text as code."""

    if nesting >= 64:
        return None
    index = start + 1
    while index < len(source):
        character = source[index]
        if character == "\\":
            if index + 1 >= len(source):
                return None
            if source[index + 1] == "\r" and index + 2 < len(source) and source[index + 2] == "\n":
                index += 3
            else:
                index += 2
        elif character == "`":
            return index + 1
        elif character == "$" and index + 1 < len(source) and source[index + 1] == "{":
            end = _skip_js_template_expression(source, index + 2, nesting)
            if end is None:
                return None
            index = end
        else:
            index += 1
    return None


def _identifier_character(character: str) -> bool:
    return character.isalnum() or character in "_$" or ord(character) >= 128


def _strip_trailing_javascript(source: str) -> tuple[str, int]:
    """Scan JavaScript once and remove only final semicolons/comments outside literals."""

    index = 0
    first_token: int | None = None
    last_token_is_semicolon = False
    semicolon_start: int | None = None
    trailing_comment_start: int | None = None
    nonsemicolon_seen = False
    previous_can_end_expression = False
    previous_is_brace_close = False
    uncertain = False
    while index < len(source):
        character = source[index]
        if character.isspace():
            index += 1
            continue
        if character == "/" and index + 1 < len(source) and source[index + 1] == "/":
            if first_token is not None and trailing_comment_start is None:
                trailing_comment_start = index
            index += 2
            while index < len(source) and source[index] not in "\r\n\u2028\u2029":
                index += 1
            continue
        if character == "/" and index + 1 < len(source) and source[index + 1] == "*":
            if first_token is not None and trailing_comment_start is None:
                trailing_comment_start = index
            end = _skip_js_block_comment(source, index)
            if end is None:
                uncertain = True
                break
            index = end
            continue

        start = index
        if first_token is None:
            first_token = start
        token_is_semicolon = False
        token_can_end_expression = False
        token_is_brace_close = False

        if character in "'\"":
            end = _skip_js_quoted(source, index, character)
            if end is None:
                uncertain = True
                break
            index = end
            token_can_end_expression = True
        elif character == "`":
            end = _skip_js_template(source, index)
            if end is None:
                uncertain = True
                break
            index = end
            token_can_end_expression = True
        elif character == "/":
            if previous_is_brace_close:
                uncertain = True
                break
            if previous_can_end_expression:
                index += 2 if index + 1 < len(source) and source[index + 1] == "=" else 1
            else:
                end = _skip_js_regex(source, index)
                if end is None:
                    uncertain = True
                    break
                index = end
                token_can_end_expression = True
        elif character.isalpha() or character in "_$" or ord(character) >= 128:
            index += 1
            while index < len(source) and _identifier_character(source[index]):
                index += 1
            word = source[start:index]
            token_can_end_expression = word not in _REGEX_PREFIX_WORDS
        elif character.isdigit():
            index += 1
            while index < len(source) and (source[index].isalnum() or source[index] in "._"):
                index += 1
            token_can_end_expression = True
        else:
            two_character = source[index : index + 2]
            three_character = source[index : index + 3]
            token = (
                three_character
                if three_character in _THREE_CHARACTER_JS_OPERATORS
                else two_character
            )
            if token in _TWO_CHARACTER_JS_OPERATORS or token in _THREE_CHARACTER_JS_OPERATORS:
                index += len(token)
            else:
                token = character
                index += 1
            if token == ";":
                token_is_semicolon = True
            elif token in {")", "]", "}", "++", "--"}:
                token_can_end_expression = True
                token_is_brace_close = token == "}"

        if token_is_semicolon:
            if not last_token_is_semicolon:
                semicolon_start = start
            last_token_is_semicolon = True
            previous_can_end_expression = False
            previous_is_brace_close = False
        else:
            semicolon_start = None
            trailing_comment_start = None
            last_token_is_semicolon = False
            nonsemicolon_seen = True
            previous_can_end_expression = token_can_end_expression
            previous_is_brace_close = token_is_brace_close

    if first_token is None:
        raise ValueError("no JavaScript expression was provided; comments alone are not an expression")
    if uncertain:
        return source, first_token

    cut_at: int | None = None
    if last_token_is_semicolon:
        if not nonsemicolon_seen:
            raise ValueError("no JavaScript expression was provided before the trailing semicolon")
        cut_at = semicolon_start
    if trailing_comment_start is not None and (cut_at is None or trailing_comment_start < cut_at):
        cut_at = trailing_comment_start
    normalized = source[:cut_at].rstrip() if cut_at is not None else source.rstrip()
    if not normalized.strip():
        raise ValueError("no JavaScript expression remains after trailing semicolons and comments")
    return normalized, first_token


def _bounded_evaluation_function(function_source: str, max_bytes: int) -> str:
    """Call a page function and cap its compact JSON representation before MCP replies."""

    return (
        "async () => { const value = await (" + function_source + ")(); "
        "const serialized = JSON.stringify(value); if (typeof serialized !== 'string') return value; "
        "const encoded = new TextEncoder().encode(serialized); "
        f"const maxBytes = {max_bytes}; "
        f"if (encoded.length <= maxBytes) return {{{_EVAL_RESULT_MARKER}: true, truncated: false, value}}; "
        "let previewEnd = Math.min(maxBytes, encoded.length); "
        "while (previewEnd > 0 && (encoded[previewEnd] & 0xc0) === 0x80) previewEnd -= 1; "
        "const preview = new TextDecoder().decode(encoded.subarray(0, previewEnd)); "
        f"return {{{_EVAL_RESULT_MARKER}: true, truncated: true, preview, max_bytes: maxBytes}}; }}"
    )


def evaluation_function(
    expression: str | None,
    *,
    selector: str | None = None,
    attribute: str | None = None,
    property_name: str | None = None,
    all_matches: bool = False,
    max_bytes: int | None = None,
) -> str:
    """Build the ``browser_evaluate`` function for caller code or selector-based DOM reads."""

    if max_bytes is not None and max_bytes <= 0:
        raise ValueError("evaluation result size limit must be positive")
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
        function = (
            "() => { const elements = Array.from(document.querySelectorAll("
            f"{json.dumps(selector)})); const values = elements.map((element) => {read}); "
            f"return {result}; }}"
        )
        if max_bytes is not None:
            return _bounded_evaluation_function(function, max_bytes)
        return function
    if attribute is not None or property_name is not None:
        raise ValueError("--attr and --property require --selector")
    if all_matches:
        raise ValueError("--all requires --selector with --attr or --property")
    if expression is None or not expression.strip():
        raise ValueError("provide a JavaScript expression or function")
    source, first_token = _strip_trailing_javascript(expression.strip())
    code_source = source[first_token:]
    function_source: str | None = None
    if re.match(r"^(?:async\s+)?function(?:\s|\*|\()", code_source):
        function_source = source
    head, separator, _body = code_source.partition("=>")
    stripped_head = head.strip()
    if stripped_head.startswith("async "):
        stripped_head = stripped_head[len("async ") :].strip()
    if separator and (
        stripped_head.isidentifier()
        or (stripped_head.startswith("(") and stripped_head.endswith(")"))
    ):
        function_source = source
    if function_source is None:
        function_source = f"() => (\n{source}\n)"
    if max_bytes is not None:
        return _bounded_evaluation_function(function_source, max_bytes)
    return function_source


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
