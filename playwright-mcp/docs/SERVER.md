# Playwright MCP Server

This service exposes a Playwright-driven browser via WebSocket and MCP endpoints. It is intended to run as a shared testing service used by multiple projects.

## Interfaces

- WebSocket server (recommended for tests): `ws://HOST:WS_PORT`
- MCP server (for Copilot or tooling): `http://HOST:MCP_PORT/mcp`

## Core Capabilities

- Navigation, clicking, typing, and evaluation
- Screenshots and HTML capture
- Tracing (trace.zip) for performance analysis
- Console log capture and clearing
- Cookie management
- Storage state export/import
- Optional video recording (`PLAYWRIGHT_VIDEO_DIR`)
- Health checks

## Environment Variables

### WebSocket server
- `WS_PORT` (default: `3000`)
- `WS_HOST` (default: `0.0.0.0`)
- `ACCESS_TOKEN` or `WS_AUTH_TOKEN`
- `AUTH_REQUIRED` (default: `true`)
- `WS_MAX_SESSIONS` (default: `10`)
- `WS_SESSION_TIMEOUT` (default: `3600`)
- `PLAYWRIGHT_HEADLESS` (default: `true`)
- `PLAYWRIGHT_BROWSER` (default: `chromium`)
- `PLAYWRIGHT_VIDEO_DIR` (optional; enables video capture)
- `SSL_ENABLED` (default: `false`)
- `SSL_CERT_PATH` / `SSL_KEY_PATH`

### MCP server
- `MCP_PORT` (default: `8765`)
- `MCP_SERVER_NAME` (default: `playwright-mcp`)
- `MCP_AUTH_TOKEN` or `ACCESS_TOKEN`
- `AUTH_REQUIRED` (default: `true`)
- `MCP_ALLOWED_HOSTS`
- `MCP_ALLOWED_ORIGINS`
- `PLAYWRIGHT_HEADLESS`, `PLAYWRIGHT_BROWSER`

## WebSocket Commands

Basic:
- `navigate`, `click`, `fill`, `type`, `press`, `evaluate`
- `get_content`, `get_url`, `wait_for_selector`, `wait_for_url`, `wait_for_load_state`
- `screenshot`

State and diagnostics:
- `cookies`, `set_cookies`, `clear_cookies`
- `export_storage_state`, `import_storage_state`
- `get_console_logs`, `clear_console_logs`
- `start_tracing`, `stop_tracing`
- `get_video_path`
- `health`, `close_session`

Authentication:
- `login` (simple form login helper)

## Example: Start WebSocket Server

```bash
ACCESS_TOKEN=token WS_PORT=3000 python3 ws_server.py
```

## Example: Start MCP Server

```bash
ACCESS_TOKEN=token MCP_PORT=8765 python3 mcp_server.py
```

## Notes

- Traces and screenshots are saved to `/screenshots` inside the container.
- Storage state exported to `path` is saved under `/screenshots`.
- Video recording is only available when `PLAYWRIGHT_VIDEO_DIR` is set.