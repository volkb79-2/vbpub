# Topos pwmcp-instance — browser/UI-testing service

Topos's consumer deployment of the upstream `vbpub/pwmcp` service, as a **proper ciu
sub-stack of the topos ciu root** (`topos/ciu.global.defaults.toml.j2`). It is test
infrastructure for the (future) operator-console React UI — **not** part of topos's
runtime. The topos daemon itself runs **native** (a systemd unit on a Unix socket,
host-forensics of `/sys/fs/cgroup`); it is deliberately not a ciu stack.

## A ciu sub-stack, on topos's OWN network (not dstdns's)
It inherits `deploy.*` (project `topos`, env `ui-test`, network `topos-ui-test`) from the
topos root global and is registered under the **non-default** `[deploy.profiles.tools]`,
so a plain `ciu up`/`ciu down` never touches it. It ships only `ciu.compose.yml.j2` +
`ciu.defaults.toml.j2` (+ the topos-local `ciu.toml.j2` override + `pwmcp.contract.json`);
the rendered `ciu.compose.yml`/`ciu.toml`/`ciu.global.toml`/`ciu.env` are gitignored.

Because topos has **no containerized daemon** to own a network, this stack **owns** the
`topos-ui-test` network (`pwmcp.network.external_default = false`). Previously it wrongly
piggybacked on the **dstdns** devcontainer network (`$DOCKER_NETWORK_INTERNAL` →
`dstdns-98535c-network`) — that cross-project reach is removed.

Policy (topos-local, in `ciu.toml.j2`): isolated per-session browsers; at most two native
Playwright clients; 15-min requested lease / 1-hour ceiling / 15-s idle recycle; no
host-published or external/TLS route; the non-root, cap-dropped, health-checked image.

## Start and verify (from the topos ciu root, `/workspaces/vbpub/topos`)
```bash
ciu env generate --define-root .          # one-time: generate the gitignored ciu.env
source ciu.env
ciu up   --dir pwmcp-instance --dry-run   # render without deploying
ciu up   --dir pwmcp-instance             # or: ciu up --profile tools
ciu down --profile tools                  # teardown (down takes --profile, not --dir)
```
Container `topos-ui-test-pwmcp` joins `topos-ui-test` with stable aliases, so tests and
MCP clients use container DNS:
```text
ws://pwmcp:3000/       native Playwright connection
http://pwmcp:8931/mcp  Playwright MCP
http://pwmcp:8932/mcp  Chrome DevTools MCP
http://pwmcp:8933/mcp  Lighthouse MCP
```
The Playwright client version comes from `pwmcp.contract.json`; do not run `playwright
install` (browser binaries stay inside pwmcp).

## Reaching a Topos UI under test
The browser runs in a different container, so it cannot reach a Topos server bound to the
devcontainer's loopback. When the web UI ships, its browser-test harness must:
1. attach the Topos test server/runner to the **`topos-ui-test`** network (this stack owns it);
2. bind the test-only HTTP listener to that container's network interface;
3. retain the D-002 capability token and all production redaction/security checks; and
4. navigate pwmcp to the runner's container DNS name, never `localhost`.

This test-only bind is not permission to weaken topos's production loopback-only default.
The future web handoff must provide the deterministic server fixture + URL to pwmcp tests.

## Upgrade (re-vendor)
Resolve `pwmcp-latest/latest.json`, download the versioned bundle + sidecar, verify SHA-256,
replace only the files listed in `UPSTREAM.toml` (which no longer includes the standalone
`ciu.global*` — a post-`ab1ebf1` bundle ships sub-stack sources only), update the recorded
release/checksum/contract together, then re-run the dry-run + health checks.
