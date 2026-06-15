# PWMCP — Playwright-as-a-Service

PWMCP is a thin, hardened [ciu](https://github.com/volkb79-2/vbpub/tree/main/ciu) packaging of the **official Microsoft Playwright images**. It runs two services so a project never installs a browser into its own devcontainer or CI runner.

## Services

| Service | Image | Port | Purpose |
|---|---|---|---|
| `pwmcp-playwright` | `ghcr.io/volkb79-2/pwmcp-playwright:<version>` | 3000 | Native Playwright `run-server` endpoint — full Playwright API via `connect()` |
| `pwmcp-mcp` | `mcr.microsoft.com/playwright/mcp:latest` | 8931 | MCP (HTTP/SSE) surface for AI clients (VS Code Copilot, etc.) at `/mcp` |

The `pwmcp-playwright` image is the official Microsoft Playwright base image with the matching playwright JS package pre-installed so `run-server` works offline in an egress-restricted container.

## Deployment Modes

**internal** (default): both services join the project Docker network with plain HTTP and no authentication. Sibling containers (test runner, devcontainer) reach them by **container name** on the shared network — never via `localhost`:

```
PLAYWRIGHT_SERVER_WS=ws://pwmcp-playwright:3000/
MCP endpoint: http://pwmcp-mcp:8931/mcp
```

The network boundary is the access control. This is the correct mode for dev and CI.

**external** (`external.enabled = true`): front with a running [tls-edge](https://github.com/volkb79-2/vbpub/tree/main/tls-edge) (Traefik). Each service gets a TLS-terminated route on its own hostname, protected by a per-route basicAuth guard. See `ciu.defaults.toml.j2` for the `[pwmcp.external]` settings.

## Quick Start

```bash
cd pwmcp
ciu --generate-env -d .
ciu -d .
```

## Consumer Connection

### Playwright `connect()` (test suites)

```python
# pip install playwright==1.60.0   ← must match the pinned version
from playwright.async_api import async_playwright

async with async_playwright() as p:
    browser = await p.chromium.connect("ws://pwmcp-playwright:3000/")
    page = await browser.new_page()
    await page.goto("https://example.com")
    await browser.close()
```

The `pip install playwright` version **must** match `pwmcp.playwright_version` in `ciu.defaults.toml.j2` (currently `1.60.0`). The Dockerfile bakes in the same version so the wire protocol is in lockstep.

### MCP (AI clients)

Add to your MCP client configuration (e.g. VS Code `settings.json`):

```json
{
  "mcp": {
    "servers": {
      "pwmcp-mcp": {
        "type": "http",
        "url": "http://pwmcp-mcp:8931/mcp"
      }
    }
  }
}
```

In external mode the URL becomes `https://<mcp_host>/mcp` (set `pwmcp.external.mcp_host`).

## Version Pin

`pwmcp.playwright_version` in `ciu.defaults.toml.j2` is the single source of truth. It pins:
- the base image tag (`mcr.microsoft.com/playwright:v<version>-<distro>`)
- the playwright JS package baked into `pwmcp-playwright`
- the version consumers must `pip install playwright==<version>`

When upgrading, update `playwright_version` (and `playwright_server.image.tag`) in `ciu.defaults.toml.j2` and rebuild the image (`docker buildx bake all --push`).

## Browser Isolation Hardening

Applied to **both services in both modes**:

- Run as UID/GID 1000 (non-root user shipped by the official base image)
- `cap_drop: ALL`
- `no-new-privileges: true`
- `shm_size: 2gb` (Chromium requires shared memory)

## Build & Push

```bash
# Build (local load)
docker buildx bake all --load

# Push to GHCR
GITHUB_USERNAME=<user> GITHUB_PUSH_PAT=<token> docker buildx bake all --push
```

Or use the ciu build runner:

```bash
ciu-build -d . build-images
ciu-build -d . push-images
```

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — two-service design
- [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) — internal and external deploy procedures
- [docs/SECURITY.md](docs/SECURITY.md) — browser isolation rationale and hardening
- [docs/USAGE.md](docs/USAGE.md) — consumer connect() and MCP usage details
