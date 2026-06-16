# Usage

## Version coupling — read this first

The Playwright wire protocol is **version-strict**: the client library version you `pip install`
must exactly match the Playwright version baked into the running `pwmcp` image.
A version mismatch causes protocol errors at connect time.

**How to read the required version from the bundle:**

```bash
grep 'playwright_version' ciu.defaults.toml.j2
# playwright_version = "1.61.0"
pip install playwright==1.61.0   # use that exact value — do not omit the pin
```

Do **not** run `playwright install` — browser binaries belong in the pwmcp container,
not in your project.

## Unified Image: Two Services, One Hostname

The unified image exposes both endpoints from the single service alias `pwmcp`:

| Endpoint | URL | Purpose |
|---|---|---|
| Playwright run-server | `ws://pwmcp:3000/` | Full Playwright API via `connect()` |
| MCP | `http://pwmcp:8931/mcp` | MCP-compatible AI clients |

## Playwright `connect()` — Test Suites

The `pwmcp` service exposes the native Playwright remote server protocol on port 3000. Test suites connect to it with `chromium.connect()` (or `firefox.connect()`, `webkit.connect()`) and get the **full Playwright API**.

### Install Requirement

The `playwright` Python (or JS) package version **must match** the pinned `pwmcp.playwright_version` in `ciu.defaults.toml.j2` (currently `1.61.0`). Mismatched versions cause protocol errors.

```bash
pip install playwright==1.61.0
```

### Python Example

```python
import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        # container-name addressing on the shared Docker network
        browser = await p.chromium.connect("ws://pwmcp:3000/")
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
PLAYWRIGHT_SERVER_WS=ws://pwmcp:3000/
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

The `pwmcp` service provides an MCP-compatible HTTP/SSE interface for AI clients such as VS Code Copilot at port 8931.

### Endpoint

- Internal: `http://pwmcp:8931/mcp`
- External (tls-edge): `https://<unified_host>/mcp`

SSE streaming is also available at `/sse`.

### `PWMCP_MCP_ALLOWED_HOSTS` and DNS-rebinding protection

`@playwright/mcp` implements DNS-rebinding protection: every incoming request is checked against an allowlist of permitted `Host` header values. The server's default allowlist contains only its bind address (`0.0.0.0`), which does **not** match the `Host: pwmcp:8931` header sent by a sibling container accessing the service by name. Without correction this returns **HTTP 403** to every internal caller.

The ciu template resolves this by injecting `PWMCP_MCP_ALLOWED_HOSTS` with both ciu-derived names for the container:

```
PWMCP_MCP_ALLOWED_HOSTS=pwmcp:8931,<project>-<env>-pwmcp:8931
```

The `supervisord.conf` passes `--allowed-hosts %(ENV_PWMCP_MCP_ALLOWED_HOSTS)s` to `playwright-mcp`. This is the preferred fix: it pins the allowlist to known internal names rather than using `*` (which disables the check). The Docker network boundary already controls who can reach the port.

To add extra allowed hosts (e.g. a custom DNS alias), set `extra_args` in `ciu.toml.j2`:

```toml
[pwmcp.unified]
extra_args = "my-alias:8931"
```

To disable the check entirely (not recommended; use only if you control the network):

```toml
[pwmcp.unified]
extra_args = "*"
```

### VS Code MCP Configuration (internal)

```json
{
  "mcp": {
    "servers": {
      "playwright": {
        "type": "http",
        "url": "http://pwmcp:8931/mcp"
      }
    }
  }
}
```

This works when VS Code is running inside a devcontainer on the same Docker network as `pwmcp`.

### VS Code MCP Configuration (external with guard)

```json
{
  "mcp": {
    "servers": {
      "playwright": {
        "type": "http",
        "url": "https://pw.example.com/mcp",
        "headers": {
          "Authorization": "Basic <base64(pwmcp:secret)>"
        }
      }
    }
  }
}
```

### MCP Browser Options

The default browser is `chromium` (the only browser available in the unified image via `--executable-path`). The `browser` setting in `pwmcp.unified` controls the browser argument passed to `playwright-mcp`. To change browser type or add capabilities, set in `ciu.toml.j2`:

```toml
[pwmcp.unified]
browser = "chromium"
extra_args = "my-alias:8931"   # comma-separated extra allowed-hosts
```

For `--caps` or other playwright-mcp flags, they cannot be passed directly via the current `extra_args` field (which only controls allowed-hosts extensions). For advanced configuration, override the supervisord.conf by mounting a custom one.

## Multiple Consumers

Both services support multiple simultaneous consumers:
- `run-server` (port 3000): each `browser.connect()` call creates an independent browser session; isolate further by using separate browser contexts or pages
- `@playwright/mcp` (port 8931): the MCP server handles concurrent MCP clients

No per-consumer authentication exists in internal mode — the network boundary is the control.

## Consumer Integration (external projects)

External projects consume pwmcp by downloading the versioned bundle from GitHub Releases.
No Docker build is needed — the image is on GHCR.

### Resolving the latest version

Scan `pwmcp-v*` releases for the highest semver, or check `pwmcp-latest/latest.json` for the thin redirect:

```bash
# latest.json contains: version, tag, asset name, sha256, download URL
curl -fsSL "https://github.com/volkb79-2/vbpub/releases/download/pwmcp-latest/latest.json"
```

### Initial setup

```bash
# Pin a specific release:
VERSION="1.61.0-r2"
mkdir -p services/pwmcp
curl -fsSL "https://github.com/volkb79-2/vbpub/releases/download/pwmcp-v${VERSION}/pwmcp-${VERSION}.tar.xz" \
  -o "pwmcp-${VERSION}.tar.xz"

# Verify the bundle (sidecar lives in the same release):
curl -fsSL "https://github.com/volkb79-2/vbpub/releases/download/pwmcp-v${VERSION}/pwmcp-${VERSION}.tar.xz.sha256" \
  -o "pwmcp-${VERSION}.tar.xz.sha256"
sha256sum -c "pwmcp-${VERSION}.tar.xz.sha256"

tar -xJf "pwmcp-${VERSION}.tar.xz" --strip-components=1 -C services/pwmcp

# Read the required Playwright version from the bundle (wire protocol pin):
PW_VER=$(grep playwright_version services/pwmcp/ciu.defaults.toml.j2 | grep -oP '"\K[^"]+')
pip install playwright==${PW_VER}   # must match exactly — do not omit the pin

# Start the stack (installs ciu if needed: pip install ciu):
cd services/pwmcp && ciu --generate-env -d . && ciu -d .
```

The unified container comes up as `pwmcp-pwmcp` (default project name `pwmcp`) on the `pwmcp` Docker network, serving port 3000 (WS) and port 8931 (MCP).

### Staying up-to-date

Download a newer bundle, verify it, re-read `playwright_version`, reinstall the pinned client, redeploy:

```bash
VERSION="1.62.0-r1"
curl -fsSL "https://github.com/volkb79-2/vbpub/releases/download/pwmcp-v${VERSION}/pwmcp-${VERSION}.tar.xz" \
  -o "pwmcp-${VERSION}.tar.xz"
curl -fsSL "https://github.com/volkb79-2/vbpub/releases/download/pwmcp-v${VERSION}/pwmcp-${VERSION}.tar.xz.sha256" \
  -o "pwmcp-${VERSION}.tar.xz.sha256"
sha256sum -c "pwmcp-${VERSION}.tar.xz.sha256"
tar -xJf "pwmcp-${VERSION}.tar.xz" --strip-components=1 -C services/pwmcp
PW_VER=$(grep playwright_version services/pwmcp/ciu.defaults.toml.j2 | grep -oP '"\K[^"]+')
pip install playwright==${PW_VER}
ciu -d services/pwmcp
```

### Connecting from consumer containers

Add the consumer service to the `pwmcp` Docker network so it can reach the service by container name:

```yaml
services:
  my-test-runner:
    environment:
      PLAYWRIGHT_SERVER_WS: ws://pwmcp:3000/
      MCP_URL: http://pwmcp:8931/mcp
    networks:
      - pwmcp   # join the pwmcp stack's network (never use localhost)

networks:
  pwmcp:
    external: true   # owned by the pwmcp stack; must be running first
```
