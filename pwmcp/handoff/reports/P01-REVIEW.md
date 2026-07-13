# P01 Review Report (Pass #2 — merge gate)

Reviewer session under `docs/controller-workflow-v2.md` §6–§8. Reviewed the
implementer-committed branch `feat/pwmcp-p01-chrome-devtools-mcp` against the
handoff `pwmcp/handoff/P01-chrome-devtools-mcp.md`, the standing review
checklist, and the pwmcp README contracts. Unlike the implementer, this session
HAD working Docker + ciu + network, so all gates were actually executed against
a locally built image (`ghcr.io/volkb79-2/pwmcp:1.61.0-r3`) rather than deferred.

## Outcome

10 issues found and fixed in the worktree (2 CRITICAL / total-non-function, 6
functional, 2 minor). After fixes, the full smoke suite passes 8/8 including an
end-to-end Chromium drive, and the ciu compose render is verified purely
additive. Merged.

## Findings (all flagged-by-pass-1: NO unless noted)

The self-review (pass #1) found and fixed 10 issues (see P01-SELFREVIEW.md);
none of the issues below were among them — pass #1 did not catch any of these.

### CRITICAL

1. **mcp-proxy invoked with a non-existent `--command` flag.** Committed
   supervisord line was `mcp-proxy --command "chrome-devtools-mcp …" --port 8932`.
   Verified against the `mcp-proxy@6.5.2` package source (`dist/bin/mcp-proxy.mjs`):
   the child command is a **positional after `--`**, not a `--command` flag
   (usage: `mcp-proxy [options] -- <command> [args...]`). As written, mcp-proxy
   would try to exec a binary literally named "chrome-devtools-mcp --headless …"
   → ENOENT → `devtools-mcp` never reaches RUNNING. The whole feature was
   non-functional. **Fix:** `mcp-proxy --port 8932 -- chrome-devtools-mcp …`.

2. **`--no-sandbox` silently dropped → Chrome aborts "No usable sandbox".**
   The committed command passed a bare `--no-sandbox` to `chrome-devtools-mcp`.
   That is not a chrome-devtools-mcp option; it is NOT forwarded to Chrome (I
   confirmed via a shim that logged the exact argv Puppeteer passes — `--no-sandbox`
   was absent). In the hardened container (no-new-privileges, cap_drop ALL, no
   unprivileged userns) Chrome then aborts with SIGABRT
   `FATAL: No usable sandbox!`. `@playwright/mcp` works only because Playwright
   forwards `--no-sandbox` itself. **Fix:** use the documented passthrough
   `--chrome-arg=--no-sandbox --chrome-arg=--disable-setuid-sandbox` (per the
   server's own `--help` examples). Verified: `list_pages` and `new_page` then
   drive Chromium with `isError=false`.

### Functional

3. **`ciu.defaults.toml.j2` image tag not bumped.** docker-bake bumped
   `PWMCP_VERSION_PYPI` r2→r3, but `[pwmcp.unified.image].tag` was left at
   `1.61.0-r2`. Rendered defaults would therefore pull the OLD image without the
   devtools server — the feature would not deploy by default. **Fix:** → `1.61.0-r3`.

4. **Smoke script omitted the `Accept` header → HTTP 406 on every initialize.**
   MCP streamable-HTTP requires `Accept: application/json, text/event-stream`;
   without it both servers return 406 (confirmed live), so every "correct Host"
   assertion would fail. **Fix:** added the header to all MCP requests.

5. **Smoke script did not parse the SSE response frame.** Responses are SSE
   (`event: message` / `data: {json}`), not raw JSON; piping the body to `jq`
   errors (`Invalid numeric literal`). **Fix:** added `sse_extract_json()`.

6. **tools/list check could never pass (stateful session).** Streamable-HTTP is
   session-stateful; a bare `tools/list` returns `Bad Request: No valid session
   ID provided`. **Fix:** rewrote the check as a full session handshake
   (initialize → capture `Mcp-Session-Id` → `notifications/initialized` →
   `tools/call`) and made it call `new_page` against a `data:` URL, satisfying
   the handoff's "prove it actually drove Chromium" requirement.

7. **`timeout 30 wait_for_supervisord` cannot run a shell function** (rc 127
   "No such file or directory") — `timeout` only execs external binaries. This
   was introduced by pass #1's own fix. **Fix:** call the (self-bounded) function
   directly; removed the now-unused `check_cmd`.

8. **Container had no `unix_http_server` socket → `supervisorctl` unusable.**
   supervisord.conf explicitly omitted the socket, so the smoke script's
   program-status and fault-isolation checks (which use `supervisorctl`) were
   inert against the image. **Fix:** added a `[unix_http_server]` on
   `/tmp/supervisor.sock` (writable by uid 1000, no network exposure, no
   hardening change) + `[supervisorctl]`.

9. **`supervisorctl` needs `-c <conf>`** — the config is at a non-default path
   (`/etc/supervisor/conf.d/pwmcp.conf`); bare `supervisorctl` reads the base
   image's default config pointing at `/var/run/supervisor.sock`. **Fix:** passed
   `-c` in all four calls.

### Minor

10. **jq operator-precedence bug** in the tools/list filter
    (`.error == null and .result.tools | type == "array"` binds `|` across the
    whole expression) and **dead `_BAKE_VAR_PATTERN`** regex in
    `resolve-playwright-version.py`. Both fixed/removed.

## Note on `--executable-path`

The handoff listed the upstream flag as `--executablePath` (camelCase); the code
uses kebab `--executable-path`. Confirmed harmless: chrome-devtools-mcp uses yargs
with camel-case expansion, and the shim proved the correct Chromium binary
(`/ms-playwright/chromium-1228/...`) is honored. Left as-is for consistency with
`@playwright/mcp`.

## Gate output

### Docker build (`docker buildx bake pwmcp-pypi-latest --load`)
Succeeded — `npm install -g chrome-devtools-mcp@1.5.0 mcp-proxy@6.5.2` completed
on the base image's Node, resolving the REPORT's "Node version unverified" gap
(base Node satisfies chrome-devtools-mcp's `^20.19 || ^22.12 || >=23`).

### Smoke suite (`scripts/smoke-endpoints.sh`, against the built image)
```
CHECK 1: All three programs RUNNING (with 30s poll) ... PASS
CHECK 2: MCP initialize with correct Host header (JSON-RPC validated) ... PASS
CHECK 3: MCP with forged Host header (expect rejection) ... PASS        # 8931 → 403
CHECK 4: DevTools MCP initialize with correct Host header ... PASS
CHECK 5: DevTools MCP with forged Host header (expect SUCCESS — no allowlist) ... PASS
CHECK 6: DevTools MCP end-to-end new_page (drives Chromium) ... PASS     # isError=false
CHECK 7: MCP (8931) works after devtools health check ... PASS
CHECK 8: MCP (8931) still works after devtools-mcp stopped ... PASS      # fault isolation
Results: 8 passed, 0 failed (8 total)
```
Independently confirmed: `@playwright/mcp` (8931) forged-Host → 403; devtools
`new_page`/`list_pages` return real page state; stopping `devtools-mcp` leaves
8931 and 3000 responding (fault isolation).

### ciu compose render (before → after, defaults)
Rendered both the pre-branch and current `ciu.compose.yml.j2`. Diff is purely
additive: `PWMCP_DEVTOOLS_ALLOWED_HOSTS`, the `127.0.0.1:8932:8932` port map, and
the devtools Traefik router (with `/devtools` StripPrefix + guard middleware).
The 3000 and 8931 lines are byte-identical — satisfies the handoff's
"byte-identical except additive lines" contract.

## flagged-by-pass-1 tally (trial metric, §6)

- Issues pass #1 (self-review) found: 10 (all valid, all already fixed in the
  committed code).
- Issues pass #2 found that pass #1 missed: **10** (2 CRITICAL/total-non-function,
  6 functional, 2 minor) — flagged-by-pass-1 = **NO** for all.
- Of pass #1's 10 findings, pass #2 would independently have caught ~9
  (all except possibly the "Chrome-major compatibility note" doc nicety).

Net: pass #1 was necessary but not sufficient. The two CRITICAL defects (proxy
invocation, sandbox passthrough) each made the feature completely non-functional
and were only surfaced by actually building and driving the container — which the
gate (pass #2) did and the self-review did not.
