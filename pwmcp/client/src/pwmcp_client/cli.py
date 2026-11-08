"""The ``pwmcp`` console script, registered on the shared cli-extended grammar."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

from cli_extended import (
    ArgumentSpec,
    CliFailure,
    CliIdentity,
    CliRegistry,
    CliRuntime,
    LogLevel,
    OptionSpec,
    RegisteredCli,
    VerbGroup,
    VerbSpec,
    VersionLookupError,
    register_doctor,
    register_skills_verbs,
)

from . import ocr as ocr_module
from . import storage_state as storage_module
from .blocked import BlockVerdict, detect as detect_block
from .diagnostics import DOCTOR_CHECKS, try_load_contract
from .mcp_client import (
    _EVAL_RESULT_MARKER,
    McpError,
    McpResponseTooLarge,
    McpTimeout,
    McpToolError,
    StreamableHttpClient,
    decode_html_value,
    decode_tool_value,
    evaluation_function,
    html_extraction_function,
    is_browser_crash,
    is_retryable_page_crash,
    page_diagnostics_function,
    visible_selector_function,
)
from .page_content import extract_content
from .release import assess_skew, version_to_release
from .session import BrowserLease, PwmcpError
from .user_agent import ResolvedUserAgent, UserAgentError, cdp_override_params, resolve

DEFAULT_CONTRACT = "http://pwmcp:3000/contract"
_VERSION_FILE = Path(__file__).resolve().parent / "VERSION"
EXIT_NOT_INSTALLED = 3
# `pwmcp inspect` recognised bot protection (a challenge or block page) instead of the target.
EXIT_BLOCKED = 4
# The main document returned an HTTP 4xx or 5xx response.
EXIT_HTTP_ERROR = 5
_BLOCKED_CONTENT_CHARS = 4000
_PROBE_TIMEOUT_MS = 5000


def identity(version_file: Path = _VERSION_FILE) -> CliIdentity:
    """Resolve the CLI identity: installed metadata and the packaged VERSION must agree."""

    return CliIdentity.resolve(
        name="PWMCP",
        long_name="client utility",
        command="pwmcp",
        distribution="pwmcp-client",
        version_file=version_file,
    )


def _contract(args: argparse.Namespace, runtime: CliRuntime) -> None:
    contract, problem = try_load_contract(args.contract)
    if contract is None:
        raise CliFailure(
            f"cannot load the pwmcp contract from {args.contract} ({problem})",
            hint="start the pwmcp stack or pass --contract URL_OR_FILE",
        )
    if runtime.json_mode:
        runtime.output.primary(dict(contract.__dict__))
    else:
        runtime.output.primary(json.dumps(contract.__dict__, indent=2, sort_keys=True))


def _versions(args: argparse.Namespace, runtime: CliRuntime) -> None:
    """Client version, server release (when reachable) and their skew verdict.

    ``--version``/``version`` stay the framework's exact, network-free line (the shared
    CLI contract); this verb is the one that talks to the server.
    """

    client = runtime.identity.version
    contract, problem = try_load_contract(args.contract)
    server = contract.release if contract is not None else None
    skew = assess_skew(client, server) if server is not None else None
    if runtime.json_mode:
        runtime.output.primary({
            "client": client,
            "client_release": version_to_release(client) if _is_client_version(client) else None,
            "server": server,
            "skew": skew.status if skew else "unknown",
            "skew_message": skew.message if skew else f"server unreachable: {problem}",
        })
        return
    runtime.output.primary(f"pwmcp {client} (server {server if server is not None else 'unreachable'})")
    if skew is None:
        runtime.output.warn(f"cannot read the server contract from {args.contract} ({problem})")
    elif skew.status != "ok":
        runtime.output.warn(f"{skew.status}: {skew.message}")


def _is_client_version(value: str) -> bool:
    try:
        version_to_release(value)
    except ValueError:
        return False
    return True


def _server_identity(contract: object) -> ResolvedUserAgent:
    """The server-level user-agent setting as published in the contract (None = default)."""

    value = getattr(contract, "user_agent", None)
    if not value:
        return ResolvedUserAgent(None, "default")
    return ResolvedUserAgent(str(value), str(getattr(contract, "user_agent_source", "custom")))


def _apply_client_hints(context: object, page: object, ua: str, runtime: CliRuntime) -> None:
    """Set the UA and its matching client-hint metadata over CDP on this page.

    ``new_context(user_agent=...)`` already sets the UA string; this additionally sets
    ``userAgentMetadata`` (Sec-CH-UA brands, navigator.userAgentData) so they do not
    still say HeadlessChrome. Only done when the UA names a recognisable browser; a
    failure is reported, never silent, and leaves the UA-string override in place.
    """

    params = cdp_override_params(ua)
    if "userAgentMetadata" not in params:
        runtime.output.warn("client-hint metadata not set: the user agent names no recognised browser")
        return
    try:
        session = context.new_cdp_session(page)  # type: ignore[attr-defined]
        session.send("Emulation.setUserAgentOverride", params)
    except Exception as exc:  # noqa: BLE001 - any CDP failure degrades to UA-string-only, loudly
        runtime.output.warn(f"client-hint metadata not applied (user agent string only): {exc}")


def _observed_user_agent(page: object) -> str | None:
    """What the page itself reports, so the result states the measured identity."""

    try:
        value = page.evaluate("navigator.userAgent")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - measurement is best-effort; fall back to the configured value
        return None
    return value if isinstance(value, str) and value else None


_SETTLE_MS = 500
_SHELL_BYTES = 1024
_SHRINK_FACTOR = 2
_MAX_RETRIES = 10
_MAX_HTML_BYTES = 512 * 1024
_DEFAULT_MAX_EVAL_BYTES = 1024 * 1024
_MAX_EVAL_BYTES = 4 * 1024 * 1024
_MCP_HTTP_TIMEOUT_MARGIN_SECONDS = 2.0


def _failure_hint(exc: BaseException) -> str:
    """Keep browser lifecycle failures distinct from service reachability failures."""

    if isinstance(exc, McpResponseTooLarge):
        return (
            "the MCP response exceeded its configured transport limit; return a smaller or aggregated value"
        )
    if isinstance(exc, McpTimeout) or "request timed out" in str(exc).lower():
        return "the MCP request timed out; increase --timeout for eval or --timeout-ms for inspect"
    if isinstance(exc, McpToolError) and is_browser_crash(exc):
        return (
            "the PWMCP MCP endpoint replied, but the page or browser crashed or was closed; "
            "if it repeats, check that the browser service is running"
        )
    if is_browser_crash(exc):
        return (
            "the page or browser crashed or was closed; if it repeats, check that PWMCP is still running "
            "and reachable, then reduce page load or check browser memory and /dev/shm"
        )
    if isinstance(exc, McpToolError):
        return (
            "the PWMCP MCP endpoint replied; check the page URL, its wait condition, and the requested JavaScript; "
            "serialize calls if the shared browser reports that it is already in use"
        )
    if isinstance(exc, McpError):
        return "check that the PWMCP MCP endpoint is reachable and PWMCP_MCP_URL names its /mcp endpoint"
    if isinstance(exc, PwmcpError):
        return "check that PWMCP is running and reachable from this container"
    return "check that PWMCP is running and reachable from this container, and that the page wait condition is reachable"


def _print_eval_truncation(
    preview: str,
    max_bytes: int,
    runtime: CliRuntime,
    *,
    status: int | None,
    diagnostics: list[str],
) -> None:
    """Print a bounded JSON preview and keep its truncation signal machine-readable."""

    if runtime.json_mode:
        runtime.output.primary({
            "content": preview,
            "diagnostics": diagnostics,
            "format": "eval",
            "http_error": _is_http_error(status),
            "http_status": status,
            "max_bytes": max_bytes,
            "preview": preview,
            "truncated": True,
        })
    else:
        runtime.output.primary(preview)
        runtime.output.emit(LogLevel.WARN, f"[truncated after {max_bytes} bytes]", force=True)


class _PageDiagnostics:
    """What the page reported while loading: console errors and failed requests (PWMCP-25)."""

    def __init__(self) -> None:
        self.console_errors = 0
        self.failed_requests = 0
        self.statuses: Counter[int] = Counter()
        self.main_status: int | None = None
        self.document: object | None = None  # the latest main-frame document response
        self._page: object | None = None

    def attach(self, page: object) -> None:
        self._page = page
        page.on("console", self._console)  # type: ignore[attr-defined]
        page.on("response", self._response)  # type: ignore[attr-defined]
        page.on("requestfailed", self._request_failed)  # type: ignore[attr-defined]

    def _console(self, message: object) -> None:
        if getattr(message, "type", None) == "error":
            self.console_errors += 1

    def _response(self, response: object) -> None:
        status = getattr(response, "status", 0)
        request = response.request  # type: ignore[attr-defined]
        if request.resource_type == "document":
            # A challenge that resolves itself re-navigates, so the LATEST main-frame document
            # (not the first response) says what the page is now.
            if getattr(request, "frame", None) is getattr(self._page, "main_frame", None):
                self.document = response
        elif status >= 400:
            self.statuses[status] += 1

    def _request_failed(self, _request: object) -> None:
        self.failed_requests += 1

    @property
    def http_status(self) -> int | None:
        """Return the latest main-document status, preferring the response event."""

        status = getattr(self.document, "status", None)
        if isinstance(status, int) and not isinstance(status, bool):
            return status
        if isinstance(self.main_status, int) and not isinstance(self.main_status, bool):
            return self.main_status
        return None

    def summary(self) -> str | None:
        parts = []
        status = self.http_status
        if status is not None and status >= 400:
            parts.append(f"main document HTTP {status}")
        if self.console_errors:
            parts.append(f"{self.console_errors} console errors")
        if self.statuses:
            by_status = ", ".join(f"{status} x{count}" for status, count in sorted(self.statuses.items()))
            parts.append(f"{sum(self.statuses.values())} failed subresources ({by_status})")
        if self.failed_requests:
            parts.append(f"{self.failed_requests} requests failed outright")
        if not parts:
            return None
        return "page diagnostics: " + "; ".join(parts) + " (the page may not have rendered completely)"


class _Attempt:
    """One navigation of ``pwmcp inspect``."""

    def __init__(
        self,
        payload: str | bytes,
        observed_ua: str | None,
        stable: bool,
        diagnostics: _PageDiagnostics,
        *,
        blocked: BlockVerdict | None = None,
        status: int | None = None,
        final_url: str | None = None,
        waited_ms: int = 0,
    ) -> None:
        self.payload = payload
        self.observed_ua = observed_ua
        self.stable = stable
        self.diagnostics = diagnostics
        self.blocked = blocked
        self.status = status
        self.final_url = final_url
        self.waited_ms = waited_ms


def _safe(read: object, default: object) -> object:
    """A best-effort read of a page that may be mid-navigation or already closed."""

    try:
        return read()  # type: ignore[operator]
    except Exception:  # noqa: BLE001 - the probe must never hide the real outcome behind its own failure
        return default


def _blocked_attempt(
    page: object,
    diagnostics: _PageDiagnostics,
    started: float,
    text: str | None,
) -> _Attempt | None:
    """Recognise bot protection on the page as it is NOW; None when it is not recognised.

    ``text`` is the body text when the caller already has it, else it is read here. The status
    and headers come from the latest main-frame document response.
    """

    document = diagnostics.document
    status = diagnostics.http_status
    headers = _safe(lambda: dict(document.headers), {}) if document is not None else {}  # type: ignore[attr-defined]
    if text is None:
        text = _safe(lambda: page.locator("body").inner_text(timeout=_PROBE_TIMEOUT_MS), "")  # type: ignore[attr-defined]
    title = _safe(lambda: page.title(), "")  # type: ignore[attr-defined]
    frames = _safe(lambda: [frame.url for frame in page.frames], [])  # type: ignore[attr-defined]
    verdict = detect_block(
        status=status,
        headers=headers,  # type: ignore[arg-type]
        title=title,  # type: ignore[arg-type]
        text=text,  # type: ignore[arg-type]
        frames=frames,  # type: ignore[arg-type]
    )
    if verdict is None:
        return None
    return _Attempt(
        text[:_BLOCKED_CONTENT_CHARS],  # type: ignore[index]
        _observed_user_agent(page),
        True,
        diagnostics,
        blocked=verdict,
        status=status,
        final_url=_safe(lambda: page.url, None),  # type: ignore[attr-defined,arg-type]
        waited_ms=int((time.monotonic() - started) * 1000),
    )


def _inspect_once(
    lease: object,
    args: argparse.Namespace,
    active: ResolvedUserAgent,
    image_type: str,
    runtime: CliRuntime,
    *,
    check_stable: bool,
    storage: storage_module.ScopedState | None = None,
) -> _Attempt:
    context_options: dict[str, object] = {"ignore_https_errors": args.ignore_https_errors}
    if active.user_agent is not None:
        context_options["user_agent"] = active.user_agent
    if storage is not None:
        # Only the cookies/origins that belong to the target host (see storage_state.py).
        context_options["storage_state"] = storage.state
    # A fresh context per attempt: the override dies with it, which is the restore.
    context = lease.browser.new_context(**context_options)  # type: ignore[attr-defined]
    try:
        page = context.new_page()
        diagnostics = _PageDiagnostics()
        diagnostics.attach(page)
        if active.user_agent is not None:
            _apply_client_hints(context, page, active.user_agent, runtime)
        started = time.monotonic()
        try:
            response = page.goto(args.url, wait_until=args.wait_until, timeout=args.timeout_ms)
            diagnostics.main_status = getattr(response, "status", None)
            if diagnostics.document is None:
                diagnostics.document = response
            if args.wait_for:
                page.locator(args.wait_for).filter(visible=True).first.wait_for(
                    state="visible", timeout=args.timeout_ms
                )
        except Exception:
            # A wait that ends on a challenge page is "blocked", not a generic timeout; any
            # other failure is re-raised unchanged for the caller's retry/error handling.
            blocked = _blocked_attempt(page, diagnostics, started, None)
            if blocked is None:
                raise
            return blocked

        stable = True
        if args.format in {"text", "snapshot"}:
            def read() -> str:
                if args.format == "text":
                    if args.selector is None and not args.main:
                        return page.locator("body").inner_text(timeout=args.timeout_ms)
                    source = (
                        page.locator(args.selector).first.inner_html(timeout=args.timeout_ms)
                        if args.selector
                        else page.locator("body").inner_html(timeout=args.timeout_ms)
                    )
                    return extract_content(source, fmt="text", base_url=page.url, main=args.main)
                return page.locator("body").aria_snapshot(timeout=args.timeout_ms)

            payload: str | bytes = read()
            blocked = _blocked_attempt(page, diagnostics, started, payload if args.format == "text" else None)
            if blocked is not None:
                return blocked
            if check_stable:
                # Stable-DOM check: the content must not change over a short settle window.
                page.wait_for_timeout(_SETTLE_MS)
                settled = read()
                stable = settled == payload
                payload = settled
        elif args.format in {"markdown", "links"}:
            def read() -> str:
                source = (
                    page.locator(args.selector).first.inner_html(timeout=args.timeout_ms)
                    if args.selector
                    else page.locator("body").inner_html(timeout=args.timeout_ms)
                )
                return extract_content(
                    source,
                    fmt=args.format,
                    base_url=page.url,
                    main=args.main,
                )

            payload = read()
            blocked = _blocked_attempt(page, diagnostics, started, None)
            if blocked is not None:
                return blocked
            if check_stable:
                page.wait_for_timeout(_SETTLE_MS)
                settled = read()
                stable = settled == payload
                payload = settled
        else:
            payload = page.screenshot(
                full_page=args.full_page,
                type=image_type,
                animations="disabled",
                timeout=args.timeout_ms,
            )
            blocked = _blocked_attempt(page, diagnostics, started, None)
            if blocked is not None:
                return blocked
        return _Attempt(
            payload,
            _observed_user_agent(page),
            stable,
            diagnostics,
            status=diagnostics.http_status,
            final_url=_safe(lambda: page.url, None),  # type: ignore[attr-defined,arg-type]
        )
    finally:
        context.close()


def _small_result_warning(args: argparse.Namespace, payload: str | bytes, sizes: list[int]) -> str | None:
    """Warn when a text result is much smaller than an earlier attempt, or looks like a shell (PWMCP-26)."""

    if not isinstance(payload, str):
        return None
    size = len(payload.encode("utf-8"))
    earlier = sizes[:-1]
    if earlier and size * _SHRINK_FACTOR < max(earlier):
        return (
            f"result is {size} bytes, much smaller than an earlier attempt ({max(earlier)} bytes); "
            "the page probably rendered only partially this time"
        )
    if not args.wait_for and size < _SHELL_BYTES:
        return (
            f"result is only {size} bytes and no --wait-for was given; if more was expected the page "
            "may not have finished rendering (pass --wait-for SELECTOR, a longer --timeout-ms or --retries N)"
        )
    return None


def _is_http_error(status: int | None) -> bool:
    return status is not None and 400 <= status <= 599


def _raise_http_error(status: int | None, *, allow: bool) -> None:
    if _is_http_error(status) and not allow:
        raise CliFailure(
            f"main document returned HTTP {status}",
            exit_code=EXIT_HTTP_ERROR,
            hint="pass --allow-http-error when a 4xx/5xx response body is expected",
        )


def _inspect_html(args: argparse.Namespace, runtime: CliRuntime) -> None:
    """Read bounded page or element HTML through the server's browser_evaluate MCP tool."""

    if not 1 <= args.max_html_bytes <= _MAX_HTML_BYTES:
        raise CliFailure(f"--max-html-bytes must be between 1 and {_MAX_HTML_BYTES}")
    if args.wait_until != "load":
        raise CliFailure(
            "--format html currently uses the MCP browser_navigate load condition; --wait-until must be load"
        )
    if args.ignore_https_errors or args.user_agent is not None or args.storage_state:
        raise CliFailure(
            "--format html uses the configured MCP browser context and cannot apply --ignore-https-errors, "
            "--user-agent, or --storage-state"
        )

    retries = args.retries if args.retries is not None else 1
    attempts = retries + 1
    result: tuple[str, int, bool] | None = None
    status: int | None = None
    for number in range(1, attempts + 1):
        try:
            http_timeout = args.timeout_ms / 1000
            if args.wait_for:
                http_timeout += _MCP_HTTP_TIMEOUT_MARGIN_SECONDS
            with StreamableHttpClient(timeout=http_timeout) as client:
                client.call_tool("browser_navigate", {"url": args.url})
                diagnostics_started = time.monotonic()
                page_diagnostics = decode_tool_value(
                    client.call_tool("browser_evaluate", {"function": page_diagnostics_function()})
                )
                if not isinstance(page_diagnostics, dict):
                    raise McpError("MCP page diagnostics returned a non-object result")
                title = page_diagnostics.get("title")
                text = page_diagnostics.get("text")
                status_value = page_diagnostics.get("status")
                final_url = page_diagnostics.get("final_url")
                frames = page_diagnostics.get("frames")
                if (
                    not isinstance(title, str)
                    or not isinstance(text, str)
                    or (status_value is not None and (not isinstance(status_value, int) or isinstance(status_value, bool)))
                    or not isinstance(final_url, str)
                    or not isinstance(frames, list)
                    or any(not isinstance(frame, str) for frame in frames)
                ):
                    raise McpError("MCP page diagnostics returned invalid values")
                status = status_value
                verdict = detect_block(status=status_value, headers={}, title=title, text=text, frames=frames)
                if verdict is not None:
                    _report_html_blocked(
                        args,
                        runtime,
                        verdict,
                        status=status_value,
                        final_url=final_url,
                        waited_ms=int((time.monotonic() - diagnostics_started) * 1000),
                    )
                if args.wait_for:
                    client.call_tool(
                        "browser_evaluate",
                        {"function": visible_selector_function(args.wait_for, args.timeout_ms)},
                    )
                evaluated = client.call_tool(
                    "browser_evaluate",
                    {
                        "function": html_extraction_function(
                            args.selector, args.html_mode, args.max_html_bytes
                        )
                    },
                )
                result = decode_html_value(
                    decode_tool_value(evaluated), max_bytes=args.max_html_bytes
                )
            break
        except McpError as exc:
            retryable_page_crash = is_retryable_page_crash(exc)
            browser_lease_closed = is_browser_crash(exc) and not retryable_page_crash
            automatic_crash_retry = args.retries is None and number == 1 and retryable_page_crash
            explicit_retry = (
                args.retries is not None
                and number < attempts
                and not browser_lease_closed
            )
            if not (automatic_crash_retry or explicit_retry):
                raise CliFailure(f"pwmcp inspect failed: {exc}", hint=_failure_hint(exc)) from exc
            if automatic_crash_retry:
                note = f"attempt {number}/{attempts} hit a page/browser crash ({exc}); retrying automatically once"
            else:
                note = f"attempt {number}/{attempts} failed: {exc}; retrying"
            runtime.output.emit(LogLevel.WARN, note, force=True)

    if result is None:
        raise CliFailure("pwmcp inspect failed: the MCP browser returned no HTML result")
    html, total_bytes, truncated = result
    included_bytes = len(html.encode("utf-8"))
    if truncated:
        runtime.output.emit(
            LogLevel.WARN,
            f"HTML result truncated at {included_bytes} bytes (page content is {total_bytes} bytes); "
            "use a narrower --selector",
            force=True,
        )

    output_path = Path(args.output).expanduser() if args.output else None
    http_error = _is_http_error(status)
    diagnostics = [f"main document HTTP {status}"] if http_error else []
    if http_error:
        runtime.output.emit(
            LogLevel.WARN,
            f"page diagnostics: main document HTTP {status} (the page may not have rendered completely)",
            force=True,
        )
    metadata: dict[str, object] = {
        "content": html if output_path is None else None,
        "diagnostics": diagnostics,
        "format": "html",
        "http_error": http_error,
        "http_status": status,
        "truncated": truncated,
        "size_bytes": total_bytes,
        "included_bytes": included_bytes,
    }
    if output_path is not None:
        try:
            output_path.write_text(html, encoding="utf-8")
        except OSError as exc:
            raise CliFailure(f"cannot write html output to {output_path}: {exc}") from exc
        runtime.output.info(f"wrote html output to {output_path}")
        if runtime.json_mode:
            runtime.output.primary({**metadata, "output": str(output_path)})
        _raise_http_error(status, allow=getattr(args, "allow_http_error", False))
        return
    if runtime.json_mode:
        runtime.output.primary(metadata)
    else:
        runtime.output.primary(html)
    _raise_http_error(status, allow=getattr(args, "allow_http_error", False))


def _report_html_blocked(
    args: argparse.Namespace,
    runtime: CliRuntime,
    verdict: BlockVerdict,
    *,
    status: int | None,
    final_url: str,
    waited_ms: int,
) -> None:
    """Keep bounded HTML inspection consistent with inspect's bot-protection contract."""

    message = (
        f"blocked by bot protection ({verdict.description}); HTML was not returned because the page "
        "is a challenge page, not the target"
    )
    result = {
        "blocked": True,
        "content": None,
        "diagnostics": [message],
        "format": "html",
        "provider": verdict.provider,
        "kind": verdict.kind,
        "signals": list(verdict.signals),
        "status": status,
        "http_status": status,
        "http_error": _is_http_error(status),
        "final_url": final_url,
        "waited_ms": waited_ms,
        "message": message,
    }
    if runtime.json_mode:
        runtime.output.primary_json(result)
    else:
        runtime.output.primary(message)
    if args.output:
        runtime.output.emit(LogLevel.WARN, f"--output {args.output} was not written: the result is not the target", force=True)
    raise CliFailure(
        message,
        exit_code=EXIT_BLOCKED,
        hint=(
            "report this to the user as BLOCKED; do not retry or work around it. A human can open the page "
            "once and export a storage state for --storage-state (README 'Bot protection')"
        ),
    )


def _inspect(args: argparse.Namespace, runtime: CliRuntime) -> None:
    """Read rendered page text, an ARIA snapshot, or a remote screenshot."""

    try:
        parsed = urlsplit(args.url)
        hostname = parsed.hostname
    except ValueError as exc:
        raise CliFailure(f"URL is malformed: {exc}") from exc
    if parsed.scheme not in {"http", "https"} or not hostname:
        raise CliFailure("URL must be an absolute http:// or https:// URL")
    if args.timeout_ms <= 0:
        raise CliFailure("--timeout-ms must be a positive integer")
    if args.retries is not None and not 0 <= args.retries <= _MAX_RETRIES:
        raise CliFailure(f"--retries must be between 0 and {_MAX_RETRIES}")
    if args.format == "screenshot" and not args.output:
        raise CliFailure("--output is required when --format=screenshot")
    content_formats = {"text", "markdown", "links"}
    if args.selector is not None and args.format not in {*content_formats, "html"}:
        raise CliFailure("--selector requires --format text, markdown, links, or html")
    if args.main and args.format not in content_formats:
        raise CliFailure("--main requires --format text, markdown, or links")
    if args.main and args.selector is not None:
        raise CliFailure("--main and --selector cannot be used together")
    if args.format != "html" and (args.html_mode != "outer" or args.max_html_bytes != _MAX_HTML_BYTES):
        raise CliFailure("--html-mode and --max-html-bytes require --format html")
    if args.format == "html":
        _inspect_html(args, runtime)
        return

    # PWMCP-09: an explicit per-call --user-agent is validated before connecting.
    call_override: ResolvedUserAgent | None = None
    if args.user_agent is not None:
        try:
            call_override = resolve(args.user_agent)
        except UserAgentError as exc:
            raise CliFailure(f"--user-agent is invalid: {exc}") from exc

    # Operator-supplied session (human-in-the-loop): validated and scoped to this URL's host
    # before connecting; its values never reach a message, only the counts do.
    storage: storage_module.ScopedState | None = None
    if args.storage_state:
        try:
            storage = storage_module.load_scoped(args.storage_state, args.url)
        except storage_module.StorageStateError as exc:
            raise CliFailure(
                f"--storage-state is unusable: {exc}",
                hint="see README 'Bot protection' for how a human exports a storage state",
            ) from exc

    output_path = Path(args.output).expanduser() if args.output else None
    image_type = "png"
    if args.format == "screenshot" and output_path is not None:
        suffix = output_path.suffix.lower()
        if suffix in {".jpg", ".jpeg"}:
            image_type = "jpeg"
        elif suffix != ".png":
            raise CliFailure("screenshot output must end in .png, .jpg, or .jpeg")

    diagnostics: list[str] = []
    try:
        from playwright.sync_api import Error as PlaywrightError
    except ImportError as exc:
        raise CliFailure(
            "the Playwright Python package is not installed",
            hint="install the client bundle for this PWMCP release",
        ) from exc

    try:
        with BrowserLease.connect(
            contract_url=args.contract,
            label="pwmcp-inspect",
            timeout_ms=args.timeout_ms,
        ) as lease:
            # Effective identity: the per-call --user-agent wins, else the server-level
            # setting the service publishes in its contract, else the browser default.
            active = call_override or _server_identity(getattr(lease, "contract", None))
            # PWMCP-24: an explicit --retries N allows N more failed or unstable attempts.
            # With no option, keep the one-attempt behavior except for one page-local crash.
            attempts = args.retries + 1 if args.retries is not None else 2
            sizes: list[int] = []
            number = 0
            while True:  # the last attempt always ends the loop: it returns (break) or raises
                number += 1
                last = number == attempts
                try:
                    attempt = _inspect_once(
                        lease,
                        args,
                        active,
                        image_type,
                        runtime,
                        check_stable=args.retries is not None and not last,
                        storage=storage,
                    )
                except PlaywrightError as exc:
                    retryable_page_crash = is_retryable_page_crash(exc)
                    browser_lease_closed = is_browser_crash(exc) and not retryable_page_crash
                    automatic_crash_retry = (
                        args.retries is None and number == 1 and retryable_page_crash
                    )
                    explicit_retry = args.retries is not None and not last and not browser_lease_closed
                    if not (automatic_crash_retry or explicit_retry):
                        raise
                    if automatic_crash_retry:
                        note = f"attempt {number}/{attempts} hit a page/browser crash ({exc}); retrying automatically once"
                    else:
                        note = f"attempt {number}/{attempts} failed: {exc}; retrying"
                    runtime.output.emit(LogLevel.WARN, note, force=True)
                    continue
                if isinstance(attempt.payload, str):
                    sizes.append(len(attempt.payload.encode("utf-8")))
                if attempt.stable or last:
                    break
                runtime.output.emit(
                    LogLevel.WARN,
                    f"attempt {number}/{attempts}: the page was still changing; retrying",
                    force=True,
                )
            payload, observed_ua = attempt.payload, attempt.observed_ua
            # PWMCP-25 / PWMCP-26: say when the result may be partial (stderr only, stdout unchanged).
            # (A blocked attempt is reported as such below; "small result" would only mislead.)
            if attempt.blocked is None:
                diagnostics = [
                    note
                    for note in (attempt.diagnostics.summary(), _small_result_warning(args, payload, sizes))
                    if note
                ]
                for note in diagnostics:
                    runtime.output.emit(LogLevel.WARN, note, force=True)
    except (PwmcpError, PlaywrightError, OSError, ValueError) as exc:
        raise CliFailure(f"pwmcp inspect failed: {exc}", hint=_failure_hint(exc)) from exc

    # Transparency (PWMCP-09). When an override is active, report the identity on stderr and
    # include it in the structured JSON result. The result envelope itself is unconditional.
    overridden = active.source != "default"
    fields = {
        "user_agent": observed_ua or active.user_agent,
        "user_agent_source": active.source,
    }
    if overridden:
        runtime.output.emit(
            LogLevel.INFO,
            f"user agent: {fields['user_agent']} (source: {fields['user_agent_source']})",
            force=True,
        )
    # Same transparency for an operator-supplied session: say that one was applied (counts only).
    if storage is not None:
        fields["storage_state"] = {"cookies": storage.cookies, "origins": storage.origins}  # type: ignore[assignment]
        runtime.output.emit(
            LogLevel.INFO,
            f"storage state: {storage.cookies} cookies and {storage.origins} origins applied for this host "
            "(values are never printed)",
            force=True,
        )
    reported = overridden or storage is not None

    if attempt.blocked is not None:
        blocked_fields = fields if reported else {}
        _report_blocked(attempt, args, blocked_fields, runtime, storage is not None)

    http_error = _is_http_error(attempt.status)
    result: dict[str, object] = {
        "content": payload if output_path is None else None,
        "diagnostics": diagnostics,
        "format": args.format,
        "http_error": http_error,
        "http_status": attempt.status,
    }
    if reported:
        result.update(fields)

    if output_path is None:
        if runtime.json_mode:
            runtime.output.primary(result)
        else:
            runtime.output.primary(payload)
        _raise_http_error(attempt.status, allow=args.allow_http_error)
        return

    try:
        if args.format == "screenshot":
            output_path.write_bytes(payload)
        else:
            output_path.write_text(payload.rstrip("\n") + "\n", encoding="utf-8")
    except OSError as exc:
        raise CliFailure(f"cannot write {args.format} output to {output_path}: {exc}") from exc
    runtime.output.info(f"wrote {args.format} output to {output_path}")
    if runtime.json_mode:
        result["output"] = str(output_path)
        runtime.output.primary(result)
    _raise_http_error(attempt.status, allow=args.allow_http_error)


def _report_blocked(
    attempt: _Attempt,
    args: argparse.Namespace,
    fields: dict[str, object],
    runtime: CliRuntime,
    used_storage: bool,
) -> None:
    """State, never hide, that the page is bot protection: exit EXIT_BLOCKED, never write ``--output``.

    stdout carries the explanation and the challenge page's text (in ``--json``: ``blocked: true``
    and the details), so nothing can mistake it for the target. stderr and the exit code say it too.
    """

    verdict = attempt.blocked
    assert verdict is not None  # the caller checked; narrows the type
    where = "in the content field" if runtime.json_mode else "below"
    message = (
        f"blocked by bot protection ({verdict.description}); "
        f"page content {where} is the challenge page, not the target"
    )
    waited = f"{attempt.waited_ms / 1000:.1f} s"
    detail = f"HTTP {attempt.status}" if attempt.status is not None else "no HTTP status"
    detail += f", signals: {', '.join(verdict.signals)}"
    if args.wait_for:
        detail += f"; still blocked after waiting {waited} for {args.wait_for!r}"
    result: dict[str, object] = {
        "blocked": True,
        "content": attempt.payload,
        "diagnostics": [],
        "format": args.format,
        "http_error": _is_http_error(attempt.status),
        "http_status": attempt.status,
        "provider": verdict.provider,
        "kind": verdict.kind,
        "signals": list(verdict.signals),
        "status": attempt.status,
        "final_url": attempt.final_url,
        "waited_ms": attempt.waited_ms,
        "message": message,
        **fields,
    }
    if runtime.json_mode:
        runtime.output.primary_json(result)
    else:
        runtime.output.primary(f"{message}\n\n{attempt.payload}")
    if args.output:
        runtime.output.emit(LogLevel.WARN, f"--output {args.output} was not written: the result is not the target", force=True)
    if used_storage:
        hint = (
            "the supplied storage state did not get past it: the cookies may have expired or be bound to "
            "another IP address or user agent; a human must export a fresh one (README 'Bot protection')"
        )
    else:
        hint = (
            "report this to the user as BLOCKED; do not retry or work around it. A human can open the page "
            "once and export a storage state for --storage-state (README 'Bot protection')"
        )
    raise CliFailure(f"{message} ({detail})", exit_code=EXIT_BLOCKED, hint=hint)


_REGION = re.compile(r"^\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*$")


def _parse_region(value: str) -> tuple[int, int, int, int]:
    found = _REGION.match(value)
    if not found or int(found.group(3)) == 0 or int(found.group(4)) == 0:
        raise CliFailure("--region must be x,y,w,h in whole pixels with a positive width and height", exit_code=2)
    x, y, w, h = (int(group) for group in found.groups())
    return x, y, w, h


def _capture_element(args: argparse.Namespace, runtime: CliRuntime) -> bytes:
    """Open ``args.url`` in a fresh context (as ``inspect`` does) and return a PNG of the element.

    The element is captured with Playwright's compositor screenshot, not ``canvas.toDataURL``:
    it works for 2D and WebGL canvases and any other element, and never trips over a tainted
    canvas. The price is that the PNG is the element as DISPLAYED (CSS size), not the canvas's
    intrinsic bitmap; ``--scale`` compensates for small text.
    """

    try:
        parsed = urlsplit(args.url)
        hostname = parsed.hostname
    except ValueError as exc:
        raise CliFailure(f"URL is malformed: {exc}", exit_code=2) from exc
    if parsed.scheme not in {"http", "https"} or not hostname:
        raise CliFailure("URL must be an absolute http:// or https:// URL", exit_code=2)
    region = _parse_region(args.region) if args.region else None
    try:
        from playwright.sync_api import Error as PlaywrightError
    except ImportError as exc:
        raise CliFailure(
            "the Playwright Python package is not installed",
            hint="install the client bundle for this PWMCP release",
        ) from exc

    try:
        with BrowserLease.connect(
            contract_url=args.contract, label="pwmcp-ocr", timeout_ms=args.timeout_ms
        ) as lease:
            active = _server_identity(getattr(lease, "contract", None))
            options: dict[str, object] = {"ignore_https_errors": args.ignore_https_errors}
            if active.user_agent is not None:
                options["user_agent"] = active.user_agent
            context = lease.browser.new_context(**options)
            try:
                page = context.new_page()
                if active.user_agent is not None:
                    _apply_client_hints(context, page, active.user_agent, runtime)
                page.goto(args.url, wait_until="load", timeout=args.timeout_ms)
                target = page.locator(args.selector).filter(visible=True).first
                target.wait_for(state="visible", timeout=args.timeout_ms)
                if args.wait_ms:
                    page.wait_for_timeout(args.wait_ms)
                if region is None:
                    return target.screenshot(type="png", timeout=args.timeout_ms)
                target.scroll_into_view_if_needed(timeout=args.timeout_ms)
                box = target.bounding_box(timeout=args.timeout_ms)
                if box is None or region[0] + region[2] > box["width"] or region[1] + region[3] > box["height"]:
                    size = "not rendered" if box is None else f"{box['width']:g}x{box['height']:g}"
                    raise CliFailure(
                        f"--region {args.region} does not fit inside the element ({size})", exit_code=2
                    )
                clip = {"x": box["x"] + region[0], "y": box["y"] + region[1], "width": region[2], "height": region[3]}
                return page.screenshot(type="png", clip=clip, timeout=args.timeout_ms)
            finally:
                context.close()
    except (PwmcpError, PlaywrightError, OSError, ValueError) as exc:
        raise CliFailure(
            f"pwmcp ocr could not capture the element: {exc}",
            hint="check that PWMCP is running, the selector matches a visible element and the page loads",
        ) from exc


def _ocr_image_bytes(args: argparse.Namespace) -> bytes:
    if args.image == "-":
        data = sys.stdin.buffer.read()
        source = "standard input"
    else:
        path = Path(args.image).expanduser()
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise CliFailure(f"cannot read image {path}: {exc}") from exc
        source = str(path)
    if not data:
        raise CliFailure(f"the image from {source} is empty")
    return data


def _ocr(args: argparse.Namespace, runtime: CliRuntime) -> None:
    """Recognise text in an image (a file/stdin, or an element captured from a page)."""

    if bool(args.url) == bool(args.image):
        raise CliFailure("give exactly one of an image (--image PATH|-) or a page URL (with --selector)",
                         exit_code=2, show_help=True)
    if args.url and not args.selector:
        raise CliFailure("--selector is required when a URL is given", exit_code=2)
    if args.image and (args.selector or args.region or args.wait_ms):
        raise CliFailure("--selector, --region and --wait-ms apply only to a page URL, not --image", exit_code=2)
    if args.timeout_ms <= 0 or args.wait_ms < 0:
        raise CliFailure("--timeout-ms must be positive and --wait-ms must not be negative", exit_code=2)
    fmt = args.format
    if runtime.json_mode:
        if fmt == "tsv":
            raise CliFailure("--json and --format tsv contradict each other", exit_code=2)
        fmt = "json"

    # Resolve the sidecar BEFORE any capture, so a missing configuration fails fast.
    contract = None
    problem = ""
    discovered = not (args.sidecar_url or os.environ.get(ocr_module.OCR_URL_ENV))
    if discovered:
        contract, problem = try_load_contract(args.contract)
        if contract is None:
            # Distinct from "the server does not advertise OCR": here we do not know either way.
            raise CliFailure(
                f"cannot reach the pwmcp server to learn the OCR sidecar URL: {problem}",
                hint=f"start the pwmcp stack, fix --contract, or skip discovery with --sidecar-url / {ocr_module.OCR_URL_ENV}",
            )
    try:
        sidecar_url = ocr_module.resolve_sidecar_url(args.sidecar_url, contract=contract)
    except ocr_module.OcrError as exc:
        hint = "set pwmcp.ocr.enabled = true in the stack config" if isinstance(exc, ocr_module.OcrNotConfigured) else None
        raise CliFailure(str(exc), exit_code=2, hint=hint) from exc

    image = _capture_element(args, runtime) if args.url else _ocr_image_bytes(args)
    try:
        result = ocr_module.recognize(
            image,
            sidecar_url=sidecar_url,
            engine=args.engine,
            lang=args.lang,
            psm=args.psm,
            scale=args.scale,
            whitelist=args.whitelist,
            min_conf=args.min_conf,
            timeout=args.timeout_ms / 1000,
        )
    except ocr_module.OcrError as exc:
        raise CliFailure(f"pwmcp ocr failed: {exc}", hint="GET <sidecar>/v1/engines lists what the sidecar offers") from exc

    if result.completeness_unknown and args.min_conf is not None:
        runtime.output.warn(
            "the OCR sidecar predates the low_conf contract: it may have silently dropped the words below "
            f"--min-conf {args.min_conf:g}, so this result cannot be called complete; upgrade the sidecar"
        )
    if result.low_conf_words:
        runtime.output.warn(
            # (OcrResult.from_dict refuses flags without a threshold, so min_conf_applied is a number here)
            f"{result.low_conf_words} word(s) are below --min-conf {result.min_conf_applied:g} and flagged "
            "low_conf; they are still included in the result"
        )
    payload: object
    if fmt == "json":
        payload = result.to_dict()
    elif fmt == "tsv":
        payload = result.to_tsv()
    else:
        payload = result.text
    if args.output:
        output_path = Path(args.output).expanduser()
        text = json.dumps(payload, indent=2) if fmt == "json" else str(payload)
        try:
            output_path.write_text(text.rstrip("\n") + "\n", encoding="utf-8")
        except OSError as exc:
            raise CliFailure(f"cannot write OCR output to {output_path}: {exc}") from exc
        runtime.output.info(f"wrote {fmt} output to {output_path}")
        return
    if fmt == "json":
        runtime.output.primary_json(payload)
    else:
        runtime.output.primary(payload)


def _eval(args: argparse.Namespace, runtime: CliRuntime) -> None:
    """Navigate through PWMCP MCP and print the browser_evaluate value as JSON."""

    try:
        parsed = urlsplit(args.url)
    except ValueError as exc:
        raise CliFailure(f"URL is malformed: {exc}") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise CliFailure("URL must be an absolute http:// or https:// URL")
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        raise CliFailure("--timeout must be a positive number of seconds")
    if not 1 <= args.max_bytes <= _MAX_EVAL_BYTES:
        raise CliFailure(f"--max-bytes must be between 1 and {_MAX_EVAL_BYTES}")
    try:
        function = evaluation_function(
            args.expression,
            selector=args.selector,
            attribute=args.attr,
            property_name=args.property,
            all_matches=args.all_matches,
            max_bytes=args.max_bytes,
        )
    except ValueError as exc:
        raise CliFailure(str(exc)) from exc

    try:
        response_limit = args.max_bytes * 12 + 64 * 1024
        with StreamableHttpClient(timeout=args.timeout, max_response_bytes=response_limit) as client:
            client.call_tool("browser_navigate", {"url": args.url})
            page_diagnostics = decode_tool_value(
                client.call_tool("browser_evaluate", {"function": page_diagnostics_function()})
            )
            if not isinstance(page_diagnostics, dict):
                raise McpError("MCP page diagnostics returned a non-object result")
            status_value = page_diagnostics.get("status")
            if status_value is not None and (
                not isinstance(status_value, int) or isinstance(status_value, bool)
            ):
                raise McpError("MCP page diagnostics returned an invalid HTTP status")
            evaluated = client.call_tool("browser_evaluate", {"function": function})
            value = decode_tool_value(evaluated)
    except McpError as exc:
        raise CliFailure(f"pwmcp eval failed: {exc}", hint=_failure_hint(exc)) from exc
    status = status_value
    http_error = _is_http_error(status)
    diagnostics = [f"main document HTTP {status}"] if http_error else []
    if http_error:
        runtime.output.emit(
            LogLevel.WARN,
            f"page diagnostics: main document HTTP {status} (the page may not have rendered completely)",
            force=True,
        )
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, dict) and value.get(_EVAL_RESULT_MARKER) is True:
        if value.get("truncated") is True:
            _print_eval_truncation(
                str(value.get("preview", "")),
                args.max_bytes,
                runtime,
                status=status,
                diagnostics=diagnostics,
            )
            _raise_http_error(status, allow=args.allow_http_error)
            return
        value = value.get("value")
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    encoded_bytes = encoded.encode("utf-8", errors="backslashreplace")
    if len(encoded_bytes) <= args.max_bytes:
        if runtime.json_mode:
            runtime.output.primary({
                "content": value,
                "diagnostics": diagnostics,
                "format": "eval",
                "http_error": http_error,
                "http_status": status,
            })
        else:
            runtime.output.primary_json(value)
        _raise_http_error(status, allow=args.allow_http_error)
        return
    preview = encoded_bytes[: args.max_bytes].decode("utf-8", errors="ignore")
    _print_eval_truncation(
        preview,
        args.max_bytes,
        runtime,
        status=status,
        diagnostics=diagnostics,
    )
    _raise_http_error(status, allow=args.allow_http_error)


def build_cli(cli_identity: CliIdentity | None = None) -> RegisteredCli:
    """Build the registered CLI; import-safe (nothing runs until ``.run()``)."""

    registry = CliRegistry(
        cli_identity or identity(),
        prog="pwmcp",
        description="Diagnose and inspect the pwmcp Playwright service from a consumer container.",
        getting_started=(
            "pwmcp doctor",
            "pwmcp contract",
            "pwmcp inspect https://example.com --format snapshot",
            'pwmcp eval https://example.com "document.title"',
            "pwmcp ocr --image console.png",
        ),
        global_options=(
            OptionSpec(
                ("--contract",),
                "pwmcp contract URL or JSON file",
                group="CONTRACT",
                metavar="URL_OR_FILE",
                parser_kwargs={"default": DEFAULT_CONTRACT},
            ),
        ),
        unexpected_exceptions="report",
    )
    registry.register(VerbSpec(
        "eval",
        description="evaluate JavaScript in a page through PWMCP's browser_evaluate MCP tool and print JSON",
        group=VerbGroup.EXPLORATION.value,
        examples=(
            'pwmcp eval https://example.com "document.title"',
            'pwmcp eval https://example.com --selector "meta[name=description]" --attr content',
            'pwmcp eval https://example.com --selector "script[type=application/json]" --property textContent',
            'pwmcp eval https://example.com --selector ".card" --property textContent --all --timeout 60',
            'pwmcp eval https://example.com "document.body.innerText" --max-bytes 262144',
        ),
        include_progress=False,
        arguments=(
            ArgumentSpec(
                "url",
                "absolute HTTP or HTTPS page URL to open in the remote MCP browser",
                metavar="URL",
            ),
            ArgumentSpec(
                "expression",
                "JavaScript expression or function; omit when reading an element attribute or property",
                metavar="EXPRESSION",
                parser_kwargs={"nargs": "?", "default": None},
            ),
        ),
        options=(
            OptionSpec(("--selector",), "CSS selector for an element attribute or property read", group="ELEMENT", metavar="CSS"),
            OptionSpec(("--attr",), "read this attribute from the selected element(s)", group="ELEMENT", metavar="NAME"),
            OptionSpec(("--property",), "read this property from the selected element(s)", group="ELEMENT", metavar="NAME"),
            OptionSpec(
                ("--all",),
                "return a JSON array for every matching element instead of the first match",
                group="ELEMENT",
                parser_kwargs={"action": "store_true", "dest": "all_matches"},
            ),
            OptionSpec(
                ("--timeout",),
                "maximum time per MCP request for navigation and evaluation in seconds",
                group="WAIT",
                metavar="SECONDS",
                parser_kwargs={"type": float, "default": 30.0},
            ),
            OptionSpec(
                ("--max-bytes",),
                f"maximum encoded result bytes (1 to {_MAX_EVAL_BYTES}; truncation is identified)",
                group="OUTPUT",
                metavar="BYTES",
                parser_kwargs={"type": int, "default": _DEFAULT_MAX_EVAL_BYTES},
            ),
            OptionSpec(
                ("--allow-http-error",),
                "return a main-document 4xx/5xx body with exit 0 instead of exit 5",
                group="OUTPUT",
                parser_kwargs={"action": "store_true"},
            ),
        ),
        handler=_eval,
    ))
    registry.register(VerbSpec(
        "contract",
        description="print the pwmcp server contract (release, Playwright version, endpoints, limits) as JSON",
        group=VerbGroup.EXPLORATION.value,
        examples=("pwmcp contract", "pwmcp contract --contract ./pwmcp.contract.json"),
        include_progress=False,
        handler=_contract,
    ))
    registry.register(VerbSpec(
        "versions",
        description="print the client version and, when reachable, the server release plus their skew verdict",
        group=VerbGroup.EXPLORATION.value,
        examples=("pwmcp versions", "pwmcp versions --contract ./pwmcp.contract.json"),
        include_progress=False,
        handler=_versions,
    ))
    registry.register(VerbSpec(
        "inspect",
        description="inspect a rendered webpage through the remote PWMCP browser; exits 4 for bot protection and "
        "5 for a main-document HTTP 4xx/5xx",
        group=VerbGroup.EXPLORATION.value,
        examples=(
            "pwmcp inspect https://example.com",
            "pwmcp inspect https://example.com --format snapshot --output page.yml",
            "pwmcp inspect https://example.com --format html --selector main --html-mode outer",
            "pwmcp inspect https://example.com --format markdown --main",
            "pwmcp inspect https://example.com --format links --main",
            "pwmcp inspect https://example.com --format screenshot --output page.png --full-page",
            "pwmcp inspect https://example.com --wait-for '#content' --wait-until domcontentloaded",
            "pwmcp inspect https://example.com --wait-for 'h1' --timeout-ms 45000 --retries 3",
            "pwmcp inspect https://self-signed.example --ignore-https-errors",
            "pwmcp inspect https://example.com --user-agent edge-windows",
            "pwmcp inspect https://example.com --storage-state ./example.state.json",
            "pwmcp inspect https://example.com/optional --allow-http-error",
        ),
        include_progress=False,
        arguments=(ArgumentSpec(
            "url",
            "absolute HTTP or HTTPS page URL to open in the remote browser",
            metavar="URL",
        ),),
        options=(
            OptionSpec(
                ("--format",),
                "output text, an ARIA snapshot, bounded HTML, Markdown, links, or a PNG/JPEG screenshot",
                group="OUTPUT",
                metavar="FORMAT",
                parser_kwargs={
                    "choices": ("text", "snapshot", "html", "markdown", "links", "screenshot"),
                    "default": "text",
                },
            ),
            OptionSpec(
                ("--selector",),
                "CSS selector whose rendered text, Markdown, links, or HTML to return",
                group="OUTPUT",
                metavar="CSS",
            ),
            OptionSpec(
                ("--main",),
                "extract the first main, [role=main], or article region for text, Markdown, or links",
                group="OUTPUT",
                parser_kwargs={"action": "store_true"},
            ),
            OptionSpec(
                ("--html-mode",),
                "return outerHTML or innerHTML when --format html is selected",
                group="OUTPUT",
                metavar="MODE",
                parser_kwargs={"choices": ("outer", "inner"), "default": "outer"},
            ),
            OptionSpec(
                ("--max-html-bytes",),
                f"maximum HTML bytes returned (1 to {_MAX_HTML_BYTES}; truncation is identified)",
                group="OUTPUT",
                metavar="BYTES",
                parser_kwargs={"type": int, "default": _MAX_HTML_BYTES},
            ),
            OptionSpec(
                ("--output",),
                "write the result to a file instead of stdout (required for screenshots)",
                group="OUTPUT",
                metavar="PATH",
            ),
            OptionSpec(
                ("--wait-until",),
                "navigation condition before inspection",
                group="WAIT",
                metavar="STATE",
                parser_kwargs={
                    "choices": ("commit", "domcontentloaded", "load", "networkidle"),
                    "default": "load",
                },
            ),
            OptionSpec(
                ("--wait-for",),
                "CSS selector that must become visible before inspection",
                group="WAIT",
                metavar="SELECTOR",
            ),
            OptionSpec(
                ("--timeout-ms",),
                "navigation and selector timeout in milliseconds",
                group="WAIT",
                metavar="MILLISECONDS",
                parser_kwargs={"type": int, "default": 30_000},
            ),
            OptionSpec(
                ("--retries",),
                "retry up to N more times (fresh navigation each) after a failure or unstable page; "
                "by default only a recognised browser crash is retried once; --retries 0 disables it",
                group="WAIT",
                metavar="N",
                parser_kwargs={"type": int, "default": None},
            ),
            OptionSpec(
                ("--full-page",),
                "capture the full scrollable page when taking a screenshot",
                group="OUTPUT",
                parser_kwargs={"action": "store_true"},
            ),
            OptionSpec(
                ("--user-agent",),
                "present this user agent for this inspection only: a preset "
                "(edge-windows, chrome-windows, firefox-windows), 'default' for the browser's "
                "own, or a custom printable-ASCII string; overrides the server-level setting",
                group="IDENTITY",
                metavar="PRESET_OR_STRING",
            ),
            OptionSpec(
                ("--storage-state",),
                "reuse a session a HUMAN exported from their own browser (a Playwright storage-state "
                "JSON file, mode 0600); only the cookies and origins of the URL's host are applied, "
                "values are never printed. For pages behind bot protection that a human has cleared",
                group="IDENTITY",
                metavar="FILE",
            ),
            OptionSpec(
                ("--ignore-https-errors",),
                "accept invalid HTTPS certificates for any host in this inspection context",
                group="TLS",
                parser_kwargs={"action": "store_true"},
            ),
            OptionSpec(
                ("--allow-http-error",),
                "return a main-document 4xx/5xx body with exit 0 instead of exit 5",
                group="OUTPUT",
                parser_kwargs={"action": "store_true"},
            ),
        ),
        handler=_inspect,
    ))
    registry.register(VerbSpec(
        "ocr",
        description="recognise text in an image (a screenshot file, stdin, or a page element) through the OCR sidecar",
        group=VerbGroup.EXPLORATION.value,
        examples=(
            "pwmcp ocr --image console.png",
            "pwmcp ocr --image console.png --format tsv --min-conf 60",
            "cat console.png | pwmcp ocr --image - --json",
            "pwmcp ocr https://scp.example/console --selector canvas --scale 2 --output screen.txt",
            "pwmcp ocr https://scp.example/console --selector canvas --region 0,0,640,200 --wait-ms 1000",
        ),
        include_progress=False,
        arguments=(ArgumentSpec(
            "url",
            "page URL to open in the remote browser and capture an element from (needs --selector); "
            "omit when using --image",
            metavar="URL",
            parser_kwargs={"nargs": "?", "default": None},
        ),),
        options=(
            OptionSpec(
                ("--image",),
                "recognise this local PNG/JPEG file, or '-' for standard input (the usual path: save a "
                "screenshot with the MCP browser_take_screenshot tool, then OCR it)",
                group="INPUT",
                metavar="PATH",
            ),
            OptionSpec(
                ("--selector",),
                "CSS selector of the element (e.g. a console canvas) to capture from the page",
                group="INPUT",
                metavar="CSS",
            ),
            OptionSpec(
                ("--region",),
                "capture only this rectangle of the element: x,y,w,h in pixels, relative to the element",
                group="INPUT",
                metavar="x,y,w,h",
            ),
            OptionSpec(
                ("--wait-ms",),
                "wait this long after the element is visible, before capturing it",
                group="WAIT",
                metavar="MILLISECONDS",
                parser_kwargs={"type": int, "default": 0},
            ),
            OptionSpec(
                ("--timeout-ms",),
                "navigation, capture and sidecar timeout in milliseconds",
                group="WAIT",
                metavar="MILLISECONDS",
                parser_kwargs={"type": int, "default": 30_000},
            ),
            OptionSpec(
                ("--engine",),
                "recognition engine (the sidecar lists them at /v1/engines)",
                group="RECOGNITION",
                metavar="NAME",
                parser_kwargs={"default": ocr_module.DEFAULT_ENGINE},
            ),
            OptionSpec(
                ("--lang",),
                "Tesseract language code(s), e.g. eng or eng+deu",
                group="RECOGNITION",
                metavar="LANG",
                parser_kwargs={"default": "eng"},
            ),
            OptionSpec(
                ("--psm",),
                "page segmentation mode: 3 auto, 4 columns, 6 uniform block (default; consoles), "
                "7 single line, 11 sparse text, 13 raw line",
                group="RECOGNITION",
                metavar="MODE",
                parser_kwargs={"type": int, "choices": (3, 4, 6, 7, 11, 13), "default": 6},
            ),
            OptionSpec(
                ("--scale",),
                "upscale the image by this integer factor before recognition (1-8); Tesseract wants "
                "glyphs of about 30 px or more; reported boxes stay in original pixels",
                group="RECOGNITION",
                metavar="FACTOR",
                parser_kwargs={"type": int, "default": 1},
            ),
            OptionSpec(
                ("--whitelist",),
                "only recognise these characters (printable ASCII, at most 128)",
                group="RECOGNITION",
                metavar="CHARS",
            ),
            OptionSpec(
                ("--min-conf",),
                "flag words whose confidence (0-100) is below N as low_conf; they are still returned, never removed. "
                "Without it nothing is assessed (low_conf is null, not false). Tesseract confidence is NOT calibrated: "
                "a 91 percent mean confidence read a garbled dialog and 10.0.2.15 as 16.0.2.15; a flag is a hint, never a safety "
                "check, and acting on text needs an EXACT match of a known screen signature",
                group="RECOGNITION",
                metavar="CONF",
                parser_kwargs={"type": float, "default": None},
            ),
            OptionSpec(
                ("--sidecar-url",),
                "OCR sidecar base URL; default: $PWMCP_OCR_URL, else the ocr_url the pwmcp contract "
                "advertises (set ocr.enabled on the server)",
                group="SIDECAR",
                metavar="URL",
            ),
            OptionSpec(
                ("--format",),
                "text (the complete recognised text only), json (full result) or tsv (one row per word, with a low_conf column); --json implies json",
                group="OUTPUT",
                metavar="FORMAT",
                parser_kwargs={"choices": ("text", "json", "tsv"), "default": "text"},
            ),
            OptionSpec(
                ("--output",),
                "write the result to a file instead of stdout",
                group="OUTPUT",
                metavar="PATH",
            ),
            OptionSpec(
                ("--ignore-https-errors",),
                "accept invalid HTTPS certificates when opening the page (URL mode only)",
                group="TLS",
                parser_kwargs={"action": "store_true"},
            ),
        ),
        handler=_ocr,
    ))
    register_doctor(
        registry,
        DOCTOR_CHECKS,
        description="check that the pwmcp contract is reachable and the installed Playwright matches it",
    )
    register_skills_verbs(registry, package="pwmcp_client")
    return registry.build()


def main(argv: list[str] | None = None) -> int:
    try:
        cli = build_cli()
    except VersionLookupError as exc:
        print(f"[ERROR] cannot determine the pwmcp-client version: {exc}", file=sys.stderr)
        return EXIT_NOT_INSTALLED
    return cli.run(argv=argv)


if __name__ == "__main__":
    raise SystemExit(main())
