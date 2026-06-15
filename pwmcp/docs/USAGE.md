# Usage

## Playwright `connect()` — Test Suites

The `pwmcp-playwright` service exposes the native Playwright remote server protocol on port 3000. Test suites connect to it with `chromium.connect()` (or `firefox.connect()`, `webkit.connect()`) and get the **full Playwright API** — the same as running a local browser.

### Install Requirement

The `playwright` Python (or JS) package version **must match** the pinned `pwmcp.playwright_version` in `ciu.defaults.toml.j2` (currently `1.60.0`). Mismatched versions cause protocol errors.

```bash
pip install playwright==1.60.0
```

### Python Example

```python
import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        # container-name addressing on the shared Docker network
        browser = await p.chromium.connect("ws://pwmcp-playwright:3000/")
        page = await browser.new_page()
        await page.goto("https://example.com")
        screenshot = await page.screenshot()
        await browser.close()

asyncio.run(main())
```

### Environment Variable Pattern

Pass the WebSocket URL via environment variable so it works in both local dev and CI without code changes:

```bash
# In docker-compose or CI env:
PLAYWRIGHT_SERVER_WS=ws://pwmcp-playwright:3000/
```

```python
import os
from playwright.async_api import async_playwright

async def main():
    ws_url = os.environ["PLAYWRIGHT_SERVER_WS"]
    async with async_playwright() as p:
        browser = await p.chromium.connect(ws_url)
        # ... tests ...
        await browser.close()
```

### External Mode

In external mode the URL becomes a `wss://` URL with basicAuth credentials:

```python
browser = await p.chromium.connect(
    "wss://pw.example.com/",
    headers={"Authorization": "Basic <base64(user:secret)>"},
)
```

## MCP — AI Clients

The `pwmcp-mcp` service (`mcr.microsoft.com/playwright/mcp`) provides an MCP-compatible HTTP/SSE interface for AI clients such as VS Code Copilot.

### Endpoint

- Internal: `http://pwmcp-mcp:8931/mcp`
- External (tls-edge): `https://<mcp_host>/mcp`

SSE streaming is also available at `/sse`.

### VS Code MCP Configuration (internal)

```json
{
  "mcp": {
    "servers": {
      "playwright": {
        "type": "http",
        "url": "http://pwmcp-mcp:8931/mcp"
      }
    }
  }
}
```

This works when VS Code is running inside a devcontainer on the same Docker network as `pwmcp-mcp`.

### VS Code MCP Configuration (external with guard)

```json
{
  "mcp": {
    "servers": {
      "playwright": {
        "type": "http",
        "url": "https://pw-mcp.example.com/mcp",
        "headers": {
          "Authorization": "Basic <base64(pwmcp:secret)>"
        }
      }
    }
  }
}
```

### MCP Browser Options

The default browser is `chromium`. To change it or add extra options, set `pwmcp.playwright_mcp.browser` or `pwmcp.playwright_mcp.extra_args` in `ciu.toml.j2`:

```toml
[pwmcp.playwright_mcp]
browser = "chromium"
extra_args = "--caps=vision,pdf"
```

## Multiple Consumers

Both services support multiple simultaneous consumers:
- `pwmcp-playwright`: each `browser.connect()` call creates an independent browser session; isolate further by using separate browser contexts or pages
- `pwmcp-mcp`: the MCP image handles concurrent MCP clients

No per-consumer authentication exists in internal mode — the network boundary is the control.
