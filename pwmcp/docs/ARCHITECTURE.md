# Architecture

## Overview

PWMCP is a thin ciu packaging of the **official Microsoft Playwright images**. It provides browser automation as a service so consuming projects never install a browser into their own devcontainer or CI runner.

Two services are composed together:

```
┌─────────────────────────────────────────────────────────────────────┐
│  Project Docker network (e.g. myproject-dev)                        │
│                                                                     │
│  ┌───────────────────────────┐  ┌────────────────────────────────┐  │
│  │  pwmcp-playwright         │  │  pwmcp-mcp                     │  │
│  │  (playwright-server)      │  │  (playwright/mcp)              │  │
│  │                           │  │                                │  │
│  │  mcr.microsoft.com/       │  │  mcr.microsoft.com/            │  │
│  │  playwright:v<ver>-noble  │  │  playwright/mcp:latest         │  │
│  │  + playwright@<ver> JS    │  │                                │  │
│  │                           │  │                                │  │
│  │  ENTRYPOINT: playwright   │  │  MCP (HTTP/SSE) at /mcp        │  │
│  │  run-server :3000         │  │  port 8931                     │  │
│  └───────────────────────────┘  └────────────────────────────────┘  │
│          ▲                                ▲                          │
│          │ ws://pwmcp-playwright:3000/    │ http://pwmcp-mcp:8931/mcp│
│  ┌───────┴──────────────────────────────┴──────────────────────┐   │
│  │  test runner / devcontainer / CI job (sibling container)     │   │
│  └─────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

## Service 1: pwmcp-playwright

- **Base image**: `mcr.microsoft.com/playwright:v<playwright_version>-<image_distro>` (ships browser binaries)
- **Layer added**: `playwright@<playwright_version>` JS package installed globally via npm (`PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1` to avoid re-downloading)
- **Entrypoint**: `playwright run-server --port 3000 --host 0.0.0.0`
- **Protocol**: native Playwright WebSocket protocol — consumers connect with `chromium.connect("ws://pwmcp-playwright:3000/")`
- **Image**: built from `containers/playwright-server/Dockerfile`; published to `ghcr.io/volkb79-2/pwmcp-playwright:<version>`

## Service 2: pwmcp-mcp

- **Image**: `mcr.microsoft.com/playwright/mcp:latest` (Microsoft's official MCP image)
- **Protocol**: MCP over HTTP/SSE at `/mcp` and `/sse` on port 8931
- **Consumer**: any MCP-capable AI client (VS Code Copilot, etc.)
- **Image tag**: `latest` — the MCP image has its own independent version line, not tied to the Playwright version

## Version Pin

`pwmcp.playwright_version` in `ciu.defaults.toml.j2` is the single source of truth:
- Determines the `playwright-server` base image tag
- Determines the playwright JS package version baked into the image
- Consumers must `pip install playwright==<playwright_version>` to match the wire protocol

## Deployment Modes

**internal** (default): services on the project Docker network only, plain HTTP, no auth. The network boundary is the access control.

pwmcp **joins the project network it is placed in** via the ciu `deploy.network_name` variable. When deployed as a sub-stack of a parent project (e.g. `dstdns`), containers are named `<project>-<env>-pwmcp-playwright` and `<project>-<env>-pwmcp-mcp` and join the parent project's network. They are reachable from any sibling container using the short service alias (`pwmcp-mcp:8931`, `ws://pwmcp-playwright:3000/`) or the full container name. When run standalone the project name defaults to `pwmcp`.

**external** (`pwmcp.external.enabled = true`): services also join the `ingress_public` network and gain Traefik labels for TLS termination and optional basicAuth guard via tls-edge.

## Hardening

Both services in both modes run with:
- `user: 1000:1000` (non-root, matches official image's built-in non-root user)
- `cap_drop: [ALL]`
- `security_opt: [no-new-privileges:true]`
- `shm_size: 2gb` (Chromium's shared-memory requirement)
