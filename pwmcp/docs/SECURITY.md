# Security

## Why Offload the Browser?

Running a browser (Chromium) inside a devcontainer or CI job is a security liability:
- The browser process has broad OS access, large attack surface, and often runs as the container user
- Browser binary versions diverge across environments, causing flaky tests and reproducibility gaps
- Installing browsers in every job image bloats images and slows cold starts

PWMCP solves this by running the browser in a dedicated, hardened container. The browser is isolated from the consumer's code and filesystem. Consumers connect over a protocol boundary (WebSocket / MCP), not via in-process calls.

## Browser Isolation Hardening

The following hardening is applied in **both deployment modes** (internal and external). It is not optional — it is the justification for offloading the browser in the first place.

| Control | Setting | Rationale |
|---|---|---|
| Non-root user | `user: 1000:1000` | Official Playwright base image ships UID 1000; no reason to run as root |
| Drop all capabilities | `cap_drop: [ALL]` | Chromium does not need any Linux capabilities; remove the entire set |
| No privilege escalation | `no-new-privileges: true` | Prevents setuid/setgid bits from granting elevated privileges at runtime |
| Shared memory | `shm_size: 2gb` | Chromium writes renderer frames to `/dev/shm`; too small causes crashes |

These are set in `ciu.compose.yml.j2` under `[pwmcp.hardening]` and cannot be overridden per-mode.

## Internal Mode Access Control

In internal mode the Docker network is the access control boundary. Services are not exposed outside the project network. Any container on the same network can connect — no additional auth is applied. This is appropriate for dev and CI where the network is already controlled.

Do not expose `pwmcp` ports to `0.0.0.0` in internal mode.

The run-server recovery/admin endpoint (default 8940) is intentionally absent
from Compose `ports` and Traefik routes. Anyone on the internal network can
close sessions or restart the run-server, so it must remain inside that trust
boundary. Client-requested leases cannot raise the server-configured maximum.

## External Mode Access Control

In external mode, access is controlled by:

1. **TLS termination** at Traefik (tls-edge). All traffic is encrypted in transit.
2. **Per-route basicAuth guard** (`guard_enabled = true` by default). Traefik enforces HTTP Basic Auth before forwarding the request. Consumers must supply credentials.

The guard htpasswd hash is stored in `ciu.toml.j2` (the operator's override file, not committed to shared repos with the hash in plaintext). Use `ASK_EXTERNAL:PWMCP_GUARD_HTPASSWD` as a placeholder when generating env; supply the real hash in the deployed override.

The guard covers the Playwright WebSocket route, the @playwright/mcp HTTP route, the chrome-devtools-mcp HTTP route, and the lighthouse-mcp HTTP route independently.

## Credential Hygiene

- The guard htpasswd hash is a bcrypt hash (`htpasswd -nbB`), not a plaintext password
- Rotate the guard secret by regenerating the htpasswd hash and redeploying
- Do not commit the real htpasswd hash in shared version control
- Traefik access logs do not include Authorization header values by default

## Network Isolation Summary

```
internal mode:
  [project network] — only containers on this network can reach the services
  no ports published to host (unless expose = true for dev convenience)

external mode:
  [project network] — same as internal
  [ingress_public]  — tls-edge/Traefik is the only external entry point
  Traefik enforces TLS + basicAuth before forwarding
```

## chrome-devtools-mcp Host-Allowlist Gap

`chrome-devtools-mcp` is served via `mcp-proxy` (the stdio→streamable-HTTP proxy package).
Unlike `@playwright/mcp`, which provides built-in DNS-rebinding protection via the
`--allowed-hosts` flag, **`mcp-proxy` does not have a native `Host` header allowlist**.

This means the `PWMCP_DEVTOOLS_ALLOWED_HOSTS` environment variable injected by the ciu
compose template is **informational only** — it is not enforced by the mcp-proxy server
itself. The gap is mitigated by:

1. **Internal mode**: the Docker network boundary is the access control. Only sibling
   containers on the same network can reach port 8932.
2. **External mode**: requests pass through Traefik (tls-edge), which enforces TLS +
   basicAuth before reaching mcp-proxy. The Traefik route is configured to accept the
   known hostname only.

If stronger Host-header enforcement is required for internal-mode deployments, options
include:
- Placing a reverse proxy (e.g. another Traefik or nginx instance) in front of mcp-proxy
  that validates the Host header before forwarding.
- Replacing mcp-proxy with a future proxy that supports `--allowed-hosts` natively.
- Using mcp-proxy's API key mechanism (`--apiKey` / `MCP_PROXY_API_KEY`) as an
  additional bearer-token check for trusted callers.

## lighthouse-mcp Host-Allowlist Gap

`lighthouse-mcp` is also served via `mcp-proxy` (same package as chrome-devtools-mcp,
port 8933). It shares the identical host-allowlist gap: **`mcp-proxy` has no native
`Host` header allowlist**.

The `PWMCP_LIGHTHOUSE_ALLOWED_HOSTS` environment variable is **informational only**.
Mitigations are identical to the chrome-devtools-mcp gap above:

1. **Internal mode**: Docker network boundary is the access control.
2. **External mode**: Traefik enforces TLS + basicAuth before forwarding.

The same remediation options apply (reverse proxy, future proxy, or mcp-proxy API key).


## P03: Shared Browser Mode — Risk Posture

`browser_mode = "shared"` (opt-in, default remains `"per-session"`) trades
per-consumer browser isolation for a pooled, cross-tool-capable Chromium.
Each traded-away risk gets an explicit mitigation and a documented residual:

### State-bleed residual

**Update (self-review, 2026-07-13):** the original implementation launched
`mcp` and `devtools-mcp` with `--isolated` when attached to the shared
browser, intended to give each MCP session its own incognito-style browser
context. Running the P03 handoff's own required cross-tool proof (navigate
via Playwright, then trace the same page via DevTools — `scripts/
smoke-endpoints.sh --mode shared`, check "cross-tool proof") against a real
built image showed this was broken: with `--isolated` on both servers, each
attached to its OWN separate browser context, so DevTools traced a blank
`chrome://new-tab-page/` instead of the page Playwright navigated — the
core workflow this package exists for did not work at all.

`--isolated` has been **removed** from both `mcp` and `devtools-mcp` in
`supervisord.shared.conf` so they share Chromium's one default browser
context, which fixes the cross-tool workflow (verified: the trace now
correctly references the navigated URL). The direct consequence, also
empirically verified (two MCP sessions, one sets `document.cookie` via
`browser_evaluate` against an in-container HTTP fixture, the other reads
it back and observes the first session's value) is:

**Shared mode provides NO per-session state isolation.** Two concurrent
consumers on the shared browser see each other's cookies, localStorage, and
DOM state for the same origin. This is an accepted, permanent residual risk
of shared mode as currently implemented, not a gap pending upstream
confirmation — use `POST /browser/reset` between untrusted consumers, or
use `per-session` mode (the default) when per-consumer isolation is
required. `scripts/smoke-endpoints.sh --mode shared`'s state-bleed check
now asserts this observed behavior (bleed present) rather than asserting an
isolation guarantee that does not exist, per this package's own instruction
to document real residual risk rather than claim unverified isolation.

### Crash blast radius

A Chromium crash in shared mode affects **every** attached MCP session
simultaneously (vs. per-session mode, where a crash is scoped to the one
session that triggered it). Mitigation: `autorestart=true` on
`[program:chromium]` recovers the browser process within seconds (verified:
a `kill -9` recovery test in this package restarted the program and a
subsequent MCP tool call succeeded without a container restart); in-flight
tool calls during the crash window fail with a transport error to the
caller, who is expected to retry (reconnect-on-demand — no cached dead
connection is held by either MCP server, since they dial the CDP endpoint
per-call).

### Admin endpoint trust

`admin-server` grants itself exactly three capabilities and nothing more:
close CDP browser contexts (`/browser/reset`), restart the single named
`chromium` supervisord program (`/browser/restart`), and read CDP/process
liveness (`/browser/health`). It has no generic supervisord RPC access (only
`stopProcess`/`startProcess("chromium")` are called), no shell/exec surface,
and never parses a request body (so there is no injectable parameter
surface even though bodies are technically accepted by the HTTP layer).
The admin port (default 8939) is injected into the container environment
for `admin-server` to bind, but the ciu compose template **never** adds it
to `ports:` and **never** creates a Traefik router for it, in either
internal or external deployment mode — it is reachable only from sibling
containers on the same Docker network, same as the CDP port is reachable
only from `127.0.0.1` inside the container. Anyone who can reach the
internal Docker network can call `/browser/reset` (deletes all state) or
`/browser/restart` (brief service interruption) without authentication —
this is an intentional trade consistent with the rest of pwmcp's internal-
mode threat model (the network boundary is the access control), not a gap
specific to P03.

### CDP never leaves the container

Verified directly: with the shared-mode container running,
`docker run --rm --network <net> curlimages/curl -fsS http://<container>:9222/json/version`
from a sibling container fails to connect (`curl: (7) Failed to connect`),
while the MCP ports and the admin port on the same network succeed. CDP
binds `127.0.0.1` only inside the container; the compose template does not
publish it and does not route it through Traefik.
