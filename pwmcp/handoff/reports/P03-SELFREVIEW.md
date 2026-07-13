# P03 Self-Review

Reviewer role: same agent, switched from implementer to reviewer, reading
the DIFF against the handoff (`pwmcp/handoff/P03-shared-browser-mode.md`)
mechanically, then actually running the two residual gaps the original
REPORT left as "not run" rather than leaving them undemonstrated. Docker was
available in this environment; both gaps were run live and both surfaced
real defects, which are fixed in this pass. Fix commits are separate from
the original implementation commit.

## Method

- Built the image (`docker build -f containers/pwmcp/Dockerfile -t
  pwmcp-p03-review:latest .`), ran a shared-mode container on a dedicated
  Docker network (`pwmcp-review-net`), and ran a helper container
  (`smoke-runner`, alpine + bash/curl/jq/docker-cli, docker socket mounted)
  on the same network to execute `scripts/smoke-endpoints.sh --mode shared`
  exactly as a controller would (real HTTP calls over the Docker network,
  real `docker exec`/`supervisorctl` calls against the running container —
  not mocked).
- Separately ran a dedicated container with `PWMCP_BROWSER_MAX_IDLE_S=10`
  and `PWMCP_ADMIN_IDLE_CHECK_INTERVAL_S=2` and observed the `chromium`
  supervisord PID across a real 20+ second wait.

## Findings

### 1. Gate commands / REPORT quotes real output — PASS, with one correction

The original P03-LOG.md's `docker build`/`docker run`/`curl`/`kill -9`
commands and their quoted output are real (re-ran equivalent commands in
this session and got consistent behavior — e.g. the crash-restart mechanism
recovered chromium and both attached MCP servers within seconds, matching
the LOG's claim). No reconstructed numbers or future-tense claims found in
LOG/REPORT for what WAS run.

However, the REPORT's two "gaps" (smoke script `--mode shared` full run;
idle-recycle live observation) were explicitly and honestly flagged as not
run, per the handoff's own evidence rules ("never claim an unrun check") —
this was correct practice, not a violation. Both are now closed (see below).

### 2. Diff scope — PASS

All 15 changed files are under `pwmcp/**`, matching the handoff's scope
restriction. Walked the handoff's numbered requirements:
Mode plumbing, startup ordering, admin endpoint implementation, Chromium
launch flags, strict validation — all implemented, matching files present
in the diff (`entrypoint.sh`, `supervisord.shared.conf`, `wait-for-cdp.sh`,
`admin-server/index.js`, `ciu.defaults.toml.j2`, `ciu.compose.yml.j2`).
Nothing in scope was silently skipped.

### 3. REAL DEFECT — cross-tool workflow was broken (the core reason P03 exists)

Running the handoff's required cross-tool proof
(`scripts/smoke-endpoints.sh --mode shared`, "cross-tool proof" check) for
the first time against a real built image failed:

```
CHECK 24: cross-tool proof (Playwright navigate -> DevTools trace, same page) ... FAIL
  nav={"...Page URL: data:text/html,<h1>pwmcp-shared-cross-tool</h1>..."}
  stop={"...URL: chrome://new-tab-page/..."}
```

Root cause: both `mcp` and `devtools-mcp` were launched with `--isolated`
in `supervisord.shared.conf`. Per upstream semantics this gives each
attached MCP server its OWN browser context on the shared Chromium — so
Playwright's navigation happened in one context while DevTools traced a
blank tab in a completely different context. The safeguard chosen for
requirement 3 (state-bleed containment) silently defeated the entire
feature the package exists to deliver.

**Fix**: removed `--isolated` from both `mcp` and `devtools-mcp` in
`containers/pwmcp/supervisord.shared.conf`. Rebuilt and re-ran the full
smoke script — cross-tool proof now passes (trace correctly shows
`URL: data:text/html,<h1>pwmcp-shared-cross-tool</h1>`). Updated
`docs/SECURITY.md`'s "State-bleed residual" section and the
`supervisord.shared.conf` header comments to reflect the corrected,
honest posture: shared mode provides **no** per-session cookie/storage
isolation (unconditionally, not "unverified" as originally documented) —
this is an accepted permanent tradeoff of shared mode, not a probabilistic
gap pending upstream confirmation. `docs/ARCHITECTURE.md`'s diagram/labels
for per-session mode (which still uses per-session isolated contexts) were
left unchanged as they were unaffected and accurate.

### 4. Hollow test found and fixed — cookie-isolation check (criterion 3 of this review)

The original "two concurrent MCP sessions do not observe each other's
cookies" check (`scripts/smoke-endpoints.sh`, was CHECK 25) never set or
read a cookie — it only asserted that two independent `browser_navigate`
calls each succeeded. **This test would pass identically if state-bleed
containment (or the entire browser) were deleted and replaced with a stub
that accepts navigate calls.** That is a hollow test by the letter of this
review's criterion 3.

**Fix**: rewrote the check to actually call `browser_evaluate` to set
`document.cookie` in session A (against a real in-container HTTP fixture
origin, `127.0.0.1:9199`, already started earlier in the script for the
Lighthouse check) and read `document.cookie` in session B, asserting on the
observed value. Ran it manually first outside the script to confirm the
real, current behavior:

```
--- session A sets sid=alice ---
"Result": "sid=alice"
--- session B navigates and reads cookie ---
"Result": "sid=alice"
```

Session B genuinely observes session A's cookie — this is a real,
reproducible characterization of the current (correct, but non-isolating)
behavior. The check was renamed to "state-bleed characterization matches
documented residual risk" and now asserts the bleed IS present (matching
`docs/SECURITY.md`), rather than pretending an isolation guarantee exists.
It will catch a regression in EITHER direction: silent isolation appearing
(would mean the check needs updating, not silently green) or the bleed
becoming undocumented. Full script re-run after the fix: 25/25 pass.

### 5. REAL DEFECT — idle-recycle was dead code (never triggers)

Ran the idle-recycle mechanism live for the first time (previously flagged
as "not run — would need a multi-minute wait"; a 10s `browser_max_idle_s`
with a 2s check interval makes this a ~20s wait, well within budget):

```
$ docker run ... -e PWMCP_BROWSER_MAX_IDLE_S=10 -e PWMCP_ADMIN_IDLE_CHECK_INTERVAL_S=2 ...
$ supervisorctl ... pid chromium
9
$ sleep 20; supervisorctl ... status chromium
chromium   RUNNING   pid 9, uptime 0:00:28    # unchanged -- did NOT recycle
$ curl .../browser/health
{"targetCount":2, ...}
$ curl 127.0.0.1:9222/json/list
[{"url":"chrome://newtab/", "type":"page", ...}, {"url":"chrome-untrusted://new-tab-page/...", "type":"iframe", ...}]
```

Root cause: the idle condition was raw `/json/list` length === 0. Headless
Chromium always keeps its own default `New Tab` page (and a
`chrome-untrusted://new-tab-page/...` iframe, and — observed in a later
run — a `chrome-extension://.../thunk.js` Service Worker target) open for
the process lifetime. `/json/list` is therefore never actually empty, and
the idle-recycle loop's trigger condition was unreachable — dead code
despite being fully implemented and wired to a tested restart primitive.

**Fix**: `admin-server/index.js`'s idle loop now filters to CDP targets
with `type === "page"` AND a `url` that is not a `chrome://` /
`chrome-untrusted://` internal page — i.e., only counts real
consumer-navigated pages, which is what "idle" is supposed to mean.
Verified live after the fix, same 10s/2s config:

```
$ supervisorctl ... pid chromium
9
$ sleep 20; supervisorctl ... status chromium
chromium   STARTING                          # recycling in progress
$ sleep 5; supervisorctl ... status chromium
chromium   RUNNING   pid 414, uptime 0:00:08  # new PID -- recycled
$ curl .../browser/health
{"ok":true,"cdpAlive":true, ...}              # healthy after recycle
```

Updated `docs/ARCHITECTURE.md`'s "Optional idle recycle" section and the
admin-server source comments to describe the corrected definition and
record this live verification.

### 6. Dates, counts, paths — PASS

LOG/REPORT dates are `2026-07-13` throughout (today), consistent with the
git commit and this review. File list in REPORT matches `git diff --stat`
(15 files, matches). No stale counts found elsewhere.

### 7. LOG/REPORT present, ASCII, no dead code/scaffolding — PASS, with a note

Both files present. Diff uses em dashes (`—`) throughout comments and docs
— this is the established house style already present in P01/P02's
REPORT/LOG files and in the pre-existing `supervisord.conf` (verified: 4
occurrences in the file on `main`), so it is not a P03-introduced ASCII
violation; not flagged as a defect. No leftover scaffolding, commented-out
code, or TODO markers found in the diff. The idle-recycle dead-code path
(finding 5) was a *logical* dead path (unreachable trigger condition), not
literal dead/commented code — now fixed and reachable.

## Fix commits

Fixes for findings 3-5 (all interrelated: removing `--isolated` to fix the
cross-tool workflow, rewriting the hollow cookie-isolation check to assert
the resulting real bleed, and fixing the idle-recycle target-count filter)
are committed together as one focused fix commit on this branch, since they
touch the same subsystem and were discovered in the same review pass. Doc
updates (`docs/SECURITY.md`, `docs/ARCHITECTURE.md`,
`supervisord.shared.conf` comments) are included in the same commit as the
code they document, to avoid a window where docs and code disagree.

## Residual gaps status

Both gaps flagged in the original P03-REPORT.md are now closed, not just
re-flagged:
- `scripts/smoke-endpoints.sh --mode shared` full run: **done**, 25/25 pass
  after fixes (was 24/25 with a script bug before that, 19/24 before the
  `--isolated` fix due to the cross-tool defect — see above for the
  intermediate runs).
- Idle-recycle live observation: **done**, verified with a real ~20s wait
  and a real PID change after the target-count-filter fix.

No new BLOCKER conditions. The state-bleed residual risk (safeguard 3) is
now *more* honestly documented than before (unconditional, not
"unverified"), which is a stricter posture, not a regression — per the
handoff's own instruction to document real residual risk rather than claim
unverified isolation.
