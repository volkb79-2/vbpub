import asyncio
import os
import tempfile
import unittest
from pathlib import Path

from pwmcp_client import (
    ArtifactManager,
    LayoutSelectors,
    PlaywrightMCPConfig,
    SessionManager,
    UIHarness,
    default_layout_selectors,
    merge_selectors,
    validate_selectors,
)
from pwmcp_client.retry import RetryPolicy, async_retry


class FakeClient:
    def __init__(self, base_dir: Path | None = None) -> None:
        self.calls = []
        self.base_dir = base_dir

    async def set_viewport_size(self, width: int, height: int):
        self.calls.append(("set_viewport_size", width, height))

    async def navigate(self, url: str, wait_until: str, timeout: int):
        self.calls.append(("navigate", url, wait_until, timeout))
        return {"url": url}

    async def wait_for_load_state(self, state: str, timeout: int = 30000):
        self.calls.append(("wait_for_load_state", state, timeout))

    async def click(self, selector: str, timeout: int = 10000, button: str = "left", click_count: int = 1):
        self.calls.append(("click", selector, timeout, button, click_count))

    async def fill(self, selector: str, value: str, timeout: int = 10000):
        self.calls.append(("fill", selector, value, timeout))

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
        name = path or "storage-state.json"
        if self.base_dir:
            storage_path = self.base_dir / name
            storage_path.write_text("{}", encoding="utf-8")
        return {"path": name}

    async def import_storage_state(self, state=None, path: str = None):
        self.calls.append(("import_storage_state", path))
        return {"imported": True}

    async def get_video_path(self):
        return {"path": "/tmp/video.webm"}


class TestConfig(unittest.TestCase):
    def test_config_defaults(self):
        os.environ.pop("PWMCP_BASE_URL", None)
        os.environ.pop("PWMCP_EXTERNAL_BASE_URL", None)
        os.environ.pop("PWMCP_PROXY_BASE_URL", None)
        config = PlaywrightMCPConfig.from_env()
        self.assertEqual(config.ws_url, "ws://localhost:3000")
        self.assertEqual(config.wait_state, "networkidle")
        self.assertEqual(config.action_timeout_ms, 10000)
        self.assertEqual(config.viewport_width, 1280)
        self.assertEqual(config.viewport_height, 720)
        self.assertTrue(config.headless)


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
            action_timeout_ms=15000,
            wait_state="networkidle",
            viewport_width=1024,
            viewport_height=768,
            headless=True,
            artifacts_dir=Path(self.temp_dir.name),
        )
        self.client = FakeClient(base_dir=Path(self.temp_dir.name))
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

    async def test_capture_artifacts(self):
        result = await self.ui.capture_artifacts(prefix="run", include_trace=False)
        self.assertIn("screenshot", result)
        self.assertIn("console_log", result)


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


class TestSelectors(unittest.TestCase):
    def test_default_selectors(self):
        selectors = default_layout_selectors()
        validate_selectors(selectors)
        self.assertIn("nav", selectors.required)

    def test_merge_selectors(self):
        selectors = merge_selectors(["main"], ["footer"])
        self.assertEqual(selectors.as_list(), ["main", "footer"])

    def test_validate_selectors_empty(self):
        with self.assertRaises(ValueError):
            validate_selectors(LayoutSelectors(required=()))


class TestSessionManager(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.client = FakeClient(base_dir=Path(self.temp_dir.name))
        self.artifacts = ArtifactManager(Path(self.temp_dir.name))
        self.session = SessionManager(client=self.client, artifacts=self.artifacts)

    async def test_session_save_and_load(self):
        await self.session.save()
        result = await self.session.load()
        self.assertTrue(result.storage_state_path.exists())
        self.assertTrue(result.cookies_path.exists())

    async def test_session_ensure(self):
        async def login_callback():
            return None

        result = await self.session.ensure(login_callback=login_callback)
        self.assertTrue(result.storage_state_path.exists())


if __name__ == "__main__":
    unittest.main()