#!/usr/bin/env python3
"""
CLI for Playwright MCP WebSocket client.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from typing import Optional

from pwmcp_client.client import PlaywrightWSClient

logger = logging.getLogger(__name__)


def _configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )


def _default_ws_url() -> str:
    return os.getenv('WS_URL', 'ws://localhost:3000')


def _default_token() -> Optional[str]:
    return os.getenv('WS_AUTH_TOKEN') or os.getenv('ACCESS_TOKEN')


async def _ws_ping(url: str, token: Optional[str], timeout: float) -> int:
    async with PlaywrightWSClient(url=url, auth_token=token, timeout=timeout):
        logger.info("WS ping successful")
    return 0


async def _ws_navigate(url: str, token: Optional[str], timeout: float, page: str, wait_until: str, nav_timeout: int) -> int:
    async with PlaywrightWSClient(url=url, auth_token=token, timeout=timeout) as client:
        result = await client.navigate(page, wait_until=wait_until, timeout=nav_timeout)
        print(json.dumps(result, indent=2))
    return 0


async def _ws_screenshot(url: str, token: Optional[str], timeout: float, path: Optional[str], full_page: bool) -> int:
    async with PlaywrightWSClient(url=url, auth_token=token, timeout=timeout) as client:
        result = await client.screenshot(path=path, full_page=full_page)
        print(json.dumps(result, indent=2))
    return 0


async def _ws_eval(url: str, token: Optional[str], timeout: float, script: str) -> int:
    async with PlaywrightWSClient(url=url, auth_token=token, timeout=timeout) as client:
        result = await client.evaluate(script)
        print(json.dumps(result, indent=2))
    return 0


async def _ws_health(url: str, token: Optional[str], timeout: float) -> int:
    async with PlaywrightWSClient(url=url, auth_token=token, timeout=timeout) as client:
        result = await client.health()
        print(json.dumps(result, indent=2))
    return 0


async def _ws_console_logs(url: str, token: Optional[str], timeout: float) -> int:
    async with PlaywrightWSClient(url=url, auth_token=token, timeout=timeout) as client:
        result = await client.get_console_logs()
        print(json.dumps(result, indent=2))
    return 0


async def _ws_console_clear(url: str, token: Optional[str], timeout: float) -> int:
    async with PlaywrightWSClient(url=url, auth_token=token, timeout=timeout) as client:
        result = await client.clear_console_logs()
        print(json.dumps(result, indent=2))
    return 0


async def _ws_trace_start(url: str, token: Optional[str], timeout: float) -> int:
    async with PlaywrightWSClient(url=url, auth_token=token, timeout=timeout) as client:
        result = await client.start_tracing()
        print(json.dumps(result, indent=2))
    return 0


async def _ws_trace_stop(url: str, token: Optional[str], timeout: float, path: Optional[str]) -> int:
    async with PlaywrightWSClient(url=url, auth_token=token, timeout=timeout) as client:
        result = await client.stop_tracing(path=path)
        print(json.dumps(result, indent=2))
    return 0


async def _ws_export_state(url: str, token: Optional[str], timeout: float, path: Optional[str]) -> int:
    async with PlaywrightWSClient(url=url, auth_token=token, timeout=timeout) as client:
        result = await client.export_storage_state(path=path)
        print(json.dumps(result, indent=2))
    return 0


async def _ws_import_state(url: str, token: Optional[str], timeout: float, path: Optional[str]) -> int:
    async with PlaywrightWSClient(url=url, auth_token=token, timeout=timeout) as client:
        result = await client.import_storage_state(path=path)
        print(json.dumps(result, indent=2))
    return 0


async def _ws_video_path(url: str, token: Optional[str], timeout: float) -> int:
    async with PlaywrightWSClient(url=url, auth_token=token, timeout=timeout) as client:
        result = await client.get_video_path()
        print(json.dumps(result, indent=2))
    return 0


async def _ws_login(
    url: str,
    token: Optional[str],
    timeout: float,
    login_url: str,
    username: str,
    password: str,
    username_selector: str,
    password_selector: str,
    submit_selector: str,
    success_url_pattern: Optional[str],
) -> int:
    async with PlaywrightWSClient(url=url, auth_token=token, timeout=timeout) as client:
        result = await client.login(
            url=login_url,
            username=username,
            password=password,
            username_selector=username_selector,
            password_selector=password_selector,
            submit_selector=submit_selector,
            success_url_pattern=success_url_pattern,
        )
        print(json.dumps(result, indent=2))
    return 0


def _add_ws_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--url", default=_default_ws_url(), help="WebSocket URL")
    parser.add_argument("--token", default=_default_token(), help="Auth token")
    parser.add_argument("--timeout", type=float, default=30.0, help="Client timeout seconds")


def main() -> int:
    parser = argparse.ArgumentParser(prog="pwmcp", description="Playwright MCP WebSocket CLI")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")

    subparsers = parser.add_subparsers(dest="command", required=True)

    ws_parser = subparsers.add_parser("ws", help="WebSocket operations")
    ws_sub = ws_parser.add_subparsers(dest="ws_command", required=True)

    ws_ping = ws_sub.add_parser("ping", help="Connect and disconnect")
    _add_ws_common_args(ws_ping)

    ws_nav = ws_sub.add_parser("navigate", help="Navigate to a URL")
    _add_ws_common_args(ws_nav)
    ws_nav.add_argument("--page", required=True, help="Target URL")
    ws_nav.add_argument("--wait-until", default="networkidle", help="Playwright wait_until")
    ws_nav.add_argument("--nav-timeout", type=int, default=30000, help="Navigation timeout ms")

    ws_shot = ws_sub.add_parser("screenshot", help="Take a screenshot")
    _add_ws_common_args(ws_shot)
    ws_shot.add_argument("--path", help="Output path (optional)")
    ws_shot.add_argument("--full-page", action="store_true", help="Capture full page")

    ws_eval = ws_sub.add_parser("eval", help="Evaluate JS in page")
    _add_ws_common_args(ws_eval)
    ws_eval.add_argument("--script", required=True, help="JavaScript expression")

    ws_health = ws_sub.add_parser("health", help="Fetch server health")
    _add_ws_common_args(ws_health)

    ws_login = ws_sub.add_parser("login", help="Login via form")
    _add_ws_common_args(ws_login)
    ws_login.add_argument("--login-url", required=True, help="Login URL")
    ws_login.add_argument("--username", required=True, help="Username")
    ws_login.add_argument("--password", required=True, help="Password")
    ws_login.add_argument("--username-selector", default="#username", help="Username selector")
    ws_login.add_argument("--password-selector", default="#password", help="Password selector")
    ws_login.add_argument("--submit-selector", default="button[type='submit']", help="Submit selector")
    ws_login.add_argument("--success-url-pattern", help="Success URL pattern (optional)")

    ws_console = ws_sub.add_parser("console-logs", help="Fetch console logs")
    _add_ws_common_args(ws_console)

    ws_console_clear = ws_sub.add_parser("console-clear", help="Clear console logs")
    _add_ws_common_args(ws_console_clear)

    ws_trace_start = ws_sub.add_parser("trace-start", help="Start tracing")
    _add_ws_common_args(ws_trace_start)

    ws_trace_stop = ws_sub.add_parser("trace-stop", help="Stop tracing")
    _add_ws_common_args(ws_trace_stop)
    ws_trace_stop.add_argument("--path", help="Output trace filename (optional)")

    ws_export_state = ws_sub.add_parser("state-export", help="Export storage state")
    _add_ws_common_args(ws_export_state)
    ws_export_state.add_argument("--path", help="Output filename (optional)")

    ws_import_state = ws_sub.add_parser("state-import", help="Import storage state")
    _add_ws_common_args(ws_import_state)
    ws_import_state.add_argument("--path", required=True, help="Storage state filename")

    ws_video_path = ws_sub.add_parser("video-path", help="Get recorded video path (if enabled)")
    _add_ws_common_args(ws_video_path)

    args = parser.parse_args()
    _configure_logging(args.verbose)

    if args.command == "ws":
        if args.ws_command == "ping":
            return asyncio.run(_ws_ping(args.url, args.token, args.timeout))
        if args.ws_command == "navigate":
            return asyncio.run(_ws_navigate(args.url, args.token, args.timeout, args.page, args.wait_until, args.nav_timeout))
        if args.ws_command == "screenshot":
            return asyncio.run(_ws_screenshot(args.url, args.token, args.timeout, args.path, args.full_page))
        if args.ws_command == "eval":
            return asyncio.run(_ws_eval(args.url, args.token, args.timeout, args.script))
        if args.ws_command == "health":
            return asyncio.run(_ws_health(args.url, args.token, args.timeout))
        if args.ws_command == "login":
            return asyncio.run(
                _ws_login(
                    args.url,
                    args.token,
                    args.timeout,
                    args.login_url,
                    args.username,
                    args.password,
                    args.username_selector,
                    args.password_selector,
                    args.submit_selector,
                    args.success_url_pattern,
                )
            )
        if args.ws_command == "console-logs":
            return asyncio.run(_ws_console_logs(args.url, args.token, args.timeout))
        if args.ws_command == "console-clear":
            return asyncio.run(_ws_console_clear(args.url, args.token, args.timeout))
        if args.ws_command == "trace-start":
            return asyncio.run(_ws_trace_start(args.url, args.token, args.timeout))
        if args.ws_command == "trace-stop":
            return asyncio.run(_ws_trace_stop(args.url, args.token, args.timeout, args.path))
        if args.ws_command == "state-export":
            return asyncio.run(_ws_export_state(args.url, args.token, args.timeout, args.path))
        if args.ws_command == "state-import":
            return asyncio.run(_ws_import_state(args.url, args.token, args.timeout, args.path))
        if args.ws_command == "video-path":
            return asyncio.run(_ws_video_path(args.url, args.token, args.timeout))

    parser.error("Unknown command")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
