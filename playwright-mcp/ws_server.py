#!/usr/bin/env python3
"""
WebSocket Server for Playwright Browser Automation

Provides a multi-client WebSocket service for browser automation.
Designed for use by multiple projects and remote clients.
"""

import asyncio
import json
import logging
import os
import secrets
import ssl
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import websockets
from websockets.asyncio.server import ServerConnection
from playwright.async_api import async_playwright, Browser, BrowserContext, Page, Playwright

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration from environment
WS_PORT = int(os.getenv('WS_PORT', '3000'))
WS_HOST = os.getenv('WS_HOST', '0.0.0.0')
ACCESS_TOKEN = os.getenv('ACCESS_TOKEN', '')
WS_AUTH_TOKEN = os.getenv('WS_AUTH_TOKEN', '')
AUTH_REQUIRED = os.getenv('AUTH_REQUIRED', 'true').lower() == 'true'
MAX_SESSIONS = int(os.getenv('WS_MAX_SESSIONS', '10'))
SESSION_TIMEOUT = int(os.getenv('WS_SESSION_TIMEOUT', '3600'))
PLAYWRIGHT_HEADLESS = os.getenv('PLAYWRIGHT_HEADLESS', 'true').lower() == 'true'
PLAYWRIGHT_BROWSER = os.getenv('PLAYWRIGHT_BROWSER', 'chromium')
PLAYWRIGHT_VIDEO_DIR = os.getenv('PLAYWRIGHT_VIDEO_DIR', '')
SSL_CERT_PATH = os.getenv('SSL_CERT_PATH', '/app/certs/server.crt')
SSL_KEY_PATH = os.getenv('SSL_KEY_PATH', '/app/certs/server.key')
SSL_ENABLED = os.getenv('SSL_ENABLED', 'false').lower() == 'true'


def _resolve_ws_token() -> str:
    if WS_AUTH_TOKEN:
        return WS_AUTH_TOKEN
    return ACCESS_TOKEN


@dataclass
class BrowserSession:
    session_id: str
    context: BrowserContext
    page: Page
    created_at: float = field(default_factory=time.time)
    last_used: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def touch(self) -> None:
        self.last_used = time.time()

    def is_expired(self, timeout: int) -> bool:
        return (time.time() - self.last_used) > timeout


class PlaywrightWebSocketServer:
    def __init__(self) -> None:
        self.sessions: Dict[str, BrowserSession] = {}
        self.client_sessions: Dict[ServerConnection, str] = {}
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._cleanup_task: Optional[asyncio.Task] = None

        self.handlers: Dict[str, Callable] = {
            'navigate': self._handle_navigate,
            'screenshot': self._handle_screenshot,
            'click': self._handle_click,
            'fill': self._handle_fill,
            'type': self._handle_type,
            'press': self._handle_press,
            'evaluate': self._handle_evaluate,
            'get_content': self._handle_get_content,
            'get_url': self._handle_get_url,
            'wait_for_selector': self._handle_wait_for_selector,
            'wait_for_url': self._handle_wait_for_url,
            'wait_for_load_state': self._handle_wait_for_load_state,
            'select_option': self._handle_select_option,
            'check': self._handle_check,
            'uncheck': self._handle_uncheck,
            'hover': self._handle_hover,
            'focus': self._handle_focus,
            'get_attribute': self._handle_get_attribute,
            'get_text': self._handle_get_text,
            'get_inner_html': self._handle_get_inner_html,
            'get_input_value': self._handle_get_input_value,
            'is_visible': self._handle_is_visible,
            'is_enabled': self._handle_is_enabled,
            'is_checked': self._handle_is_checked,
            'query_selector': self._handle_query_selector,
            'query_selector_all': self._handle_query_selector_all,
            'reload': self._handle_reload,
            'go_back': self._handle_go_back,
            'go_forward': self._handle_go_forward,
            'set_viewport_size': self._handle_set_viewport_size,
            'cookies': self._handle_cookies,
            'set_cookies': self._handle_set_cookies,
            'clear_cookies': self._handle_clear_cookies,
            'close_session': self._handle_close_session,
            'health': self._handle_health,
            'login': self._handle_login,
            'get_console_logs': self._handle_get_console_logs,
            'clear_console_logs': self._handle_clear_console_logs,
            'start_tracing': self._handle_start_tracing,
            'stop_tracing': self._handle_stop_tracing,
            'export_storage_state': self._handle_export_storage_state,
            'import_storage_state': self._handle_import_storage_state,
            'get_video_path': self._handle_get_video_path,
        }

    async def start(self) -> None:
        ws_token = _resolve_ws_token()
        if AUTH_REQUIRED and not ws_token:
            raise ValueError("AUTH_REQUIRED=true but no ACCESS_TOKEN/WS_AUTH_TOKEN provided")

        logger.info(
            "Initializing Playwright (%s, headless=%s)",
            PLAYWRIGHT_BROWSER,
            PLAYWRIGHT_HEADLESS,
        )

        self._playwright = await async_playwright().start()

        if PLAYWRIGHT_BROWSER == 'chromium':
            self._browser = await self._playwright.chromium.launch(headless=PLAYWRIGHT_HEADLESS)
        elif PLAYWRIGHT_BROWSER == 'firefox':
            self._browser = await self._playwright.firefox.launch(headless=PLAYWRIGHT_HEADLESS)
        elif PLAYWRIGHT_BROWSER == 'webkit':
            self._browser = await self._playwright.webkit.launch(headless=PLAYWRIGHT_HEADLESS)
        else:
            raise ValueError(f"Unsupported browser: {PLAYWRIGHT_BROWSER}")

        logger.info("Playwright browser initialized")
        self._cleanup_task = asyncio.create_task(self._cleanup_expired_sessions())

        ssl_context = None
        if SSL_ENABLED:
            ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ssl_context.load_cert_chain(SSL_CERT_PATH, SSL_KEY_PATH)
            logger.info("TLS enabled with cert: %s", SSL_CERT_PATH)

        protocol = "wss" if SSL_ENABLED else "ws"
        logger.info("Starting WebSocket server on %s://%s:%s", protocol, WS_HOST, WS_PORT)

        async with websockets.serve(
            self._handle_connection,
            WS_HOST,
            WS_PORT,
            ssl=ssl_context,
            max_size=10 * 1024 * 1024,
            ping_interval=30,
            ping_timeout=10,
        ):
            logger.info("WebSocket server running on %s://%s:%s", protocol, WS_HOST, WS_PORT)
            await asyncio.Future()

    async def stop(self) -> None:
        logger.info("Shutting down WebSocket server...")

        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass

        for session_id in list(self.sessions.keys()):
            await self._close_session(session_id)

        if self._browser:
            await self._browser.close()

        if self._playwright:
            await self._playwright.stop()

        logger.info("WebSocket server stopped")

    async def _cleanup_expired_sessions(self) -> None:
        while True:
            try:
                await asyncio.sleep(60)
                expired = [
                    sid for sid, session in self.sessions.items()
                    if session.is_expired(SESSION_TIMEOUT)
                ]
                for session_id in expired:
                    logger.info("Cleaning up expired session: %s", session_id)
                    await self._close_session(session_id)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in session cleanup: %s", e)

    async def _handle_connection(self, websocket: ServerConnection) -> None:
        client_id = str(uuid.uuid4())[:8]
        logger.info("New connection from %s (client: %s)", websocket.remote_address, client_id)

        ws_token = _resolve_ws_token()
        if AUTH_REQUIRED:
            try:
                auth_msg = await asyncio.wait_for(websocket.recv(), timeout=10.0)
                auth_data = json.loads(auth_msg)

                if auth_data.get('type') != 'auth' or auth_data.get('token') != ws_token:
                    await websocket.send(json.dumps({
                        'type': 'error',
                        'error': 'Authentication failed'
                    }))
                    await websocket.close(1008, 'Authentication failed')
                    return

                await websocket.send(json.dumps({
                    'type': 'auth_success',
                    'message': 'Authenticated successfully'
                }))
                logger.info("Client %s authenticated", client_id)

            except asyncio.TimeoutError:
                await websocket.close(1008, 'Authentication timeout')
                return
            except Exception as e:
                logger.error("Auth error: %s", e)
                await websocket.close(1008, 'Authentication error')
                return

        try:
            session = await self._create_session()
            self.client_sessions[websocket] = session.session_id

            await websocket.send(json.dumps({
                'type': 'connected',
                'session_id': session.session_id,
                'message': 'Session created successfully'
            }))

            async for message in websocket:
                await self._handle_message(websocket, session.session_id, message)

        except websockets.exceptions.ConnectionClosed:
            logger.info("Client %s disconnected", client_id)
        except Exception as e:
            logger.error("Error handling client %s: %s", client_id, e)
        finally:
            session_id = self.client_sessions.pop(websocket, None)
            if session_id:
                await self._close_session(session_id)
                logger.info("Session %s closed for client %s", session_id, client_id)

    async def _create_session(self) -> BrowserSession:
        if len(self.sessions) >= MAX_SESSIONS:
            raise RuntimeError(f"Maximum sessions ({MAX_SESSIONS}) reached")

        if not self._browser:
            raise RuntimeError("Browser not initialized")

        session_id = f"session_{secrets.token_hex(8)}"
        if PLAYWRIGHT_VIDEO_DIR:
            context = await self._browser.new_context(record_video_dir=PLAYWRIGHT_VIDEO_DIR)
        else:
            context = await self._browser.new_context()
        page = await context.new_page()

        session = BrowserSession(session_id=session_id, context=context, page=page)
        self._attach_console_logger(session)
        session.metadata["tracing_active"] = False
        self.sessions[session_id] = session
        logger.info("Created session: %s", session_id)

        return session

    async def _close_session(self, session_id: str) -> None:
        session = self.sessions.pop(session_id, None)
        if session:
            try:
                await session.context.close()
            except Exception as e:
                logger.error("Error closing session %s: %s", session_id, e)

    def _get_session(self, session_id: str) -> BrowserSession:
        session = self.sessions.get(session_id)
        if not session:
            raise ValueError(f"Session not found: {session_id}")
        session.touch()
        return session

    def _attach_console_logger(self, session: BrowserSession) -> None:
        console_logs: list[dict[str, Any]] = []

        def _console_handler(message) -> None:
            try:
                console_logs.append({
                    "type": message.type,
                    "text": message.text,
                    "location": message.location,
                })
            except Exception as exc:  # pragma: no cover - defensive logging only
                logger.debug("Failed to capture console log: %s", exc)

        session.page.on("console", _console_handler)
        session.metadata["console_logs"] = console_logs

    async def _handle_message(self, websocket: ServerConnection, session_id: str, message: str) -> None:
        try:
            data = json.loads(message)
            msg_id = data.get('id', str(uuid.uuid4())[:8])
            command = data.get('command', '')
            args = data.get('args', {})

            handler = self.handlers.get(command)
            if not handler:
                await websocket.send(json.dumps({
                    'type': 'error',
                    'id': msg_id,
                    'error': f"Unknown command: {command}"
                }))
                return

            result = await handler(session_id, args)

            await websocket.send(json.dumps({
                'type': 'response',
                'id': msg_id,
                'success': True,
                'data': result
            }))

        except json.JSONDecodeError as e:
            await websocket.send(json.dumps({
                'type': 'error',
                'error': f"Invalid JSON: {e}"
            }))
        except Exception as e:
            logger.error("Command error: %s", e)
            msg_id = None
            try:
                msg_id = data.get('id')
            except NameError:
                pass
            await websocket.send(json.dumps({
                'type': 'error',
                'id': msg_id,
                'error': str(e)
            }))

    async def _handle_navigate(self, session_id: str, args: dict) -> dict:
        session = self._get_session(session_id)
        url = args.get('url')
        if not url:
            raise ValueError("url is required")

        wait_until = args.get('wait_until', 'networkidle')
        timeout = args.get('timeout', 30000)

        await session.page.goto(url, wait_until=wait_until, timeout=timeout)

        return {
            'url': session.page.url,
            'title': await session.page.title()
        }

    async def _handle_screenshot(self, session_id: str, args: dict) -> dict:
        session = self._get_session(session_id)

        path = args.get('path')
        if path:
            filename = Path(path).name
            import re
            if not re.match(r'^[\w\-\.]+$', filename):
                raise ValueError(f"Invalid filename: {filename}")
            if '..' in filename or filename.startswith('.'):
                raise ValueError(f"Invalid filename: {filename}")
            screenshot_path = Path("/screenshots") / filename
        else:
            screenshot_path = Path("/screenshots") / f"screenshot_{int(time.time())}.png"

        full_page = args.get('full_page', True)

        await session.page.screenshot(path=str(screenshot_path), full_page=full_page)

        return {
            'path': str(screenshot_path),
            'url': session.page.url
        }

    async def _handle_click(self, session_id: str, args: dict) -> dict:
        session = self._get_session(session_id)
        selector = args.get('selector')
        if not selector:
            raise ValueError("selector is required")

        timeout = args.get('timeout', 10000)
        button = args.get('button', 'left')
        click_count = args.get('click_count', 1)

        await session.page.click(
            selector,
            timeout=timeout,
            button=button,
            click_count=click_count
        )

        return {'clicked': selector, 'url': session.page.url}

    async def _handle_fill(self, session_id: str, args: dict) -> dict:
        session = self._get_session(session_id)
        selector = args.get('selector')
        value = args.get('value', '')
        if not selector:
            raise ValueError("selector is required")

        timeout = args.get('timeout', 10000)

        await session.page.fill(selector, value, timeout=timeout)

        return {'filled': selector, 'url': session.page.url}

    async def _handle_type(self, session_id: str, args: dict) -> dict:
        session = self._get_session(session_id)
        selector = args.get('selector')
        text = args.get('text', '')
        if not selector:
            raise ValueError("selector is required")

        delay = args.get('delay', 0)
        timeout = args.get('timeout', 10000)

        await session.page.locator(selector).press_sequentially(text, delay=delay, timeout=timeout)

        return {'typed': selector, 'url': session.page.url}

    async def _handle_press(self, session_id: str, args: dict) -> dict:
        session = self._get_session(session_id)
        key = args.get('key')
        if not key:
            raise ValueError("key is required")

        selector = args.get('selector')

        if selector:
            await session.page.locator(selector).press(key)
        else:
            await session.page.keyboard.press(key)

        return {'pressed': key, 'url': session.page.url}

    async def _handle_evaluate(self, session_id: str, args: dict) -> dict:
        session = self._get_session(session_id)
        script = args.get('script')
        if not script:
            raise ValueError("script is required")

        result = await session.page.evaluate(script)

        return {'result': result, 'url': session.page.url}

    async def _handle_get_content(self, session_id: str, args: dict) -> dict:
        session = self._get_session(session_id)
        content = await session.page.content()

        return {
            'content': content,
            'url': session.page.url,
            'title': await session.page.title()
        }

    async def _handle_get_url(self, session_id: str, args: dict) -> dict:
        session = self._get_session(session_id)

        return {
            'url': session.page.url,
            'title': await session.page.title()
        }

    async def _handle_wait_for_selector(self, session_id: str, args: dict) -> dict:
        session = self._get_session(session_id)
        selector = args.get('selector')
        if not selector:
            raise ValueError("selector is required")

        state = args.get('state', 'visible')
        timeout = args.get('timeout', 30000)

        await session.page.wait_for_selector(selector, state=state, timeout=timeout)

        return {'found': selector, 'url': session.page.url}

    async def _handle_wait_for_url(self, session_id: str, args: dict) -> dict:
        session = self._get_session(session_id)
        url_pattern = args.get('url')
        if not url_pattern:
            raise ValueError("url is required")

        timeout = args.get('timeout', 30000)

        await session.page.wait_for_url(url_pattern, timeout=timeout)

        return {'url': session.page.url}

    async def _handle_wait_for_load_state(self, session_id: str, args: dict) -> dict:
        session = self._get_session(session_id)
        state = args.get('state', 'networkidle')
        timeout = args.get('timeout', 30000)

        await session.page.wait_for_load_state(state, timeout=timeout)

        return {'state': state, 'url': session.page.url}

    async def _handle_select_option(self, session_id: str, args: dict) -> dict:
        session = self._get_session(session_id)
        selector = args.get('selector')
        if not selector:
            raise ValueError("selector is required")

        value = args.get('value')
        label = args.get('label')
        index = args.get('index')

        if value is not None:
            await session.page.select_option(selector, value=value)
        elif label is not None:
            await session.page.select_option(selector, label=label)
        elif index is not None:
            await session.page.select_option(selector, index=index)
        else:
            raise ValueError("value, label, or index is required")

        return {'selected': selector, 'url': session.page.url}

    async def _handle_check(self, session_id: str, args: dict) -> dict:
        session = self._get_session(session_id)
        selector = args.get('selector')
        if not selector:
            raise ValueError("selector is required")

        await session.page.check(selector)

        return {'checked': selector, 'url': session.page.url}

    async def _handle_uncheck(self, session_id: str, args: dict) -> dict:
        session = self._get_session(session_id)
        selector = args.get('selector')
        if not selector:
            raise ValueError("selector is required")

        await session.page.uncheck(selector)

        return {'unchecked': selector, 'url': session.page.url}

    async def _handle_hover(self, session_id: str, args: dict) -> dict:
        session = self._get_session(session_id)
        selector = args.get('selector')
        if not selector:
            raise ValueError("selector is required")

        await session.page.hover(selector)

        return {'hovered': selector, 'url': session.page.url}

    async def _handle_focus(self, session_id: str, args: dict) -> dict:
        session = self._get_session(session_id)
        selector = args.get('selector')
        if not selector:
            raise ValueError("selector is required")

        await session.page.focus(selector)

        return {'focused': selector, 'url': session.page.url}

    async def _handle_get_attribute(self, session_id: str, args: dict) -> dict:
        session = self._get_session(session_id)
        selector = args.get('selector')
        name = args.get('name')
        if not selector or not name:
            raise ValueError("selector and name are required")

        value = await session.page.get_attribute(selector, name)

        return {'selector': selector, 'attribute': name, 'value': value}

    async def _handle_get_text(self, session_id: str, args: dict) -> dict:
        session = self._get_session(session_id)
        selector = args.get('selector')
        if not selector:
            raise ValueError("selector is required")

        text = await session.page.text_content(selector)

        return {'selector': selector, 'text': text}

    async def _handle_get_inner_html(self, session_id: str, args: dict) -> dict:
        session = self._get_session(session_id)
        selector = args.get('selector')
        if not selector:
            raise ValueError("selector is required")

        html = await session.page.inner_html(selector)

        return {'selector': selector, 'html': html}

    async def _handle_get_input_value(self, session_id: str, args: dict) -> dict:
        session = self._get_session(session_id)
        selector = args.get('selector')
        if not selector:
            raise ValueError("selector is required")

        value = await session.page.input_value(selector)

        return {'selector': selector, 'value': value}

    async def _handle_is_visible(self, session_id: str, args: dict) -> dict:
        session = self._get_session(session_id)
        selector = args.get('selector')
        if not selector:
            raise ValueError("selector is required")

        visible = await session.page.is_visible(selector)

        return {'selector': selector, 'visible': visible}

    async def _handle_is_enabled(self, session_id: str, args: dict) -> dict:
        session = self._get_session(session_id)
        selector = args.get('selector')
        if not selector:
            raise ValueError("selector is required")

        enabled = await session.page.is_enabled(selector)

        return {'selector': selector, 'enabled': enabled}

    async def _handle_is_checked(self, session_id: str, args: dict) -> dict:
        session = self._get_session(session_id)
        selector = args.get('selector')
        if not selector:
            raise ValueError("selector is required")

        checked = await session.page.is_checked(selector)

        return {'selector': selector, 'checked': checked}

    async def _handle_query_selector(self, session_id: str, args: dict) -> dict:
        session = self._get_session(session_id)
        selector = args.get('selector')
        if not selector:
            raise ValueError("selector is required")

        element = await session.page.query_selector(selector)

        return {'selector': selector, 'found': element is not None}

    async def _handle_query_selector_all(self, session_id: str, args: dict) -> dict:
        session = self._get_session(session_id)
        selector = args.get('selector')
        if not selector:
            raise ValueError("selector is required")

        elements = await session.page.query_selector_all(selector)

        return {'selector': selector, 'count': len(elements)}

    async def _handle_reload(self, session_id: str, args: dict) -> dict:
        session = self._get_session(session_id)
        wait_until = args.get('wait_until', 'networkidle')

        await session.page.reload(wait_until=wait_until)

        return {'url': session.page.url}

    async def _handle_go_back(self, session_id: str, args: dict) -> dict:
        session = self._get_session(session_id)

        await session.page.go_back()

        return {'url': session.page.url}

    async def _handle_go_forward(self, session_id: str, args: dict) -> dict:
        session = self._get_session(session_id)

        await session.page.go_forward()

        return {'url': session.page.url}

    async def _handle_set_viewport_size(self, session_id: str, args: dict) -> dict:
        session = self._get_session(session_id)
        width = args.get('width', 1280)
        height = args.get('height', 720)

        await session.page.set_viewport_size({'width': width, 'height': height})

        return {'width': width, 'height': height}

    async def _handle_cookies(self, session_id: str, args: dict) -> dict:
        session = self._get_session(session_id)
        cookies = await session.context.cookies()

        return {'cookies': cookies}

    async def _handle_set_cookies(self, session_id: str, args: dict) -> dict:
        session = self._get_session(session_id)
        cookies = args.get('cookies', [])

        await session.context.add_cookies(cookies)

        return {'set': len(cookies)}

    async def _handle_clear_cookies(self, session_id: str, args: dict) -> dict:
        session = self._get_session(session_id)
        await session.context.clear_cookies()

        return {'cleared': True}

    async def _handle_close_session(self, session_id: str, args: dict) -> dict:
        await self._close_session(session_id)
        return {'closed': session_id}

    async def _handle_health(self, session_id: str, args: dict) -> dict:
        return {
            'status': 'healthy',
            'sessions': len(self.sessions),
            'max_sessions': MAX_SESSIONS,
            'browser': PLAYWRIGHT_BROWSER,
            'headless': PLAYWRIGHT_HEADLESS
        }

    async def _handle_login(self, session_id: str, args: dict) -> dict:
        session = self._get_session(session_id)

        url = args.get('url')
        if not url:
            raise ValueError("url is required")

        username = args.get('username')
        password = args.get('password')
        if not username or not password:
            raise ValueError("username and password are required")

        username_selector = args.get('username_selector', '#username')
        password_selector = args.get('password_selector', '#password')
        submit_selector = args.get('submit_selector', "button[type='submit']")
        success_url_pattern = args.get('success_url_pattern')

        await session.page.goto(url, wait_until='networkidle')
        await session.page.fill(username_selector, username)
        await session.page.fill(password_selector, password)
        await session.page.click(submit_selector)

        if success_url_pattern:
            await session.page.wait_for_url(success_url_pattern, timeout=10000)
        else:
            await session.page.wait_for_load_state('networkidle')

        return {
            'logged_in': True,
            'url': session.page.url,
            'title': await session.page.title()
        }

    async def _handle_get_console_logs(self, session_id: str, args: dict) -> dict:
        session = self._get_session(session_id)
        logs = session.metadata.get("console_logs", [])
        return {"logs": list(logs)}

    async def _handle_clear_console_logs(self, session_id: str, args: dict) -> dict:
        session = self._get_session(session_id)
        logs = session.metadata.get("console_logs")
        if isinstance(logs, list):
            logs.clear()
        return {"cleared": True}

    async def _handle_start_tracing(self, session_id: str, args: dict) -> dict:
        session = self._get_session(session_id)
        if session.metadata.get("tracing_active"):
            return {"started": False, "reason": "already_active"}

        await session.context.tracing.start(
            screenshots=bool(args.get("screenshots", True)),
            snapshots=bool(args.get("snapshots", True)),
            sources=bool(args.get("sources", True)),
        )
        session.metadata["tracing_active"] = True
        return {"started": True}

    async def _handle_stop_tracing(self, session_id: str, args: dict) -> dict:
        session = self._get_session(session_id)
        if not session.metadata.get("tracing_active"):
            return {"stopped": False, "reason": "not_active"}

        filename = args.get("path")
        if filename:
            filename = Path(filename).name
        else:
            filename = f"trace_{session_id}_{int(time.time())}.zip"

        trace_path = Path("/screenshots") / filename
        await session.context.tracing.stop(path=str(trace_path))
        session.metadata["tracing_active"] = False

        return {"stopped": True, "path": str(trace_path)}

    async def _handle_export_storage_state(self, session_id: str, args: dict) -> dict:
        session = self._get_session(session_id)
        path = args.get("path")

        if path:
            filename = Path(path).name
            storage_path = Path("/screenshots") / filename
            await session.context.storage_state(path=str(storage_path))
            return {"path": str(storage_path)}

        state = await session.context.storage_state()
        return {"state": state}

    async def _handle_import_storage_state(self, session_id: str, args: dict) -> dict:
        session = self._get_session(session_id)
        state = args.get("state")
        path = args.get("path")

        if path:
            filename = Path(path).name
            storage_path = Path("/screenshots") / filename
            if not storage_path.exists():
                raise ValueError(f"Storage state file not found: {storage_path}")
            state = json.loads(storage_path.read_text(encoding="utf-8"))

        if not state:
            raise ValueError("state or path is required")

        await session.context.close()
        context = await self._browser.new_context(storage_state=state)
        page = await context.new_page()

        session.context = context
        session.page = page
        self._attach_console_logger(session)
        session.metadata["tracing_active"] = False

        return {"imported": True}

    async def _handle_get_video_path(self, session_id: str, args: dict) -> dict:
        session = self._get_session(session_id)
        if not session.page.video:
            return {"path": None}
        path = await session.page.video.path()
        return {"path": str(path)}


async def main() -> None:
    server = PlaywrightWebSocketServer()
    try:
        await server.start()
    except KeyboardInterrupt:
        logger.info("Received shutdown signal")
    finally:
        await server.stop()


if __name__ == "__main__":
    asyncio.run(main())
