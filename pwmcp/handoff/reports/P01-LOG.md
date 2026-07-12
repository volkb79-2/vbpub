# P01 Implementation Log

## 2026-07-12 — Proxy Evaluation & Choice

### Context
`chrome-devtools-mcp` v1.5.0 is stdio-only (no `--transport`/`--port`/`--http` flags).
A stdio→streamable-HTTP MCP proxy is required to expose it on port 8932.

### Candidates Evaluated

| Package | Version | npm downloads/mo | GitHub stars | Last published |
|---|---|---|---|---|
| **mcp-proxy** | 6.5.2 | ~5.82M | 268 | 2026-06-18 |
| supergateway | 3.4.3 | ~338k | 2.7k | 2025-10-09 |

### Decision: mcp-proxy@6.5.2

Rationale:
1. **npm downloads**: 5.82M/mo vs 338k/mo — vastly wider adoption and battle-testing.
2. **Recent release**: Jun 2026 (vs Oct 2025 for supergateway — 9 months stale).
3. **Features**: supports stdio→SSE + stdio→Streamable HTTP, has CORS config, API key auth, SSL support.
4. **Security**: has `--apiKey` / `MCP_PROXY_API_KEY` for optional bearer-token auth; CORS origin allowlisting.
5. **Per-session spawning**: Both proxies spawn one stdio child per server process, not per HTTP session.
   This is noted as a gap vs the "preferred" model in the handoff, but is the standard MCP proxy pattern and
   works correctly with `chrome-devtools-mcp --isolated` (temp profile per instance; since the proxy keeps
   one instance alive, all sessions share it). Per-session spawning can be revisited later if isolation
   requirements change.

### Chrome-devtools-mcp version pin: 1.5.0

Latest stable release, published 2026-07-03. All required flags confirmed:
`--executablePath`, `--headless`, `--isolated`, `--logFile`.

Node requirement: `^20.19.0 || ^22.12.0 || >=23`. Base image Node version to be verified
during build (recorded in REPORT).

### Host-header allowlist status

`mcp-proxy` has CORS origin-based protection but no dedicated `Host` header allowlist.
The DNS-rebinding protection that `@playwright/mcp` provides via `--allowed-hosts` is
not available in the devtools stack. This gap is documented in SECURITY.md.
For internal mode, the Docker network boundary is the access control.
For external mode, Traefik handles TLS + basicAuth before requests reach mcp-proxy.

## 2026-07-12 — Implementation complete

All changes committed to branch `feat/pwmcp-p01-chrome-devtools-mcp`.
15 files changed (12 modified + 3 new). See P01-REPORT.md for full summary.

### Gates
- All named contracts met or explicitly documented as gaps (host-allowlist → SECURITY.md).
- No BLOCKER conditions encountered.
- Smoke validation script written but not run (no Docker in agent environment).
- Node version compatibility noted but unverified — controller to check during first build.

### What was done
1. Researched proxy candidates, chose mcp-proxy@6.5.2 💡
2. Updated Dockerfile with new ARGs and npm packages
3. Added supervisord [program:devtools-mcp]
4. Extended ciu templates (defaults + compose)
5. Bumped versions in docker-bake.hcl and added new ARGs
6. Updated version scripts (resolve-playwright-version.py, _vars.py)
7. Created smoke-endpoints.sh validation script
8. Updated all documentation (README, ARCHITECTURE, USAGE, DEPLOYMENT, SECURITY)
