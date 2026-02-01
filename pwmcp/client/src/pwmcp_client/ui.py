from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Awaitable, Callable, Iterable, Optional
from urllib.parse import urljoin, urlparse

from pwmcp_client.artifacts import ArtifactManager
from pwmcp_client.client import PlaywrightWSClient
from pwmcp_client.config import PlaywrightMCPConfig
from pwmcp_client.retry import RetryPolicy, async_retry


@dataclass
class UIHarness:
    client: PlaywrightWSClient
    config: PlaywrightMCPConfig
    artifacts: ArtifactManager

    def build_url(self, path_or_url: str) -> str:
        if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
            if self.config.external_base_url and self.config.proxy_base_url:
                external = self.config.external_base_url.rstrip("/")
                proxy = self.config.proxy_base_url.rstrip("/")
                if path_or_url.startswith(external):
                    return path_or_url.replace(external, proxy, 1)

            if self.config.proxy_base_url:
                parsed = urlparse(path_or_url)
                proxy = self.config.proxy_base_url.rstrip("/")
                path = parsed.path.lstrip("/")
                if parsed.query:
                    path = f"{path}?{parsed.query}"
                return urljoin(proxy + "/", path)

            return path_or_url
        if not self.config.base_url:
            raise ValueError("base_url is required for relative paths")
        return urljoin(self.config.base_url.rstrip("/") + "/", path_or_url.lstrip("/"))

    async def goto(self, path_or_url: str, wait_until: Optional[str] = None, timeout_ms: Optional[int] = None) -> dict:
        url = self.build_url(path_or_url)
        await self.client.set_viewport_size(
            width=self.config.viewport_width,
            height=self.config.viewport_height,
        )
        return await self.client.navigate(
            url,
            wait_until=wait_until or self.config.wait_state,
            timeout=timeout_ms or self.config.nav_timeout_ms,
        )

    async def wait_for_load_state(self, state: Optional[str] = None, timeout_ms: Optional[int] = None) -> dict:
        return await self.client.wait_for_load_state(
            state=state or self.config.wait_state,
            timeout=timeout_ms or self.config.nav_timeout_ms,
        )

    async def wait_for_url(self, url_pattern: str, timeout_ms: Optional[int] = None) -> dict:
        return await self.client.wait_for_url(
            url_pattern,
            timeout=timeout_ms or self.config.nav_timeout_ms,
        )

    async def click(self, selector: str, timeout_ms: Optional[int] = None, button: str = "left", click_count: int = 1) -> dict:
        return await self.client.click(
            selector,
            timeout=timeout_ms or self.config.action_timeout_ms,
            button=button,
            click_count=click_count,
        )

    async def fill(self, selector: str, value: str, timeout_ms: Optional[int] = None) -> dict:
        return await self.client.fill(
            selector,
            value,
            timeout=timeout_ms or self.config.action_timeout_ms,
        )

    async def wait_for_selector(self, selector: str, state: str = "visible", timeout_ms: int = 30000) -> dict:
        return await self.client.wait_for_selector(selector, state=state, timeout=timeout_ms)

    async def assert_visible(self, selector: str) -> None:
        visible = await self.client.is_visible(selector)
        if not visible:
            raise AssertionError(f"Expected selector to be visible: {selector}")

    async def assert_layout(self, selectors: Iterable[str]) -> None:
        for selector in selectors:
            await self.assert_visible(selector)

    async def assert_text_contains(self, selector: str, expected: str) -> None:
        result = await self.client.get_text(selector)
        text = result.get("text") or ""
        if expected not in text:
            raise AssertionError(f"Expected '{expected}' in text for {selector}. Got: {text}")

    async def click_and_wait(self, selector: str, wait_state: Optional[str] = None) -> None:
        await self.click(selector)
        await self.wait_for_load_state(state=wait_state)

    async def fill_and_submit(
        self,
        field_selector: str,
        value: str,
        submit_selector: str,
        wait_state: Optional[str] = None,
    ) -> None:
        await self.client.fill(field_selector, value)
        await self.click(submit_selector)
        await self.wait_for_load_state(state=wait_state)

    async def login_form(
        self,
        url: str,
        username: str,
        password: str,
        username_selector: str = "#username",
        password_selector: str = "#password",
        submit_selector: str = "button[type='submit']",
        success_url_pattern: Optional[str] = None,
    ) -> dict:
        return await self.client.login(
            url=url,
            username=username,
            password=password,
            username_selector=username_selector,
            password_selector=password_selector,
            submit_selector=submit_selector,
            success_url_pattern=success_url_pattern,
        )

    async def capture_screenshot(self, prefix: str = "shot", full_page: bool = True) -> dict:
        self.artifacts.ensure_base_dir()
        name = self.artifacts.screenshot_name(prefix=prefix)
        return await self.client.screenshot(path=name, full_page=full_page)

    async def capture_html(self, filename: str = "page.html") -> dict:
        self.artifacts.ensure_base_dir()
        result = await self.client.get_content()
        path = self.artifacts.base_dir / filename
        self.artifacts.write_text(path, result.get("content", ""))
        return {"path": str(path), "url": result.get("url")}

    async def start_trace(self, screenshots: bool = True, snapshots: bool = True, sources: bool = True) -> dict:
        return await self.client.start_tracing(
            screenshots=screenshots,
            snapshots=snapshots,
            sources=sources,
        )

    async def stop_trace(self, filename: Optional[str] = None) -> dict:
        if filename is None:
            filename = self.artifacts.screenshot_name(prefix="trace", ext="zip")
        return await self.client.stop_tracing(path=filename)

    async def save_cookies(self, filename: str = "cookies.json") -> dict:
        cookies = await self.client.cookies()
        path = self.artifacts.base_dir / filename
        self.artifacts.write_json(path, cookies)
        return {"path": str(path), "count": len(cookies.get("cookies", []))}

    async def load_cookies(self, path: str) -> dict:
        cookie_path = self.artifacts.base_dir / path
        data = cookie_path.read_text(encoding="utf-8")
        cookies = json.loads(data)
        return await self.client.set_cookies(cookies.get("cookies", []))

    async def save_storage_state(self, filename: str = "storage-state.json") -> dict:
        self.artifacts.ensure_base_dir()
        result = await self.client.export_storage_state(path=filename)
        return result

    async def load_storage_state(self, filename: str = "storage-state.json") -> dict:
        return await self.client.import_storage_state(path=filename)

    async def ensure_storage_state(
        self,
        filename: str,
        login_callback: Callable[[], Awaitable[dict | None]],
    ) -> dict:
        self.artifacts.ensure_base_dir()
        path = self.artifacts.base_dir / filename
        if path.exists():
            await self.load_storage_state(filename)
            return {"path": str(path), "loaded": True}
        await login_callback()
        result = await self.save_storage_state(filename)
        result["created"] = True
        return result

    async def retry(
        self,
        func: Callable[[], Awaitable[dict]],
        policy: Optional[RetryPolicy] = None,
        retry_on: tuple[type[BaseException], ...] = (Exception,),
    ) -> dict:
        return await async_retry(func, policy or RetryPolicy(), retry_on=retry_on)

    async def get_console_logs(self) -> dict:
        return await self.client.get_console_logs()

    async def clear_console_logs(self) -> dict:
        return await self.client.clear_console_logs()

    async def get_video_path(self) -> dict:
        return await self.client.get_video_path()

    async def capture_artifacts(
        self,
        prefix: str = "run",
        full_page: bool = True,
        include_trace: bool = False,
        include_console: bool = True,
        include_video: bool = True,
        include_screenshot: bool = True,
    ) -> dict:
        self.artifacts.ensure_base_dir()
        results: dict[str, object] = {}

        if include_screenshot:
            shot_name = self.artifacts.screenshot_name(prefix=prefix)
            results["screenshot"] = await self.client.screenshot(path=shot_name, full_page=full_page)

        if include_console:
            logs = await self.get_console_logs()
            log_path = self.artifacts.base_dir / f"{prefix}-console.json"
            self.artifacts.write_json(log_path, logs)
            results["console_log"] = str(log_path)

        if include_video:
            results["video"] = await self.get_video_path()

        if include_trace:
            trace_name = self.artifacts.screenshot_name(prefix=f"{prefix}-trace", ext="zip")
            results["trace"] = await self.client.stop_tracing(path=trace_name)

        return results