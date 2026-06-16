# Usage

## Version coupling — read this first

The Playwright wire protocol is **version-strict**: the client library version you `pip install`
must exactly match the Playwright version baked into the running `pwmcp-playwright` image.
A version mismatch causes protocol errors at connect time.

**How to read the required version from the bundle:**

```bash
grep 'playwright_version' ciu.defaults.toml.j2
# playwright_version = "1.60.0"
pip install playwright==1.60.0   # use that exact value — do not omit the pin
```

Do **not** run `playwright install` — browser binaries belong in the pwmcp container,
not in your project.

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

## Consumer Integration (external projects)

External projects consume pwmcp by downloading the versioned bundle from GitHub Releases.
No Docker build is needed — the image is on GHCR.

### Initial setup

```bash
# Pin a specific release or use "pwmcp-latest" for the rolling latest:
PWMCP_VERSION="pwmcp-v1.60.0-r1"
mkdir -p services/pwmcp
curl -fsSL "https://github.com/volkb79-2/vbpub/releases/download/${PWMCP_VERSION}/${PWMCP_VERSION#pwmcp-v}.tar.gz" \
  | tar -xJ --strip-components=1 -C services/pwmcp

# Read the required Playwright version from the bundle (wire protocol pin):
PW_VER=$(grep playwright_version services/pwmcp/ciu.defaults.toml.j2 | grep -oP '"\K[^"]+')
pip install playwright==${PW_VER}   # must match exactly — do not omit the pin

# Start the stack (installs ciu if needed: pip install ciu):
cd services/pwmcp && ciu --generate-env -d . && ciu -d .
```

Services come up on the `pwmcp` Docker network as `pwmcp-playwright` (port 3000) and
`pwmcp-mcp` (port 8931).

### Staying up-to-date

Download a newer bundle, re-read `playwright_version`, reinstall the pinned client, redeploy:

```bash
PWMCP_VERSION="pwmcp-v1.61.0-r1"
curl -fsSL "https://github.com/volkb79-2/vbpub/releases/download/${PWMCP_VERSION}/${PWMCP_VERSION#pwmcp-v}.tar.gz" \
  | tar -xJ --strip-components=1 -C services/pwmcp
PW_VER=$(grep playwright_version services/pwmcp/ciu.defaults.toml.j2 | grep -oP '"\K[^"]+')
pip install playwright==${PW_VER}
ciu -d services/pwmcp
```

### Connecting from consumer containers

Add the consumer service to the `pwmcp` Docker network so it can reach the services by
container name:

```yaml
services:
  my-test-runner:
    environment:
      PLAYWRIGHT_SERVER_WS: ws://pwmcp-playwright:3000/
    networks:
      - pwmcp   # join the pwmcp stack's network (never use localhost)

networks:
  pwmcp:
    external: true   # owned by the pwmcp stack; must be running first
```
