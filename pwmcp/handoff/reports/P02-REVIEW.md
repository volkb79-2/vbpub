# P02 Review Report (Pass #2 — merge gate)

Reviewer session under `docs/controller-workflow-v2.md` §6–§8. Reviewed the
implementer-committed branch `feat/pwmcp-p02-lighthouse-audit-server` against
the handoff `pwmcp/handoff/P02-lighthouse-audit-server.md`, the standing
review checklist, and the pwmcp README contracts. This session HAD working
Docker, so every gate was actually executed against a locally built image
(`ghcr.io/volkb79-2/pwmcp:1.61.0-r4`) rather than deferred — same posture as
the P01 pass #2.

## Outcome

4 issues found and fixed in the worktree (1 CRITICAL, 2 functional, 1 minor).
After fixes, the full smoke suite passes 16/16 including a real end-to-end
`lighthouse_audit` against an in-container HTML fixture, a live audit-timeout
mechanism test (Chromium confirmed killed, server confirmed still RUNNING,
client gets a typed `isError:true` result), and a purely-additive ciu compose
render (both internal and external/expose modes). Merged.

## Findings (flagged-by-pass-1: NO for all)

The self-review (pass #1, `P02-SELFREVIEW.md`) found and fixed 5 issues (1
CRITICAL AbortController dead code, 2 functional, 2 minor). None of the
findings below were among them.

### CRITICAL

1. **Timeout-cleanup handler crashes the whole server process.** Pass #1's
   own fix for the AbortController dead code introduced a new defect: the
   `setTimeout` callback in `runLighthouse()` called `chrome.kill().catch(()
   => {})`. Verified live: triggering an actual timeout (audited a
   deliberately-hanging in-container HTTP listener with
   `LIGHTHOUSE_TIMEOUT_MS=10000`) crashed the Node process with
   `TypeError: Cannot read properties of undefined (reading 'catch')` —
   `chrome.kill()` does not reliably return a Promise in every chrome-launcher
   code path. Because this runs inside a bare (unawaited) `setTimeout`
   callback, the throw was an **unhandled exception that killed the entire
   lighthouse-mcp process**, not just the one timed-out audit — every
   in-flight request on that connection got `-32000: Connection closed`
   instead of a typed tool error, directly violating the handoff's failure-
   safety contract ("Audit failures ... map to a closed set of typed tool
   errors ... no raw stack traces"). supervisord respawned the program, so
   the outage was self-healing but real (all in-flight audits on the crashed
   process instance were lost, not just the offending one).
   **Fix:** wrapped the kill call in `Promise.resolve(chrome.kill()).catch(()
   => {})` inside a `try/catch` (both index.js:112-177), added a `timedOut`
   flag so a timeout now surfaces as a clean `isError:true` "Lighthouse audit
   timed out after Nms" tool result, and added process-level
   `uncaughtException`/`unhandledRejection` handlers as a safety net so no
   future bug in cleanup code can take the whole server down.
   **Verified live (post-fix):** same hang scenario now returns
   `{"result":{"content":[{"type":"text","text":"Audit failed: Lighthouse
   audit timed out after 10000ms"}],"isError":true}}`, `supervisorctl status`
   shows `lighthouse-mcp RUNNING` throughout (no restart), and `ps aux`
   inside the container confirms the Lighthouse-launched Chromium process is
   gone afterward (only the persistent devtools-mcp Chrome remains) — this is
   exactly the handoff's required mechanism test ("kills/hangs a target and
   asserts the Chromium process is gone afterwards").

### Functional

2. **`ciu.defaults.toml.j2` image tag not bumped** — same class of defect as
   P01 finding #3. `docker-bake.hcl` bumped `PWMCP_VERSION_PYPI` r3→r4, but
   `[pwmcp.unified.image].tag` was left at `1.61.0-r3`. Rendered defaults
   would deploy the OLD image (without lighthouse-mcp) by default. **Fix:**
   → `1.61.0-r4`. Verified via a from-scratch Jinja2 render of
   `ciu.compose.yml.j2` (before: pre-P02 `main`@3240dd1, after: this branch)
   in both internal and external/expose modes — after the fix the rendered
   `image:` line correctly reads `...pwmcp:1.61.0-r4` and the diff is
   otherwise purely additive (new `PWMCP_LIGHTHOUSE_ALLOWED_HOSTS` env line,
   third port mapping, own Traefik router block, plus the doc-comment word
   updates "three"→"four" endpoints / "both"→"all" services, which are
   expected given a new service was added — the 3000/8931/8932 lines are
   otherwise byte-identical).

3. **Smoke-test check 9 audited a non-auditable target.** The committed
   `lighthouse_audit` end-to-end check pointed at `http://<host>:8931/mcp` —
   the `@playwright/mcp` streamable-HTTP JSON-RPC endpoint, not an HTML page.
   Verified live: Lighthouse returned `{"scores":{"performance":null,
   "seo":null},"opportunities":[]}` — a **hollow-in-the-other-direction**
   test (it correctly failed here, but only because I ran it; the assertion
   `.scores.performance != null` was never exercised against a real page by
   pass #1 or the implementer, since neither had Docker). Any future refactor
   that accidentally made this endpoint return SOME response, even an error
   page, risked a false pass. **Fix:** the check now starts a tiny disposable
   HTML fixture INSIDE the pwmcp container itself (Node's built-in `http`
   module, already present — no extra container needed) on
   `127.0.0.1:9199`, and audits that. This is a genuine "reachable in-network
   HTTP URL" per the handoff. **Verified live (post-fix):** check 9 passes
   with real numeric `performance`/`seo` scores.

### Minor

4. **`LIGHTHOUSE_VERSION` ARG pinned a package nobody imports.** The
   Dockerfile's global `npm install -g ... lighthouse@${LIGHTHOUSE_VERSION}`
   installed a copy of `lighthouse` that the vendored server never uses — the
   server's `import lighthouse from "lighthouse"` resolves from its own
   `containers/pwmcp/lighthouse-mcp/package.json`, which declared
   `"lighthouse": "^13.4.0"` (caret range, no lockfile committed). This means
   the ARG-driven "exact pin" the handoff requires ("Pin `lighthouse` ...
   exactly, via the P01 templated-ARG mechanism") was decorative — the
   actually-loaded version could drift on any rebuild pulling a newer 13.x.
   **Fix:** removed the unused global install (saves build time/image
   bloat); changed the vendored `package.json` to an exact `"lighthouse":
   "13.4.0"` (no caret); added a build-time assertion (Dockerfile RUN step)
   that fails the build if the installed `node_modules/lighthouse` version
   ever drifts from the `LIGHTHOUSE_VERSION` ARG, so the two can't silently
   diverge again. **Verified via full rebuild**: the assertion step ran and
   passed (`13.4.0` == `13.4.0`).

## Gate output

### Docker build (`docker buildx bake pwmcp-pypi-latest --load`)
Succeeded both before and after the fixes — `npm install -g playwright
@playwright/mcp chrome-devtools-mcp mcp-proxy` + vendored `npm install
--production` for lighthouse-mcp, plus the new lighthouse-version drift
assertion. Image: `ghcr.io/volkb79-2/pwmcp:1.61.0-r4`.

### Smoke suite (`scripts/smoke-endpoints.sh`, against the built image)
Ran against a standalone `docker run` container (no ports published; hit by
container-internal bridge IP, same approach as the P01 pass-2 gate) with
`PROJECT`/`ENV` set so the derived container name matched:

```
CHECK 1:  All four programs RUNNING (30s poll) ................. PASS
CHECK 2:  MCP initialize, correct Host ........................... PASS
CHECK 3:  MCP forged Host (expect rejection) ..................... PASS
CHECK 4:  DevTools initialize, correct Host ...................... PASS
CHECK 5:  DevTools forged Host (expect SUCCESS — no allowlist) ... PASS
CHECK 6:  DevTools end-to-end new_page (drives Chromium) ......... PASS
CHECK 7:  Lighthouse initialize, correct Host .................... PASS
CHECK 8:  Lighthouse forged Host (expect SUCCESS — no allowlist) . PASS
CHECK 9:  Lighthouse real lighthouse_audit (categories present) .. PASS  (fixed target, see finding 3)
CHECK 10: Lighthouse rejects file:// ............................. PASS
CHECK 11: Lighthouse rejects data: ............................... PASS
CHECK 12: Lighthouse rejects chrome:// ............................ PASS
CHECK 13: MCP (8931) works after lighthouse health check ......... PASS
CHECK 14: DevTools (8932) works after lighthouse health check ..... PASS
CHECK 15: MCP (8931) still works after lighthouse-mcp stopped ..... PASS
CHECK 16: DevTools (8932) still works after lighthouse-mcp stopped  PASS
Results: 16 passed, 0 failed (16 total)
```

Independently confirmed beyond the script: audit-timeout mechanism test (see
finding 1) — hanging target + 10s timeout → clean typed error, server stays
RUNNING, Chromium process reaped.

### ciu compose render (before → after, both internal and external/expose modes)
Rendered `ciu.compose.yml.j2` + `ciu.defaults.toml.j2` from pre-P02 `main`
(3240dd1) and this branch with a from-scratch Jinja2 script (no live ciu
CLI dependency needed for a template-only diff). Both modes: diff is purely
additive (new env var, port mapping, Traefik router block) plus the expected
doc-comment updates; the 3000/8931/8932 configuration is otherwise
byte-identical. Image tag correctly reflects `1.61.0-r4` after fix #2.

## Server-selection & contract spot-check

Independently confirmed the implementer's LOG rationale for vendoring rather
than adopting `lighthouse-mcp@0.1.15`: that package's README documents RFC
1918 IP blocking, which would indeed reject `http://<docker-alias>/`-style
internal targets — correct call for this environment's "audit internal
service" use case. URL validation (`file://`/`data:`/`chrome://` rejection),
response bounds (100 KB / 10 opportunities), and the four-program supervisord
integration were spot-checked against the running container and match the
handoff.

## flagged-by-pass-1 tally (trial metric, §6)

- Issues pass #1 (self-review) found: 5 (1 CRITICAL, 2 functional, 2 minor;
  all valid, all already fixed in the committed code).
- Issues pass #2 found that pass #1 missed: **4** (1 CRITICAL, 2 functional,
  1 minor) — flagged-by-pass-1 = **NO** for all.
- Of pass #1's 5 findings, pass #2 would independently have caught at least
  3 (the AbortController dead code was superseded by this pass's own
  live-timeout test; the hollow file:// test and the bash `local` bug would
  both have surfaced under an actual smoke run).

Net: consistent with P01 — pass #1 (no Docker) is necessary but not
sufficient. This pass's single CRITICAL finding is notable because it was
introduced BY pass #1's own fix for a different defect (AbortController dead
code): the self-review correctly diagnosed the timeout not killing Chrome,
but its replacement code had its own unhandled-exception bug that only a
live triggered-timeout run — not code reading — could surface. Both this
pass's CRITICAL and the missed-audit-target hollow test (finding 3) required
actually driving the running container, not static review.
