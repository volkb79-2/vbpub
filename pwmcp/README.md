# PWMCP — Playwright-as-a-Service

PWMCP is a thin, hardened [ciu](https://github.com/volkb79-2/vbpub/tree/main/ciu) packaging of the **official Microsoft Playwright unified image**. It provides browser automation as a service so consuming projects never install a browser into their own devcontainer or CI runner.

## Unified Image

The unified image `ghcr.io/volkb79-2/pwmcp` bundles the Playwright `run-server`, `@playwright/mcp`, and `chrome-devtools-mcp` into a **single container** under a **single hostname** `pwmcp`:

| Endpoint | Port | Purpose |
|---|---|---|
| `ws://pwmcp:3000/` | 3000 | Native Playwright `connect()` — full API, test suites |
| `http://pwmcp:8931/mcp` | 8931 | `@playwright/mcp` (HTTP/SSE) — AI clients (VS Code Copilot, etc.) |
| `http://pwmcp:8932/mcp` | 8932 | `chrome-devtools-mcp` (CDP profiling via mcp-proxy) — performance tracing |

All services are managed by `supervisord` (PID 1), which reaps children and forwards SIGTERM on shutdown.

## Deployment Modes

**internal** (default): the container joins the project Docker network with plain HTTP and no authentication. Sibling containers (test runner, devcontainer) reach both endpoints by **container name** on the shared network — never via `localhost`:

```
ws://pwmcp:3000/              — Playwright connect() endpoint
http://pwmcp:8931/mcp         — @playwright/mcp endpoint
http://pwmcp:8932/mcp         — chrome-devtools-mcp (CDP profiling)
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

### MCP

#### `@playwright/mcp` (port 8931)

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

#### `chrome-devtools-mcp` (port 8932)

For performance profiling, CDP tracing, and CPU/network throttling emulation, add a second MCP server entry:

```json
{
  "mcp": {
    "servers": {
      "pwmcp": {
        "type": "http",
        "url": "http://pwmcp:8931/mcp"
      },
      "chrome-devtools": {
        "type": "http",
        "url": "http://pwmcp:8932/mcp"
      }
    }
  }
}
```

In external mode the URL becomes `https://<unified_host>/devtools/mcp` (the `/devtools` prefix is stripped by Traefik before forwarding to the backend).

Note: `chrome-devtools-mcp` is served via `mcp-proxy` (stdio→streamable-HTTP proxy). Unlike `@playwright/mcp`, it does not have native `--allowed-hosts` DNS-rebinding protection. In internal mode the Docker network boundary is the access control; see [SECURITY.md](docs/SECURITY.md) for details.

## `PWMCP_MCP_ALLOWED_HOSTS` Environment Variable

`@playwright/mcp` has DNS-rebinding protection: it rejects requests whose `Host` header does not match an allowlist. The ciu compose template injects `PWMCP_MCP_ALLOWED_HOSTS` at container start with the two ciu-derived container names:

```
PWMCP_MCP_ALLOWED_HOSTS=pwmcp:8931,<project>-<env>-pwmcp:8931
```

The image default is `localhost:8931,127.0.0.1:8931` for standalone usage. Override via `extra_args` in `ciu.toml.j2` to add further hosts.

## Package Version Pins

### Playwright Version

`pwmcp.playwright_version` in `ciu.defaults.toml.j2` is the single source of truth. It pins:
- the base image tag (`mcr.microsoft.com/playwright:v<version>-<distro>`)
- the playwright JS package baked into the image
- the version consumers must `pip install playwright==<version>`

### npm Package Pins

The following npm packages are pinned via `docker-bake.hcl` ARGs (with matching defaults in the `Dockerfile`):

| Package | Pin | Source |
|---|---|---|
| `@playwright/mcp` | `0.0.76` | MCP HTTP/SSE server for AI clients |
| `chrome-devtools-mcp` | `1.5.0` | CDP-based performance tracing and DevTools insights |
| `mcp-proxy` | `6.5.2` | stdio→streamable-HTTP proxy for chrome-devtools-mcp |

`@playwright/mcp` bundles playwright-core for chromium-1226, verified to work with Playwright 1.61.0 base images.
`chrome-devtools-mcp@1.5.0` targets Chrome/Chromium 130+ (DevTools Protocol compatibility).
`chrome-devtools-mcp` requires Node `^20.19.0 || ^22.12.0 || >=23` (verify compatibility when upgrading the base image).

## Browser Isolation Hardening

Applied in both modes:

- Run as UID/GID 1000 (non-root user shipped by the official base image)
- `cap_drop: ALL`
- `no-new-privileges: true`
- `shm_size: 2gb` (Chromium requires shared memory)

## Release Model

Versioned bundles are published to GitHub Releases under the `pwmcp-v<version>` tag:

```
pwmcp-v1.61.0-r2
  pwmcp-1.61.0-r2.tar.xz          ← the deployment bundle
  pwmcp-1.61.0-r2.tar.xz.sha256   ← sha256sum-verifiable sidecar
```

The release notes embed the SHA256 digest. Verify any downloaded bundle with:

```bash
sha256sum -c pwmcp-1.61.0-r2.tar.xz.sha256
```

"Latest" is resolved programmatically by scanning `pwmcp-v*` releases and picking the highest semver — this works in a monorepo where GitHub's repo-global "Latest" badge cannot be per-project. The thin `pwmcp-latest` release contains only `latest.json` (a JSON redirect pointing at the versioned release); it does **not** duplicate the heavy bundle asset.

### Downloading a specific version

```bash
VERSION="1.61.0-r2"
curl -fsSL "https://github.com/volkb79-2/vbpub/releases/download/pwmcp-v${VERSION}/pwmcp-${VERSION}.tar.xz" \
  -o "pwmcp-${VERSION}.tar.xz"
curl -fsSL "https://github.com/volkb79-2/vbpub/releases/download/pwmcp-v${VERSION}/pwmcp-${VERSION}.tar.xz.sha256" \
  -o "pwmcp-${VERSION}.tar.xz.sha256"
sha256sum -c "pwmcp-${VERSION}.tar.xz.sha256"
tar -xJf "pwmcp-${VERSION}.tar.xz"
```

## Build

```bash
# Build unified image (local load)
docker buildx bake pwmcp --load

# Push to GHCR
docker buildx bake pwmcp --push
```

Or use the ciu build runner:

```bash
ciu-build -d . build-images
ciu-build -d . push-images
```

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — unified image design and chromium path resolution
- [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) — internal and external deploy procedures
- [docs/SECURITY.md](docs/SECURITY.md) — browser isolation rationale and hardening
- [docs/USAGE.md](docs/USAGE.md) — consumer connect() and MCP usage details
