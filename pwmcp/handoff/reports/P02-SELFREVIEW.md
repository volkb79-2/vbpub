# P02 Self-Review Report

Review of the committed diff `feat/pwmcp-p02-lighthouse-audit-server` (base: `main` after P01 merge) against the handoff `P02-lighthouse-audit-server.md`.

## Gate-Command Audit

The handoff requires the following gate commands to be run:

1. **Docker build** (`docker buildx bake pwmcp-pypi-latest --load`) — NOT run. Agent environment has no Docker. Same limitation as P01.
2. **Smoke suite** (`scripts/smoke-endpoints.sh` against built image) — NOT run. Same limitation.
3. **ciu compose render** (before→after diff, defaults) — NOT run. Same limitation.

**Verdict**: No gate command was run. The REPORT accurately states this limitation. No reconstructed or fabricated output appears in LOG or REPORT. All smoke-test output references are accurately marked as "committed and ready" — no future-tense claims disguised as results.

## Scope Audit

All 17 files in the diff are under `pwmcp/`. No file outside `pwmcp/**` was touched. The handoff says "Touch only `pwmcp/**`" — ✓ satisfied.

## Numbered Requirements Walk

Walk of every named contract in the handoff's §Required Contracts:

| # | Contract | Status | Evidence |
|---|---|---|---|
| 1 | **Server selection**: evaluate existing packages; adopt only if pinnable, stdio, injectable, bounded; else vendored | ✓ | LOG documents evaluation of 3 packages. Decision: vendored in-repo server. |
| 2 | **Tools**: `lighthouse_audit(url, categories?, form_factor?)` | ✓ | Implemented in index.js lines 310-338. Categories subset: performance, accessibility, seo, best-practices. |
| 3 | **Tools**: `lighthouse_metrics(url, form_factor?)` | ✓ | Implemented in index.js lines 340-369. Returns LCP, CLS, TBT, FCP, SI, TTI. |
| 4 | **Response bounds**: cap items AND total bytes in tool descriptions | ✓ | `MAX_OPPORTUNITIES=10`, `MAX_RESPONSE_BYTES=102400`. Both stated in tool descriptions. `capResponse()` enforces both. |
| 5 | **Unknown categories/form_factors**: typed errors, no silent defaults | ✓ | `validateCategories()` throws McpError for unknown categories. `validateFormFactor()` throws for invalid values. |
| 6 | **URL validation**: http/https only; reject file:/data:/chrome:/ | ✓ | `validateUrl()` checks protocol, throws McpError. Smoke tests 4d/4e/4f verify rejection of all three. |
| 7 | **Internal Docker hostnames allowed** | ✓ | No RFC 1918 IP blocking. Internal hostnames resolve freely. Documented in SECURITY.md. |
| 8 | **Failure safety**: typed tool errors, safe messages, no stack traces | ✓ | Catch block sanitizes error messages (strips paths, parenthesized details). Returns `isError=true` tool results. |
| 9 | **Audit timeout**: configurable, default ≤120s, Chrome process killed | ✓ | `TIMEOUT_MS` default 120000, bounded [10000,300000]. `runLighthouse()` kills Chrome via `setTimeout`. |
| 10 | **Container integration**: mirror P01 exactly | ✓ | New `[program:lighthouse-mcp]` via mcp-proxy. `PWMCP_LIGHTHOUSE_ALLOWED_HOSTS` env var. `lighthouse_port=8933` in defaults. Fourth port mapping in compose. Own Traefik router. No hardening relaxation. |
| 11 | **Version bump** per P01 templated mechanism | ✓ | `PWMCP_VERSION_PYPI` 1.61.0-r3→r4. `PWMCP_VERSION_NPM` 1.61.1-r2→r3. |
| 12 | **Smoke validation**: initialize, forged Host, real tool call, file:// rejection, supervisord status, fault isolation | ✓ | Checks 1, 7-14 in smoke-endpoints.sh cover all required scenarios. |
| 13 | **Documentation**: README, USAGE, SECURITY, ARCHITECTURE updated | ✓ | All five doc files updated with 4th service references. |
| 14 | **Out of scope**: no persistent browser, no history/trending, no auth | ✓ | Not implemented. |

## Adversarial Tests Audit

All smoke-test checks with observable assertions:

| Check | Test | Observable Assertion | Hollow? |
|---|---|---|---|
| 1 | Wait for 4 programs RUNNING | `supervisorctl status` output parsed; program state checked | No — asserts actual process state |
| 2 | MCP initialize correct Host | HTTP 2xx + valid JSON-RPC with `serverInfo.name` | No — `jq -e` validates actual response body |
| 3 | MCP forged Host (expect rejection) | HTTP non-2xx OR JSON-RPC `.error != null` | No — asserts rejection |
| 4 | DevTools initialize correct Host | Same as check 2 | No |
| 5 | DevTools forged Host (expect SUCCESS) | Valid initialize (mcp-proxy gap) | No — asserts server responds (gap is documented) |
| 6 | DevTools new_page end-to-end | `.result.content` exists, `.isError != true` | No — asserts real browser interaction |
| 7 | Lighthouse initialize correct Host | Same as check 2 | No |
| 8 | Lighthouse forged Host (expect SUCCESS) | Same gap as check 5 | No |
| 9 | Lighthouse real audit tool call | `.result.content[0].text` JSON has `.scores` fields | No — asserts actual audit result |
| 10 | Lighthouse rejects file:// | JSON-RPC error OR `.isError == true` | **Fixed** — transport failure now fails (was hollow) |
| 11 | Lighthouse rejects data: | Same as check 10 | No — new check, no hollow branch |
| 12 | Lighthouse rejects chrome:// | Same as check 10 | No — new check, no hollow branch |
| 13 | MCP (8931) works after lighthouse checks | Initialize succeeds | No |
| 14 | DevTools (8932) works after lighthouse checks | Initialize succeeds | No |
| 15 | MCP works after lighthouse-mcp stopped | Initialize succeeds | No — requires actual supervisorctl stop |
| 16 | DevTools works after lighthouse-mcp stopped | Initialize succeeds | No — same |

**Hollow test found and fixed**: Check 10 (file:// rejection) originally passed unconditionally on transport failure (empty body → `record_pass`). This would falsely pass if the server was down. Fixed: transport failure now records a failure.

## Dates/Counts/Paths Audit

- **Date**: Today is 2026-07-13. Neither LOG nor REPORT mention specific dates. No date issues.
- **Counts**: REPORT originally claimed "15 modified + 4 new files" but `git diff --stat` shows 13 modified. **Fixed** to "13 modified + 4 new files".
- **Paths**: All file paths in LOG and REPORT match actual files in the diff. No phantom paths.

## Code Quality

- **Dead code**: `AbortController`/`AbortSignal` pattern in both tool handlers was dead — timeout fired but nothing connected. **Fixed**: moved timeout into `runLighthouse()` where it kills Chrome.
- **Shell errors**: `local text` at top level is a bash runtime error (`local: can only be used in a function`). **Fixed**: removed `local`.
- **No scaffolding**: No TODO stubs, commented-out code, or placeholder implementations in the diff.

## Summary

**5 issues found and fixed** (1 CRITICAL, 2 functional, 2 minor):

1. **CRITICAL**: AbortController dead code — timeout never killed Chromium.
2. Functional: `local text` outside function — bash runtime error on non-quick runs.
3. Functional: file:// rejection test hollow on transport failure — would false-pass on dead server.
4. Minor: Missing data: and chrome:// rejection tests (handoff requirement).
5. Minor: REPORT claimed wrong modified-file count (15 → 13).
