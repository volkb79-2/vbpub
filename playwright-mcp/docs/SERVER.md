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
- Console log export to artifacts
- HAR recording per session (optional)
- Cookie management
- Storage state export/import
- Optional video recording (`PLAYWRIGHT_VIDEO_DIR`)
- Health checks

## Feature list

- Multi-session WebSocket control with per-session routing (`session_id`)
- Live event stream for command telemetry (opt-in per session)
- Optional console event streaming (per session)
- Workspace isolation with per-session workspace + artifact directories
- Artifact APIs for screenshots, traces, console logs, HAR, storage state
- Optional browser pooling for faster test startup

## Environment Variables

### WebSocket server
- `WS_PORT` (default: `3000`)
- `WS_HOST` (default: `0.0.0.0`)
- `ACCESS_TOKEN` or `WS_AUTH_TOKEN`
- `AUTH_REQUIRED` (default: `true`)
- `WS_MAX_SESSIONS` (default: `10`)
- `WS_SESSION_TIMEOUT` (default: `3600`)
- `WS_EVENT_STREAM_ENABLED` (default: `true`)
- `WS_CONSOLE_STREAM_ENABLED` (default: `false`)
- `WS_ARTIFACT_ROOT` (default: `/screenshots`)
- `WS_WORKSPACE_ROOT` (default: `/workspaces`)
- `WS_ARTIFACT_MAX_BYTES` (default: `5242880`)
- `ARTIFACT_HTTP_ENABLED` (default: `false`)
- `ARTIFACT_HTTP_HOST` (default: `0.0.0.0`)
- `ARTIFACT_HTTP_PORT` (default: `8090`)
- `ARTIFACT_HTTP_AUTH_REQUIRED` (default: `true`)
- `WS_HAR_ENABLED` (default: `false`)
- `WS_HAR_CONTENT` (default: `omit`)
- `BROWSER_POOL_ENABLED` (default: `false`)
- `BROWSER_POOL_SIZE` (default: `4`)
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

Sessions, events, artifacts:
- `create_session`, `list_sessions`
- `event_stream` (enable/disable per session)
- `list_artifacts`, `get_artifact`
- `export_console_logs`

Authentication:
- `login` (simple form login helper)

Session routing:
- Every command accepts optional `session_id` in `args` to target a specific session.
- This enables parallel users (e.g., admin + user A + user B) in one client connection.

## Example: Start WebSocket Server

```bash
ACCESS_TOKEN=token WS_PORT=3000 python3 ws_server.py
```

## Example: Start MCP Server

```bash
ACCESS_TOKEN=token MCP_PORT=8765 python3 mcp_server.py
```

## Notes

- Traces, screenshots, and storage state are saved under `WS_ARTIFACT_ROOT/<workspace_id>/`.
- Console logs and HAR files are saved under `WS_ARTIFACT_ROOT/<workspace_id>/` when exported/recorded.
- Workspaces are isolated under `WS_WORKSPACE_ROOT/<workspace_id>/`.
- Video recording is only available when `PLAYWRIGHT_VIDEO_DIR` is set.
- `get_artifact` returns Base64 content for JSON transport.
- When `ARTIFACT_HTTP_ENABLED=true`, `list_artifacts` and `get_artifact` include `http_url` for direct download.
- Console events are emitted to the event stream when `WS_CONSOLE_STREAM_ENABLED=true`.
- Artifact HTTP downloads enforce `Authorization: Bearer <token>` when `ARTIFACT_HTTP_AUTH_REQUIRED=true`.
- `http_url` uses `ARTIFACT_HTTP_HOST`; set it to a client-reachable host instead of `0.0.0.0`.