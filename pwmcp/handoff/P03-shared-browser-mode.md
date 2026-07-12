# pwmcp P03 - Opt-In Shared Persistent Browser Mode

## Goal

Add an opt-in `browser_mode = "shared"` (ciu var; default `"per-session"`
keeps today's behavior byte-identical) that runs ONE persistent headless
Chromium as a supervised program with a CDP endpoint, which the MCP servers
attach to instead of launching their own: `@playwright/mcp` via
`--cdp-endpoint`, `chrome-devtools-mcp` (P01) via `--browser-url`. This
enables the cross-tool workflow — drive a page with Playwright tools, then
profile THAT page with DevTools tools in the same browser — and pools
browser memory under concurrent use.

## Safeguards are the package (not optional hardening)

Shared mode trades isolation for integration; each named risk gets a
first-class, tested mitigation:

1. **Automatic restart on crash**: `[program:chromium]` runs under
   supervisord with `autorestart=true` and a bounded backoff. Attached MCP
   servers must survive a browser restart: in-flight tool calls fail with a
   typed error, and the NEXT session/tool call succeeds without container
   restart (reconnect-on-demand, not a cached dead connection). Mechanism
   test: `kill -9` the Chromium process mid-session, assert supervisord
   restarts it within a bounded window AND a subsequent MCP tool call on
   each attached server succeeds.
2. **Session/state reset on demand**: a minimal admin HTTP endpoint on its
   own internal port (`admin_port`, default 8939, internal network only,
   NEVER routed through Traefik in external mode): `POST /browser/reset`
   closes all browser contexts (cookies/storage/pages gone) without killing
   the process; `POST /browser/restart` hard-restarts the Chromium program
   via supervisord; `GET /browser/health` returns CDP liveness + context
   count + uptime. Closed endpoint set, anything else 404; no request
   bodies are interpreted (no parameters to inject). Document a consumer
   one-liner (`curl -X POST http://pwmcp:8939/browser/reset`) in USAGE.md.
3. **State-bleed containment**: each MCP session must use its own browser
   context (incognito-style) on the shared browser — verify each server
   actually does this when CDP-attached (check upstream behavior/flags;
   record findings in the LOG; if a server cannot guarantee per-session
   contexts when attached, document that residual risk explicitly in
   SECURITY.md rather than claiming isolation that does not exist). Test:
   two concurrent MCP sessions set different cookies for the same origin
   and neither observes the other's.
4. **Optional idle recycle**: `browser_max_idle_s` ciu var (default off):
   with no attached session for that long, the browser is recycled to shed
   leaked memory. Test via injected clock/short interval.
5. **CDP never leaves the container**: the remote-debugging port binds to
   localhost inside the container only — not published, not on the Docker
   network, no Traefik route. The smoke script asserts from a sibling
   container that the CDP port is NOT reachable while 8931/8932 are.

## Workflow

- Branch: `feat/pwmcp-p03-shared-browser-mode`
- Worktree: `git worktree add -b feat/pwmcp-p03-shared-browser-mode
  .worktrees/-pwmcp-p03-shared-browser-mode main`
- Starts after reviewed P01 merges (P01 supplies the second attachable
  server and the smoke harness; P02 is independent — if it has merged,
  Lighthouse keeps per-audit launch and is explicitly NOT wired to the
  shared browser in this package).
- Touch only `pwmcp/**`; write `pwmcp/handoff/reports/P03-LOG.md` /
  `P03-REPORT.md`; commit the feature branch, do not merge.

## Context To Read First (bounded)

This handoff; P01 handoff + REPORT; `containers/pwmcp/{Dockerfile,
supervisord.conf,entrypoint.sh}`; `ciu.defaults.toml.j2`;
`ciu.compose.yml.j2`; `scripts/smoke-endpoints.sh`;
`docs/{USAGE,SECURITY,ARCHITECTURE}.md`. Upstream flag references for
`@playwright/mcp --cdp-endpoint` and `chrome-devtools-mcp --browser-url`
behavior when attached (session/context semantics).

## Required Contracts (beyond the safeguards)

- **Mode plumbing**: `browser_mode` selects supervisord program set and MCP
  server flags at entrypoint time (entrypoint renders/selects config; no
  divergent Dockerfiles). `per-session` mode output must be byte-identical
  to pre-P03 rendering (diff evidence in the REPORT, same rule as P01).
- **Startup ordering**: in shared mode, MCP servers must tolerate the
  browser not yet being up (supervisord start order + retry-with-deadline
  in each attach path), and a browser that dies during MCP server startup
  must not wedge the program in a half-attached state.
- **Admin endpoint implementation** is a small supervised program (Node or
  Python stdlib http.server-grade; no new framework dependency); it may
  talk to supervisord's XML-RPC/unixsocket interface for restart — do not
  grant it any capability beyond context-close/program-restart/health.
- **Chromium launch flags**: pinned, explicit, recorded in ARCHITECTURE.md
  (headless, remote-debugging-port on 127.0.0.1, no-sandbox consistent
  with existing programs, profile under /tmp).
- Strict validation/no-silent-clamp for all new ciu vars (reject nonsense
  `browser_max_idle_s`, unknown `browser_mode` values at entrypoint with a
  clear fatal message, not a fallback).

## Required Validation

Extend `scripts/smoke-endpoints.sh` with a shared-mode pass (script takes a
mode argument or reads the rendered config): all safeguard mechanism tests
above (crash-restart, reset-clears-state, cross-session cookie isolation,
CDP unreachability from a sibling, admin endpoints), plus the P01
cross-tool proof: navigate a page via a Playwright MCP session, then start
and stop a DevTools performance trace on the SAME page via 8932 and assert
the trace references the navigated URL — the workflow this package exists
for. Same evidence rules: timeout-wrap, never claim an unrun check, record
environment limits separately; if the agent environment cannot run Docker,
commit the script and leave execution to the controller, saying so plainly.

## Documentation

`docs/ARCHITECTURE.md` (both modes, diagram, tradeoff table),
`docs/SECURITY.md` (shared-mode risk posture: state bleed residuals, crash
blast radius, admin endpoint trust), `docs/USAGE.md` (when to choose which
mode; reset one-liner), `README.md`, `docs/DEPLOYMENT.md` (new vars).

## Out Of Scope

- Making shared mode the default (revisit with usage data).
- Wiring Lighthouse (P02) to the shared browser.
- Exposing CDP or the admin port beyond the internal network; admin auth.
- Multi-browser pools, per-consumer browser affinity, session queuing.
