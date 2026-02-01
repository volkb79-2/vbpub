#!/usr/bin/env python3
"""
WebSocket Server for Playwright Browser Automation

Provides a multi-client WebSocket service for browser automation.
Designed for use by multiple projects and remote clients.
"""

import asyncio
import base64
import http.server
import json
import logging
import mimetypes
import os
import secrets
import ssl
import threading
import time
import uuid
from urllib.parse import parse_qs, quote, unquote, urlparse
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
PLAYWRIGHT_CHROMIUM_CHANNEL = os.getenv('PLAYWRIGHT_CHROMIUM_CHANNEL', '').strip()
PLAYWRIGHT_CHROMIUM_EXECUTABLE = os.getenv('PLAYWRIGHT_CHROMIUM_EXECUTABLE', '').strip()
PLAYWRIGHT_VIDEO_DIR = os.getenv('PLAYWRIGHT_VIDEO_DIR', '')
SSL_CERT_PATH = os.getenv('SSL_CERT_PATH', '/app/certs/server.crt')
SSL_KEY_PATH = os.getenv('SSL_KEY_PATH', '/app/certs/server.key')
SSL_ENABLED = os.getenv('SSL_ENABLED', 'false').lower() == 'true'
WS_EVENT_STREAM_ENABLED = os.getenv('WS_EVENT_STREAM_ENABLED', 'true').lower() == 'true'
WS_ARTIFACT_ROOT = os.getenv('WS_ARTIFACT_ROOT', '/screenshots')
WS_WORKSPACE_ROOT = os.getenv('WS_WORKSPACE_ROOT', '/workspaces')
WS_ARTIFACT_MAX_BYTES = int(os.getenv('WS_ARTIFACT_MAX_BYTES', '5242880'))
BROWSER_POOL_ENABLED = os.getenv('BROWSER_POOL_ENABLED', 'false').lower() == 'true'
BROWSER_POOL_SIZE = int(os.getenv('BROWSER_POOL_SIZE', '4'))
WS_HAR_ENABLED = os.getenv('WS_HAR_ENABLED', 'false').lower() == 'true'
WS_HAR_CONTENT = os.getenv('WS_HAR_CONTENT', 'omit').strip()
WS_CONSOLE_STREAM_ENABLED = os.getenv('WS_CONSOLE_STREAM_ENABLED', 'false').lower() == 'true'
ARTIFACT_HTTP_ENABLED = os.getenv('ARTIFACT_HTTP_ENABLED', 'false').lower() == 'true'
ARTIFACT_HTTP_HOST = os.getenv('ARTIFACT_HTTP_HOST', '0.0.0.0')
ARTIFACT_HTTP_PORT = int(os.getenv('ARTIFACT_HTTP_PORT', '8090'))
ARTIFACT_HTTP_AUTH_REQUIRED = os.getenv('ARTIFACT_HTTP_AUTH_REQUIRED', '').strip()
if ARTIFACT_HTTP_AUTH_REQUIRED == "":
    ARTIFACT_HTTP_AUTH_REQUIRED = str(AUTH_REQUIRED)
ARTIFACT_HTTP_AUTH_REQUIRED = ARTIFACT_HTTP_AUTH_REQUIRED.lower() == "true"


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
        self._context_pool: list[BrowserContext] = []
        self._pool_lock = asyncio.Lock()
        self._pool_enabled = BROWSER_POOL_ENABLED
        self._session_owners: Dict[str, set[ServerConnection]] = {}
        self._artifact_httpd: Optional[http.server.ThreadingHTTPServer] = None
        self._artifact_thread: Optional[threading.Thread] = None

        self.handlers: Dict[str, Callable] = {
            'create_session': self._handle_create_session,
            'list_sessions': self._handle_list_sessions,
            'event_stream': self._handle_event_stream,
            'list_artifacts': self._handle_list_artifacts,
            'get_artifact': self._handle_get_artifact,
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
            'export_console_logs': self._handle_export_console_logs,
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
            launch_kwargs = {"headless": PLAYWRIGHT_HEADLESS}
            if PLAYWRIGHT_CHROMIUM_EXECUTABLE:
                launch_kwargs["executable_path"] = PLAYWRIGHT_CHROMIUM_EXECUTABLE
            elif PLAYWRIGHT_CHROMIUM_CHANNEL:
                launch_kwargs["channel"] = PLAYWRIGHT_CHROMIUM_CHANNEL
            self._browser = await self._playwright.chromium.launch(**launch_kwargs)
        elif PLAYWRIGHT_BROWSER == 'firefox':
            self._browser = await self._playwright.firefox.launch(headless=PLAYWRIGHT_HEADLESS)
        elif PLAYWRIGHT_BROWSER == 'webkit':
            self._browser = await self._playwright.webkit.launch(headless=PLAYWRIGHT_HEADLESS)
        else:
            raise ValueError(f"Unsupported browser: {PLAYWRIGHT_BROWSER}")

        logger.info("Playwright browser initialized")

        if self._pool_enabled and PLAYWRIGHT_VIDEO_DIR:
            logger.warning("Browser pooling disabled because PLAYWRIGHT_VIDEO_DIR is set")
            self._pool_enabled = False

        if self._pool_enabled and BROWSER_POOL_SIZE > 0:
            for _ in range(BROWSER_POOL_SIZE):
                context = await self._create_context("pool")
                self._context_pool.append(context)
            logger.info("Prewarmed browser pool: %s", len(self._context_pool))
        self._cleanup_task = asyncio.create_task(self._cleanup_expired_sessions())

        if ARTIFACT_HTTP_ENABLED:
            self._start_artifact_http_server()

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

        self._stop_artifact_http_server()

        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass

        for session_id in list(self.sessions.keys()):
            await self._close_session(session_id)

        for context in list(self._context_pool):
            try:
                await context.close()
            except Exception as exc:
                logger.debug("Error closing pooled context: %s", exc)
        self._context_pool.clear()

        if self._browser:
            await self._browser.close()

        if self._playwright:
            await self._playwright.stop()

        logger.info("WebSocket server stopped")

    def _normalize_workspace_id(self, workspace_id: Optional[str]) -> str:
        raw = workspace_id or f"workspace_{secrets.token_hex(6)}"
        sanitized = "".join(ch for ch in raw if ch.isalnum() or ch in ("-", "_"))
        if not sanitized:
            raise ValueError("Invalid workspace_id")
        return sanitized

    def _workspace_root(self) -> Path:
        return Path(WS_WORKSPACE_ROOT)

    def _artifact_root(self) -> Path:
        return Path(WS_ARTIFACT_ROOT)

    def _workspace_dir(self, workspace_id: str) -> Path:
        return self._workspace_root() / workspace_id

    def _artifact_dir(self, workspace_id: str) -> Path:
        return self._artifact_root() / workspace_id

    def _ensure_dir(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)

    def _start_artifact_http_server(self) -> None:
        token = _resolve_ws_token()

        class ArtifactHandler(http.server.BaseHTTPRequestHandler):
            def log_message(self, format: str, *args) -> None:  # noqa: A003 - match BaseHTTPRequestHandler
                return

            def _reject(self, status: int, message: str) -> None:
                body = message.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
                if ARTIFACT_HTTP_AUTH_REQUIRED and token:
                    auth = self.headers.get("Authorization", "")
                    parsed = urlparse(self.path)
                    query = parse_qs(parsed.query)
                    query_token = query.get("token", [""])[0]
                    if auth != f"Bearer {token}" and query_token != token:
                        self._reject(401, "Unauthorized")
                        return

                parsed = urlparse(self.path)
                if not parsed.path.startswith("/artifacts/"):
                    self._reject(404, "Not Found")
                    return

                relative = parsed.path[len("/artifacts/") :]
                if not relative:
                    self._reject(400, "Missing workspace id")
                    return

                parts = relative.split("/", 1)
                workspace_id = parts[0]
                rel_path = parts[1] if len(parts) > 1 else ""
                if not rel_path:
                    self._reject(400, "Missing artifact path")
                    return

                artifacts_dir = self.server.artifact_root / workspace_id
                candidate = (artifacts_dir / unquote(rel_path)).resolve()
                if artifacts_dir not in candidate.parents and artifacts_dir != candidate:
                    self._reject(400, "Invalid artifact path")
                    return

                if not candidate.exists() or not candidate.is_file():
                    self._reject(404, "Artifact not found")
                    return

                content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
                data = candidate.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        server = http.server.ThreadingHTTPServer((ARTIFACT_HTTP_HOST, ARTIFACT_HTTP_PORT), ArtifactHandler)
        server.daemon_threads = True
        server.artifact_root = self._artifact_root()
        self._artifact_httpd = server

        thread = threading.Thread(target=server.serve_forever, name="artifact-http", daemon=True)
        thread.start()
        self._artifact_thread = thread
        logger.info("Artifact HTTP server running on http://%s:%s", ARTIFACT_HTTP_HOST, ARTIFACT_HTTP_PORT)

    def _stop_artifact_http_server(self) -> None:
        if self._artifact_httpd:
            self._artifact_httpd.shutdown()
            self._artifact_httpd.server_close()
            self._artifact_httpd = None
        if self._artifact_thread:
            self._artifact_thread.join(timeout=5)
            self._artifact_thread = None

    def _register_session_owner(self, session_id: str, websocket: ServerConnection) -> None:
        owners = self._session_owners.setdefault(session_id, set())
        owners.add(websocket)

    def _unregister_websocket(self, websocket: ServerConnection) -> None:
        to_remove = []
        for session_id, owners in self._session_owners.items():
            if websocket in owners:
                owners.discard(websocket)
                if not owners:
                    to_remove.append(session_id)
        for session_id in to_remove:
            self._session_owners.pop(session_id, None)

    async def _emit_session_event(self, session_id: str, event: str, data: dict) -> None:
        if not WS_EVENT_STREAM_ENABLED:
            return

        session = self.sessions.get(session_id)
        if session and not session.metadata.get("event_stream_enabled", True):
            return

        payload = {
            "type": "event",
            "event": event,
            "session_id": session_id,
            "ts": time.time(),
            "data": data,
        }

        owners = list(self._session_owners.get(session_id, set()))
        for websocket in owners:
            try:
                await websocket.send(json.dumps(payload))
            except Exception:
                self._unregister_websocket(websocket)

    def _resolve_artifact_path_for_dir(
        self,
        artifacts_dir: Path,
        filename: Optional[str],
        default_prefix: str,
        suffix: str,
    ) -> Path:
        if filename:
            name = Path(filename).name
        else:
            name = f"{default_prefix}_{int(time.time())}{suffix}"

        if name.startswith(".") or ".." in name:
            raise ValueError("Invalid artifact filename")

        self._ensure_dir(artifacts_dir)
        return artifacts_dir / name

    async def _emit_event(self, websocket: Optional[ServerConnection], event: str, session_id: str, data: dict) -> None:
        if not WS_EVENT_STREAM_ENABLED:
            return

        session = self.sessions.get(session_id)
        if session and not session.metadata.get("event_stream_enabled", True):
            return

        payload = {
            "type": "event",
            "event": event,
            "session_id": session_id,
            "ts": time.time(),
            "data": data,
        }

        if not websocket:
            return

        try:
            await websocket.send(json.dumps(payload))
        except Exception as exc:
            logger.debug("Failed to emit event to client: %s", exc)

    async def _borrow_context(
        self,
        workspace_id: str,
        *,
        record_har: bool,
        har_path: Optional[Path],
        har_content: Optional[str],
        storage_state: Optional[dict] = None,
    ) -> BrowserContext:
        if not self._browser:
            raise RuntimeError("Browser not initialized")

        if not self._pool_enabled or record_har or storage_state:
            return await self._create_context(
                workspace_id,
                record_har=record_har,
                har_path=har_path,
                har_content=har_content,
                storage_state=storage_state,
            )

        async with self._pool_lock:
            if self._context_pool:
                return self._context_pool.pop()

        return await self._create_context(workspace_id)

    async def _release_context(self, context: BrowserContext) -> None:
        if not self._pool_enabled:
            await context.close()
            return

        async with self._pool_lock:
            if len(self._context_pool) >= BROWSER_POOL_SIZE:
                await context.close()
                return

            self._context_pool.append(context)

    async def _reset_context(self, context: BrowserContext) -> None:
        try:
            await context.clear_cookies()
            pages = list(context.pages)
            for page in pages:
                try:
                    await page.evaluate("localStorage.clear(); sessionStorage.clear();")
                except Exception:
                    pass
                try:
                    await page.goto("about:blank")
                except Exception:
                    pass
                if page != pages[0]:
                    try:
                        await page.close()
                    except Exception:
                        pass
        except Exception as exc:
            logger.debug("Failed to reset context: %s", exc)

    async def _create_context(
        self,
        workspace_id: str,
        *,
        record_har: bool = False,
        har_path: Optional[Path] = None,
        har_content: Optional[str] = None,
        storage_state: Optional[dict] = None,
    ) -> BrowserContext:
        if not self._browser:
            raise RuntimeError("Browser not initialized")

        context_kwargs: dict[str, Any] = {}
        if storage_state:
            context_kwargs["storage_state"] = storage_state

        record_video_dir: Optional[str] = None
        if PLAYWRIGHT_VIDEO_DIR:
            video_root = Path(PLAYWRIGHT_VIDEO_DIR)
            record_video_dir = str(video_root / workspace_id / "videos")
            self._ensure_dir(Path(record_video_dir))

        if record_har and har_path:
            context_kwargs["record_har_path"] = str(har_path)
            if har_content:
                context_kwargs["record_har_content"] = har_content

        if record_video_dir:
            context_kwargs["record_video_dir"] = record_video_dir

        return await self._browser.new_context(**context_kwargs)

    def _resolve_artifact_path(self, session: BrowserSession, filename: Optional[str], default_prefix: str, suffix: str) -> Path:
        if filename:
            name = Path(filename).name
        else:
            name = f"{default_prefix}_{session.session_id}_{int(time.time())}{suffix}"

        if name.startswith(".") or ".." in name:
            raise ValueError("Invalid artifact filename")

        artifact_dir = Path(session.metadata["artifacts_dir"])
        self._ensure_dir(artifact_dir)
        return artifact_dir / name

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
            session = await self._create_session(workspace_id=f"client_{client_id}")
            self.client_sessions[websocket] = session.session_id
            self._register_session_owner(session.session_id, websocket)

            await websocket.send(json.dumps({
                'type': 'connected',
                'session_id': session.session_id,
                'workspace_id': session.metadata.get("workspace_id"),
                'artifacts_dir': session.metadata.get("artifacts_dir"),
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
            self._unregister_websocket(websocket)

    async def _create_session(
        self,
        workspace_id: Optional[str] = None,
        metadata: Optional[dict] = None,
        *,
        record_har: Optional[bool] = None,
        har_content: Optional[str] = None,
        har_path: Optional[str] = None,
    ) -> BrowserSession:
        if len(self.sessions) >= MAX_SESSIONS:
            raise RuntimeError(f"Maximum sessions ({MAX_SESSIONS}) reached")

        if not self._browser:
            raise RuntimeError("Browser not initialized")

        session_id = f"session_{secrets.token_hex(8)}"
        workspace = self._normalize_workspace_id(workspace_id)
        workspace_dir = self._workspace_dir(workspace)
        artifacts_dir = self._artifact_dir(workspace)
        self._ensure_dir(workspace_dir)
        self._ensure_dir(artifacts_dir)

        resolved_record_har = WS_HAR_ENABLED if record_har is None else bool(record_har)
        resolved_har_content = (har_content or WS_HAR_CONTENT).strip() or None
        resolved_har_path: Optional[Path] = None
        if resolved_record_har:
            resolved_har_path = self._resolve_artifact_path_for_dir(
                artifacts_dir,
                har_path,
                f"har_{session_id}",
                ".har",
            )

        context = await self._borrow_context(
            workspace,
            record_har=resolved_record_har,
            har_path=resolved_har_path,
            har_content=resolved_har_content,
        )
        await self._reset_context(context)
        page = await context.new_page()

        session = BrowserSession(session_id=session_id, context=context, page=page)
        self._attach_console_logger(session)
        session.metadata["tracing_active"] = False
        session.metadata["workspace_id"] = workspace
        session.metadata["workspace_dir"] = str(workspace_dir)
        session.metadata["artifacts_dir"] = str(artifacts_dir)
        session.metadata["event_stream_enabled"] = WS_EVENT_STREAM_ENABLED
        session.metadata["har_enabled"] = resolved_record_har
        session.metadata["har_content"] = resolved_har_content
        session.metadata["har_path"] = str(resolved_har_path) if resolved_har_path else None
        session.metadata["pool_eligible"] = self._pool_enabled and not resolved_record_har
        if metadata:
            session.metadata.update(metadata)
        self.sessions[session_id] = session
        logger.info("Created session: %s", session_id)

        return session

    async def _close_session(self, session_id: str) -> None:
        session = self.sessions.pop(session_id, None)
        if session:
            try:
                pool_eligible = bool(session.metadata.get("pool_eligible", False))
                if pool_eligible:
                    await self._reset_context(session.context)
                    await self._release_context(session.context)
                else:
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
                payload = {
                    "type": message.type,
                    "text": message.text,
                    "location": message.location,
                }
                console_logs.append(payload)
                if WS_CONSOLE_STREAM_ENABLED:
                    asyncio.create_task(self._emit_session_event(
                        session.session_id,
                        "console",
                        {
                            **payload,
                            "ts": time.time(),
                        },
                    ))
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

            target_session_id = args.get('session_id') or session_id

            handler = self.handlers.get(command)
            if not handler:
                await websocket.send(json.dumps({
                    'type': 'error',
                    'id': msg_id,
                    'error': f"Unknown command: {command}"
                }))
                return

            await self._emit_event(websocket, "command_started", target_session_id, {
                "command": command,
            })
            result = await handler(target_session_id, args)

            await self._emit_event(websocket, "command_finished", target_session_id, {
                "command": command,
                "result": result,
            })

            if command == "create_session" and isinstance(result, dict):
                new_session_id = result.get("session_id")
                if new_session_id:
                    self._register_session_owner(new_session_id, websocket)

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
            target_session = None
            try:
                target_session = data.get('args', {}).get('session_id') or session_id
            except Exception:
                target_session = session_id
            if target_session:
                await self._emit_event(websocket, "command_failed", target_session, {
                    "command": data.get('command') if isinstance(data, dict) else None,
                    "error": str(e),
                })
            await websocket.send(json.dumps({
                'type': 'error',
                'id': msg_id,
                'error': str(e)
            }))

    async def _handle_create_session(self, session_id: str, args: dict) -> dict:
        workspace_id = args.get("workspace_id")
        user_id = args.get("user_id")
        label = args.get("label")
        record_har = args.get("record_har")
        har_content = args.get("har_content")
        har_path = args.get("har_path")
        metadata = {
            "user_id": user_id,
            "label": label,
        }
        session = await self._create_session(
            workspace_id=workspace_id,
            metadata=metadata,
            record_har=record_har,
            har_content=har_content,
            har_path=har_path,
        )
        return {
            "session_id": session.session_id,
            "workspace_id": session.metadata.get("workspace_id"),
            "workspace_dir": session.metadata.get("workspace_dir"),
            "artifacts_dir": session.metadata.get("artifacts_dir"),
            "har_enabled": session.metadata.get("har_enabled"),
            "har_path": session.metadata.get("har_path"),
        }

    async def _handle_list_sessions(self, session_id: str, args: dict) -> dict:
        sessions = []
        for sid, session in self.sessions.items():
            sessions.append({
                "session_id": sid,
                "workspace_id": session.metadata.get("workspace_id"),
                "user_id": session.metadata.get("user_id"),
                "label": session.metadata.get("label"),
                "created_at": session.created_at,
                "last_used": session.last_used,
            })
        return {"sessions": sessions}

    async def _handle_event_stream(self, session_id: str, args: dict) -> dict:
        enabled = bool(args.get("enabled", True))
        session = self._get_session(session_id)
        session.metadata["event_stream_enabled"] = enabled
        return {"enabled": enabled}

    async def _handle_list_artifacts(self, session_id: str, args: dict) -> dict:
        session = self._get_session(session_id)
        artifacts_dir = Path(session.metadata["artifacts_dir"])
        if not artifacts_dir.exists():
            return {"artifacts": []}

        workspace_id = session.metadata.get("workspace_id")
        http_base = None
        if ARTIFACT_HTTP_ENABLED and workspace_id:
            http_base = f"http://{ARTIFACT_HTTP_HOST}:{ARTIFACT_HTTP_PORT}/artifacts/{workspace_id}/"

        items = []
        for path in sorted(artifacts_dir.rglob("*")):
            if path.is_dir():
                continue
            rel_path = str(path.relative_to(artifacts_dir))
            stat = path.stat()
            entry = {
                "path": rel_path,
                "size": stat.st_size,
                "mtime": stat.st_mtime,
            }
            if http_base:
                entry["http_url"] = f"{http_base}{quote(rel_path)}"
            items.append(entry)
        return {"artifacts": items}

    async def _handle_get_artifact(self, session_id: str, args: dict) -> dict:
        session = self._get_session(session_id)
        rel_path = args.get("path")
        if not rel_path:
            raise ValueError("path is required")

        artifacts_dir = Path(session.metadata["artifacts_dir"])
        candidate = (artifacts_dir / rel_path).resolve()
        if artifacts_dir not in candidate.parents and artifacts_dir != candidate:
            raise ValueError("Invalid artifact path")
        if not candidate.exists() or not candidate.is_file():
            raise ValueError("Artifact not found")

        data = candidate.read_bytes()
        truncated = False
        if len(data) > WS_ARTIFACT_MAX_BYTES:
            data = data[:WS_ARTIFACT_MAX_BYTES]
            truncated = True

        response = {
            "path": rel_path,
            "size": candidate.stat().st_size,
            "truncated": truncated,
            "content_base64": base64.b64encode(data).decode("utf-8"),
        }
        workspace_id = session.metadata.get("workspace_id")
        if ARTIFACT_HTTP_ENABLED and workspace_id:
            response["http_url"] = (
                f"http://{ARTIFACT_HTTP_HOST}:{ARTIFACT_HTTP_PORT}"
                f"/artifacts/{workspace_id}/{quote(rel_path)}"
            )
        return response

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

        screenshot_path = self._resolve_artifact_path(
            session,
            args.get("path"),
            "screenshot",
            ".png",
        )

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
            'headless': PLAYWRIGHT_HEADLESS,
            'pool_enabled': self._pool_enabled,
            'pool_size': len(self._context_pool),
            'artifact_root': WS_ARTIFACT_ROOT,
            'har_enabled': WS_HAR_ENABLED,
            'har_content': WS_HAR_CONTENT,
            'console_stream_enabled': WS_CONSOLE_STREAM_ENABLED,
            'artifact_http_enabled': ARTIFACT_HTTP_ENABLED,
            'artifact_http_host': ARTIFACT_HTTP_HOST,
            'artifact_http_port': ARTIFACT_HTTP_PORT,
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

    async def _handle_export_console_logs(self, session_id: str, args: dict) -> dict:
        session = self._get_session(session_id)
        logs = session.metadata.get("console_logs", [])
        artifacts_dir = Path(session.metadata["artifacts_dir"])
        log_path = self._resolve_artifact_path_for_dir(
            artifacts_dir,
            args.get("path"),
            "console",
            ".json",
        )
        log_path.write_text(json.dumps(list(logs), indent=2), encoding="utf-8")
        return {"path": str(log_path), "count": len(logs)}

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

        trace_path = self._resolve_artifact_path(
            session,
            args.get("path"),
            "trace",
            ".zip",
        )
        await session.context.tracing.stop(path=str(trace_path))
        session.metadata["tracing_active"] = False

        return {"stopped": True, "path": str(trace_path)}

    async def _handle_export_storage_state(self, session_id: str, args: dict) -> dict:
        session = self._get_session(session_id)
        path = args.get("path")

        if path:
            storage_path = self._resolve_artifact_path(
                session,
                path,
                "storage-state",
                ".json",
            )
            await session.context.storage_state(path=str(storage_path))
            return {"path": str(storage_path)}

        state = await session.context.storage_state()
        return {"state": state}

    async def _handle_import_storage_state(self, session_id: str, args: dict) -> dict:
        session = self._get_session(session_id)
        state = args.get("state")
        path = args.get("path")

        if path:
            storage_path = self._resolve_artifact_path(
                session,
                path,
                "storage-state",
                ".json",
            )
            if not storage_path.exists():
                raise ValueError(f"Storage state file not found: {storage_path}")
            state = json.loads(storage_path.read_text(encoding="utf-8"))

        if not state:
            raise ValueError("state or path is required")

        await session.context.close()
        workspace_id = session.metadata.get("workspace_id") or "workspace"
        record_har = bool(session.metadata.get("har_enabled", False))
        har_content = session.metadata.get("har_content")
        har_path_value = session.metadata.get("har_path")
        har_path = Path(har_path_value) if har_path_value else None

        if record_har and not har_path:
            artifacts_dir = Path(session.metadata["artifacts_dir"])
            har_path = self._resolve_artifact_path_for_dir(
                artifacts_dir,
                None,
                f"har_{session_id}",
                ".har",
            )
            session.metadata["har_path"] = str(har_path)

        context = await self._create_context(
            str(workspace_id),
            record_har=record_har,
            har_path=har_path,
            har_content=har_content,
            storage_state=state,
        )
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
