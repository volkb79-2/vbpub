# Playwright MCP Client Toolkit

This package is published as the `pwmcp-client` wheel and is tailored to the PWMCP WebSocket server in this repository.

It provides:
- A WebSocket client with feature parity to the server
- A UI testing harness with proxy-aware URL handling
- Consistent artifact management (screenshots, HTML dumps, trace paths)
- Retry utilities with backoff

## Installation

```bash
pip install pwmcp-client
```

## Environment Variables

The client reads configuration from environment variables:

- `PWMCP_WS_URL` or `WS_URL` (default: `ws://localhost:3000`)
- `PWMCP_AUTH_TOKEN` or `WS_AUTH_TOKEN` or `ACCESS_TOKEN`
- `PWMCP_BASE_URL` or `UI_BASE_URL` (base URL for relative paths)
- `PWMCP_EXTERNAL_BASE_URL` (external/public base URL)
- `PWMCP_PROXY_BASE_URL` (local proxy base URL for rewriting)
- `PWMCP_TIMEOUT` (default: `30`)
- `PWMCP_NAV_TIMEOUT_MS` (default: `30000`)
- `PWMCP_WAIT_STATE` (default: `networkidle`)
- `PWMCP_VIEWPORT_WIDTH` (default: `1280`)
- `PWMCP_VIEWPORT_HEIGHT` (default: `720`)
- `PWMCP_ARTIFACTS_DIR` (default: `artifacts`)

## Quick Start

```python
import asyncio

from pwmcp_client import PlaywrightMCPConfig, PlaywrightWSClient, UIHarness, ArtifactManager


async def main() -> None:
    config = PlaywrightMCPConfig.from_env()
    artifacts = ArtifactManager(config.artifacts_dir)

    async with PlaywrightWSClient(url=config.ws_url, auth_token=config.auth_token, timeout=config.timeout) as client:
        ui = UIHarness(client=client, config=config, artifacts=artifacts)

        await ui.goto("/login")
        await ui.assert_visible("#username")
        await ui.capture_screenshot("login")


if __name__ == "__main__":
    asyncio.run(main())
```

## Proxy-aware URL Handling

If you need to test an externally hosted UI through a localhost proxy, set:

```bash
export PWMCP_EXTERNAL_BASE_URL="https://example.com"
export PWMCP_PROXY_BASE_URL="http://localhost:8080"
```

`UIHarness.build_url()` rewrites URLs under `PWMCP_EXTERNAL_BASE_URL` to the proxy base.

## Artifacts

```python
await ui.capture_screenshot("home")
await ui.capture_html("home.html")
await ui.start_trace()
await ui.stop_trace("trace.zip")
```

## Console Logs

```python
logs = await ui.get_console_logs()
await ui.clear_console_logs()
```

## Cookies + Storage State

```python
await ui.save_cookies("cookies.json")
await ui.load_cookies("cookies.json")

await ui.save_storage_state("storage-state.json")
await ui.load_storage_state("storage-state.json")
```

## Retry Helper

```python
from pwmcp_client import RetryPolicy, async_retry


policy = RetryPolicy(attempts=3, delay_seconds=0.5, backoff_factor=2.0, max_delay_seconds=5.0)


async def go_home():
    return await ui.goto("/")


await async_retry(go_home, policy)
```

## CLI Examples

```bash
pwmcp ws ping --url ws://localhost:3000 --token <token>
pwmcp ws navigate --url ws://localhost:3000 --token <token> --page https://example.com
pwmcp ws screenshot --url ws://localhost:3000 --token <token> --path /screenshots/example.png
pwmcp ws console-logs --url ws://localhost:3000 --token <token>
pwmcp ws trace-start --url ws://localhost:3000 --token <token>
pwmcp ws trace-stop --url ws://localhost:3000 --token <token> --path trace.zip
pwmcp ws state-export --url ws://localhost:3000 --token <token> --path storage-state.json
pwmcp ws state-import --url ws://localhost:3000 --token <token> --path storage-state.json
pwmcp ws video-path --url ws://localhost:3000 --token <token>
```