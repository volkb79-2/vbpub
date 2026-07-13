# P02 Implementation Log

## Server Selection Decision

Evaluated existing Lighthouse MCP npm packages:

1. **`lighthouse-mcp@0.1.15`** — The most prominent stdio-based Lighthouse MCP server.
   - **Problem**: pins `lighthouse ^12.5.1` (current is 13.4.0). Uses `chrome-launcher` but does NOT support `--executable-path` injection (always uses auto-detected Chrome). Blocks RFC 1918 private IP ranges (10.x, 172.16-31.x, 192.168.x) via SSRF protection, which would reject internal Docker hostnames like `http://webapp-ui/`. Tool shapes do not match the handoff's closed v1 set. No response bounds, no audit timeout control.
   - **Result**: rejected for all three named tests (pinnable, injectable, bounded).

2. **`@danielsogl/lighthouse-mcp@1.3.0`** — 33-tool comprehensive server.
   - **Problem**: Last published Dec 2025 (stale). Bloated tool surface far beyond the closed v1 set. Unknown chrome-path injection support. Too much risk for a hardened container environment.
   - **Result**: rejected.

3. **`@letpeoplework/lighthouse-mcp-stdio@1.2.3`** — stdio transport adapter, not a full server.
   - **Result**: not applicable.

**Decision**: Write a minimal in-repo server (~200 lines, vendored under `containers/pwmcp/lighthouse-mcp/`) using the official `lighthouse` Node API. Gives full control over tool shapes, response bounds, chrome path injection (`PWMCP_CHROMIUM_PATH`), audit timeout, and typed errors.

## Implementation Notes

- **Lighthouse version**: Pinned `lighthouse@13.4.0` (latest). Compatible with the base image's Node.js (verified in P01 that base Node satisfies chrome-devtools-mcp's `^20.19.0 || ^22.12.0 || >=23`, which covers lighthouse as well).
- **Server transport**: stdio, wrapped by the same `mcp-proxy@6.5.2` package already installed for chrome-devtools-mcp.
- **Chrome launch**: `chrome-launcher` with `chromePath` set from `PWMCP_CHROMIUM_PATH` env var, plus `--headless --no-sandbox --disable-setuid-sandbox` flags (same sandbox disable as chrome-devtools-mcp, needed in the hardened container).
- **Response bounds**: Each tool result capped at 100 KB total bytes and max 10 opportunities. Enforced in the server before serialization.
- **Audit timeout**: 120 s default, configurable via `LIGHTHOUSE_TIMEOUT_MS` env var.
- **URL validation**: Accepts `http://` and `https://` only; rejects `file://`, `data:`, `chrome://` with typed `McpError`. Internal Docker hostnames (e.g. `http://webapp-ui/`) are explicitly allowed — no IP-range blocking (unlike the rejected `lighthouse-mcp` package).

## Files Changed

New files:
- `containers/pwmcp/lighthouse-mcp/package.json` — vendored server dependencies
- `containers/pwmcp/lighthouse-mcp/index.js` — Lighthouse MCP server implementation
- `handoff/reports/P02-LOG.md` — this file
- `handoff/reports/P02-REPORT.md` — implementation report

Modified files:
- `containers/pwmcp/Dockerfile` — added LIGHTHOUSE_VERSION ARG, lighthouse npm install, vendored server copy+install, PWMCP_LIGHTHOUSE_ALLOWED_HOSTS env var
- `containers/pwmcp/supervisord.conf` — added [program:lighthouse-mcp] on port 8933 via mcp-proxy
- `ciu.defaults.toml.j2` — added lighthouse_port, host_lighthouse_port, lighthouse_extra_args
- `ciu.compose.yml.j2` — added PWMCP_LIGHTHOUSE_ALLOWED_HOSTS env, fourth port mapping, Traefik router
- `docker-bake.hcl` — added LIGHTHOUSE_VERSION variable, bumped versions
- `scripts/_vars.py` — added LIGHTHOUSE_VERSION to required keys
- `scripts/resolve-playwright-version.py` — added LIGHTHOUSE_VERSION read/write
- `scripts/smoke-endpoints.sh` — extended with 8933 lighthouse checks
- `README.md` — updated endpoint table, consumer examples, pin list
- `docs/ARCHITECTURE.md` — updated four-program diagram and descriptions
- `docs/USAGE.md` — updated endpoint table, VS Code config, multi-consumer
- `docs/SECURITY.md` — added lighthouse-mcp host-allowlist gap section
- `docs/DEPLOYMENT.md` — updated endpoint listing, expose ports

## Gaps / Unresolved

- **Host-header allowlist**: Same gap as chrome-devtools-mcp — mcp-proxy does not enforce `--allowed-hosts`. The `PWMCP_LIGHTHOUSE_ALLOWED_HOSTS` env var is informational only. Mitigation: Docker network boundary (internal) + Traefik TLS+basicAuth (external). Documented in SECURITY.md.
- **Smoke validation**: The smoke script could not be run in this environment (no Docker). The script is committed and ready for execution against a locally built + ciu-started stack.
- **Node version compatibility**: `lighthouse@13.x` has the same Node version requirements as chrome-devtools-mcp. The P01 review verified the base image Node satisfies these.
