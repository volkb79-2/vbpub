# P02 Implementation Report

## Summary

Implemented `lighthouse-mcp` (Lighthouse audit server) as a fourth service in the pwmcp unified container, exposed on port 8933 via `mcp-proxy` (stdio→HTTP proxy), following the same sibling-server pattern established in P01.

## Server Decision

**Chosen**: In-repo vendored server at `containers/pwmcp/lighthouse-mcp/` (~200 lines).
**Rationale**: No existing npm Lighthouse MCP package met all three named criteria (pinnable, executable-path injectable, bounded results). The leading candidate `lighthouse-mcp@0.1.15` blocks RFC 1918 IPs (breaking internal Docker hostnames), pins an old Lighthouse version (12.x vs 13.x), and lacks response bounds or chrome-path injection.
**Trade-off**: Maintenance cost of a small in-repo server vs integrating an external package that doesn't fit the handoff's requirements.

## Changes (15 modified + 4 new files)

### Server
- **containers/pwmcp/lighthouse-mcp/package.json**: Dependencies on `@modelcontextprotocol/sdk`, `chrome-launcher`, `lighthouse`.
- **containers/pwmcp/lighthouse-mcp/index.js**: Two MCP tools:
  - `lighthouse_audit(url, categories?, form_factor?)` — per-category scores (0-100), top-10 opportunities with estimated savings, audited final URL.
  - `lighthouse_metrics(url, form_factor?)` — LCP/CLS/TBT/FCP/SI/TTI values and scores.
  - Response capped at 100 KB / 10 opportunities; audit timeout 120 s default.
  - URL validation: http/https only, typed errors for file:///data:/chrome://.
  - Chrome path: reads `PWMCP_CHROMIUM_PATH` for chrome-launcher injection.

### Container
- **Dockerfile**: Added `LIGHTHOUSE_VERSION` ARG (13.4.0), installs `lighthouse` globally, copies and installs the vendored server to `/opt/pwmcp/lighthouse-mcp/`, adds `PWMCP_LIGHTHOUSE_ALLOWED_HOSTS` env var.
- **supervisord.conf**: Added `[program:lighthouse-mcp]` running `mcp-proxy --port 8933 -- node /opt/pwmcp/lighthouse-mcp/index.js`.

### CIU Templates
- **ciu.defaults.toml.j2**: Added `lighthouse_port=8933`, `host_lighthouse_port=8933`, `lighthouse_extra_args`.
- **ciu.compose.yml.j2**: Added fourth port mapping, `PWMCP_LIGHTHOUSE_ALLOWED_HOSTS` env, Traefik router with `/lighthouse` StripPrefix.

### Build System
- **docker-bake.hcl**: Added `LIGHTHOUSE_VERSION` variable (13.4.0), passed to both bake targets. Bumped `PWMCP_VERSION_PYPI` → `1.61.0-r4`, `PWMCP_VERSION_NPM` → `1.61.1-r3`.
- **_vars.py**: Added `LIGHTHOUSE_VERSION` to `_REQUIRED_KEYS`.
- **resolve-playwright-version.py**: Added `LIGHTHOUSE_VERSION` read/write in `write_release_vars()`.

### Validation
- **scripts/smoke-endpoints.sh**: Extended with 8933 checks — initialize (correct + forged Host), real `lighthouse_audit` tool call asserting category scores are present, rejection of `file://` URL with typed error, supervisord all-four-program status, and fault isolation (stopping lighthouse-mcp leaves 3000/8931/8932 unaffected).

### Documentation
- **README.md**: Updated endpoint table with port 8933, consumer config, pin list.
- **docs/ARCHITECTURE.md**: Four-program ASCII diagram, updated layers/pins/allowed-hosts.
- **docs/USAGE.md**: Added lighthouse to endpoint table, VS Code config snippets, multi-consumer notes.
- **docs/SECURITY.md**: Added lighthouse-mcp host-allowlist gap section.
- **docs/DEPLOYMENT.md**: Added port 8933 to endpoint references.

## Gaps / Unresolved

### Host-header allowlist
Same mcp-proxy gap as chrome-devtools-mcp: `PWMCP_LIGHTHOUSE_ALLOWED_HOSTS` is informational only. Mitigated by Docker network boundary (internal mode) and Traefik TLS+basicAuth (external mode). Documented in SECURITY.md.

### Per-audit browser instance
Unlike chrome-devtools-mcp (shared persistent browser), lighthouse-mcp launches a fresh headless Chromium per audit and tears it down after. This is intentional (handoff states "per-audit browser, zero idle cost") but means each audit has browser-launch overhead (~2-3 seconds). Acceptable for v1.

### Smoke validation
The agent environment does not have Docker access, so the extended `scripts/smoke-endpoints.sh` could not be run. The script is committed and ready for the controller to execute against a locally built + ciu-started stack.

## Files Changed
```
A  pwmcp/containers/pwmcp/lighthouse-mcp/package.json
A  pwmcp/containers/pwmcp/lighthouse-mcp/index.js
A  pwmcp/handoff/reports/P02-LOG.md
A  pwmcp/handoff/reports/P02-REPORT.md
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
M  pwmcp/scripts/smoke-endpoints.sh
```
