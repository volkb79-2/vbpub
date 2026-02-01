# PWMCP Standalone Service

Standalone PWMCP service with **WebSocket** and **MCP** interfaces, designed for multi-project browser automation with public access, strong authentication, and optional TLS via reverse proxy.

## Key Features

- WebSocket API for full Playwright control (recommended for tests)
- MCP server for VS Code Copilot chat
- Token authentication (required by default)
- Optional TLS via Nginx + Let’s Encrypt
- Optional video capture via `PLAYWRIGHT_VIDEO_DIR`
- Live event stream for command telemetry (per session)
- Optional console event streaming (per session)
- Workspace isolation with per-session artifact directories
- Artifact API for screenshots, traces, console logs, HARs, storage state
- Optional HTTP artifact download server
- Optional browser context pooling for faster test startups
- Buildx Bake build/push workflow
- CIU-ready standalone project (ships CIU defaults + compose templates)

## Quick Start

1. Copy and edit env file:

```bash
cp .env.sample .env
```

2. Set required fields:
- `ACCESS_TOKEN`
- `PUBLIC_FQDN` (for reverse proxy)
- `LETSENCRYPT_DIR` (parent directory, e.g., `/etc/letsencrypt`)

3. Start the container:

```bash
docker compose -f docker-compose.manual.yml up -d
```

4. (Optional) Start reverse proxy:

```bash
docker compose -f docker-compose.manual.yml --profile proxy up -d
```

## CIU Quick Start (Standalone)

If you prefer CIU orchestration, this repo includes standalone CIU configs:

```bash
cd pwmcp
ciu --generate-env -d .
ciu -d .
```

Standalone means: all CIU defaults and templates live inside this repo, so users can run
`ciu` directly after download without external configuration.

To expose direct WS/MCP/health ports without the reverse proxy, set:
`pwmcp_server.ports.expose_ws = true`, `expose_mcp = true`, `expose_health = true`, or `expose_artifact_http = true`
in [ciu.defaults.toml.j2](vbpub/pwmcp/ciu.defaults.toml.j2).

## Endpoints

- WS (direct): `ws://HOST:WS_EXTERNAL_PORT`
- MCP (direct): `http://HOST:MCP_EXTERNAL_PORT/mcp`
- WS (TLS): `wss://PUBLIC_FQDN/ws`
- MCP (TLS): `https://PUBLIC_FQDN/mcp`
- Health: `http://HOST:HEALTH_EXTERNAL_PORT/health`
- Selftest (egress): `http://HOST:HEALTH_EXTERNAL_PORT/selftest`
- Artifacts (optional): `http://HOST:ARTIFACT_HTTP_EXTERNAL_PORT/artifacts/<workspace_id>/<path>`

When using a public FQDN, ensure `MCP_ALLOWED_HOSTS` and `MCP_ALLOWED_ORIGINS`
include the public host to satisfy MCP transport security.

Artifact downloads require `Authorization: Bearer <token>` if `ARTIFACT_HTTP_AUTH_REQUIRED=true`.
Set `ARTIFACT_HTTP_HOST` to a client-reachable hostname to make `http_url` usable.

## Build & Push

```bash
./build-images.py
./push-images.py
```

## Browser selection

By default the container uses the system Chromium binary for stable builds.
You can override the Chromium source with environment variables:

- `PLAYWRIGHT_CHROMIUM_EXECUTABLE=/path/to/chromium` (preferred for stable)
- `PLAYWRIGHT_CHROMIUM_CHANNEL=chrome` (uses system Chrome channel, if installed)

## Usage Demo

Run the demo client script:

```bash
WS_URL=ws://localhost:3000 ACCESS_TOKEN=<token> python3 usage-demo.py
```

## CLI

```bash
pwmcp ws ping --url ws://localhost:3000 --token <token>
pwmcp ws navigate --url ws://localhost:3000 --token <token> --page https://example.com
pwmcp ws screenshot --url ws://localhost:3000 --token <token> --path /workspaces/artifacts/example.png
pwmcp ws health --url ws://localhost:3000 --token <token>
pwmcp ws login --url ws://localhost:3000 --token <token> --login-url https://app.example/login --username admin --password <secret>
pwmcp ws console-logs --url ws://localhost:3000 --token <token>
pwmcp ws trace-start --url ws://localhost:3000 --token <token>
pwmcp ws trace-stop --url ws://localhost:3000 --token <token> --path trace.zip
pwmcp ws state-export --url ws://localhost:3000 --token <token> --path storage-state.json
pwmcp ws state-import --url ws://localhost:3000 --token <token> --path storage-state.json
pwmcp ws video-path --url ws://localhost:3000 --token <token>
```

## Client Toolkit (pwmcp-client)

The client package provides reusable helpers for UI testing against the MCP WebSocket service:
- `PlaywrightMCPConfig` (env-driven config, proxy + viewport settings)
- `ArtifactManager` (consistent output paths)
- `UIHarness` (navigate/wait/click/fill, layout guards, artifact capture, storage state helpers)
- `SessionManager` + `SessionBundle` (persist storage state + cookies together)
- `RetryPolicy` + `async_retry` (retry with backoff)
 - `LayoutSelectors` helpers (app-specific layout guards)

Config highlights:
- `PWMCP_BASE_URL`, `PWMCP_EXTERNAL_BASE_URL`, `PWMCP_PROXY_BASE_URL`
- `PWMCP_TIMEOUT`, `PWMCP_NAV_TIMEOUT_MS`, `PWMCP_ACTION_TIMEOUT_MS`
- `PWMCP_VIEWPORT_WIDTH`, `PWMCP_VIEWPORT_HEIGHT`, `PWMCP_HEADLESS`
- `PWMCP_ARTIFACTS_DIR`

Import example:

```python
from pwmcp_client import PlaywrightMCPConfig, PlaywrightWSClient, UIHarness, ArtifactManager

config = PlaywrightMCPConfig.from_env()
artifacts = ArtifactManager(config.artifacts_dir)

async with PlaywrightWSClient(url=config.ws_url, auth_token=config.auth_token, timeout=config.timeout) as client:
	ui = UIHarness(client=client, config=config, artifacts=artifacts)
	await ui.goto("/login")
	await ui.assert_visible("#username")
	await ui.capture_artifacts(prefix="login")

session = SessionManager(client=client, artifacts=artifacts)
await session.ensure()

selectors = default_layout_selectors()
await ui.assert_layout(selectors.as_list())
```

## Build & Publish Wheels

```bash
./build-shared-wheel.py
./build-client-wheel.py
./build-server-wheel.py
./publish-client-wheel.py
./publish-server-wheel.py
```

## Client Toolkit Tests

```bash
./run-client-tests.py
```

## Documentation

- docs/ARCHITECTURE.md
- docs/SECURITY.md
- docs/DEPLOYMENT.md
- docs/USAGE.md
- docs/GAP-ANALYSIS.md
- docs/CONFIG-EXAMPLES.md
- docs/CLIENT.md
- docs/SERVER.md

## Notes

This project was extracted from `netcup-api-filter/tooling/playwright` and upgraded for public, multi-project use.
