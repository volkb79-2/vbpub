import asyncio
import os
import tempfile
import unittest
from pathlib import Path

from playwright_mcp_client import ArtifactManager, PlaywrightMCPConfig, UIHarness
from playwright_mcp_client.retry import RetryPolicy, async_retry


class FakeClient:
    def __init__(self) -> None:
        self.calls = []

    async def set_viewport_size(self, width: int, height: int):
        self.calls.append(("set_viewport_size", width, height))

    async def navigate(self, url: str, wait_until: str, timeout: int):
        self.calls.append(("navigate", url, wait_until, timeout))
        return {"url": url}

    async def wait_for_load_state(self, state: str):
        self.calls.append(("wait_for_load_state", state))

    async def click(self, selector: str):
        self.calls.append(("click", selector))

    async def fill(self, selector: str, value: str):
        self.calls.append(("fill", selector, value))

    async def login(self, **kwargs):
        self.calls.append(("login", kwargs))
        return {"logged_in": True}

    async def screenshot(self, path: str, full_page: bool):
        self.calls.append(("screenshot", path, full_page))
        return {"path": path}

    async def get_content(self):
        return {"content": "<html></html>", "url": "http://example"}

    async def start_tracing(self, **kwargs):
        self.calls.append(("start_tracing", kwargs))
        return {"started": True}

    async def stop_tracing(self, path: str):
        self.calls.append(("stop_tracing", path))
        return {"stopped": True, "path": path}

    async def cookies(self):
        return {"cookies": [{"name": "session", "value": "abc"}]}

    async def set_cookies(self, cookies):
        self.calls.append(("set_cookies", cookies))
        return {"set": len(cookies)}

    async def get_console_logs(self):
        return {"logs": []}

    async def clear_console_logs(self):
        return {"cleared": True}

    async def export_storage_state(self, path: str = None):
        self.calls.append(("export_storage_state", path))
        return {"path": path or "storage-state.json"}

    async def import_storage_state(self, state=None, path: str = None):
        self.calls.append(("import_storage_state", path))
        return {"imported": True}


class TestConfig(unittest.TestCase):
    def test_config_defaults(self):
        os.environ.pop("PWMCP_BASE_URL", None)
        os.environ.pop("PWMCP_EXTERNAL_BASE_URL", None)
        os.environ.pop("PWMCP_PROXY_BASE_URL", None)
        config = PlaywrightMCPConfig.from_env()
        self.assertEqual(config.ws_url, "ws://localhost:3000")
        self.assertEqual(config.wait_state, "networkidle")
        self.assertEqual(config.viewport_width, 1280)
        self.assertEqual(config.viewport_height, 720)


class TestArtifactManager(unittest.TestCase):
    def test_artifact_write(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            manager = ArtifactManager(base)
            manager.ensure_base_dir()
            self.assertTrue(base.exists())
            run_dir = manager.new_run_dir(prefix="test")
            self.assertTrue(run_dir.exists())


class TestUIHarness(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.config = PlaywrightMCPConfig(
            ws_url="ws://localhost:3000",
            auth_token="token",
            base_url="http://local-ui",
            external_base_url="https://example.com",
            proxy_base_url="http://localhost:8080",
            timeout=30.0,
            nav_timeout_ms=30000,
            wait_state="networkidle",
            viewport_width=1024,
            viewport_height=768,
            artifacts_dir=Path(self.temp_dir.name),
        )
        self.client = FakeClient()
        self.artifacts = ArtifactManager(Path(self.temp_dir.name))
        self.ui = UIHarness(client=self.client, config=self.config, artifacts=self.artifacts)

    async def test_build_url_proxy(self):
        url = self.ui.build_url("https://example.com/login")
        self.assertEqual(url, "http://localhost:8080/login")

    async def test_goto_sets_viewport(self):
        await self.ui.goto("/home")
        self.assertIn(("set_viewport_size", 1024, 768), self.client.calls)

    async def test_capture_html(self):
        result = await self.ui.capture_html("page.html")
        self.assertTrue(Path(result["path"]).exists())

    async def test_save_and_load_cookies(self):
        await self.ui.save_cookies("cookies.json")
        result = await self.ui.load_cookies("cookies.json")
        self.assertEqual(result["set"], 1)

    async def test_storage_state_roundtrip(self):
        await self.ui.save_storage_state("storage-state.json")
        result = await self.ui.load_storage_state("storage-state.json")
        self.assertTrue(result["imported"])


class TestRetry(unittest.IsolatedAsyncioTestCase):
    async def test_async_retry(self):
        attempts = 0

        async def flaky():
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise RuntimeError("fail")
            return "ok"

        policy = RetryPolicy(attempts=3, delay_seconds=0.01, backoff_factor=1.0, max_delay_seconds=0.01)
        result = await async_retry(flaky, policy)
        self.assertEqual(result, "ok")


if __name__ == "__main__":
    unittest.main()