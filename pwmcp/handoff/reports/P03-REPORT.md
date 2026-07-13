# P03 Implementation Report

## Summary

Implemented opt-in `browser_mode = "shared"` (ciu var, default
`"per-session"` unchanged) that runs ONE persistent headless Chromium as a
supervised program with a CDP endpoint on 127.0.0.1:9222, which `mcp`
(`@playwright/mcp`) and `devtools-mcp` (`chrome-devtools-mcp`) attach to via
`--cdp-endpoint` / `--browser-url` instead of each launching their own
browser. `lighthouse-mcp` is unchanged in both modes (out of scope, keeps
its own per-audit launch). A small Node-stdlib admin API
(`/browser/reset`, `/browser/restart`, `/browser/health`) provides the
session/state-reset safeguard.

## Changes (10 modified + 4 new files)

### Container
- **`containers/pwmcp/supervisord.conf`**: **unchanged** (byte-identical —
  verified via diff, see P03-LOG.md). This is the config selected for the
  default `per-session` mode.
- **`containers/pwmcp/supervisord.shared.conf`** (new): six programs —
  `run-server` (unchanged), `chromium` (new persistent browser,
  `autorestart=true`), `mcp` (CDP-attached, `--isolated`), `devtools-mcp`
  (CDP-attached via `mcp-proxy`, `--isolated`), `lighthouse-mcp` (unchanged,
  own launch), `admin-server` (new).
- **`containers/pwmcp/wait-for-cdp.sh`** (new): bounded poll (default 30s)
  for chromium's CDP port before exec'ing the real `mcp`/`devtools-mcp`
  command — startup-ordering safeguard; falls through to exec anyway on
  timeout so supervisord's own autorestart/backoff handles a browser that
  never comes up, rather than wedging.
- **`containers/pwmcp/admin-server/index.js`** (new): Node stdlib
  (`http`/`net`/`crypto`) only, no new framework dependency. Closed 3-route
  set (`GET /browser/health`, `POST /browser/reset`, `POST /browser/restart`),
  404 on anything else, never parses a request body. Talks to supervisord's
  existing unix-socket XML-RPC interface (hand-rolled, ~20 lines, only
  `stopProcess`/`startProcess("chromium")`) and to Chromium's CDP HTTP +
  a hand-rolled minimal WebSocket client (`Target.getBrowserContexts` /
  `Target.disposeBrowserContext`) for context reset.
- **`containers/pwmcp/Dockerfile`**: `COPY`s the three new files above; no
  new `RUN npm install` — admin-server uses only Node stdlib already
  present in the base image.
- **`containers/pwmcp/entrypoint.sh`**: reads `PWMCP_BROWSER_MODE` (default
  `per-session`), selects `supervisord -c pwmcp.conf` or
  `-c pwmcp-shared.conf` accordingly; unknown values are a fatal `exit 1`.
  In shared mode, also strictly validates `PWMCP_BROWSER_MAX_IDLE_S` and
  `PWMCP_ADMIN_PORT` (non-negative integer / 1-65535 respectively) — fatal
  on nonsense input, no silent clamp.

### CIU Templates
- **`ciu.defaults.toml.j2`**: added `[pwmcp.unified]` keys `browser_mode`
  (default `"per-session"`), `admin_port` (default `8939`),
  `browser_max_idle_s` (default `0`, disabled).
- **`ciu.compose.yml.j2`**: injects `PWMCP_BROWSER_MODE` always;
  `PWMCP_ADMIN_PORT` / `PWMCP_BROWSER_MAX_IDLE_S` only when
  `browser_mode == "shared"`. The admin port is **never** added to the
  `ports:` block and **never** given a Traefik router, in either mode —
  grepped both conditionals in the file to confirm no `admin_port` reference
  exists in the `expose`/`ports:` or `labels:`/Traefik sections.

### Validation
- **`scripts/smoke-endpoints.sh`**: added `--mode shared` (default
  `per-session`, backward compatible with existing `--quick`-only usage).
  New section 6 (`[SHARED-BROWSER-MODE]`) covers: admin health/reset/404,
  CDP-unreachable-from-sibling (via a `curlimages/curl` sibling container),
  crash-restart mechanism (`kill -9` chromium, poll for RUNNING, then assert
  both `mcp` and `devtools-mcp` recover), the cross-tool proof (Playwright
  `browser_navigate` then DevTools `performance_start_trace`/
  `performance_stop_trace`, asserting the trace references the navigated
  URL), and a same-origin two-session isolation smoke check.
  `shellcheck -S warning` passes (one pre-existing unrelated warning).

### Documentation
- **`docs/ARCHITECTURE.md`**: new "P03" section — mode-comparison ASCII
  diagram, pinned Chromium launch flags, mode-plumbing mechanism, admin
  endpoint table, startup-ordering, idle-recycle definition.
- **`docs/SECURITY.md`**: new "P03" section — state-bleed residual (explicit,
  see below), crash blast radius, admin endpoint trust boundary,
  CDP-never-leaves-the-container (with the actual verification command/output).
- **`docs/USAGE.md`**: when to choose which mode; admin endpoint one-liners.
- **`README.md`**: top-level pointer section.
- **`docs/DEPLOYMENT.md`**: new-vars table.

## Safeguards vs. handoff — status

1. **Automatic restart on crash**: met. `kill -9`'d chromium, supervisord
   reported RUNNING again within seconds; next MCP tool call on both `mcp`
   and `devtools-mcp` succeeded with no container restart (see P03-LOG.md
   for the exact commands/output).
2. **Session/state reset on demand**: met. `/browser/reset`,
   `/browser/restart`, `/browser/health` implemented and manually verified
   (real output in P03-LOG.md); closed endpoint set (unknown path → 404
   verified); no request bodies interpreted.
3. **State-bleed containment**: `--isolated` applied to both attached
   servers. **Not proven** at the CDP-session-mapping level — documented
   as an explicit residual risk in `docs/SECURITY.md` rather than claimed
   as verified isolation, per the handoff's own instruction for this case.
   The two-concurrent-session cookie test added to smoke-endpoints.sh is a
   transport-level check (both sessions succeed independently), not a
   cookie-visibility assertion — see the script comment; a stronger
   cookie-write/cookie-read assertion would need `document.cookie` access
   via an MCP `evaluate`-style tool, which is left to the controller to add
   if the residual risk needs to be closed rather than documented.
4. **Optional idle recycle**: implemented (`browser_max_idle_s`,
   `PWMCP_ADMIN_IDLE_CHECK_INTERVAL_S` for test injection). Code path shares
   the same restart primitive as `/browser/restart` (independently
   verified) but the idle-triggered restart itself was not run end-to-end
   (would require a multi-minute observation window even with a short
   interval) — noted in P03-LOG.md, not a BLOCKER.
5. **CDP never leaves the container**: met and verified — a sibling
   container on the same Docker network cannot reach port 9222
   (`curl: (7) Failed to connect`), while MCP/admin ports on the same
   network succeed.

## Required Contracts — status
- Mode plumbing: met (both configs baked, entrypoint selects, no divergent
  Dockerfiles).
- Per-session byte-identical: met — `supervisord.conf` diff is empty (see
  P03-LOG.md). Note: `ciu.compose.yml.j2`'s *rendered output* gains one new
  `PWMCP_BROWSER_MODE: "per-session"` env line even in default config (the
  mode has to be communicated to the container somehow); this is a
  non-behavioral addition to the compose file, not the runtime process
  topology, which is what stayed byte-identical.
- Startup ordering: met (`wait-for-cdp.sh` + supervisord `autorestart`).
- Admin endpoint implementation constraints (small, stdlib, no new
  framework, minimal supervisord capability): met.
- Chromium launch flags pinned/recorded: met (ARCHITECTURE.md).
- Strict validation, no silent clamp: met for `PWMCP_BROWSER_MODE`,
  `PWMCP_BROWSER_MAX_IDLE_S`, `PWMCP_ADMIN_PORT` (entrypoint.sh, fatal
  `exit 1` paths).

## Gaps / Unresolved (as of the original implementation pass)
1. **State-bleed containment upstream proof** (see Safeguard 3 above) —
   documented in `docs/SECURITY.md`, not a BLOCKER per the handoff's own
   framing ("if a server cannot guarantee ... document that residual risk
   explicitly ... rather than claiming isolation that does not exist").
   **Superseded** — see `P03-SELFREVIEW.md`: `--isolated` did not actually
   provide the isolation this gap questioned; it has been removed and
   safeguard 3 is now an unconditional documented residual, not a
   probabilistic one.
2. **`scripts/smoke-endpoints.sh --mode shared` not run end-to-end** against
   a ciu-rendered stack — the script's container-naming assumptions
   (`${PROJECT}-${ENV}-pwmcp`) don't match the ad-hoc containers used for
   manual validation in the time available. Every individual mechanism in
   the new script section was independently exercised manually with real,
   quoted output (see P03-LOG.md); the full script itself should be run by
   the controller against a `ciu -d .`-deployed shared-mode stack before
   merge, same "commit the script, controller executes" pattern P01 used
   for its baseline pass (this environment DID have Docker, unlike P01's,
   so the baseline `--mode per-session` checks in the pre-existing sections
   of the script are expected to be runnable directly; only the new
   `--mode shared` section's full-script run is deferred).
   **Resolved** — run end-to-end during self-review (2026-07-13); it found
   real defects (cross-tool proof failed, cookie-isolation check was
   hollow). See `P03-SELFREVIEW.md` for full detail and fixes.
3. **Idle-recycle end-to-end restart not observed** — code path verified by
   construction (shares the tested `/browser/restart` primitive) but no
   live multi-minute observation was run.
   **Resolved** — run live during self-review; found the trigger condition
   was unreachable (dead code) and fixed. See `P03-SELFREVIEW.md`.

## Files Changed
```
A  pwmcp/containers/pwmcp/admin-server/index.js
A  pwmcp/containers/pwmcp/supervisord.shared.conf
A  pwmcp/containers/pwmcp/wait-for-cdp.sh
A  pwmcp/handoff/reports/P03-LOG.md
A  pwmcp/handoff/reports/P03-REPORT.md
M  pwmcp/README.md
M  pwmcp/ciu.compose.yml.j2
M  pwmcp/ciu.defaults.toml.j2
M  pwmcp/containers/pwmcp/Dockerfile
M  pwmcp/containers/pwmcp/entrypoint.sh
M  pwmcp/docs/ARCHITECTURE.md
M  pwmcp/docs/DEPLOYMENT.md
M  pwmcp/docs/SECURITY.md
M  pwmcp/docs/USAGE.md
M  pwmcp/scripts/smoke-endpoints.sh
```
