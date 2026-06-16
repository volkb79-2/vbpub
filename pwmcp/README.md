# PWMCP — Playwright-as-a-Service

PWMCP is a thin, hardened [ciu](https://github.com/volkb79-2/vbpub/tree/main/ciu) packaging of the **official Microsoft Playwright images**. It provides browser automation as a service so consuming projects never install a browser into their own devcontainer or CI runner.

## Unified Image (Recommended)

The unified image `ghcr.io/volkb79-2/pwmcp` bundles **both** the Playwright `run-server` and `@playwright/mcp` into a **single container** under a **single hostname** `pwmcp`:

| Endpoint | Port | Purpose |
|---|---|---|
| `ws://pwmcp:3000/` | 3000 | Native Playwright `connect()` — full API, test suites |
| `http://pwmcp:8931/mcp` | 8931 | MCP (HTTP/SSE) — AI clients (VS Code Copilot, etc.) |

Both services are managed by `supervisord` (PID 1), which reaps children and forwards SIGTERM on shutdown.

## Legacy Two-Image Setup

The legacy setup runs two separate services (set `pwmcp.unified.enabled = false` in `ciu.toml.j2` to activate):

| Service | Image | Port | Purpose |
|---|---|---|---|
| `pwmcp-playwright` | `ghcr.io/volkb79-2/pwmcp-playwright:<version>` | 3000 | Native Playwright `run-server` endpoint |
| `pwmcp-mcp` | `mcr.microsoft.com/playwright/mcp:latest` | 8931 | MCP (HTTP/SSE) surface |

## Deployment Modes

**internal** (default): the container joins the project Docker network with plain HTTP and no authentication. Sibling containers (test runner, devcontainer) reach both endpoints by **container name** on the shared network — never via `localhost`:

```
ws://pwmcp:3000/          — Playwright connect() endpoint
http://pwmcp:8931/mcp     — MCP endpoint
```

The network boundary is the access control. This is the correct mode for dev and CI.

**external** (`external.enabled = true`): front with a running [tls-edge](https://github.com/volkb79-2/vbpub/tree/main/tls-edge) (Traefik). Set `pwmcp.external.unified_host` to a domain name; each port gets a separate TLS route. Routes are protected by a per-route basicAuth guard. See `ciu.defaults.toml.j2` for the `[pwmcp.external]` settings.

## Quick Start

```bash
cd pwmcp
ciu --generate-env -d .
ciu -d .
```

Services come up as one container: `<project>-<env>-pwmcp` on the project network.

## Consumer Connection

### Playwright `connect()` (test suites)

```python
# pip install playwright==1.61.0   ← must match the pinned version
from playwright.async_api import async_playwright

async with async_playwright() as p:
    browser = await p.chromium.connect("ws://pwmcp:3000/")
    page = await browser.new_page()
    await page.goto("https://example.com")
    await browser.close()
```

The `pip install playwright` version **must** match `pwmcp.playwright_version` in `ciu.defaults.toml.j2` (currently `1.61.0`). The Dockerfile bakes in the same version so the wire protocol is in lockstep.

### MCP (AI clients)

Add to your MCP client configuration (e.g. VS Code `settings.json`):

```json
{
  "mcp": {
    "servers": {
      "pwmcp": {
        "type": "http",
        "url": "http://pwmcp:8931/mcp"
      }
    }
  }
}
```

In external mode the URL becomes `https://<unified_host>/mcp`.

## `PWMCP_MCP_ALLOWED_HOSTS` Environment Variable

`@playwright/mcp` has DNS-rebinding protection: it rejects requests whose `Host` header does not match an allowlist. The ciu compose template injects `PWMCP_MCP_ALLOWED_HOSTS` at container start with the two ciu-derived container names:

```
PWMCP_MCP_ALLOWED_HOSTS=pwmcp:8931,<project>-<env>-pwmcp:8931
```

The image default is `localhost:8931,127.0.0.1:8931` for standalone usage. Override via `extra_args` in `ciu.toml.j2` to add further hosts.

## Version Pin

`pwmcp.playwright_version` in `ciu.defaults.toml.j2` is the single source of truth. It pins:
- the base image tag (`mcr.microsoft.com/playwright:v<version>-<distro>`)
- the playwright JS package baked into the image
- the version consumers must `pip install playwright==<version>`

## Browser Isolation Hardening

Applied in both modes:

- Run as UID/GID 1000 (non-root user shipped by the official base image)
- `cap_drop: ALL`
- `no-new-privileges: true`
- `shm_size: 2gb` (Chromium requires shared memory)

## Build

```bash
# Build unified image (local load)
docker buildx bake pwmcp --load

# Build all images (unified + legacy playwright-server)
docker buildx bake all --load

# Push to GHCR
docker buildx bake all --push
```

Or use the ciu build runner:

```bash
ciu-build -d . build-images
ciu-build -d . push-images
```

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — unified and two-service design
- [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) — internal and external deploy procedures
- [docs/SECURITY.md](docs/SECURITY.md) — browser isolation rationale and hardening
- [docs/USAGE.md](docs/USAGE.md) — consumer connect() and MCP usage details
