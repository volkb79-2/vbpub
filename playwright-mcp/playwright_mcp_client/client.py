#!/usr/bin/env python3
"""
WebSocket Client for Playwright MCP Standalone Service
"""

import asyncio
import json
import logging
import os
import uuid
from typing import Any, Dict, List, Optional

import websockets
from websockets.asyncio.client import ClientConnection

logger = logging.getLogger(__name__)


class PlaywrightWSError(Exception):
    pass


class PlaywrightWSClient:
    def __init__(
        self,
        url: str = "ws://localhost:3000",
        auth_token: Optional[str] = None,
        timeout: float = 30.0
    ) -> None:
        self.url = url
        self.auth_token = auth_token or os.getenv('WS_AUTH_TOKEN') or os.getenv('ACCESS_TOKEN', '')
        self.timeout = timeout
        self._ws: Optional[ClientConnection] = None
        self._session_id: Optional[str] = None
        self._pending: Dict[str, asyncio.Future] = {}
        self._listener_task: Optional[asyncio.Task] = None

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def connect(self) -> None:
        logger.info("Connecting to %s...", self.url)

        self._ws = await websockets.connect(
            self.url,
            max_size=10 * 1024 * 1024,
            ping_interval=30,
            ping_timeout=10
        )

        if self.auth_token:
            await self._ws.send(json.dumps({
                'type': 'auth',
                'token': self.auth_token
            }))

            auth_response = await asyncio.wait_for(self._ws.recv(), timeout=self.timeout)
            auth_data = json.loads(auth_response)

            if auth_data.get('type') == 'error':
                raise PlaywrightWSError(f"Authentication failed: {auth_data.get('error')}")

            logger.info("Authenticated successfully")

        connect_response = await asyncio.wait_for(self._ws.recv(), timeout=self.timeout)
        connect_data = json.loads(connect_response)

        if connect_data.get('type') == 'connected':
            self._session_id = connect_data.get('session_id')
            logger.info("Connected with session: %s", self._session_id)
        else:
            raise PlaywrightWSError(f"Connection failed: {connect_data}")

        self._listener_task = asyncio.create_task(self._message_listener())

    async def _message_listener(self) -> None:
        try:
            async for message in self._ws:
                try:
                    data = json.loads(message)
                    msg_id = data.get('id')

                    if msg_id and msg_id in self._pending:
                        future = self._pending.pop(msg_id)
                        if data.get('type') == 'error':
                            future.set_exception(PlaywrightWSError(data.get('error', 'Unknown error')))
                        else:
                            future.set_result(data.get('data', {}))
                    else:
                        logger.debug("Received message without pending future: %s", data)

                except json.JSONDecodeError as e:
                    logger.error("Invalid JSON received: %s", e)

        except websockets.exceptions.ConnectionClosed:
            logger.info("WebSocket connection closed")
        except Exception as e:
            logger.error("Message listener error: %s", e)

    async def _send_command(self, command: str, args: Optional[Dict] = None) -> Dict[str, Any]:
        if not self._ws:
            raise PlaywrightWSError("Not connected")

        msg_id = str(uuid.uuid4())[:8]
        future = asyncio.get_event_loop().create_future()
        self._pending[msg_id] = future

        message = {
            'id': msg_id,
            'command': command,
            'args': args or {}
        }
        await self._ws.send(json.dumps(message))

        try:
            result = await asyncio.wait_for(future, timeout=self.timeout)
            return result
        except asyncio.TimeoutError:
            self._pending.pop(msg_id, None)
            raise PlaywrightWSError(f"Command '{command}' timed out")

    async def close(self) -> None:
        if self._listener_task:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass

        if self._ws:
            await self._ws.close()
            self._ws = None

        logger.info("WebSocket connection closed")

    async def navigate(self, url: str, wait_until: str = 'networkidle', timeout: int = 30000) -> Dict[str, Any]:
        return await self._send_command('navigate', {
            'url': url,
            'wait_until': wait_until,
            'timeout': timeout
        })

    async def screenshot(self, path: Optional[str] = None, full_page: bool = True) -> Dict[str, Any]:
        return await self._send_command('screenshot', {
            'path': path,
            'full_page': full_page
        })

    async def click(self, selector: str, timeout: int = 10000, button: str = 'left', click_count: int = 1) -> Dict[str, Any]:
        return await self._send_command('click', {
            'selector': selector,
            'timeout': timeout,
            'button': button,
            'click_count': click_count
        })

    async def fill(self, selector: str, value: str, timeout: int = 10000) -> Dict[str, Any]:
        return await self._send_command('fill', {
            'selector': selector,
            'value': value,
            'timeout': timeout
        })

    async def type(self, selector: str, text: str, delay: int = 0, timeout: int = 10000) -> Dict[str, Any]:
        return await self._send_command('type', {
            'selector': selector,
            'text': text,
            'delay': delay,
            'timeout': timeout
        })

    async def press(self, key: str, selector: Optional[str] = None) -> Dict[str, Any]:
        return await self._send_command('press', {
            'key': key,
            'selector': selector
        })

    async def evaluate(self, script: str) -> Dict[str, Any]:
        return await self._send_command('evaluate', {'script': script})

    async def get_content(self) -> Dict[str, Any]:
        return await self._send_command('get_content', {})

    async def get_url(self) -> Dict[str, Any]:
        return await self._send_command('get_url', {})

    async def wait_for_selector(self, selector: str, state: str = 'visible', timeout: int = 30000) -> Dict[str, Any]:
        return await self._send_command('wait_for_selector', {
            'selector': selector,
            'state': state,
            'timeout': timeout
        })

    async def wait_for_url(self, url_pattern: str, timeout: int = 30000) -> Dict[str, Any]:
        return await self._send_command('wait_for_url', {
            'url': url_pattern,
            'timeout': timeout
        })

    async def wait_for_load_state(self, state: str = 'networkidle', timeout: int = 30000) -> Dict[str, Any]:
        return await self._send_command('wait_for_load_state', {
            'state': state,
            'timeout': timeout
        })

    async def select_option(self, selector: str, value: Optional[str] = None, label: Optional[str] = None, index: Optional[int] = None) -> Dict[str, Any]:
        args = {'selector': selector}
        if value is not None:
            args['value'] = value
        elif label is not None:
            args['label'] = label
        elif index is not None:
            args['index'] = index
        return await self._send_command('select_option', args)

    async def check(self, selector: str) -> Dict[str, Any]:
        return await self._send_command('check', {'selector': selector})

    async def uncheck(self, selector: str) -> Dict[str, Any]:
        return await self._send_command('uncheck', {'selector': selector})

    async def hover(self, selector: str) -> Dict[str, Any]:
        return await self._send_command('hover', {'selector': selector})

    async def focus(self, selector: str) -> Dict[str, Any]:
        return await self._send_command('focus', {'selector': selector})

    async def get_attribute(self, selector: str, name: str) -> Dict[str, Any]:
        return await self._send_command('get_attribute', {
            'selector': selector,
            'name': name
        })

    async def get_text(self, selector: str) -> Dict[str, Any]:
        return await self._send_command('get_text', {'selector': selector})

    async def get_inner_html(self, selector: str) -> Dict[str, Any]:
        return await self._send_command('get_inner_html', {'selector': selector})

    async def get_input_value(self, selector: str) -> Dict[str, Any]:
        return await self._send_command('get_input_value', {'selector': selector})

    async def is_visible(self, selector: str) -> bool:
        result = await self._send_command('is_visible', {'selector': selector})
        return result.get('visible', False)

    async def is_enabled(self, selector: str) -> bool:
        result = await self._send_command('is_enabled', {'selector': selector})
        return result.get('enabled', False)

    async def is_checked(self, selector: str) -> bool:
        result = await self._send_command('is_checked', {'selector': selector})
        return result.get('checked', False)

    async def query_selector(self, selector: str) -> bool:
        result = await self._send_command('query_selector', {'selector': selector})
        return result.get('found', False)

    async def query_selector_all(self, selector: str) -> Dict[str, Any]:
        return await self._send_command('query_selector_all', {'selector': selector})

    async def reload(self, wait_until: str = 'networkidle') -> Dict[str, Any]:
        return await self._send_command('reload', {'wait_until': wait_until})

    async def go_back(self) -> Dict[str, Any]:
        return await self._send_command('go_back', {})

    async def go_forward(self) -> Dict[str, Any]:
        return await self._send_command('go_forward', {})

    async def set_viewport_size(self, width: int = 1280, height: int = 720) -> Dict[str, Any]:
        return await self._send_command('set_viewport_size', {'width': width, 'height': height})

    async def cookies(self) -> Dict[str, Any]:
        return await self._send_command('cookies', {})

    async def set_cookies(self, cookies: List[Dict[str, Any]]) -> Dict[str, Any]:
        return await self._send_command('set_cookies', {'cookies': cookies})

    async def clear_cookies(self) -> Dict[str, Any]:
        return await self._send_command('clear_cookies', {})

    async def close_session(self) -> Dict[str, Any]:
        return await self._send_command('close_session', {})

    async def health(self) -> Dict[str, Any]:
        return await self._send_command('health', {})

    async def login(
        self,
        url: str,
        username: str,
        password: str,
        username_selector: str = '#username',
        password_selector: str = '#password',
        submit_selector: str = "button[type='submit']",
        success_url_pattern: Optional[str] = None,
    ) -> Dict[str, Any]:
        return await self._send_command('login', {
            'url': url,
            'username': username,
            'password': password,
            'username_selector': username_selector,
            'password_selector': password_selector,
            'submit_selector': submit_selector,
            'success_url_pattern': success_url_pattern,
        })

    async def get_console_logs(self) -> Dict[str, Any]:
        return await self._send_command('get_console_logs', {})

    async def clear_console_logs(self) -> Dict[str, Any]:
        return await self._send_command('clear_console_logs', {})

    async def start_tracing(self, screenshots: bool = True, snapshots: bool = True, sources: bool = True) -> Dict[str, Any]:
        return await self._send_command('start_tracing', {
            'screenshots': screenshots,
            'snapshots': snapshots,
            'sources': sources,
        })

    async def stop_tracing(self, path: Optional[str] = None) -> Dict[str, Any]:
        return await self._send_command('stop_tracing', {
            'path': path,
        })

    async def export_storage_state(self, path: Optional[str] = None) -> Dict[str, Any]:
        return await self._send_command('export_storage_state', {
            'path': path,
        })

    async def import_storage_state(self, state: Optional[Dict[str, Any]] = None, path: Optional[str] = None) -> Dict[str, Any]:
        return await self._send_command('import_storage_state', {
            'state': state,
            'path': path,
        })

    async def get_video_path(self) -> Dict[str, Any]:
        return await self._send_command('get_video_path', {})

    async def query_selector_all(self, selector: str) -> int:
        result = await self._send_command('query_selector_all', {'selector': selector})
        return result.get('count', 0)

    async def reload(self, wait_until: str = 'networkidle') -> Dict[str, Any]:
        return await self._send_command('reload', {'wait_until': wait_until})

    async def go_back(self) -> Dict[str, Any]:
        return await self._send_command('go_back', {})

    async def go_forward(self) -> Dict[str, Any]:
        return await self._send_command('go_forward', {})

    async def set_viewport_size(self, width: int, height: int) -> Dict[str, Any]:
        return await self._send_command('set_viewport_size', {
            'width': width,
            'height': height
        })

    async def cookies(self) -> List[Dict[str, Any]]:
        result = await self._send_command('cookies', {})
        return result.get('cookies', [])

    async def set_cookies(self, cookies: List[Dict[str, Any]]) -> Dict[str, Any]:
        return await self._send_command('set_cookies', {'cookies': cookies})

    async def clear_cookies(self) -> Dict[str, Any]:
        return await self._send_command('clear_cookies', {})

    async def health(self) -> Dict[str, Any]:
        return await self._send_command('health', {})

    async def login(
        self,
        url: str,
        username: str,
        password: str,
        username_selector: str = '#username',
        password_selector: str = '#password',
        submit_selector: str = "button[type='submit']",
        success_url_pattern: Optional[str] = None
    ) -> Dict[str, Any]:
        return await self._send_command('login', {
            'url': url,
            'username': username,
            'password': password,
            'username_selector': username_selector,
            'password_selector': password_selector,
            'submit_selector': submit_selector,
            'success_url_pattern': success_url_pattern
        })


async def connect(url: str = "ws://localhost:3000", auth_token: Optional[str] = None) -> PlaywrightWSClient:
    client = PlaywrightWSClient(url, auth_token)
    await client.connect()
    return client
