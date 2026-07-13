# Architecture

## Overview

PWMCP is a thin ciu packaging of the **official Microsoft Playwright unified image**. It provides browser automation as a service so consuming projects never install a browser into their own devcontainer or CI runner.

## Unified Image (Only Supported Mode)

The unified image bundles the Playwright `run-server`, `@playwright/mcp`, `chrome-devtools-mcp`, and `lighthouse-mcp` into a **single container** under a **single hostname**. This is the only deployment mode.

```
┌────────────────────────────────────────────────────────────────────────────────────────────────────┐
│  Project Docker network (e.g. myproject-dev)                                                       │
│                                                                                                    │
│  ┌─────────────────────────────────────────────────────────────────────────────────────────────┐  │
│  │  pwmcp  (unified image: ghcr.io/volkb79-2/pwmcp:<version>)                                 │  │
│  │                                                                                             │  │
│  │  ┌─────────────────────────┐  ┌──────────────────────────┐  ┌──────────────┐ ┌───────────┐ │  │
│  │  │  playwright run-server  │  │  @playwright/mcp         │  │  mcp-proxy   │ │ mcp-proxy │ │  │
│  │  │  (supervisord program)  │  │  (supervisord program)   │  │  (supervisor)│ │(supervisor)│ │  │
│  │  │                         │  │                          │  │  ↓           │ │  ↓         │ │  │
│  │  │  :3000 WebSocket        │  │  :8931 HTTP/SSE at /mcp  │  │  chrome-     │ │ lighthouse-│ │  │
│  │  │                         │  │                          │  │  devtools-   │ │ mcp :8933  │ │  │
│  │  │                         │  │                          │  │  mcp :8932   │ │            │ │  │
│  │  └─────────────────────────┘  └──────────────────────────┘  └──────────────┘ └───────────┘ │  │
│  │            supervisord (PID 1 — reaps, forwards SIGTERM)                                     │  │
│  └─────────────────────────────────────────────────────────────────────────────────────────────┘  │
│          ▲                                ▲                    ▲                  ▲                  │
│          │ ws://pwmcp:3000/               │               http://            http://                │
│          │                                │ http://         pwmcp:8932/mcp    pwmcp:8933/mcp        │
│          │                                │ pwmcp:8931/mcp                                        │
│  ┌───────┴────────────────────────────────┴──────────────────────────────────────────────────┐   │
│  │  test runner / devcontainer / AI client / profiling / audit tool (sibling container)       │   │
│  └──────────────────────────────────────────────────────────────────────────────────────────┘   │
└────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

### Unified Image: `ghcr.io/volkb79-2/pwmcp:<version>`

- **Base image**: `mcr.microsoft.com/playwright:v<playwright_version>-<image_distro>` (ships browser binaries)
- **Layers added**:
  - `playwright@<playwright_version>` JS package installed globally via npm (needed for `run-server`)
  - `@playwright/mcp@<version>` installed globally via npm (MCP HTTP/SSE server; pinned for reproducibility)
  - `chrome-devtools-mcp@1.5.0` installed globally via npm (CDP-based MCP server; stdio-only, wrapped by mcp-proxy)
  - `lighthouse@13.4.0` installed globally via npm (Node API for programmatic audits)
  - `mcp-proxy@6.5.2` installed globally via npm (stdio→streamable-HTTP proxy for chrome-devtools-mcp and lighthouse-mcp)
  - `lighthouse-mcp` vendored server at `/opt/pwmcp/lighthouse-mcp/` (in-repo, ~200 lines)
  - `supervisor` (apt) — PID-1 process manager
  - `/etc/pwmcp-chromium-path.txt` — baked chromium binary path (see below)
- **Process manager**: `supervisord --nodaemon` as PID 1; manages four programs (`run-server`, `mcp`, `devtools-mcp`, `lighthouse-mcp`)
- **Entrypoint**: `/usr/local/bin/pwmcp-entrypoint.sh` — exports `PWMCP_CHROMIUM_PATH` then execs supervisord
- **Ports**: 3000 (WebSocket) + 8931 (HTTP/SSE @playwright/mcp) + 8932 (HTTP/SSE chrome-devtools-mcp via mcp-proxy) + 8933 (HTTP/SSE lighthouse-mcp via mcp-proxy)
- **Built from**: `containers/pwmcp/Dockerfile`

### Chromium Path Resolution

`@playwright/mcp` bundles its own `playwright-core` which resolves a chromium binary path based on its internal chromium revision. This revision may not match the one in the Microsoft base image. The unified image resolves this at build time:

1. During the Docker build, `playwright.chromium.executablePath()` from the globally-installed `playwright@<version>` package is written to `/etc/pwmcp-chromium-path.txt`.
2. The entrypoint script exports this as `PWMCP_CHROMIUM_PATH`.
3. `supervisord.conf` passes `--executable-path %(ENV_PWMCP_CHROMIUM_PATH)s` to `playwright-mcp` and `chrome-devtools-mcp` (via mcp-proxy), bypassing the bundled chromium discovery.
   The lighthouse-mcp server reads `PWMCP_CHROMIUM_PATH` at runtime and passes it to `chrome-launcher` as `chromePath`.

### Allowed Hosts

`@playwright/mcp` has DNS-rebinding protection via its `--allowed-hosts` flag. The unified container receives `PWMCP_MCP_ALLOWED_HOSTS` from the ciu compose template:

```
PWMCP_MCP_ALLOWED_HOSTS=pwmcp:8931,<project>-<env>-pwmcp:8931
```

`supervisord.conf` passes `--allowed-hosts %(ENV_PWMCP_MCP_ALLOWED_HOSTS)s` to `playwright-mcp`. The image default (for standalone use) is `localhost:8931,127.0.0.1:8931`.

`chrome-devtools-mcp` (via `mcp-proxy`) does **not** have a native `--allowed-hosts` flag. A parallel `PWMCP_DEVTOOLS_ALLOWED_HOSTS` env var is injected by the ciu compose template for documentation and external-mode Traefik rules:

```
PWMCP_DEVTOOLS_ALLOWED_HOSTS=pwmcp:8932,<project>-<env>-pwmcp:8932
```

`lighthouse-mcp` (also via `mcp-proxy`) has the same allowlist gap. A `PWMCP_LIGHTHOUSE_ALLOWED_HOSTS` env var tracks hostnames for documentation and Traefik rules:

```
PWMCP_LIGHTHOUSE_ALLOWED_HOSTS=pwmcp:8933,<project>-<env>-pwmcp:8933
```

See [SECURITY.md](SECURITY.md) for the host-allowlist gap analysis.

## Release Model

Versioned bundles are published to GitHub Releases following the monorepo-wide scheme:

- **Immutable versioned release**: `pwmcp-v<version>` — carries the bundle artifact plus a `.sha256` sidecar. The SHA256 is also recorded in the release notes for verification without downloading the sidecar. This is the source of truth.
- **Thin "latest" redirect**: `pwmcp-latest` — contains only `latest.json`, a manifest pointing at the versioned release. No heavy asset duplication. This exists only as a stable discovery URL.
- **Resolving "latest"**: scan `pwmcp-v*` releases and pick the highest semver. This is monorepo-safe (GitHub's repo-global "Latest" badge is not per-project).

### Bundle verification

```bash
sha256sum -c pwmcp-<version>.tar.xz.sha256
```

## Version Pin

`pwmcp.playwright_version` in `ciu.defaults.toml.j2` is the single source of truth:
- Determines the base image tag for the unified image
- Determines the playwright JS package version baked into the image
- Consumers must `pip install playwright==<playwright_version>` to match the wire protocol

Additional npm package pins (see `docker-bake.hcl`):
- `@playwright/mcp@<version>` — MCP HTTP/SSE server
- `chrome-devtools-mcp@1.5.0` — CDP profiling MCP server
- `mcp-proxy@6.5.2` — stdio→streamable-HTTP proxy (used by both chrome-devtools-mcp and lighthouse-mcp)
- `lighthouse@13.4.0` — Node API for programmatic Lighthouse audits

## Deployment Modes

**internal** (default): service on the project Docker network only, plain HTTP, no auth. The network boundary is the access control.

PWMCP **joins the project network it is placed in** via the ciu `deploy.network_name` variable. When deployed as a sub-stack of a parent project (e.g. `dstdns`), the unified container is named `<project>-<env>-pwmcp` and joins the parent project's shared network. It is reachable from any sibling container using either the **short service alias** or the **full container name**:

```
ws://pwmcp:3000/                          # short alias (compose service name)
ws://<project>-<env>-pwmcp:3000/          # full container name
http://pwmcp:8931/mcp                     # @playwright/mcp short alias
http://<project>-<env>-pwmcp:8931/mcp     # @playwright/mcp full container name
http://pwmcp:8932/mcp                     # chrome-devtools-mcp short alias
http://<project>-<env>-pwmcp:8932/mcp     # chrome-devtools-mcp full container name
http://pwmcp:8933/mcp                     # lighthouse-mcp short alias
http://<project>-<env>-pwmcp:8933/mcp     # lighthouse-mcp full container name
```

**external** (`pwmcp.external.enabled = true`): the service also joins the `ingress_public` network and gains Traefik labels for TLS termination and optional basicAuth guard via tls-edge.

## Hardening

Both modes run with:
- `user: 1000:1000` (non-root, matches official image's built-in non-root user `ubuntu`)
- `cap_drop: [ALL]`
- `security_opt: [no-new-privileges:true]`
- `shm_size: 2gb` (Chromium's shared-memory requirement)
