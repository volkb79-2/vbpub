#!/usr/bin/env python3
"""
MCP Server for Playwright Browser Automation

Provides Model Context Protocol (MCP) interface to Playwright for AI agent exploration.
"""

import logging
import os
from pathlib import Path
from typing import Optional

from mcp.server import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from playwright.async_api import async_playwright, Browser, Page

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration from environment
MCP_PORT = int(os.getenv('MCP_PORT', '8765'))
MCP_SERVER_NAME = os.getenv('MCP_SERVER_NAME', 'playwright-mcp')
PLAYWRIGHT_HEADLESS = os.getenv('PLAYWRIGHT_HEADLESS', 'true').lower() == 'true'
PLAYWRIGHT_BROWSER = os.getenv('PLAYWRIGHT_BROWSER', 'chromium')
PLAYWRIGHT_CHROMIUM_CHANNEL = os.getenv('PLAYWRIGHT_CHROMIUM_CHANNEL', '').strip()
PLAYWRIGHT_CHROMIUM_EXECUTABLE = os.getenv('PLAYWRIGHT_CHROMIUM_EXECUTABLE', '').strip()

ACCESS_TOKEN = os.getenv('ACCESS_TOKEN', '')
MCP_AUTH_TOKEN = os.getenv('MCP_AUTH_TOKEN', '')
AUTH_REQUIRED = os.getenv('AUTH_REQUIRED', 'true').lower() == 'true'

ALLOWED_HOSTS = [h.strip() for h in os.getenv('MCP_ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',') if h.strip()]
ALLOWED_ORIGINS = [o.strip() for o in os.getenv('MCP_ALLOWED_ORIGINS', 'http://localhost,http://127.0.0.1').split(',') if o.strip()]

# Configure transport security to allow explicit hosts/origins
transport_security = TransportSecuritySettings(
    enable_dns_rebinding_protection=True,
    allowed_hosts=[*ALLOWED_HOSTS, f"localhost:{MCP_PORT}", f"127.0.0.1:{MCP_PORT}"],
    allowed_origins=ALLOWED_ORIGINS,
)

mcp = FastMCP(
    MCP_SERVER_NAME,
    host="0.0.0.0",
    port=MCP_PORT,
    transport_security=transport_security,
)

# Global browser state
_browser: Optional[Browser] = None
_page: Optional[Page] = None
_playwright = None


def _resolve_mcp_token() -> str:
    if MCP_AUTH_TOKEN:
        return MCP_AUTH_TOKEN
    return ACCESS_TOKEN


class AuthMiddleware:
    def __init__(self, app, required: bool, token: str) -> None:
        self.app = app
        self.required = required
        self.token = token

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not self.required:
            await self.app(scope, receive, send)
            return

        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        auth_header = headers.get("authorization", "")
        expected = f"Bearer {self.token}"

        if not self.token or auth_header != expected:
            await send({
                "type": "http.response.start",
                "status": 401,
                "headers": [(b"content-type", b"application/json")],
            })
            await send({
                "type": "http.response.body",
                "body": b'{"error":"unauthorized"}',
            })
            return

        await self.app(scope, receive, send)


async def ensure_browser():
    global _browser, _page, _playwright

    if _browser is None or not _browser.is_connected():
        logger.info("Initializing %s browser (headless=%s)", PLAYWRIGHT_BROWSER, PLAYWRIGHT_HEADLESS)
        _playwright = await async_playwright().start()

        if PLAYWRIGHT_BROWSER == 'chromium':
            launch_kwargs = {"headless": PLAYWRIGHT_HEADLESS}
            if PLAYWRIGHT_CHROMIUM_EXECUTABLE:
                launch_kwargs["executable_path"] = PLAYWRIGHT_CHROMIUM_EXECUTABLE
            elif PLAYWRIGHT_CHROMIUM_CHANNEL:
                launch_kwargs["channel"] = PLAYWRIGHT_CHROMIUM_CHANNEL
            _browser = await _playwright.chromium.launch(**launch_kwargs)
        elif PLAYWRIGHT_BROWSER == 'firefox':
            _browser = await _playwright.firefox.launch(headless=PLAYWRIGHT_HEADLESS)
        elif PLAYWRIGHT_BROWSER == 'webkit':
            _browser = await _playwright.webkit.launch(headless=PLAYWRIGHT_HEADLESS)
        else:
            raise ValueError(f"Unsupported browser: {PLAYWRIGHT_BROWSER}")

        _page = await _browser.new_page()
        logger.info("Browser initialized successfully")

    return _browser, _page


@mcp.tool()
async def navigate(url: str) -> dict:
    try:
        _, page = await ensure_browser()
        await page.goto(url, wait_until="networkidle", timeout=30000)

        return {
            "success": True,
            "url": page.url,
            "title": await page.title(),
        }
    except Exception as e:
        logger.error("Navigation error: %s", e)
        return {"success": False, "error": str(e)}


@mcp.tool()
async def screenshot(path: Optional[str] = None, full_page: bool = True) -> dict:
    try:
        _, page = await ensure_browser()

        if path is None:
            import time
            path = f"mcp-screenshot-{int(time.time())}.png"

        screenshot_path = Path("/screenshots") / Path(path).name
        await page.screenshot(path=str(screenshot_path), full_page=full_page)

        return {
            "success": True,
            "path": str(screenshot_path),
            "url": page.url,
        }
    except Exception as e:
        logger.error("Screenshot error: %s", e)
        return {"success": False, "error": str(e)}


@mcp.tool()
async def get_content() -> dict:
    try:
        _, page = await ensure_browser()
        content = await page.content()

        return {
            "success": True,
            "content": content,
            "url": page.url,
            "title": await page.title(),
        }
    except Exception as e:
        logger.error("Get content error: %s", e)
        return {"success": False, "error": str(e)}


@mcp.tool()
async def click(selector: str) -> dict:
    try:
        _, page = await ensure_browser()
        await page.click(selector, timeout=10000)

        return {"success": True, "selector": selector, "url": page.url}
    except Exception as e:
        logger.error("Click error: %s", e)
        return {"success": False, "error": str(e), "selector": selector}


@mcp.tool()
async def fill(selector: str, value: str) -> dict:
    try:
        _, page = await ensure_browser()
        await page.fill(selector, value, timeout=10000)

        return {"success": True, "selector": selector, "url": page.url}
    except Exception as e:
        logger.error("Fill error: %s", e)
        return {"success": False, "error": str(e), "selector": selector}


@mcp.tool()
async def evaluate(script: str) -> dict:
    try:
        _, page = await ensure_browser()
        result = await page.evaluate(script)

        return {"success": True, "result": result, "url": page.url}
    except Exception as e:
        logger.error("Evaluate error: %s", e)
        return {"success": False, "error": str(e)}


@mcp.tool()
async def get_url() -> dict:
    try:
        _, page = await ensure_browser()
        return {"success": True, "url": page.url, "title": await page.title()}
    except Exception as e:
        logger.error("Get URL error: %s", e)
        return {"success": False, "error": str(e)}


def build_asgi_app():
    token = _resolve_mcp_token()
    if AUTH_REQUIRED and not token:
        raise ValueError("AUTH_REQUIRED=true but no ACCESS_TOKEN/MCP_AUTH_TOKEN provided")

    app = mcp.streamable_http_app()
    return AuthMiddleware(app, AUTH_REQUIRED, token)


if __name__ == "__main__":
    import uvicorn

    logger.info("Starting MCP server on 0.0.0.0:%s", MCP_PORT)
    logger.info("Server name: %s", MCP_SERVER_NAME)
    logger.info("Browser: %s (headless=%s)", PLAYWRIGHT_BROWSER, PLAYWRIGHT_HEADLESS)
    logger.info("Allowed hosts: %s", transport_security.allowed_hosts)

    app = build_asgi_app()
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=MCP_PORT,
        log_level="info",
    )
