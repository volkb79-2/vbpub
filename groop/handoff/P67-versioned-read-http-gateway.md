# P67 - Versioned Read HTTP/WebSocket Gateway

<!-- controller-workflow-v2 header: parsed by the controller; see docs/controller-workflow-v2.md §7 -->
> **Tier:** pro-high
> **Depends-on:** P63 (merged, reviewed — typed client), P52 (merged — versioned envelope)
> **Base:** main
> **Session-hint:** fresh
> **Escalate-if:** satisfying the read gateway requires changing the P52 wire protocol or `api.py`; or the only viable transport pulls a heavy new runtime dependency the repo does not already vendor and cannot be done with the stdlib `http.server`/`wsgiref` surface — surface the tradeoff and escalate rather than adding a large dependency unilaterally.

## Goal

Expose the P52 versioned read surface (`hello`/`current`/`history`/`entity`,
and `health` once P66 lands) over HTTP (and optionally a read-only WebSocket
for `current` polling) by **consuming P63's typed `DaemonClient` methods** — so
a browser or a remote MCP/web frontend can read daemon state without speaking
the `AF_UNIX` line protocol. This is the HTTP/WebSocket transport P63 explicitly
put Out-Of-Scope; it is a distinct process/adapter, not a client-surface change.

## Dependency And Workflow

- The gateway is a thin adapter: HTTP route -> `DaemonClient.request_*` ->
  serialize the typed result back to JSON. It MUST NOT re-open the socket or
  re-serialize envelopes itself; the typed client owns transport, decode,
  validation, and error mapping. Map `DaemonResponseError.code` onto HTTP
  status (e.g. `not_found`->404, `invalid_type`->400, `out_of_range`->400,
  `unavailable`->503) and `DaemonConnectError`->502/503.
- Prefer the stdlib HTTP surface already used elsewhere in the repo; do not add
  a web framework without escalating first (see Escalate-if).
- Branch: `feat/groop-p67-versioned-read-http-gateway`
- Worktree: `.worktrees/groop-p67-versioned-read-http-gateway`
- Touch only `groop/**`; write P67-LOG.md/P67-REPORT.md; commit, do not merge.

## Context To Read First (bounded)

`groop/README.md`, this handoff, `groop/src/groop/daemon/client.py` (the typed
methods and exception hierarchy you adapt), `docs/DAEMON.md` (P52/P63 sections),
and any existing stdlib-HTTP usage in `groop/src/groop/` to match style. Do not
read UI, DAMON/BPF, actions, or record/replay code, and do not read `api.py`
beyond confirming you are not duplicating its logic.

## Required Contracts

- Read-only GET routes for each versioned op, backed exclusively by the typed
  client. No write routes.
- Deterministic error mapping from the typed client exceptions/`.code` to HTTP
  status; never leak socket paths, stack traces, or server exception text (the
  P52 server already sanitizes messages — preserve that).
- `metrics_meta` (with its `Sensitivity` values) is passed through so a browser
  can apply redaction; do not strip it.
- Import isolation: the gateway module must not pull heavy imports at
  `groop.daemon` import time.

## Required Deterministic Tests

Stand up a real `DaemonApi` over a temp `AF_UNIX` socket, point a real
`DaemonClient` at it, run the gateway against a bound ephemeral loopback port,
and exercise routes with the stdlib HTTP client — no mocked client. Cover: each
route happy path returns the decoded JSON with correct status; each P52 error
code maps to its HTTP status; a down daemon yields the connect-error status;
`metrics_meta` survives the round trip. Events/bounded polling, not sleeps.

## Gates And Evidence

```bash
PYTHONPATH=groop/src python3 -m pytest <focused P67 tests> -q -W error -p no:schemathesis
timeout 900 env PYTHONPATH=groop/src python3 -m pytest groop/tests -q -W error -p no:schemathesis
python3 -m py_compile <changed files>
git diff --check
```

State the environment for each result; record `-p no:schemathesis`/textual
skips as known artifacts. Update `docs/DAEMON.md` and the P67 LOG/REPORT.

## Out Of Scope

- Write/mutation routes, auth/TLS termination, and multi-tenant concerns.
- The MCP frontend itself (P58); the versioned client surface (P63/P66 own it).
- Any change to the P52 wire, envelope, or error codes.
