# Topos PWMCP browser-test service

This directory is Topos's consumer deployment of the upstream `vbpub/pwmcp`
service. It is test infrastructure for the operator-console React UI; it is not
part of Topos's runtime or production authentication boundary.

## Pinned inputs

`UPSTREAM.toml` records the verified PWMCP release bundle and required CIU
version. The upstream deployment templates and contract are copied byte-for-
byte from that bundle; Topos-specific policy lives only in the sparse
`ciu.global.toml.j2` and `ciu.toml.j2` overrides.

Current policy:

- internal Docker networks only: the owned `topos-ui-test` service network and
  the CIU-detected workspace consumer network, with no host-published or
  external/TLS route;
- isolated per-session browsers by default;
- at most two native Playwright clients;
- 15-minute requested lease, one-hour server ceiling, and 15-second idle
  recycle;
- non-root PWMCP image with its upstream capability drop and health check.

## Start and verify

From this directory, with CIU 4.6.0 and Docker Compose available:

```bash
ciu env generate --define-root .
ciu up --dir . --dry-run
ciu up --dir .
curl --fail http://pwmcp:3000/health
```

CIU generates ignored `ciu.env`, `ciu.toml`, `ciu.global.toml`,
`ciu.compose.yml`, and `.ciu/` state. PWMCP joins the detected workspace network
with its stable aliases, so tests and MCP clients use container DNS:

```text
ws://pwmcp:3000/       native Playwright connection
http://pwmcp:8931/mcp  Playwright MCP
http://pwmcp:8932/mcp  Chrome DevTools MCP
http://pwmcp:8933/mcp  Lighthouse MCP
```

The exact Playwright client version comes from `pwmcp.contract.json`. Do not
run `playwright install`; browser binaries remain inside PWMCP.

## Reaching a Topos UI under test

The browser runs in a different container, so it cannot reach a Topos server
bound to the devcontainer's loopback address. A browser-test harness must:

1. attach the Topos test server/runner to the configured consumer network (the
   current devcontainer already owns the CIU-detected workspace network);
2. bind the test-only HTTP listener to that container's network interface;
3. retain the D-002 capability token and all production redaction/security
   checks; and
4. navigate PWMCP to the runner's container DNS name, never `localhost`.

This test-only bind is not permission to weaken Topos's production loopback-
only default. The future web handoff must provide the deterministic server
fixture and URL to pwmcp/pwmcp-browser tests.

## Stop or reset

```bash
ciu down
ciu clean
```

## Upgrade

Resolve `pwmcp-latest/latest.json`, download the versioned bundle and sidecar,
verify SHA-256, replace only the files listed in `UPSTREAM.toml`, update the
recorded release/checksum/contract together, then run the dry-run and health
checks. Never copy mutable files directly from the upstream working tree.
