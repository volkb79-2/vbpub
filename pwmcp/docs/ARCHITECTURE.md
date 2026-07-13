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


## P03: Opt-In Shared Persistent Browser Mode

`browser_mode` (ciu var under `[pwmcp.unified]`) selects between two runtime
topologies. Default is `"per-session"` — byte-identical to pre-P03 behavior
(verified: `containers/pwmcp/supervisord.conf` is unmodified by this
package; see `pwmcp/handoff/reports/P03-REPORT.md` for the diff evidence).
`"shared"` is opt-in.

```
per-session (default):                      shared (opt-in):
┌──────────────┐  ┌──────────────┐          ┌──────────────┐  ┌──────────────┐
│ @playwright/  │  │ chrome-      │          │ @playwright/  │  │ chrome-      │
│ mcp           │  │ devtools-mcp │          │ mcp           │  │ devtools-mcp │
│ (own browser) │  │ (own browser)│          │ --cdp-endpoint│  │ --browser-url│
└──────┬───────┘  └──────┬───────┘          └──────┬───────┘  └──────┬───────┘
       │ launches         │ launches                 │ attaches         │ attaches
       ▼                  ▼                          ▼                  ▼
  [chromium A]       [chromium B]              ┌──────────────────────────┐
  (per session,      (per session,              │  chromium (ONE process)  │
   isolated)          isolated)                 │  CDP 127.0.0.1:9222 only │
                                                 └──────────────────────────┘
                                                          ▲
                                                          │ health/reset/restart
                                                 ┌──────────────────────────┐
                                                 │  admin-server (Node std) │
                                                 │  0.0.0.0:8939 (internal) │
                                                 └──────────────────────────┘
lighthouse-mcp: unchanged in BOTH modes — always launches its own per-audit
Chromium via chrome-launcher (explicit P03 "Out Of Scope").
```

### Chromium launch flags (shared mode)

Pinned, explicit (see `containers/pwmcp/supervisord.shared.conf`
`[program:chromium]`):

```
<PWMCP_CHROMIUM_PATH> --headless=new   --remote-debugging-port=9222 --remote-debugging-address=127.0.0.1   --no-sandbox --disable-setuid-sandbox --disable-gpu   --user-data-dir=/tmp/pwmcp-shared-chromium-profile
```

`--remote-debugging-address=127.0.0.1` is the CDP-never-leaves-the-container
control (see docs/SECURITY.md) — the Docker network cannot route to
another container's loopback interface, so this is sufficient by itself;
the ciu compose template additionally never publishes a CDP port and never
adds a Traefik route for it.

### Mode plumbing

Both `supervisord.conf` (per-session) and `supervisord.shared.conf` (shared)
are baked into every image (`Dockerfile` `COPY`s both) — there are no
divergent Dockerfiles. `entrypoint.sh` reads `PWMCP_BROWSER_MODE`
(`per-session` default, or `shared`) and execs
`supervisord -c <selected-file>`. An unrecognized value is a fatal
entrypoint error (`exit 1` with a message on stderr), not a silent
fallback.

### Admin endpoint (shared mode only)

`admin-server` (`containers/pwmcp/admin-server/index.js`, Node stdlib
`http`/`net`/`crypto` only — no new framework dependency) exposes exactly
three routes, closed set, everything else 404, no request body is ever
parsed:

| Method | Path | Effect |
|---|---|---|
| `GET`  | `/browser/health`  | CDP liveness (`/json/version`) + open-target count (`/json/list`) + admin-server uptime |
| `POST` | `/browser/reset`   | `Target.getBrowserContexts` + `Target.disposeBrowserContext` over a hand-rolled CDP WebSocket client — closes all contexts (cookies/storage/pages gone) without killing the Chromium process |
| `POST` | `/browser/restart` | `supervisor.stopProcess`/`supervisor.startProcess("chromium")` over supervisord's unix-socket XML-RPC interface (the same transport `supervisorctl` uses) |

It talks to supervisord only for `stopProcess`/`startProcess` on the single
named `chromium` program — no broader RPC surface is used or exposed.
`admin_port` (default 8939) is injected into the container environment but
is **never** added to the compose `ports:` list and **never** given a
Traefik router, in either deployment mode — see docs/SECURITY.md.

### Startup ordering

`wait-for-cdp.sh` (`containers/pwmcp/wait-for-cdp.sh`) prefixes the `mcp`
and `devtools-mcp` program commands in shared mode: it polls
`http://127.0.0.1:9222/json/version` for up to 30s before exec'ing the real
attach command. If the browser never comes up in time, the wrapped command
is exec'd anyway — its own connection failure then triggers supervisord's
`autorestart`/`startretries` backoff rather than the program hanging
half-attached indefinitely.

### Optional idle recycle

`browser_max_idle_s` (default `0`, disabled). When set, `admin-server`
polls `/json/list` on an interval (`PWMCP_ADMIN_IDLE_CHECK_INTERVAL_S`,
default 5s) and restarts the `chromium` program (via the same
supervisord path as `/browser/restart`) once zero *consumer* targets have
been observed continuously for at least `browser_max_idle_s` seconds.

**Self-review correction (2026-07-13):** the original idle condition was
raw `/json/list` length === 0, which never fires in practice -- headless
Chromium always keeps its own default `chrome://newtab/` page, a
`chrome-untrusted://new-tab-page/...` iframe, and a Service Worker target
open for the process lifetime, so the target list is never actually empty.
"Idle" now means zero targets of CDP `type === "page"` with a `url` that
is not one of Chromium's own `chrome://` / `chrome-untrusted://` pages —
i.e. no consumer-navigated page left open. Verified live: with
`browser_max_idle_s=10` / `PWMCP_ADMIN_IDLE_CHECK_INTERVAL_S=2` and no page
navigated, `chromium`'s supervisord PID changed (recycled) within one
check interval of the deadline; see `pwmcp/handoff/reports/
P03-SELFREVIEW.md`.
