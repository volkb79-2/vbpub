# P03 Frontier Review — Shared Persistent Browser Mode (3-way benchmark)

**Reviewer:** controller frontier-pass (Opus 4.8 high, native claude, full Docker access)
**Date:** 2026-07-13
**Legs reviewed:** `bench/pwmcp-p03-terra-med` (codex/gpt-5.6-terra), `bench/pwmcp-p03-luna-high` (codex/gpt-5.6-luna), `bench/pwmcp-p03-sonnet5-high` (claude/sonnet-5)
**Verdict:** **MERGE `sonnet5-high`** into `main` (`git merge --no-ff`, merge commit `bcd7258`). terra-med and luna-high retained as reference branches/worktrees.

---

## Method

All three legs were built and gated independently with Docker — the capability the two codex
legs lacked in their sandboxes. Each leg was built to a distinct image tag
(`pwmcp-p03-{terra,luna,sonnet}:latest`), run as a distinct shared-mode container on an
isolated bridge network, and driven through its own committed smoke suite plus targeted
manual mechanism tests. Images/containers were pruned per leg to avoid resource exhaustion.

Because these container images bind the MCP servers to the container's eth0 interface and
enforce a Host-header allowlist, the smoke suites were run from a sibling container on the
same Docker network with `PWMCP_HOST=pwmcp` and the compose-injected allowed-hosts env vars
(the "real" deployment path). Host-published-port runs fail with connection-refused for a
pure networking reason unrelated to the implementations, and that was confirmed and excluded.

---

## Scope discipline (all three)

All three legs touch **only `pwmcp/**`** — no scope creep. All three keep the pre-P03
`supervisord.conf` byte-identical (per-session mode unaffected) and select a separate
`supervisord*.conf` at entrypoint time based on `PWMCP_BROWSER_MODE`. All three pin Chromium
flags (`--headless=new`, loopback-only `--remote-debugging-port=9222 --remote-debugging-address=127.0.0.1`,
`--no-sandbox --disable-setuid-sandbox`, `/tmp` profile) and implement strict entrypoint
validation of the new ciu vars (fatal, no silent clamp). Critically, **all three avoid
`--isolated` on both MCP servers**, so all three are architecturally capable of the cross-tool
workflow (the trap sonnet's self-review caught and removed).

---

## Gate results per leg

### sonnet5-high — WINNER (25/25, all safeguards verified)

| Gate | Result | Evidence |
|---|---|---|
| Docker build | PASS | clean build from worktree and from merged main |
| All 6 shared programs RUNNING | PASS | supervisorctl status |
| Admin health / reset / 404 | PASS | CHECK 17–19 |
| CDP unreachable from sibling (8931/8932 reachable) | PASS | CHECK 20 |
| Crash-restart (`kill -9` chromium, autorestart, next tool call succeeds on BOTH servers) | PASS | CHECK 21–23 |
| **Cross-tool proof (Playwright navigate → DevTools trace same page, trace references navigated URL)** | **PASS** | CHECK 24 |
| State-bleed characterization matches documented residual risk | PASS | CHECK 25 (bleed observed & asserted, per SECURITY.md) |
| Full `smoke-endpoints.sh --mode shared` | **25 passed, 0 failed** | independent run, sibling container |

Admin endpoint is Node stdlib only (`http`/`net`/`crypto`) with a hand-rolled ~20-line
supervisord XML-RPC client (stopProcess/startProcess chromium only) and a hand-rolled CDP
WebSocket client for reset — no new framework dependency. Idle-recycle filters real
`type==="page"` non-`chrome://` targets (the dead-code trap its self-review caught and fixed).

### terra-med — FAIL on core safeguards (cross-tool OK; reset + isolation broken)

| Gate | Result | Evidence |
|---|---|---|
| Docker build | PASS | clean |
| All shared programs RUNNING | PASS | its `smoke-shared-browser.sh` TEST 01 |
| Admin health / 404 from sibling | PASS | TEST 02–03 |
| Cross-tool proof (idle disabled) | **PASS** | its `shared-browser-mcp-check.mjs cross` — trace references navigated URL |
| **`POST /browser/reset`** | **FAIL** | HTTP 503 `{"error":"browser_unavailable","detail":"Internal error"}` — reset handler crashes |
| **Cookie isolation (safeguard 3)** | **FAIL** | its own TEST 05 asserts no-bleed; session B observed session A's `p03=A` cookie |
| Admin restart | FAIL | 503 during scripted run |
| DevTools recovery after adverse startup ordering | FAIL | TEST 14: 8932 `ECONNREFUSED` |
| Idle recycle | (untested cleanly — see note) | with `browser_max_idle_s=3` the recycle **raced** the cross-tool/reset tests and caused spurious about:blank/503; disabling idle isolated the genuine defects above |

Notable: terra's cross-tool contract actually works, but its `/browser/reset` endpoint is
broken (503) and its safeguard-3 mechanism test is internally contradictory — it *asserts*
per-session cookie isolation that the implementation cannot deliver (playwright-mcp over a
shared `--cdp-endpoint` shares one context; cookies bleed). The test therefore fails at
runtime. This is exactly the class of defect a static-only sandbox cannot surface.

### luna-high — FAIL on core safeguards (18/22 base checks; reset wedges CDP; isolation fails; cross-tool not reached)

| Gate | Result | Evidence |
|---|---|---|
| Docker build | PASS | clean (heaviest build; ~1091 LOC diff) |
| CHECK 1–18 (programs, both MCP init, forged-host, lighthouse audit + URL rejections, fault isolation, CDP-boundary, admin health) | PASS | extended `smoke-endpoints.sh shared` |
| **CHECK 19 admin endpoint set / reset accepts no params** | **FAIL** | reset path |
| **CHECK 20 reset leaves Chromium live** | **FAIL** | post-reset `health={"cdp_live":false,...}` — reset wedges the shared browser |
| **CHECK 21 reset clears state without restart** | **FAIL** | same root cause |
| **CHECK 22 two concurrent sessions isolate cookies** | **FAIL** | both sessions observe `pwmcp_a=alpha; pwmcp_b=beta` — cookies bleed |
| Cross-tool proof (later check) | **NOT REACHED** | run aborts at CHECK 22 with a 503; the shared browser is wedged by the failed reset |

luna's admin reset (Node + full Playwright `connectOverCDP`) throws 503 and leaves
`cdp_live:false`, i.e. it does not merely fail to clear state — it disrupts the shared
browser. Combined with the same inherent cookie-bleed as the other legs, luna fails
safeguards 2 and 3 at runtime and never demonstrates the core cross-tool proof.

---

## Flagged-by-this-pass tally (defects the leg's own self-review missed)

The central finding of this review is inherent and consistent: **playwright-mcp attached via
`--cdp-endpoint` shares a single browser context across MCP sessions, so cookies bleed between
concurrent sessions on all three legs.** Only sonnet handled this correctly.

| Leg | Defects found only by this frontier Docker pass | Self-review handled it? |
|---|---|---|
| **sonnet5-high** | none new. Its self-review already ran the full 25/25 suite live and caught+fixed the two feature-breaking defects itself (`--isolated` broke cross-tool; dead-code idle recycle). Reset works; bleed is correctly documented + asserted as residual risk (not falsely claimed as isolation). | Yes |
| **terra-med** | (1) `/browser/reset` returns HTTP 503 at runtime — safeguard 2 broken. (2) Safeguard-3 mechanism test asserts cookie isolation the impl cannot deliver → fails at runtime (bleed). (3) Admin restart flaky (503). (4) DevTools MCP fails to recover after adverse startup ordering (8932 ECONNREFUSED). (5) Idle-recycle default interval races live tool calls. Self-review documented state-bleed as a residual **but still shipped a gate asserting the opposite**. | No — static-only; claimed no runtime pass and deferred to controller |
| **luna-high** | (1) `/browser/reset` throws 503 and wedges CDP (`cdp_live:false`) — safeguard 2 not just failing but destructive. (2) Cookie isolation fails (bleed) — safeguard 3. (3) Cross-tool proof never demonstrated (run aborts on the wedged browser). luna's self-review did an extensive adversarial audit of its *check script* (fixed 9 hollow checks) but could not run any of it under Docker, so the runtime reset/isolation failures went undetected. | No — static-only; claimed no runtime pass and deferred to controller |

---

## Adversarial-test quality (real mechanism vs hollow)

- **sonnet5-high:** real. `kill -9` on the chromium PID + poll for supervisord restart + real
  MCP tool call on both servers; real sibling-container CDP probe; real two-session cookie
  set/read asserting the *observed* bleed; real Playwright-navigate→DevTools-trace with a URL
  assertion. Verified end-to-end by this pass.
- **terra-med:** its `smoke-shared-browser.sh` is arguably the most *ambitious* harness
  (in-flight typed-error on crash, adverse startup ordering, idle recycle), i.e. the tests are
  real mechanism tests — but several **fail against terra's own implementation** (reset 503,
  isolation bleed, devtools recovery), so the leg does not pass its own gates.
- **luna-high:** real mechanism tests too (seeded cookies, PID-change assertions, sibling CDP
  probe), and its self-review meaningfully de-hollowed them — but the reset/isolation checks
  fail at runtime and the browser wedges before the cross-tool proof.

---

## Documentation

All three updated ARCHITECTURE / SECURITY / USAGE / DEPLOYMENT / README under `pwmcp/docs`.
sonnet's docs are the most complete (ARCHITECTURE +107, SECURITY +82) and, decisively,
**accurate**: SECURITY.md documents the cookie-bleed residual as an accepted, opt-in risk
rather than claiming an isolation guarantee that does not hold — which is precisely the
behavior the runtime gates confirm. terra and luna both document a state-bleed residual in
prose yet ship isolation-asserting gates that contradict it.

---

## Decision & post-merge verification

**Merged `sonnet5-high` into `main`** (`git merge --no-ff bench/pwmcp-p03-sonnet5-high`, merge
commit **`bcd7258`**). Rebuilt the image from merged `main` and re-ran the full shared-mode
smoke from a sibling container: **25 passed, 0 failed**, including CHECK 24 cross-tool proof
and CHECK 21–23 crash-restart recovery. sonnet required no fixes — it was clean.

terra-med and luna-high branches/worktrees are **retained** as reference. Their reset and
cookie-isolation defects are fixable (reset needs the disconnect-not-close semantics sonnet
uses; the safeguard-3 gate needs to assert the observed bleed as residual risk rather than
isolation), but they are not the merge target.
