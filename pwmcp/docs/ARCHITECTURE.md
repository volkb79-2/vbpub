# Architecture

## Overview

PWMCP is a thin ciu packaging of the **official Microsoft Playwright unified image**. It provides browser automation as a service so consuming projects never install a browser into their own devcontainer or CI runner.

## Unified Image (Only Supported Mode)

The unified image bundles **both** the Playwright `run-server` and `@playwright/mcp` into a **single container** under a **single hostname**. This is the only deployment mode.

```
┌─────────────────────────────────────────────────────────────────────┐
│  Project Docker network (e.g. myproject-dev)                        │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  pwmcp  (unified image: ghcr.io/volkb79-2/pwmcp:<version>)  │  │
│  │                                                              │  │
│  │  ┌─────────────────────────┐  ┌──────────────────────────┐  │  │
│  │  │  playwright run-server  │  │  @playwright/mcp         │  │  │
│  │  │  (supervisord program)  │  │  (supervisord program)   │  │  │
│  │  │                         │  │                          │  │  │
│  │  │  :3000 WebSocket        │  │  :8931 HTTP/SSE at /mcp  │  │  │
│  │  └─────────────────────────┘  └──────────────────────────┘  │  │
│  │            supervisord (PID 1 — reaps, forwards SIGTERM)      │  │
│  └──────────────────────────────────────────────────────────────┘  │
│          ▲                                ▲                          │
│          │ ws://pwmcp:3000/               │ http://pwmcp:8931/mcp    │
│  ┌───────┴────────────────────────────────┴─────────────────────┐   │
│  │  test runner / devcontainer / AI client (sibling container)   │   │
│  └─────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

### Unified Image: `ghcr.io/volkb79-2/pwmcp:<version>`

- **Base image**: `mcr.microsoft.com/playwright:v<playwright_version>-<image_distro>` (ships browser binaries)
- **Layers added**:
  - `playwright@<playwright_version>` JS package installed globally via npm (needed for `run-server`)
  - `@playwright/mcp@0.0.76` installed globally via npm (MCP HTTP/SSE server; exposes `playwright-mcp` bin; pinned for reproducibility — track upstream when bumping PLAYWRIGHT_VERSION)
  - `supervisor` (apt) — PID-1 process manager
  - `/etc/pwmcp-chromium-path.txt` — baked chromium binary path (see below)
- **Process manager**: `supervisord --nodaemon` as PID 1; manages two programs (`run-server`, `mcp`)
- **Entrypoint**: `/usr/local/bin/pwmcp-entrypoint.sh` — exports `PWMCP_CHROMIUM_PATH` then execs supervisord
- **Ports**: 3000 (WebSocket) + 8931 (HTTP/SSE)
- **Built from**: `containers/pwmcp/Dockerfile`

### Chromium Path Resolution

`@playwright/mcp` bundles its own `playwright-core` which resolves a chromium binary path based on its internal chromium revision. This revision may not match the one in the Microsoft base image. The unified image resolves this at build time:

1. During the Docker build, `playwright.chromium.executablePath()` from the globally-installed `playwright@<version>` package is written to `/etc/pwmcp-chromium-path.txt`.
2. The entrypoint script exports this as `PWMCP_CHROMIUM_PATH`.
3. `supervisord.conf` passes `--executable-path %(ENV_PWMCP_CHROMIUM_PATH)s` to `playwright-mcp`, bypassing the bundled chromium discovery.

### Allowed Hosts

`@playwright/mcp` has DNS-rebinding protection. The unified container receives `PWMCP_MCP_ALLOWED_HOSTS` from the ciu compose template:

```
PWMCP_MCP_ALLOWED_HOSTS=pwmcp:8931,<project>-<env>-pwmcp:8931
```

`supervisord.conf` passes `--allowed-hosts %(ENV_PWMCP_MCP_ALLOWED_HOSTS)s` to `playwright-mcp`. The image default (for standalone use) is `localhost:8931,127.0.0.1:8931`.

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

## Deployment Modes

**internal** (default): service on the project Docker network only, plain HTTP, no auth. The network boundary is the access control.

PWMCP **joins the project network it is placed in** via the ciu `deploy.network_name` variable. When deployed as a sub-stack of a parent project (e.g. `dstdns`), the unified container is named `<project>-<env>-pwmcp` and joins the parent project's shared network. It is reachable from any sibling container using either the **short service alias** or the **full container name**:

```
ws://pwmcp:3000/                          # short alias (compose service name)
ws://<project>-<env>-pwmcp:3000/          # full container name
http://pwmcp:8931/mcp                     # MCP short alias
http://<project>-<env>-pwmcp:8931/mcp     # MCP full container name
```

**external** (`pwmcp.external.enabled = true`): the service also joins the `ingress_public` network and gains Traefik labels for TLS termination and optional basicAuth guard via tls-edge.

## Hardening

Both modes run with:
- `user: 1000:1000` (non-root, matches official image's built-in non-root user `ubuntu`)
- `cap_drop: [ALL]`
- `security_opt: [no-new-privileges:true]`
- `shm_size: 2gb` (Chromium's shared-memory requirement)
