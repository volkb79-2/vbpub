# Architecture

## Overview
PWMCP is a standalone browser automation service that exposes two interfaces:
- **WebSocket API** for full Playwright control (recommended for tests)
- **MCP HTTP endpoint** for VS Code Copilot/AI agents

The container is designed to run on any host, expose public ports, and support TLS via a reverse proxy.

## Components

### 1) WebSocket Service (ws_server.py)
- Multi-client connections
- One browser session per client
- Token authentication (required by default)
- Optional TLS (direct wss)

### 2) MCP Service (mcp_server.py)
- MCP tools for navigation/screenshot/evaluate
- Token authentication (required by default)
- Host/origin allowlist

### 3) Reverse Proxy (nginx)
- Terminates TLS with Let’s Encrypt certificates
- Routes `/ws` to WebSocket port 3000
- Routes `/mcp` to MCP port 8765
- Enforces HTTPS for public access

## Network Flow

```
Client ── wss://PUBLIC_FQDN/ws ──► Nginx ──► ws://pwmcp-server:3000
Client ── https://PUBLIC_FQDN/mcp ──► Nginx ──► http://pwmcp-server:8765
```

## Authentication
Both WS and MCP endpoints require a token when `AUTH_REQUIRED=true`. Tokens are provided via:
- `ACCESS_TOKEN` (shared default)
- `WS_AUTH_TOKEN` (override)
- `MCP_AUTH_TOKEN` (override)

## Storage
- `/workspaces/artifacts`: screenshots and artifacts captured by WS/MCP
- `/workspaces`: working directory for user scripts
- `/certs`: optional direct TLS certificates (if not using reverse proxy)

## Scaling Notes
- One Playwright browser process per container.
- Sessions are isolated per WebSocket client.
- Horizontal scaling: run multiple containers behind a load balancer.
