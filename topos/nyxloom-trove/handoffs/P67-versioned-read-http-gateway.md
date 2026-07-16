# P67 - Versioned Read HTTP Gateway (trust-boundary hardened)

<!-- controller-workflow-v2 header: parsed by the controller; see docs/controller-workflow-v2.md §7 -->
> **Tier:** sonnet5-high
> **Depends-on:** P63 (merged - typed client), P52 (merged - versioned envelope), P69 (merged - the trust-boundary analysis this rewrite implements)
> **Base:** main
> **Session-hint:** fresh
> **Serialize-with:** none
> **Escalate-if:** the read gateway cannot be built on the stdlib HTTP surface without a web framework; or any contract in "The trust boundary" below cannot be met as specified. Do NOT improvise an auth scheme -- a gateway that ships without one is the failure this rewrite exists to prevent.

<!--
CARVE SOURCE (controller-workflow-v2 §8): **review-derived** (P69 pass #2).
This handoff is a REWRITE, not a new package. The previously carved P67 put
"auth/TLS termination" under Out Of Scope while its entire purpose is to open a
listening HTTP port onto a daemon whose only boundary today is a 0660 root:topos
Unix socket. P69's audit found it not dispatchable as written and named the four
contract groups it must add; this is those contracts. Tier raised flash->sonnet5:
this is now a privilege-boundary slice, not a thin adapter.
-->

## Goal

Expose the P52 versioned read surface (`hello`/`current`/`history`/`entity`, and
`health` once P66 lands) over HTTP by **consuming P63's typed `DaemonClient`
methods**, so a browser frontend (P73) can read daemon state without speaking the
`AF_UNIX` line protocol.

The gateway is a thin adapter in its *data* path and a real security boundary in
its *access* path. Both halves are the package.

## Why this needs contracts, not just routes

Today the daemon's boundary is the filesystem: a `0660 root:topos` Unix socket
(`docs/DAEMON.md:15-21`). Membership in the `topos` group *is* the authorization
decision, enforced by the kernel, and P52's default authorization hook is
deliberately a no-op allow because of that (`src/topos/daemon/api.py:177-183`).

An HTTP listener destroys that property. The gateway process is in the `topos`
group; a browser user is not the gateway process. Nothing in the current stack
knows who is on the other end of a TCP connection, and P52 serves raw values plus
sensitivity metadata -- it does not redact (`api.py:394-469`). So a naive gateway
serves `sensitive`-classified telemetry to anyone who can reach the port.

## The trust boundary (the four contract groups)

### 1. Safe bind by default

- Default bind is loopback only (`127.0.0.1`, and `::1` if dual-stack). Never
  `0.0.0.0`, never a wildcard, never a LAN address by default.
- A non-loopback bind MUST be refused unless the operator passes an explicit,
  documented opt-in flag AND supplies the authentication configuration from (2).
  Refusal is a typed startup error, not a warning that proceeds.
- The listening port is never advertised or auto-bound through a daemon socket
  option.

### 2. Authentication and a redaction ceiling

- No telemetry leaves the process without an authenticated principal.
- Each principal maps to a **redaction ceiling** drawn from the closed
  `Sensitivity` enum in CONTRACTS §10 (`public` / `operational` / `sensitive`).
- **Redaction happens server-side, before bytes reach the browser.** Client-side
  masking is not an authorization boundary -- the viewer can read the raw HTTP
  response. A value above the principal's ceiling is replaced with a typed
  redaction marker; the key, label, and unit stay. Redaction **replaces a value,
  it never drops a key** (the standing P58 review lesson).
- v1 may implement authentication as a trusted local reverse proxy passing a
  verified identity over a private hop, provided the gateway **refuses to trust a
  forwarded identity header from a non-loopback peer**. Blindly trusting
  `X-Forwarded-*` from any source is forbidden and is an automatic review reject.

### 3. Origin / CSRF discipline

- Same-origin by default. No `Access-Control-Allow-Origin: *`, no credentialed
  wildcard CORS, no JSONP. Any cross-origin allowance is an explicit allowlist.
- Reject every mutating HTTP method (`POST`/`PUT`/`PATCH`/`DELETE`) now, so a
  later route cannot silently inherit browser credentials.
- If cookie auth is used: `Secure`, `HttpOnly`, `SameSite`, plus Origin checking.

### 4. Read-only routing enforcement

- Only documented GET routes exist, each mapping to exactly one typed P63 client
  call. No route traversal, no unsupported query fields, no pass-through of
  arbitrary parameters into the client.
- Deterministic error mapping from the typed client's `.code` to HTTP status
  (`not_found`->404, `invalid_type`/`out_of_range`->400, `unavailable`->503,
  `DaemonConnectError`->502/503). Never leak socket paths, stack traces, or
  exception text -- P52 already sanitizes; preserve that.
- `metrics_meta` (with its `Sensitivity` values) passes through intact, so the UI
  can render *why* a value is redacted. Do not strip it.

## Required Contracts (data path)

- Routes are backed **exclusively** by `DaemonClient.request_*`. The gateway must
  not re-open the socket, hand-roll an envelope, or re-implement decode/validate
  -- P58 was rejected twice for exactly this. If a typed method you need does not
  exist (notably versioned `health`, which P66 owns), **gate that route off; do
  not fall back to the legacy `request_health`** (`client.py:161-178`).
- The gateway module must not pull heavy imports at `topos.daemon` import time.
- Prefer the stdlib HTTP surface; a web framework requires escalation first.

## Acceptance Oracles (numbered, adversarial)

Stand up a real `DaemonApi` over a temp `AF_UNIX` socket, a real `DaemonClient`
against it, and the gateway on an ephemeral port. No mocked client. Events or
bounded polling, never sleeps.

1. **Default bind is loopback.** Start with no bind argument; assert the listening
   socket is bound to a loopback address. A test that only asserts "it serves a
   request" passes against `0.0.0.0` and is worthless here.
2. **Non-loopback bind is refused** without the explicit opt-in + auth config, and
   the refusal is a typed error, not a log line followed by a bound socket.
3. **Unauthenticated request returns 401/403 and zero telemetry bytes.** Assert on
   the response body, not just the status: a body that leaks the frame while the
   status says 403 is the bug this oracle exists to catch.
4. **Redaction is server-side.** A principal with an `operational` ceiling requests
   an entity carrying a `sensitive` metric; assert the raw value is **absent from
   the HTTP response bytes** and a typed marker is present, with the metric's key
   still there. Grep the response body for the raw value -- if it appears, the
   test must fail.
5. **Forwarded-identity headers from a non-loopback peer are not trusted.**
6. **Every mutating method is rejected** on every route.
7. Each route happy path returns decoded JSON with `metrics_meta` intact; each P52
   error code maps to its documented HTTP status; a down daemon yields the
   connect-error status.

## Out Of Scope

- Write/mutation routes (permanently: actions keep their root/admin/typed-
  confirmation/audit posture, and a browser does not shortcut it).
- TLS termination itself (document the reverse-proxy deployment; do not implement
  a TLS stack).
- The browser UI (P73 consumes this).
- WebSocket / server push (P68 owns the subscribe transport).
- Any change to the P52 wire, envelope, or error codes.

## Gates

```bash
PYTHONPATH=topos/src python3 -m pytest <focused P67 tests> -q -W error -p no:schemathesis
timeout 900 env PYTHONPATH=topos/src python3 -m pytest topos/tests -q -W error -p no:schemathesis
python3 -m py_compile <changed files>
git diff --check
```

State the environment for each result. Update `docs/DAEMON.md` with the gateway's
deployment posture (bind default, auth requirement, reverse-proxy shape) and write
P67-LOG.md / P67-REPORT.md.
