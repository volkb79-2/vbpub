# P01 Implementation Report

## Summary

Implemented `chrome-devtools-mcp` (CDP-based performance tracing) as a third service
in the pwmcp unified container, exposed on port 8932 via `mcp-proxy` (stdio→HTTP proxy).

## Changes (12 modified + 3 new files)

### Container
- **Dockerfile**: Added `PLAYWRIGHT_MCP_VERSION` (templatized the hardcoded 0.0.76 pin),
  `CHROME_DEVTOOLS_MCP_VERSION` (1.5.0), `MCP_PROXY_VERSION` (6.5.2) as Docker ARGs.
  Installed `chrome-devtools-mcp` and `mcp-proxy` alongside existing packages.
  Added `PWMCP_DEVTOOLS_ALLOWED_HOSTS` env var (default `localhost:8932,127.0.0.1:8932`).
- **supervisord.conf**: Added `[program:devtools-mcp]` block running
  `mcp-proxy --command "chrome-devtools-mcp --headless --no-sandbox --executable-path ... --isolated" --port 8932`.

### CIU Templates
- **ciu.defaults.toml.j2**: Added `devtools_port=8932`, `host_devtools_port=8932`,
  `devtools_extra_args` to `[pwmcp.unified]`.
- **ciu.compose.yml.j2**: Added third port mapping, `PWMCP_DEVTOOLS_ALLOWED_HOSTS` env,
  Traefik router for devtools-mcp in external mode.

### Build System
- **docker-bake.hcl**: Bumped `PWMCP_VERSION_PYPI` → `1.61.0-r3`, `PWMCP_VERSION_NPM` → `1.61.1-r2`.
  Added `PLAYWRIGHT_MCP_VERSION`, `CHROME_DEVTOOLS_MCP_VERSION`, `MCP_PROXY_VERSION` variables
  and passed them as ARGs to both bake targets.
- **resolve-playwright-version.py**: Added `_read_bake_var()` helper, updated `write_release_vars()`
  to emit new package version vars, updated `main()` to read pins from bake file.
- **_vars.py**: Added new vars to `_REQUIRED_KEYS`.

### Validation
- **scripts/smoke-endpoints.sh**: Comprehensive smoke test covering MCP initialize with correct/forged
  Host headers, end-to-end tool call, supervisord status, and fault isolation.

### Documentation
- **README.md**: Updated endpoint table, added devtools consumer snippet, expanded pin list.
- **docs/ARCHITECTURE.md**: Three-program ASCII diagram, updated layers/pins/allowed-hosts.
- **docs/USAGE.md**: Added devtools to endpoint table, VS Code config snippets, multi-consumer notes.
- **docs/DEPLOYMENT.md**: Added port 8932 to endpoint references.
- **docs/SECURITY.md**: Documented mcp-proxy host-allowlist gap.

## Proxy Decision
- **Chosen**: `mcp-proxy@6.5.2` over `supergateway@3.4.3`
- **Rationale**: 5.82M vs 338k npm downloads/mo, Jun 2026 release vs Oct 2025, richer security features.
- **Trade-off**: mcp-proxy spawns one stdio child per server (not per HTTP session), so all MCP
  clients share one chrome-devtools-mcp instance. Acceptable for v1; per-session isolation can be
  revisited if needed.

## Gaps / Unresolved

### Host-header allowlist
`mcp-proxy` has no native `--allowed-hosts` flag (unlike `@playwright/mcp`).
The `PWMCP_DEVTOOLS_ALLOWED_HOSTS` env var is informational only — not enforced by the proxy.
Mitigated by Docker network boundary (internal mode) and Traefik TLS+basicAuth (external mode).
Documented in `docs/SECURITY.md`.

### Node version compatibility
`chrome-devtools-mcp@1.5.0` requires `^20.19.0 || ^22.12.0 || >=23`.
The base image (`mcr.microsoft.com/playwright:v1.61.0-noble`) ships Node.js at a version
determined by the Microsoft Playwright team. Verify compatibility during the first build:
`docker build` will fail at npm install if the Node version is insufficient.
If blocked, either upgrade the distro or pin an older chrome-devtools-mcp release.

### Smoke validation
The agent environment does not have Docker access, so `scripts/smoke-endpoints.sh` could not
be run. The script is committed and ready for the controller to execute against a locally
built + ciu-started stack.

## Files Changed
```
A  pwmcp/handoff/reports/P01-LOG.md
A  pwmcp/handoff/reports/P01-REPORT.md
A  pwmcp/scripts/smoke-endpoints.sh
M  pwmcp/README.md
M  pwmcp/ciu.compose.yml.j2
M  pwmcp/ciu.defaults.toml.j2
M  pwmcp/containers/pwmcp/Dockerfile
M  pwmcp/containers/pwmcp/supervisord.conf
M  pwmcp/docker-bake.hcl
M  pwmcp/docs/ARCHITECTURE.md
M  pwmcp/docs/DEPLOYMENT.md
M  pwmcp/docs/SECURITY.md
M  pwmcp/docs/USAGE.md
M  pwmcp/scripts/_vars.py
M  pwmcp/scripts/resolve-playwright-version.py
```
