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
from pwmcp_shared.constants import DEFAULT_WS_PORT, ENV_ACCESS_TOKEN, ENV_WS_AUTH_TOKEN

logger = logging.getLogger(__name__)


class PlaywrightWSError(Exception):
    pass


class PlaywrightWSClient:
    def __init__(
        self,
        url: str = f"ws://localhost:{DEFAULT_WS_PORT}",
        auth_token: Optional[str] = None,
        timeout: float = 30.0
    ) -> None:
        self.url = url
        self.auth_token = auth_token or os.getenv(ENV_WS_AUTH_TOKEN) or os.getenv(ENV_ACCESS_TOKEN, '')
        self.timeout = timeout
        self._ws: Optional[ClientConnection] = None
        self._session_id: Optional[str] = None
        self._pending: Dict[str, asyncio.Future] = {}
        self._listener_task: Optional[asyncio.Task] = None
        self._event_handlers: List[Any] = []

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

                    if data.get('type') == 'event':
                        await self._dispatch_event(data)
                        continue

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

        payload_args = args or {}
        message = {
            'id': msg_id,
            'command': command,
            'args': payload_args
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

    async def _dispatch_event(self, data: Dict[str, Any]) -> None:
        for handler in list(self._event_handlers):
            try:
                result = handler(data)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as exc:
                logger.debug("Event handler error: %s", exc)

    def on_event(self, handler) -> None:
        self._event_handlers.append(handler)

    async def navigate(self, url: str, wait_until: str = 'networkidle', timeout: int = 30000, session_id: Optional[str] = None) -> Dict[str, Any]:
        return await self._send_command('navigate', {
            'url': url,
            'wait_until': wait_until,
            'timeout': timeout,
            'session_id': session_id
        })

    async def screenshot(self, path: Optional[str] = None, full_page: bool = True, session_id: Optional[str] = None) -> Dict[str, Any]:
        return await self._send_command('screenshot', {
            'path': path,
            'full_page': full_page,
            'session_id': session_id
        })

    async def click(self, selector: str, timeout: int = 10000, button: str = 'left', click_count: int = 1, session_id: Optional[str] = None) -> Dict[str, Any]:
        return await self._send_command('click', {
            'selector': selector,
            'timeout': timeout,
            'button': button,
            'click_count': click_count,
            'session_id': session_id
        })

    async def fill(self, selector: str, value: str, timeout: int = 10000, session_id: Optional[str] = None) -> Dict[str, Any]:
        return await self._send_command('fill', {
            'selector': selector,
            'value': value,
            'timeout': timeout,
            'session_id': session_id
        })

    async def type(self, selector: str, text: str, delay: int = 0, timeout: int = 10000, session_id: Optional[str] = None) -> Dict[str, Any]:
        return await self._send_command('type', {
            'selector': selector,
            'text': text,
            'delay': delay,
            'timeout': timeout,
            'session_id': session_id
        })

    async def press(self, key: str, selector: Optional[str] = None, session_id: Optional[str] = None) -> Dict[str, Any]:
        return await self._send_command('press', {
            'key': key,
            'selector': selector,
            'session_id': session_id
        })

    async def evaluate(self, script: str, session_id: Optional[str] = None) -> Dict[str, Any]:
        return await self._send_command('evaluate', {'script': script, 'session_id': session_id})

    async def get_content(self, session_id: Optional[str] = None) -> Dict[str, Any]:
        return await self._send_command('get_content', {'session_id': session_id})

    async def get_url(self, session_id: Optional[str] = None) -> Dict[str, Any]:
        return await self._send_command('get_url', {'session_id': session_id})

    async def wait_for_selector(self, selector: str, state: str = 'visible', timeout: int = 30000, session_id: Optional[str] = None) -> Dict[str, Any]:
        return await self._send_command('wait_for_selector', {
            'selector': selector,
            'state': state,
            'timeout': timeout,
            'session_id': session_id
        })

    async def wait_for_url(self, url_pattern: str, timeout: int = 30000, session_id: Optional[str] = None) -> Dict[str, Any]:
        return await self._send_command('wait_for_url', {
            'url': url_pattern,
            'timeout': timeout,
            'session_id': session_id
        })

    async def wait_for_load_state(self, state: str = 'networkidle', timeout: int = 30000, session_id: Optional[str] = None) -> Dict[str, Any]:
        return await self._send_command('wait_for_load_state', {
            'state': state,
            'timeout': timeout,
            'session_id': session_id
        })

    async def select_option(self, selector: str, value: Optional[str] = None, label: Optional[str] = None, index: Optional[int] = None, session_id: Optional[str] = None) -> Dict[str, Any]:
        args = {'selector': selector}
        if value is not None:
            args['value'] = value
        elif label is not None:
            args['label'] = label
        elif index is not None:
            args['index'] = index
        args['session_id'] = session_id
        return await self._send_command('select_option', args)

    async def check(self, selector: str, session_id: Optional[str] = None) -> Dict[str, Any]:
        return await self._send_command('check', {'selector': selector, 'session_id': session_id})

    async def uncheck(self, selector: str, session_id: Optional[str] = None) -> Dict[str, Any]:
        return await self._send_command('uncheck', {'selector': selector, 'session_id': session_id})

    async def hover(self, selector: str, session_id: Optional[str] = None) -> Dict[str, Any]:
        return await self._send_command('hover', {'selector': selector, 'session_id': session_id})

    async def focus(self, selector: str, session_id: Optional[str] = None) -> Dict[str, Any]:
        return await self._send_command('focus', {'selector': selector, 'session_id': session_id})

    async def get_attribute(self, selector: str, name: str, session_id: Optional[str] = None) -> Dict[str, Any]:
        return await self._send_command('get_attribute', {
            'selector': selector,
            'name': name,
            'session_id': session_id
        })

    async def get_text(self, selector: str, session_id: Optional[str] = None) -> Dict[str, Any]:
        return await self._send_command('get_text', {'selector': selector, 'session_id': session_id})

    async def get_inner_html(self, selector: str, session_id: Optional[str] = None) -> Dict[str, Any]:
        return await self._send_command('get_inner_html', {'selector': selector, 'session_id': session_id})

    async def get_input_value(self, selector: str, session_id: Optional[str] = None) -> Dict[str, Any]:
        return await self._send_command('get_input_value', {'selector': selector, 'session_id': session_id})

    async def is_visible(self, selector: str, session_id: Optional[str] = None) -> bool:
        result = await self._send_command('is_visible', {'selector': selector, 'session_id': session_id})
        return result.get('visible', False)

    async def is_enabled(self, selector: str, session_id: Optional[str] = None) -> bool:
        result = await self._send_command('is_enabled', {'selector': selector, 'session_id': session_id})
        return result.get('enabled', False)

    async def is_checked(self, selector: str, session_id: Optional[str] = None) -> bool:
        result = await self._send_command('is_checked', {'selector': selector, 'session_id': session_id})
        return result.get('checked', False)

    async def query_selector(self, selector: str, session_id: Optional[str] = None) -> bool:
        result = await self._send_command('query_selector', {'selector': selector, 'session_id': session_id})
        return result.get('found', False)

    async def query_selector_all(self, selector: str, session_id: Optional[str] = None) -> int:
        result = await self._send_command('query_selector_all', {'selector': selector, 'session_id': session_id})
        return result.get('count', 0)

    async def reload(self, wait_until: str = 'networkidle', session_id: Optional[str] = None) -> Dict[str, Any]:
        return await self._send_command('reload', {'wait_until': wait_until, 'session_id': session_id})

    async def go_back(self, session_id: Optional[str] = None) -> Dict[str, Any]:
        return await self._send_command('go_back', {'session_id': session_id})

    async def go_forward(self, session_id: Optional[str] = None) -> Dict[str, Any]:
        return await self._send_command('go_forward', {'session_id': session_id})

    async def set_viewport_size(self, width: int, height: int, session_id: Optional[str] = None) -> Dict[str, Any]:
        return await self._send_command('set_viewport_size', {
            'width': width,
            'height': height,
            'session_id': session_id
        })

    async def cookies(self, session_id: Optional[str] = None) -> List[Dict[str, Any]]:
        result = await self._send_command('cookies', {'session_id': session_id})
        return result.get('cookies', [])

    async def set_cookies(self, cookies: List[Dict[str, Any]], session_id: Optional[str] = None) -> Dict[str, Any]:
        return await self._send_command('set_cookies', {'cookies': cookies, 'session_id': session_id})

    async def clear_cookies(self, session_id: Optional[str] = None) -> Dict[str, Any]:
        return await self._send_command('clear_cookies', {'session_id': session_id})

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
        session_id: Optional[str] = None
    ) -> Dict[str, Any]:
        return await self._send_command('login', {
            'url': url,
            'username': username,
            'password': password,
            'username_selector': username_selector,
            'password_selector': password_selector,
            'submit_selector': submit_selector,
            'success_url_pattern': success_url_pattern,
            'session_id': session_id
        })

    async def create_session(
        self,
        workspace_id: Optional[str] = None,
        user_id: Optional[str] = None,
        label: Optional[str] = None,
        record_har: Optional[bool] = None,
        har_content: Optional[str] = None,
        har_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        return await self._send_command('create_session', {
            'workspace_id': workspace_id,
            'user_id': user_id,
            'label': label,
            'record_har': record_har,
            'har_content': har_content,
            'har_path': har_path,
        })

    async def list_sessions(self) -> Dict[str, Any]:
        return await self._send_command('list_sessions', {})

    async def event_stream(self, enabled: bool = True, session_id: Optional[str] = None) -> Dict[str, Any]:
        return await self._send_command('event_stream', {
            'enabled': enabled,
            'session_id': session_id,
        })

    async def list_artifacts(self, session_id: Optional[str] = None) -> Dict[str, Any]:
        return await self._send_command('list_artifacts', {
            'session_id': session_id,
        })

    async def get_artifact(self, path: str, session_id: Optional[str] = None) -> Dict[str, Any]:
        return await self._send_command('get_artifact', {
            'path': path,
            'session_id': session_id,
        })

    async def export_console_logs(self, path: Optional[str] = None, session_id: Optional[str] = None) -> Dict[str, Any]:
        return await self._send_command('export_console_logs', {
            'path': path,
            'session_id': session_id,
        })


async def connect(url: str = f"ws://localhost:{DEFAULT_WS_PORT}", auth_token: Optional[str] = None) -> PlaywrightWSClient:
    client = PlaywrightWSClient(url, auth_token)
    await client.connect()
    return client


if __name__ == "__main__":
    async def example() -> None:
        url = os.getenv('WS_URL', f"ws://localhost:{DEFAULT_WS_PORT}")
        async with PlaywrightWSClient(url) as client:
            result = await client.navigate("https://example.com")
            logger.info("Title: %s", result.get('title'))

    asyncio.run(example())
